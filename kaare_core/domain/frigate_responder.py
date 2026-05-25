import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

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
    if min_dur > 0 and duration < min_dur:
        return False
    return True


def _role_display(role: str) -> str:
    cfg = _cfg()
    roles = cfg.get("roles", {})
    r = roles.get(role, {})
    return r.get("display_name") or role


def build_context_prompt(event: dict, cam_cfg: dict) -> str:
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

    now = datetime.now()
    time_str = now.strftime("%H:%M")
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = "morgen"
    elif 12 <= hour < 17:
        time_of_day = "ettermiddag"
    elif 17 <= hour < 22:
        time_of_day = "kveld"
    else:
        time_of_day = "natt"

    lines = [
        f"Tidspunkt: {time_str} ({time_of_day})",
        f"Kamera: {display} (rolle: {role_display})",
        f"Detektert: {label} — sikkerhet {score}%",
        f"Varighet: {duration:.1f} sekunder",
    ]
    if zones:
        lines.append(f"Soner: {', '.join(zones)}")
    if sub_label:
        face_pct = int((sub_score or 0) * 100)
        lines.append(f"Ansiktsgjenkjenning: {sub_label} ({face_pct}% sikkerhet)")

    context = "\n".join(lines)

    return (
        f"Du ser et overvåkningsbilde fra Kåres kamerasystem.\n\n"
        f"{context}\n\n"
        f"Beskriv kort hva du ser på bildet. Er det noe uvanlig eller noe som bør handles på? "
        f"Svar på norsk, maks 3 setninger."
    )


async def analyze_event(event: dict) -> dict | None:
    from adapters.frigate_adapter import fetch_event_snapshot, fetch_snapshot_b64

    cfg = _cfg()
    cameras = cfg.get("cameras", {})
    camera = event.get("camera", "")
    cam_cfg = cameras.get(camera, {})

    # Fetch snapshot — prefer event-specific snapshot, fall back to latest
    img_b64: str | None = None
    event_id = event.get("event_id", "")
    if event_id:
        try:
            raw = await asyncio.wait_for(fetch_event_snapshot(event_id), timeout=10)
            if raw:
                img_b64 = base64.b64encode(raw).decode()
        except Exception as e:
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

    prompt = build_context_prompt(event, cam_cfg)

    try:
        from adapters.llm_adapter import ask_llm
        result = await ask_llm(prompt, images=[img_b64])
        analysis_text = result.get("text", "").strip()
    except Exception as e:
        log.error("VLM analysis failed for event %s: %s", event_id, e)
        return None

    return {
        "analysis": analysis_text,
        "event": event,
        "camera_display": cam_cfg.get("display_name") or camera,
        "role": cam_cfg.get("role", ""),
        "img_b64": img_b64,
    }


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
        lines = [l for l in _ANALYSIS_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
        drop = max(1, len(lines) // 5)  # drop oldest 20%
        lines = lines[drop:]
        _ANALYSIS_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Analysis log trimmed: removed %d oldest entries", drop)
    except Exception as e:
        log.warning("Analysis log cleanup error: %s", e)


def _write_analysis_log(entry: dict) -> None:
    try:
        _ANALYSIS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_ANALYSIS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Could not write frigate_analysis.log: %s", e)


async def handle_end_event(event: dict) -> None:
    camera = event.get("camera", "")
    label = event.get("label", "")
    score = float(event.get("score", 0))
    duration = float(event.get("duration", 0))

    if not should_analyze(camera, label, score, duration):
        log.debug("Skipping event: camera=%s label=%s score=%.2f duration=%.1f", camera, label, score, duration)
        return

    log.info("Analyzing Frigate end-event: camera=%s label=%s score=%.2f duration=%.1fs", camera, label, score, duration)

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
        "ts": datetime.now(timezone.utc).isoformat(),
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
        "analysis": result["analysis"],
    }
    _write_analysis_log(entry)
    if log_max != 0:
        _cleanup_analysis_log(log_max)
    log.info("Analysis logged for %s/%s: %s", camera, label, result["analysis"][:120])
