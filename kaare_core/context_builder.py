"""
Builds a compact situational context block injected into Kåre's system prompt.

The block gives Kåre always-available awareness:
  - Who is speaking, from which node and network
  - Household mode (home/away)
  - Recent activity from other users/nodes (from STM)
  - Frigate recent detections (Phase 2)
  - HA entity states (Phase 3)
  - Zigbee sensor states (Phase 4)
  - Media activity (Phase 5)

All sources are optional — missing sources are silently omitted.
Sensor data is hedged: "likely" not "certain".

Per-request context (node_name, network_context) is carried via contextvars
so callers don't need to thread them through every function signature.
"""

from __future__ import annotations

import logging
import subprocess
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import json

import yaml

logger = logging.getLogger("context_builder")

_NODES_PATH          = Path("/kaare/configs/nodes.yaml")
_SERVICES_PATH       = Path("/kaare/configs/services.yaml")
_FRIGATE_LOG_PATH    = Path("/kaare/logs/frigate_mqtt.log")
_HA_CONTEXT_PATH     = Path("/kaare/state/ha_context.json")
_ZIGBEE_CONTEXT_PATH = Path("/kaare/state/zigbee_context.json")
_PLEX_CACHE_PATH     = Path("/kaare/state/plex_cache.json")
_FRIGATE_TAIL_BYTES  = 32_000  # ~200 lines — enough for 30 min of Frigate activity

# Per-request context vars — set by router_generate, read by _build_system
_REQUEST_NODE: ContextVar[str]    = ContextVar("request_node",    default="")
_REQUEST_NETWORK: ContextVar[str] = ContextVar("request_network", default="local")


def set_request_context(node_name: str, network_context: str) -> None:
    """Called once per request in router_generate before calling ask_llm_with_tools."""
    _REQUEST_NODE.set(node_name or "")
    _REQUEST_NETWORK.set(network_context or "local")


def _node_room(node_id: str) -> str:
    """Return human-readable room for a node ID, or node_id if not found."""
    if not node_id:
        return ""
    try:
        data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
        node = data.get("nodes", {}).get(node_id, {})
        return node.get("room", node_id)
    except Exception:
        return node_id


def _minutes_ago(ts_iso: str) -> int:
    """Return minutes since an ISO timestamp, or -1 on error."""
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return -1


def _recent_node_activity(user_id: str, limit_minutes: int = 30) -> list[dict]:
    """
    Scan STM across all users for recent dialog turns from other users/nodes.
    Returns list of {user_id, node, minutes_ago} sorted by recency.
    """
    results = []
    try:
        from kaare_core.app_state import STM_REGISTRY
        if not STM_REGISTRY:
            return []
        for uid, stm in STM_REGISTRY._users.items():
            if uid == user_id:
                continue
            try:
                for turn in reversed(stm._dialog[-10:]):
                    if turn.role != "user":
                        continue
                    mins = _minutes_ago(turn.ts)
                    if mins < 0 or mins > limit_minutes:
                        continue
                    results.append({"user_id": uid, "minutes_ago": mins})
                    break
            except Exception:
                pass
    except Exception as e:
        logger.debug("[context_builder] STM scan error: %s", e)
    return sorted(results, key=lambda x: x["minutes_ago"])


# ── i18n labels ───────────────────────────────────────────────────────────────

_LABELS: dict[str, dict[str, str]] = {
    "header": {
        "nb": "Situasjon",
        "en": "Situation",
        "de": "Situation",
    },
    "chat": {
        "nb": "chat",
        "en": "chat",
        "de": "chat",
    },
    "node_local": {
        "nb": "{room}-node, lokalt nett",
        "en": "{room}-node, local network",
        "de": "{room}-Node, lokales Netz",
    },
    "node_vpn": {
        "nb": "{room}-node, VPN",
        "en": "{room}-node, VPN",
        "de": "{room}-Node, VPN",
    },
    "node_external": {
        "nb": "{room}-node, eksternt",
        "en": "{room}-node, external",
        "de": "{room}-Node, extern",
    },
    "chat_local": {
        "nb": "chat, lokalt nett",
        "en": "chat, local network",
        "de": "chat, lokales Netz",
    },
    "chat_vpn": {
        "nb": "chat, VPN",
        "en": "chat, VPN",
        "de": "chat, VPN",
    },
    "chat_external": {
        "nb": "chat, eksternt",
        "en": "chat, external",
        "de": "chat, extern",
    },
    "household_home": {
        "nb": "Husstand: hjemme",
        "en": "Household: home",
        "de": "Haushalt: zu Hause",
    },
    "household_away": {
        "nb": "Husstand: bortreise",
        "en": "Household: away",
        "de": "Haushalt: abwesend",
    },
    "other_active": {
        "nb": "{user} aktiv for {mins} min siden",
        "en": "{user} active {mins} min ago",
        "de": "{user} aktiv vor {mins} Min.",
    },
    "min_ago": {
        "nb": "{mins} min siden",
        "en": "{mins} min ago",
        "de": "vor {mins} Min.",
    },
    "just_now": {
        "nb": "akkurat nå",
        "en": "just now",
        "de": "gerade eben",
    },
    "frigate_person": {
        "nb": "person",
        "en": "person",
        "de": "Person",
    },
    "frigate_car": {
        "nb": "bil",
        "en": "car",
        "de": "Auto",
    },
    "frigate_animal": {
        "nb": "dyr",
        "en": "animal",
        "de": "Tier",
    },
    "frigate_motion": {
        "nb": "bevegelse",
        "en": "motion",
        "de": "Bewegung",
    },
    "frigate_detection": {
        "nb": "{label} ved {cam} ({pct}%), {ago}",
        "en": "{label} at {cam} ({pct}%), {ago}",
        "de": "{label} bei {cam} ({pct}%), {ago}",
    },
    "frigate_named": {
        "nb": "{name} ({label}) ved {cam}, {ago}",
        "en": "{name} ({label}) at {cam}, {ago}",
        "de": "{name} ({label}) bei {cam}, {ago}",
    },
    "ha_entity_state": {
        "nb": "{name}: {state} [HA, {ago}]",
        "en": "{name}: {state} [HA, {ago}]",
        "de": "{name}: {state} [HA, {ago}]",
    },
    "ha_automation": {
        "nb": "Automasjon: {name}, {ago}",
        "en": "Automation: {name}, {ago}",
        "de": "Automation: {name}, {ago}",
    },
    "media_mpd_playing": {
        "nb": "Musikk aktiv (MPD)",
        "en": "Music active (MPD)",
        "de": "Musik aktiv (MPD)",
    },
    "media_plex_playing": {
        "nb": "Plex aktiv: {title}",
        "en": "Plex active: {title}",
        "de": "Plex aktiv: {title}",
    },
}


def _l(key: str, lang: str, **kwargs: str) -> str:
    lang = lang if lang in ("nb", "en", "de") else "nb"
    tmpl = _LABELS.get(key, {}).get(lang, _LABELS.get(key, {}).get("nb", key))
    try:
        return tmpl.format(**kwargs) if kwargs else tmpl
    except Exception:
        return tmpl


def _source_label(node_name: str, network_context: str, lang: str) -> str:
    """Return a compact source description: 'kjøkken-node, lokalt nett' or 'chat, VPN'."""
    if node_name:
        room = _node_room(node_name)
        key = f"node_{network_context}" if network_context in ("local", "vpn", "external") else "node_local"
        return _l(key, lang, room=room)
    else:
        key = f"chat_{network_context}" if network_context in ("local", "vpn", "external") else "chat_local"
        return _l(key, lang)


def _time_label(minutes_ago: int, lang: str) -> str:
    if minutes_ago <= 0:
        return _l("just_now", lang)
    return _l("min_ago", lang, mins=str(minutes_ago))


# ── Frigate recent activity ───────────────────────────────────────────────────

def _load_camera_names() -> dict[str, str]:
    """Load camera_names from configs/services.yaml (frigate.camera_names)."""
    try:
        data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        return data.get("frigate", {}).get("camera_names", {}) or {}
    except Exception:
        return {}


def _frigate_label_key(raw_label: str) -> str:
    """Map Frigate label to i18n key."""
    mapping = {"person": "frigate_person", "car": "frigate_car",
               "dog": "frigate_animal", "cat": "frigate_animal"}
    return mapping.get(raw_label.lower(), "frigate_motion")


def _frigate_recent_block(lang: str, limit_minutes: int = 30, max_lines: int = 3) -> str:
    """
    Return a compact summary of recent Frigate detections (last limit_minutes).
    Reads frigate_mqtt.log directly — no network call, no embedding.
    Returns empty string if Frigate not configured, log missing, or no recent events.
    """
    if not _FRIGATE_LOG_PATH.exists():
        return ""
    try:
        # Tail last _FRIGATE_TAIL_BYTES to avoid reading a large log file
        with _FRIGATE_LOG_PATH.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _FRIGATE_TAIL_BYTES))
            raw = fh.read().decode("utf-8", errors="replace")

        camera_names = _load_camera_names()
        cutoff = datetime.now(timezone.utc).timestamp() - limit_minutes * 60
        now_utc = datetime.now(timezone.utc)

        # Parse lines — only detection_end events (not noisy updates)
        # Deduplicate by (camera, label) — keep most recent per combination
        seen: dict[tuple, dict] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("stage") != "detection_end":
                continue
            try:
                ts_dt = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
            except Exception:
                continue
            if ts_dt.timestamp() < cutoff:
                continue
            cam_raw = ev.get("camera", "")
            label   = ev.get("label", "motion")
            key     = (cam_raw, label)
            # Keep only the most recent event per camera+label
            if key not in seen or ts_dt > datetime.fromisoformat(
                seen[key]["ts"].replace("Z", "+00:00")
            ):
                seen[key] = ev

        if not seen:
            return ""

        # Sort by ts descending, take max_lines
        events = sorted(seen.values(),
                        key=lambda e: e.get("ts", ""), reverse=True)[:max_lines]

        lines = []
        for ev in events:
            cam_raw  = ev.get("camera", "")
            cam      = camera_names.get(cam_raw, cam_raw)
            label    = ev.get("label", "motion")
            score    = int(float(ev.get("score", 0)) * 100)
            sub      = ev.get("sub_label")
            try:
                ts_dt = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
                mins  = max(0, int((now_utc - ts_dt).total_seconds() / 60))
            except Exception:
                mins  = -1

            ago = _time_label(mins, lang) if mins >= 0 else ""
            lbl_str = _l(_frigate_label_key(label), lang)

            if sub:
                line = _l("frigate_named", lang, name=sub, label=lbl_str, cam=cam, ago=ago)
            else:
                line = _l("frigate_detection", lang, label=lbl_str, cam=cam, pct=str(score), ago=ago)
            lines.append(f"• {line}")

        return "\n".join(lines)

    except Exception as e:
        logger.debug("[context_builder] frigate block error: %s", e)
        return ""


# ── HA entity state + automations ────────────────────────────────────────────

def _ha_state_block(lang: str, max_entities: int = 5, max_automations: int = 2) -> str:
    """
    Return compact HA entity states and recent automations from ha_context.json.
    Written by ha_context_task background task. Empty if file missing or no entities.
    """
    if not _HA_CONTEXT_PATH.exists():
        return ""
    try:
        ctx = json.loads(_HA_CONTEXT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""

    lines = []
    now_utc = datetime.now(timezone.utc)

    # Entity states
    entities: dict = ctx.get("entities", {})
    for entity_id, info in list(entities.items())[:max_entities]:
        state = info.get("state", "")
        ts_str = info.get("ts", "")
        # Use last part of entity_id as friendly name (e.g. "binary_sensor.ytterdor" → "ytterdor")
        name = entity_id.split(".")[-1].replace("_", " ")
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            mins  = max(0, int((now_utc - ts_dt).total_seconds() / 60))
            ago   = _time_label(mins, lang)
        except Exception:
            ago = ""
        lines.append(f"• {_l('ha_entity_state', lang, name=name, state=state, ago=ago)}")

    # Recent automations
    automations: list = ctx.get("recent_automations", [])
    for auto in automations[:max_automations]:
        name = auto.get("name", "")
        ts_str = auto.get("ts", "")
        if not name:
            continue
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            mins  = max(0, int((now_utc - ts_dt).total_seconds() / 60))
            ago   = _time_label(mins, lang)
        except Exception:
            ago = ""
        lines.append(f"• {_l('ha_automation', lang, name=name, ago=ago)}")

    return "\n".join(lines)


# ── Zigbee2MQTT direct sensor state ──────────────────────────────────────────

def _zigbee_block(lang: str) -> str:
    """
    Return compact Zigbee sensor states from state/zigbee_context.json.
    Written by mqtt_adapter._process_zigbee_awareness().
    Zigbee state takes precedence over HA state for the same physical sensor.
    Returns empty string if file missing or no entries.
    """
    if not _ZIGBEE_CONTEXT_PATH.exists():
        return ""
    try:
        ctx = json.loads(_ZIGBEE_CONTEXT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""

    if not ctx:
        return ""

    now_utc = datetime.now(timezone.utc)
    lines = []
    for _topic, info in ctx.items():
        label = info.get("label", _topic.split("/")[-1])
        state = info.get("state", "")
        ts_str = info.get("ts", "")
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            mins  = max(0, int((now_utc - ts_dt).total_seconds() / 60))
            ago   = _time_label(mins, lang)
        except Exception:
            ago = ""
        suffix = f" [{ago}]" if ago else ""
        lines.append(f"• {label}: {state} [Zigbee{suffix}]")

    return "\n".join(lines)


# ── Media activity ───────────────────────────────────────────────────────────

def _media_block(lang: str) -> str:
    """
    Return a compact media activity summary.
    MPD: checked via subprocess (sync, local, instant).
    Plex: read from state/plex_cache.json (updated by media_context_task background task).
    Returns empty string if no media is active.
    """
    lines = []

    # MPD — check if playing
    try:
        result = subprocess.run(
            ["mpc", "status"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and "[playing]" in result.stdout:
            lines.append(f"• {_l('media_mpd_playing', lang)}")
    except Exception:
        pass

    # Plex — read from cache file written by media_context_task
    try:
        if _PLEX_CACHE_PATH.exists():
            cache = json.loads(_PLEX_CACHE_PATH.read_text(encoding="utf-8"))
            sessions = cache.get("sessions", [])
            if sessions:
                # Show first active session title
                first = sessions[0]
                title = first.get("title", "")
                if title:
                    lines.append(f"• {_l('media_plex_playing', lang, title=title)}")
    except Exception:
        pass

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def build_situational_context(user_id: str, lang: str = "nb") -> str:
    """
    Build a compact situational context block for injection into the system prompt.
    Never raises — all errors produce an empty string or partial block.
    """
    try:
        node_name       = _REQUEST_NODE.get()
        network_context = _REQUEST_NETWORK.get()

        lines: list[str] = []

        # Current speaker line
        source = _source_label(node_name, network_context, lang)
        lines.append(f"• {user_id}: {source}")

        # Household mode
        try:
            from kaare_core.tools.household_state import is_away
            if is_away():
                lines.append(f"• {_l('household_away', lang)}")
            else:
                lines.append(f"• {_l('household_home', lang)}")
        except Exception:
            pass

        # Other recent users
        try:
            others = _recent_node_activity(user_id, limit_minutes=30)
            for entry in others[:2]:
                uid = entry["user_id"]
                mins = entry["minutes_ago"]
                lines.append(f"• {_l('other_active', lang, user=uid, mins=str(mins))}")
        except Exception:
            pass

        # Frigate recent detections (Phase 2)
        try:
            frigate_block = _frigate_recent_block(lang)
            if frigate_block:
                lines.extend(frigate_block.split("\n"))
        except Exception:
            pass

        # HA entity states + recent automations (Phase 3)
        try:
            ha_block = _ha_state_block(lang)
            if ha_block:
                lines.extend(ha_block.split("\n"))
        except Exception:
            pass

        # Zigbee2MQTT direct sensor states (Phase 4) — shown after HA, more direct/lower latency
        try:
            zigbee_block = _zigbee_block(lang)
            if zigbee_block:
                lines.extend(zigbee_block.split("\n"))
        except Exception:
            pass

        # Media activity (Phase 5)
        try:
            media_block = _media_block(lang)
            if media_block:
                lines.extend(media_block.split("\n"))
        except Exception:
            pass

        if not lines:
            return ""

        now_str = datetime.now().strftime("%H:%M")
        header = f"{_l('header', lang)} ({now_str}):"
        return header + "\n" + "\n".join(lines)

    except Exception as e:
        logger.debug("[context_builder] build_situational_context error: %s", e)
        return ""
