import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import websockets
import yaml

from kaare_core.config import get_local_tz
from kaare_core.config import get_service as _svc
from kaare_core.service_auth import service_headers
from kaare_core.tools.i18n import _T, get_lang, t

log = logging.getLogger(__name__)

_HA_TOKEN_PATH = Path("/kaare/configs/ha_token.env")
_NODES_PATH = Path("/kaare/configs/nodes.yaml")
_ALIASES_PATH = "/kaare/configs/aliases.yaml"

HA_GATEWAY_URL = _svc("internal", "ha_gateway")

HA_TOOLS = {
    "ha_read",
    "ha_control",
    "les_ha",
    "les_alias_lista",
    "les_ha_status",
    "styr_enhet",
}


def _domain_label(domain: str, lang: str) -> str:
    key = f"ha_domain_{domain}"

    return t(key, lang) if key in _T else domain


def get_ha_token() -> str:
    token = os.environ.get("HA_TOKEN", "")
    if token:
        return token
    if _HA_TOKEN_PATH.exists():
        for line in _HA_TOKEN_PATH.read_text().splitlines():
            if line.startswith("HA_TOKEN="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token
    raise ValueError("HA_TOKEN not found — check configs/ha_token.env")


def resolve_node_entity(client_hint: str) -> str | None:
    """Return HA entity_id for a node matching client_hint (fuzzy, case-insensitive)."""
    if not _NODES_PATH.exists():
        return None
    nodes = yaml.safe_load(_NODES_PATH.read_text()).get("nodes", {})
    needle = client_hint.lower().replace(" ", "_")
    for node_id, cfg in nodes.items():
        nid = node_id.lower().replace(" ", "_")
        room = cfg.get("room", "").lower().replace(" ", "_")
        if needle == nid:
            return cfg.get("entity_id")
        if needle in nid:
            return cfg.get("entity_id")
        if needle in room:
            return cfg.get("entity_id")
        if needle in cfg.get("description", "").lower():
            return cfg.get("entity_id")
    return None


def _load_aliases() -> dict:
    try:
        with open(_ALIASES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _format_aliases(room: str | None = None, lang: str = "nb") -> str:
    data = _load_aliases()
    aliases = data.get("aliases", {})
    rooms_cfg = data.get("rooms", {})

    if not aliases:
        return t("ha_no_devices", lang)

    def _label(entity_id: str) -> str:
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        return _domain_label(domain, lang)

    room_kw: dict[str, list] = {r: [str(k).lower() for k in kws] for r, kws in rooms_cfg.items()}

    groups: dict[str, list] = {}
    unmatched: list = []
    for key in aliases:
        kl = key.lower()
        best_room, best_len = None, 0
        for r, kws in room_kw.items():
            for kw in kws:
                if kw in kl and len(kw) > best_len:
                    best_room, best_len = r, len(kw)
        if best_room:
            groups.setdefault(best_room, []).append(key)
        else:
            unmatched.append(key)

    if room:
        rl = room.lower()
        target, best_len = None, 0
        for r, kws in room_kw.items():
            for kw in kws:
                if kw == rl and len(kw) > best_len:
                    target, best_len = r, len(kw)
        if not target and rl in room_kw:
            target = rl
        if target and target in groups:
            lines = [t("ha_known_in_room", lang, room=room)]
            for k in groups[target]:
                eid = aliases[k]
                lines.append(f"  '{k}' → {eid}  [{_label(eid)}]")
            return "\n".join(lines)
        return t("ha_room_not_found", lang, room=room)

    known_rooms = sorted(groups.keys())
    lines = [
        t("ha_known_rooms", lang),
        ", ".join(r.replace("_", " ") for r in known_rooms),
        "",
        t("ha_room_hint", lang),
        t("ha_room_example", lang),
    ]
    return "\n".join(lines)


async def _control_entity(
    entity_id: str,
    action: str,
    brightness_pct: int | None = None,
    color_temp_kelvin: int | None = None,
    rgb_color: list | None = None,
    lang: str = "nb",
) -> str:
    if not entity_id or not action:
        return t("ha_entity_action_required", lang)

    params: dict[str, Any] = {}
    if action == "set_level" and brightness_pct is not None:
        params["level"] = int(brightness_pct)
    elif action == "set_color_temp" and color_temp_kelvin is not None:
        params["color_temp_kelvin"] = int(color_temp_kelvin)
    elif action == "set_color" and rgb_color is not None:
        params["rgb_color"] = rgb_color

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{HA_GATEWAY_URL}/api/ha_apply",
                json={
                    "action": action,
                    "entity_id": entity_id,
                    "params": params,
                    "source": "kare-tool",
                },
                headers=service_headers(),
            )
            r.raise_for_status()
            data = r.json()
        if data.get("status") == "ok":
            if action == "turn_on":
                msg = t("ha_turned_on", lang, entity_id=entity_id)
            elif action == "turn_off":
                msg = t("ha_turned_off", lang, entity_id=entity_id)
            elif action == "set_level":
                msg = t(
                    "ha_brightness_set", lang, entity_id=entity_id, brightness_pct=brightness_pct
                )
            elif action == "set_color_temp":
                msg = t(
                    "ha_color_temp_set",
                    lang,
                    entity_id=entity_id,
                    color_temp_kelvin=color_temp_kelvin,
                )
            elif action == "set_color":
                msg = t("ha_color_set", lang, entity_id=entity_id)
            else:
                msg = t("ha_action_done", lang, entity_id=entity_id, action=action)
            return f"OK: {msg}"
        return t("ha_status_response", lang, status=data.get("status", "ukjent"))
    except Exception as e:
        return t("ha_call_error", lang, error=e)


def _read_ha_url() -> str:
    """Return the HA base URL from services.yaml, or empty string if not configured."""
    try:
        return (_svc("home_assistant", "url") or "").rstrip("/")
    except Exception:
        return ""


async def _ha_ws_call(ha_url: str, token: str, message: dict, timeout: float = 10.0) -> Any:
    """One command over Home Assistant's WebSocket API: auth → command → result. Long-term statistics
    (`recorder/statistics_during_period`) exist only here — there is no REST endpoint for them."""
    ws_url = ha_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/websocket"
    async with asyncio.timeout(timeout):
        async with websockets.connect(ws_url, open_timeout=timeout) as ws:
            if json.loads(await ws.recv()).get("type") != "auth_required":
                raise RuntimeError("unexpected HA WebSocket greeting")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            if json.loads(await ws.recv()).get("type") != "auth_ok":
                raise RuntimeError("HA WebSocket authentication failed")
            await ws.send(json.dumps({"id": 1, **message}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") == 1 and reply.get("type") == "result":
                    if not reply.get("success"):
                        raise RuntimeError((reply.get("error") or {}).get("message") or "HA error")
                    return reply.get("result")


def _parse_ha_time(value: Any) -> datetime:
    """HA statistics give epoch milliseconds; REST history gives ISO strings."""
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, UTC)
    return datetime.fromisoformat(str(value))


async def _ha_state_changes(
    ha_url: str, headers: dict, entity_id: str, start: datetime, end: datetime, days: int, lang: str
) -> str:
    """Raw state changes from HA's REST history — for entities without long-term statistics (switches,
    binary sensors, sensors without state_class). Only as far back as HA's recorder keeps history."""
    max_rows = 40
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{ha_url}/api/history/period/{start.isoformat()}",
                params={
                    "filter_entity_id": entity_id,
                    "end_time": end.isoformat(),
                    "minimal_response": "",
                    "no_attributes": "",
                },
                headers=headers,
            )
    except Exception as exc:
        return t("ha_history_error", lang, entity_id=entity_id, error=exc)
    if r.status_code != 200:
        return t("ha_history_error", lang, entity_id=entity_id, error=f"HTTP {r.status_code}")
    series = r.json()
    changes = series[0] if series and isinstance(series[0], list) else []
    if not changes:
        return t("ha_history_no_changes", lang, entity_id=entity_id, days=days)
    try:
        local_tz = get_local_tz()
    except Exception:
        local_tz = ZoneInfo("UTC")
    rows = []
    for ch in changes[-max_rows:]:
        try:
            when = _parse_ha_time(ch.get("last_changed")).astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
        except Exception:
            when = str(ch.get("last_changed", "?"))[:16]
        rows.append(f"  {when} → {ch.get('state')}")
    lines = [t("ha_history_changes_header", lang, entity_id=entity_id, days=days), *rows]
    if len(changes) > max_rows:
        lines.append(t("ha_history_changes_truncated", lang, shown=max_rows, total=len(changes)))
    return "\n".join(lines)


async def _ha_sensor_history(entity_id: str, days: int, period: str, lang: str) -> str:
    """
    Long-term statistics for a HA entity (mean/min/max/change per day/week/month) via the recorder's
    WebSocket command `recorder/statistics_during_period`. Only entities with a state_class have them;
    for the rest the raw state changes from the REST history are returned instead.
    """
    ha_url = _read_ha_url()
    try:
        token = get_ha_token()
    except ValueError:
        return t("ha_history_not_configured", lang)
    if not ha_url:
        return t("ha_history_not_configured", lang)

    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    message = {
        "type": "recorder/statistics_during_period",
        "start_time": start.isoformat(),
        "end_time": now.isoformat(),
        "statistic_ids": [entity_id],
        "period": period,
        "types": ["mean", "min", "max", "change"],
    }

    async def _state() -> httpx.Response:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(f"{ha_url}/api/states/{entity_id}", headers=headers)

    stats, state_resp = await asyncio.gather(
        _ha_ws_call(ha_url, token, message), _state(), return_exceptions=True
    )

    unit = ""
    if not isinstance(state_resp, BaseException):
        if state_resp.status_code == 404:
            return t("ha_entity_not_found", lang, entity_id=entity_id)
        if state_resp.status_code == 200:
            unit_raw = state_resp.json().get("attributes", {}).get("unit_of_measurement", "")
            unit = f" {unit_raw}" if unit_raw else ""

    if isinstance(stats, BaseException):
        return t("ha_history_error", lang, entity_id=entity_id, error=stats)

    entries: list[dict] = (stats or {}).get(entity_id, [])
    if not entries:
        return await _ha_state_changes(ha_url, headers, entity_id, start, now, days, lang)

    # Resolve period label for header
    period_label = t(f"ha_history_period_{period}", lang)
    header = t("ha_history_header", lang, entity_id=entity_id, days=days, period=period_label)

    # Build per-period rows
    # Use local timezone for date display if available
    try:
        local_tz = get_local_tz()
    except Exception:
        local_tz = ZoneInfo("UTC")

    rows: list[str] = []
    all_means: list[float] = []
    all_mins: list[float] = []
    all_maxes: list[float] = []
    all_changes: list[float] = []

    for entry in entries:
        try:
            date_str = _parse_ha_time(entry["start"]).astimezone(local_tz).strftime("%Y-%m-%d")
        except Exception:
            date_str = str(entry.get("start", "?"))[:10]

        parts: list[str] = []
        if (v := entry.get("mean")) is not None:
            all_means.append(float(v))
            parts.append(
                f"snitt {float(v):.1f}{unit}"
                if lang == "nb"
                else f"avg {float(v):.1f}{unit}"
                if lang == "en"
                else f"Durchschn. {float(v):.1f}{unit}"
            )
        if (v := entry.get("min")) is not None:
            all_mins.append(float(v))
            parts.append(f"min {float(v):.1f}{unit}")
        if (v := entry.get("max")) is not None:
            all_maxes.append(float(v))
            parts.append(
                f"maks {float(v):.1f}{unit}" if lang == "nb" else f"max {float(v):.1f}{unit}"
            )
        if (v := entry.get("change")) is not None and v is not None:
            all_changes.append(float(v))
            parts.append(f"Δ {float(v):.1f}{unit}")
        rows.append(f"  {date_str}: {', '.join(parts)}" if parts else f"  {date_str}: —")

    # Build summary
    summary_parts: list[str] = []
    if all_maxes:
        summary_parts.append(
            t(
                "ha_history_summary",
                lang,
                max_val=max(all_maxes),
                mean_val=sum(all_means) / len(all_means) if all_means else 0,
                min_val=min(all_mins) if all_mins else 0,
                unit=unit,
            )
        )
    # Accumulated total from change values (useful for precipitation sensors that reset daily)
    if all_changes:
        total = sum(all_changes)
        summary_parts.append(t("ha_history_total", lang, total=total, unit=unit))

    lines = [header, *rows]
    if summary_parts:
        lines.append("")
        lines.extend(summary_parts)

    return "\n".join(lines)


async def _read_ha_status(entity_id: str, lang: str = "nb") -> str:
    if not entity_id:
        return t("ha_entity_id_required", lang)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{HA_GATEWAY_URL}/api/ha_status/{entity_id}", headers=service_headers()
            )
            if r.status_code == 404:
                return t("ha_entity_not_found", lang, entity_id=entity_id)
            r.raise_for_status()
            data = r.json()
        state = data.get("state", "ukjent")
        unit = data.get("unit", "")
        friendly = data.get("friendly", entity_id)
        value = f"{state} {unit}".strip()
        return f"{friendly}: {value}"
    except Exception as e:
        return t("ha_read_error", lang, entity_id=entity_id, error=e)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in ("ha_read", "les_ha"):
        action = arguments.get("action", "")
        if action == "room_list":
            return _format_aliases(None, lang=lang)
        if action == "room_devices":
            return _format_aliases(arguments.get("room"), lang=lang)
        if action == "status":
            return await _read_ha_status(arguments.get("entity_id", ""), lang=lang)
        return f"Unknown action for ha_read: '{action}'. Valid: room_list, room_devices, status."

    if name == "les_alias_lista":
        return _format_aliases(arguments.get("room"), lang=lang)

    if name == "les_ha_status":
        return await _read_ha_status(arguments.get("entity_id", ""), lang=lang)

    if name in ("ha_control", "styr_enhet"):
        action = arguments.get("action", "")

        if action == "ha_history":
            entity_id = arguments.get("entity_id", "").strip()
            if not entity_id:
                return t("ha_history_no_entity", lang)
            days = max(1, min(int(arguments.get("history_days", 7)), 365))
            period = arguments.get("history_period", "day")
            if period not in ("day", "week", "month"):
                period = "day"
            return await _ha_sensor_history(entity_id, days, period, lang)

        if arguments.get("_block_ha_write"):
            return t("ha_blocked_external", lang)
        return await _control_entity(
            entity_id=arguments.get("entity_id", ""),
            action=action,
            brightness_pct=arguments.get("brightness_pct"),
            color_temp_kelvin=arguments.get("color_temp_kelvin"),
            rgb_color=arguments.get("rgb_color"),
            lang=lang,
        )

    return f"[executor_ha] Unknown tool: {name}"
