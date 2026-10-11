import asyncio
import base64
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from adapters.frigate_adapter import derive_hints, fetch_event_snapshot, fetch_snapshot_b64
from adapters.llm_adapter import ask_llm, ask_llm_with_tools
from kaare_core import log_buffer
from kaare_core.background import spawn
from kaare_core.config import now_local
from kaare_core.model_enforcement import mentions_agenda_promise
from kaare_core.notify import Urgency, notify_user
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.executor import execute_tool
from kaare_core.tools.i18n import get_lang, t
from kaare_core.users.store import list_users

log = logging.getLogger("frigate_responder")

_CONFIG_PATH = Path("/kaare/configs/frigate_cameras.yaml")
_ANALYSIS_LOG = Path("/kaare/logs/frigate_analysis.log")
_SNAPSHOT_DIR = Path("/kaare/state/frigate_snapshots")

_SNAPSHOTS_MIN_MB = 100
_SNAPSHOTS_MAX_MB = 10_000
_LOG_MIN_MB = 10
_LOG_MAX_MB = 1_000

# Cached config — reloaded via load_camera_config() on /api/reload
_camera_cfg: dict = {}


def load_camera_config() -> dict:
    global _camera_cfg
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        _camera_cfg = raw
        log.info("frigate_cameras.yaml loaded (%d cameras)", len(raw.get("cameras", {})))
    except Exception as e:
        log.warning("Could not load frigate_cameras.yaml: %s", e)
        _camera_cfg = {}
    return _camera_cfg


def _cfg() -> dict:
    if not _camera_cfg:
        load_camera_config()
    return _camera_cfg


def should_analyze(camera: str, label: str, score: float, duration: float) -> bool:
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return False
    cameras = cfg.get("cameras", {})
    if camera not in cameras:
        return False
    cam = cameras[camera]
    labels = cam.get("labels", {})
    if label not in labels:
        return False
    lcfg = labels[label]
    if not lcfg.get("analyze", False):
        return False
    if score < lcfg.get("min_confidence", 0.0):
        return False
    min_dur = lcfg.get("min_duration_seconds", 0)
    return not (min_dur > 0 and duration < min_dur)


def _role_display(role: str) -> str:
    cfg = _cfg()
    roles = cfg.get("roles", {})
    r = roles.get(role, {})
    return r.get("display_name") or role


def build_context_prompt(event: dict, cam_cfg: dict, lang: str = "nb") -> str:
    camera = event.get("camera", "")
    display = cam_cfg.get("display_name") or camera
    role = cam_cfg.get("role", "")
    role_display = _role_display(role)
    label = event.get("label", "")
    score = int(event.get("top_score", event.get("score", 0)) * 100)
    duration = event.get("duration", 0.0)
    zones = event.get("zones") or []
    sub_label = event.get("sub_label")
    sub_score = event.get("sub_label_score")

    now = now_local()
    time_str = now.strftime("%H:%M")
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = t("frigate_time_morning", lang)
    elif 12 <= hour < 17:
        time_of_day = t("frigate_time_afternoon", lang)
    elif 17 <= hour < 22:
        time_of_day = t("frigate_time_evening", lang)
    else:
        time_of_day = t("frigate_time_night", lang)

    lines = [
        t("frigate_line_time", lang, time=time_str, time_of_day=time_of_day),
        t("frigate_line_camera", lang, display=display, role_display=role_display),
        t("frigate_line_detected", lang, label=label, score=score),
        t("frigate_line_duration", lang, duration=duration),
    ]
    if zones:
        lines.append(t("frigate_line_zones", lang, zones=", ".join(zones)))
    if sub_label:
        face_pct = int((sub_score or 0) * 100)
        lines.append(t("frigate_line_face", lang, sub_label=sub_label, face_pct=face_pct))

    context = "\n".join(lines)

    return t("frigate_prompt_wrapper", lang, context=context)


async def analyze_event(event: dict, img_b64_override: str | None = None) -> dict | None:
    cfg = _cfg()
    cameras = cfg.get("cameras", {})
    camera = event.get("camera", "")
    cam_cfg = cameras.get(camera, {})
    event_id = event.get("event_id", "")

    # Use caller-supplied image (e.g. local snapshot on retry) — skip fetch entirely
    img_b64: str | None = img_b64_override

    if not img_b64:
        if event_id and event.get("has_snapshot", True):
            try:
                raw = await asyncio.wait_for(fetch_event_snapshot(event_id), timeout=10)
                if raw:
                    img_b64 = base64.b64encode(raw).decode()
            except Exception as e:
                if "404" in str(e):
                    # Race condition: snapshot not saved yet — wait and retry once
                    await asyncio.sleep(1.5)
                    try:
                        raw = await asyncio.wait_for(fetch_event_snapshot(event_id), timeout=10)
                        if raw:
                            img_b64 = base64.b64encode(raw).decode()
                    except Exception as e2:
                        log.warning(
                            "Event snapshot failed for %s after retry: %s — trying latest",
                            event_id,
                            e2,
                        )
                else:
                    log.warning("Event snapshot failed for %s: %s — trying latest", event_id, e)

        if not img_b64:
            try:
                img_b64 = await asyncio.wait_for(fetch_snapshot_b64(camera), timeout=10)
            except Exception as e:
                log.warning("Fallback snapshot failed for %s: %s", camera, e)
                return None

    if not img_b64:
        log.warning("No snapshot available for event %s on %s", event_id, camera)
        return None

    lang = get_lang()
    prompt = build_context_prompt(event, cam_cfg, lang=lang)
    rid = f"rid-frigate-{int(time.time() * 1000)}"

    try:
        result = await ask_llm(prompt, images=[img_b64], rid=rid)
        analysis_text = result.get("text", "").strip()
    except Exception as e:
        log.error("VLM analysis failed for event %s: %s", event_id, e)
        return None

    # P118: the analysis call has no tools, so «… til neste dev-møte» could never be kept.
    # When Kåre says it, one background round gives him the agenda tool to write it down.
    if mentions_agenda_promise(analysis_text):
        spawn(_keep_agenda_promise(prompt, analysis_text, event_id, rid, lang))

    return {
        "analysis": analysis_text,
        "event": event,
        "camera_display": cam_cfg.get("display_name") or camera,
        "role": cam_cfg.get("role", ""),
        "img_b64": img_b64,
        "rid": rid,
    }


def _agenda_only_tool(lang: str) -> dict | None:
    """The note tool narrowed to one job: write to the dev-meeting agenda."""
    for tool in get_tools(lang):
        if tool.get("function", {}).get("name") == "note":
            narrowed = json.loads(json.dumps(tool))
            props = narrowed["function"]["parameters"]["properties"]
            props["action"]["enum"] = ["write"]
            props["list_name"]["enum"] = ["agenda"]
            return narrowed
    return None


async def _keep_agenda_promise(
    prompt: str, analysis_text: str, event_id: str, rid: str, lang: str
) -> None:
    """One follow-up round after an event analysis that promised «next dev meeting»: Kåre gets
    only the agenda tool and writes the item himself (source=frigate, pointer to the event).
    Runs as a background task — never delays the analysis, notifications or the log. Text only
    (no image): his own analysis already says what the image showed vs. what Frigate reported."""
    try:
        tool = _agenda_only_tool(lang)
        if tool is None:
            return
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": analysis_text},
            {"role": "user", "content": t("frigate_agenda_followup", lang)},
        ]
        result = await ask_llm_with_tools(
            messages=messages, tools=[tool], rid=rid, user_id="", source="frigate"
        )
        calls = result.get("tool_calls") or []
        if not calls:
            log.info(
                "Agenda follow-up for event %s: no tool call (text=%r)",
                event_id,
                (result.get("text") or "")[:120],
            )
            return
        for tc in calls[:1]:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if fn.get("name") != "note" or not isinstance(args, dict):
                continue
            args.update(
                {
                    "action": "write",
                    "list_name": "agenda",
                    "_rid": rid,
                    "_source": "frigate",
                    "_user_id": "global",
                    "_pointer": f"frigate_analysis.log#event_id={event_id}",
                }
            )
            res = await execute_tool("note", args)
            log.info("Agenda follow-up for event %s: %s", event_id, str(res)[:120])
    except Exception as e:
        log.warning("Agenda follow-up for event %s failed: %s", event_id, e)


def _save_snapshot(event_id: str, img_b64: str) -> Path | None:
    try:
        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        img_bytes = base64.b64decode(img_b64)
        path = _SNAPSHOT_DIR / f"{event_id}.jpg"
        path.write_bytes(img_bytes)
        return path
    except Exception as e:
        log.warning("Could not save snapshot for %s: %s", event_id, e)
        return None


def _cleanup_snapshots(max_mb: int) -> None:
    if max_mb == 0:
        return
    limit = max(_SNAPSHOTS_MIN_MB, min(max_mb, _SNAPSHOTS_MAX_MB)) * 1024 * 1024
    try:
        files = sorted(_SNAPSHOT_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= limit:
                break
            total -= f.stat().st_size
            f.unlink()
            log.debug("Snapshot cleanup: removed %s", f.name)
    except Exception as e:
        log.warning("Snapshot cleanup error: %s", e)


def _cleanup_analysis_log(max_mb: int) -> None:
    if max_mb == 0:
        return
    limit = max(_LOG_MIN_MB, min(max_mb, _LOG_MAX_MB)) * 1024 * 1024
    try:
        if not _ANALYSIS_LOG.exists():
            return
        if _ANALYSIS_LOG.stat().st_size <= limit:
            return
        with log_buffer.locked(_ANALYSIS_LOG):  # no buffered batch lands mid-rewrite (P131)
            lines = [
                ln for ln in _ANALYSIS_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            drop = max(1, len(lines) // 5)  # drop oldest 20%
            lines = lines[drop:]
            _ANALYSIS_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Analysis log trimmed: removed %d oldest entries", drop)
    except Exception as e:
        log.warning("Analysis log cleanup error: %s", e)


def _write_analysis_log(entry: dict) -> None:
    try:
        log_buffer.write(_ANALYSIS_LOG, entry)
    except Exception as e:
        log.warning("Could not write frigate_analysis.log: %s", e)


_NOTIFY_LOG = Path("/kaare/logs/notifications.log")


def _write_notify_log(entry: dict) -> None:
    try:
        log_buffer.write(_NOTIFY_LOG, entry)
    except Exception as e:
        log.warning("[notify_log] write failed: %s", e)


async def _maybe_notify_away(camera: str, label: str, score: float) -> None:
    """Fire notify_user() for admin users when household is away and threat is HIGH."""
    try:
        cam_cfg = _cfg().get("cameras", {}).get(camera, {})
        camera_role = cam_cfg.get("role", "")
        hints = derive_hints({"label": label, "confidence": score, "zones": []}, camera_role)

        if not hints.get("away_mode") or hints.get("priority") != "high":
            return

        admins = [u for u in list_users() if u.get("role") in ("admin", "adult")]
        for user in admins:
            uid = user["username"]
            source_key = f"frigate:{camera}:{label or 'unknown'}"
            lang = "en"
            try:
                lang = get_lang(uid)
            except Exception:
                pass

            if label:
                msg = t("notify_frigate_away_person", lang, camera=camera, label=label)
            else:
                msg = t("notify_frigate_away_unknown", lang, camera=camera)

            # Graduated urgency: known-normal pattern at high-confidence baseline → MEDIUM
            deviation_score = hints.get("deviation_score", 0.5)
            baseline_confidence = hints.get("baseline_confidence", 0.0)
            if baseline_confidence >= 0.5 and deviation_score < 0.3:
                urgency = Urgency.MEDIUM
                urgency_str = "medium"
            else:
                urgency = Urgency.HIGH
                urgency_str = "high"

            await notify_user(uid, msg, urgency=urgency, source_key=source_key)
            _write_notify_log(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "user_id": uid,
                    "urgency": urgency_str,
                    "source_key": source_key,
                    "channel": "sound_node",
                    "camera": camera,
                    "label": label,
                    "score": score,
                    "deviation_score": deviation_score,
                    "baseline_confidence": baseline_confidence,
                }
            )
    except Exception as e:
        log.warning("[frigate_responder] _maybe_notify_away failed: %s", e)


async def handle_end_event(event: dict) -> None:
    camera = event.get("camera", "")
    label = event.get("label", "")
    score = float(event.get("score", 0))
    duration = float(event.get("duration", 0))

    spawn(_maybe_notify_away(camera, label, score))

    if not should_analyze(camera, label, score, duration):
        log.debug(
            "Skipping event: camera=%s label=%s score=%.2f duration=%.1f",
            camera,
            label,
            score,
            duration,
        )
        return

    log.info(
        "Analyzing Frigate end-event: camera=%s label=%s score=%.2f duration=%.1fs",
        camera,
        label,
        score,
        duration,
    )

    result = await analyze_event(event)
    if not result:
        return

    storage = _cfg().get("storage", {})
    snap_max = storage.get("snapshots_max_mb", 500)
    log_max = storage.get("log_max_mb", 50)

    # Save snapshot
    event_id = event.get("event_id", "")
    if snap_max != 0 and result.get("img_b64") and event_id:
        _save_snapshot(event_id, result["img_b64"])
        _cleanup_snapshots(snap_max)

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "camera": camera,
        "display_name": result["camera_display"],
        "role": result["role"],
        "label": label,
        "score": score,
        "top_score": event.get("top_score", score),
        "duration": duration,
        "sub_label": event.get("sub_label"),
        "zones": event.get("zones", []),
        "event_id": event_id,
        "rid": result.get("rid", ""),
        "analysis": result["analysis"],
    }
    _write_analysis_log(entry)
    if log_max != 0:
        _cleanup_analysis_log(log_max)
    log.info("Analysis logged for %s/%s: %s", camera, label, result["analysis"][:120])
