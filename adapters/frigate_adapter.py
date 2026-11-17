# Frigate adapter — HTTP API + event handling
# Reads config from configs/settings.yaml and configs/frigate.env

import base64
import logging
import sys
import threading
import time
from typing import Any

import httpx

sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc
from kaare_core.config import now_local
from kaare_core.domain.frigate_store import camera_config
from kaare_core.normalcy import get_deviation_score as _get_deviation_score
from kaare_core.tools.household_state import is_away
from kaare_core.users import frigate_consent

log = logging.getLogger("frigate_adapter")

_DEDUP_CACHE: dict[str, float] = {}


# ── Config loading ────────────────────────────────────────────────────────────


def _frigate_url() -> str:
    return _svc("frigate", "url") or "http://127.0.0.1:5000"


def is_configured() -> bool:
    """True when the user has entered a Frigate address (Integrations → Frigate). No address = no Frigate."""
    return bool(_svc("frigate", "url"))


def _timeout() -> int:
    return int(_svc("frigate", "timeout") or 10)


def _snapshot_timeout() -> int:
    return int(_svc("frigate", "snapshot_timeout") or 15)


def _camera_map() -> dict[str, str]:
    """Returns {api_name: friendly_name} from services.yaml camera_names."""
    return _svc("frigate", "camera_names") or {}


def _friendly_to_api(name: str) -> str | None:
    """Resolves friendly name or partial match to Frigate API camera name."""
    cam_map = _camera_map()
    name_lower = name.lower().strip()
    # Exact API name match
    if name_lower in cam_map:
        return name_lower
    # Friendly name match
    for api_name, friendly in cam_map.items():
        if name_lower == friendly.lower() or name_lower in friendly.lower():
            return api_name
    return None


# ── HTTP helpers ──────────────────────────────────────────────────────────────


async def fetch_snapshot(camera: str) -> bytes:
    """
    Fetches the latest snapshot JPEG from Frigate for the given camera.
    Camera can be API name or friendly name.
    Returns raw JPEG bytes.
    Raises ValueError if camera not found, httpx errors on network failure.
    """
    api_name = _friendly_to_api(camera) or camera
    url = f"{_frigate_url()}/api/{api_name}/latest.jpg"
    async with httpx.AsyncClient(timeout=_snapshot_timeout()) as client:
        r = await client.get(url)
        if r.status_code == 404:
            raise ValueError(f"Camera '{camera}' not found in Frigate (tried: {api_name})")
        r.raise_for_status()
        return r.content


async def fetch_snapshot_b64(camera: str) -> str:
    """Returns snapshot as base64-encoded string (for use with VLM)."""
    raw = await fetch_snapshot(camera)
    return base64.b64encode(raw).decode("ascii")


async def get_cameras() -> list[dict]:
    """
    Returns list of cameras from Frigate config API.
    Falls back to cameras defined in settings.yaml if API call fails.
    Each entry: {api_name, friendly_name}
    """
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            r = await client.get(f"{_frigate_url()}/api/config")
            r.raise_for_status()
            config = r.json()
            cams = config.get("cameras", {})
            cam_map = _camera_map()
            result = []
            for api_name in cams:
                friendly = cam_map.get(api_name, api_name)
                result.append({"api_name": api_name, "friendly_name": friendly})
            return result
    except Exception as e:
        log.warning(
            "Could not fetch cameras from Frigate API: %s — using settings.yaml fallback", e
        )
        return [{"api_name": k, "friendly_name": v} for k, v in _camera_map().items()]


# Frigate's camera type (`generic` / `lpr` — a dedicated LPR camera), P144-s12. One source = Frigate: KTSB has no
# setting for it. Cached; the camera settings GET refreshes it from the config it already fetches.
_CAMERA_TYPES_TTL_S = 600
_camera_types: dict[str, str] = {}
_camera_types_ts = 0.0


# Frigate's own camera name (`friendly_name`), J24: the last fallback for a camera's display name, after the camera card
# and services.yaml `camera_names`. Read only, cached; filled from any config Frigate already sent, else fetched once when
# a name is needed (a failed fetch is retried after _FRIENDLY_RETRY_S).
_FRIENDLY_TTL_S = 600
_FRIENDLY_RETRY_S = 60
_friendly: dict[str, str] = {}
_friendly_ts = 0.0


def _remember_friendly(config: dict) -> None:
    global _friendly, _friendly_ts
    try:
        cams = config.get("cameras") or {}
        _friendly = {name: c["friendly_name"].strip() for name, c in cams.items()
                     if isinstance(c, dict) and isinstance(c.get("friendly_name"), str) and c["friendly_name"].strip()}
        _friendly_ts = time.monotonic()
    except Exception:
        pass


def _fetch_config_blocking() -> dict | None:
    """One GET of Frigate's /api/config (sync — callers run in a worker thread). None when Frigate does not answer."""
    base = (_frigate_url() or "").rstrip("/")
    if not base:
        return None
    try:
        r = httpx.get(f"{base}/api/config", timeout=min(float(_timeout()), 3.0))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.info("Could not read camera names from Frigate: %s", type(e).__name__)
        return None


def frigate_camera_names() -> dict[str, str]:
    """{camera: Frigate friendly_name}, cached _FRIENDLY_TTL_S. Blocking on a stale cache — call from a worker thread."""
    global _friendly_ts
    now = time.monotonic()
    if _friendly_ts and now - _friendly_ts < _FRIENDLY_TTL_S:
        return _friendly
    cfg = _fetch_config_blocking()
    if cfg is None:
        _friendly_ts = now - _FRIENDLY_TTL_S + _FRIENDLY_RETRY_S
        return _friendly
    _remember_friendly(cfg)
    return _friendly


_friendly_lock = threading.Lock()
_friendly_warming = False


def warm_camera_names() -> threading.Thread | None:
    """DT04-S05l: refresh Frigate's camera names in a daemon thread when the cache is cold or stale, so code on the event
    loop (fetch=False) gets names after a restart too. Never blocks, at most one refresh at a time; returns the thread, or
    None when nothing was started. Never raises."""
    global _friendly_warming
    try:
        if _friendly_ts and time.monotonic() - _friendly_ts < _FRIENDLY_TTL_S:
            return None
        with _friendly_lock:
            if _friendly_warming:
                return None
            _friendly_warming = True

        def _run() -> None:
            global _friendly_warming
            try:
                frigate_camera_names()
            except Exception as e:
                log.info("camera name refresh failed: %s", type(e).__name__)
            finally:
                _friendly_warming = False

        th = threading.Thread(target=_run, name="frigate-camera-names", daemon=True)
        th.start()
        return th
    except Exception as e:
        _friendly_warming = False
        log.info("camera name refresh not started: %s", type(e).__name__)
        return None


def camera_display_name(camera: str, cards: dict | None = None, fetch: bool = True) -> str:
    """The name a person sees for a camera: the camera card's display name (unless blank or just the raw id) →
    services.yaml camera_names → Frigate's friendly_name → the raw id. Never raises. fetch=False: cache only (for code
    on the event loop, which must never block on Frigate)."""
    try:
        card = (cards or {}).get(camera)
        shown = card.get("display_name") if isinstance(card, dict) else None
        if isinstance(shown, str) and shown.strip() and shown.strip() != camera:
            return shown.strip()
        svc = (_camera_map() or {}).get(camera)
        if isinstance(svc, str) and svc.strip() and svc.strip() != camera:
            return svc.strip()
        fr = (frigate_camera_names() if fetch else _friendly).get(camera)
        if fr and fr != camera:
            return fr
        if not fetch:
            warm_camera_names()  # S05l: cold/stale cache → names arrive for the next lookup, without blocking this one
    except Exception as e:
        log.info("camera display name lookup failed: %s", type(e).__name__)
    return camera


def camera_types_from_config(config: dict) -> dict[str, str]:
    """{camera: type} from a Frigate /api/config body; remembers it for camera_types() (and the friendly names for
    camera_display_name()). Never raises."""
    global _camera_types, _camera_types_ts
    _remember_friendly(config if isinstance(config, dict) else {})
    try:
        cams = config.get("cameras") or {}
        types = {name: c["type"] for name, c in cams.items()
                 if isinstance(c, dict) and isinstance(c.get("type"), str) and c["type"]}
    except Exception:
        return {}
    _camera_types, _camera_types_ts = types, time.monotonic()
    return types


def tracked_labels_from_config(config: dict) -> list[str]:
    """Every label Frigate tracks: the global `objects.track` first, then each camera's own list (a camera may override
    the global list, R137). Order kept, duplicates dropped. Never raises."""
    out: list[str] = []

    def add(labels) -> None:
        for lbl in labels or []:
            if isinstance(lbl, str) and lbl and lbl not in out:
                out.append(lbl)

    try:
        add((config.get("objects") or {}).get("track"))
        for cam in (config.get("cameras") or {}).values():
            if isinstance(cam, dict):
                add((cam.get("objects") or {}).get("track"))
    except Exception:
        pass
    return out


async def camera_types() -> dict[str, str]:
    """{camera: type} from Frigate, cached _CAMERA_TYPES_TTL_S. Frigate absent → the last known types, or {}."""
    if _camera_types_ts and time.monotonic() - _camera_types_ts < _CAMERA_TYPES_TTL_S:
        return _camera_types
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            r = await client.get(f"{_frigate_url()}/api/config")
            r.raise_for_status()
            return camera_types_from_config(r.json())
    except Exception as e:
        log.info("Could not read camera types from Frigate: %s", type(e).__name__)
        return _camera_types


async def fetch_camera_configs() -> dict[str, dict]:
    """{camera: its block in Frigate's /api/config} (P144-s27: the user's motion masks). GET only. {} when Frigate does not
    answer: nothing depends on it."""
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            r = await client.get(f"{_frigate_url()}/api/config")
            r.raise_for_status()
            body = r.json()
            cams = body.get("cameras")
        _remember_friendly(body)
        return {k: v for k, v in cams.items() if isinstance(v, dict)} if isinstance(cams, dict) else {}
    except Exception as e:
        log.info("Could not read Frigate's camera config: %s", type(e).__name__)
        return {}


async def fetch_events(
    camera: str | None = None,
    label: str | None = None,
    limit: int = 10,
    has_snapshot: bool = True,
) -> list[dict]:
    """
    Fetches recent detection events from Frigate.
    Frigate 0.17.x REST: GET /api/events?camera=&label=&limit=&has_snapshot=
    Returns list of event dicts with: id, camera, label, start_time, end_time,
    sub_label (face name if recognized), has_snapshot, thumbnail_path, etc.
    """
    params: dict[str, Any] = {"limit": limit, "has_snapshot": int(has_snapshot)}
    if camera:
        api_name = _friendly_to_api(camera) or camera
        params["camera"] = api_name
    if label:
        params["label"] = label

    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{_frigate_url()}/api/events", params=params)
        r.raise_for_status()
        events = r.json()
    # P144-s02: opted-out people come back as an anonymous «person»
    return [frigate_consent.strip_event(ev) for ev in events] if isinstance(events, list) else events


async def fetch_in_progress_events(limit: int = 50) -> list[dict]:
    """Tracks Frigate still has open (no end event yet) — GET /api/events?in_progress=1 (P144-s26: an episode stays open
    while one of them is active inside it). Read only; names stripped like every other event read."""
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{_frigate_url()}/api/events", params={"in_progress": 1, "limit": limit})
        r.raise_for_status()
        events = r.json()
    return [frigate_consent.strip_event(ev) for ev in events] if isinstance(events, list) else []


async def fetch_face_events(limit: int = 10) -> list[dict]:
    """
    Fetches recent events where a face was recognized.
    In Frigate 0.17.x, face recognition results are stored as sub_label on person events.
    NOTE: The exact field name may vary by Frigate version — verify against actual API response.
    Returns events with recognized face names.
    """
    events = await fetch_events(label="person", limit=limit * 3)
    face_events = []
    for ev in events:
        sub_label = ev.get("sub_label") or ev.get("data", {}).get("sub_label")
        if sub_label:
            ev["_face_name"] = sub_label
            face_events.append(ev)
        if len(face_events) >= limit:
            break
    return face_events


async def fetch_face_library() -> list[str]:
    """P144-s02: the person names in Frigate's face library (GET /api/faces, Frigate 0.16+), for
    linking a KTSB user to their face. Frigate's own "train" folder is not a person. Raises on
    HTTP/network errors so the caller can tell «unreachable» from «no faces»."""
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{_frigate_url()}/api/faces")
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        return []
    return sorted((n for n in data if isinstance(n, str) and n != "train"), key=str.casefold)


async def fetch_event_snapshot(event_id: str, height: int | None = None) -> bytes:
    """Fetches the snapshot JPEG for a specific event; `height` asks Frigate to scale it (P144-s25 item 0: since the
    main-stream detect the full snapshot is 2304–2560 px wide)."""
    url = f"{_frigate_url()}/api/events/{event_id}/snapshot.jpg" + (f"?height={int(height)}" if height else "")
    async with httpx.AsyncClient(timeout=_snapshot_timeout()) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


async def fetch_recording_snapshot(camera: str, ts: float, height: int = 480) -> bytes:
    """One frame from a camera's recordings at a given time (P144-s06d scene step). Frigate sends no header with the
    frame's real time, so the caller uses the time it asked for."""
    url = f"{_frigate_url()}/api/{camera}/recordings/{ts:.3f}/snapshot.jpg?height={int(height)}"
    async with httpx.AsyncClient(timeout=_snapshot_timeout()) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


async def fetch_timeline(event_id: str) -> list[dict]:
    """Frigate's timeline rows for one tracked object (P144-s25): class_type (visible / active / stationary / gone …),
    timestamp and box. The «gone» row is the last real sighting — the event's end_time lingers 6–8 s after it. Face
    sub_labels are dropped here (no names leave the adapter; consent is the pipe's job)."""
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{_frigate_url()}/api/timeline", params={"source_id": event_id, "limit": 500})
        r.raise_for_status()
        rows = r.json()
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        out.append({"timestamp": row.get("timestamp"), "camera": row.get("camera"),
                    "class_type": row.get("class_type"), "data": {"box": data.get("box"), "label": data.get("label")}})
    return out


async def get_event(event_id: str) -> dict:
    """Fetches full event JSON from Frigate REST API — includes path_data, top_score, etc."""
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{_frigate_url()}/api/events/{event_id}")
        r.raise_for_status()
        return frigate_consent.strip_event(r.json())  # P144-s02


# ── Event parsing (inbound from Frigate via HTTP POST to Kåre) ────────────────


def parse_frigate_event(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload is not a dict")

    camera = payload.get("camera")
    label = payload.get("label")
    confidence = payload.get("confidence")
    zones = payload.get("zones", [])
    ts = payload.get("ts")
    media = payload.get("media", [])
    if not isinstance(media, list):
        media = []

    if not camera or not label:
        raise ValueError("missing camera or label")

    try:
        confidence = float(confidence) if confidence is not None else 0.0
    except Exception:
        confidence = 0.0

    if not isinstance(zones, list):
        zones = []

    try:
        ts = int(ts) if ts is not None else None
    except Exception:
        ts = None

    cam_map = _camera_map()
    friendly = cam_map.get(camera, camera)

    return {
        "source": "frigate",
        "camera": camera,
        "camera_friendly": friendly,
        "label": label,
        "confidence": confidence,
        "zones": zones,
        "ts": ts,
        "media": media,
    }


# Camera roles that count as perimeter (outside/around the building).
_PERIMETER_ROLES = {"front_door", "driveway", "road_facing", "garden"}


def _is_nighttime(nighttime_start: int, nighttime_end: int) -> bool:
    hour = now_local().hour
    if nighttime_start > nighttime_end:
        return hour >= nighttime_start or hour < nighttime_end
    return nighttime_start <= hour < nighttime_end


def _load_away_mode_cfg() -> dict:
    try:
        return camera_config().get("away_mode") or {}
    except Exception:
        return {}


def derive_hints(event: dict, camera_role: str = "") -> dict:
    camera = event.get("camera", "")
    label = event.get("label")
    confidence = event.get("confidence", 0.0)
    zones = event.get("zones", [])
    deviation_score, baseline_confidence = _get_deviation_score(camera, label or "")

    if label == "person":
        category = "security"
    elif label in ["car", "truck", "bus"]:
        category = "vehicle"
    elif label in ["cat", "dog"]:
        category = "pet"
    else:
        category = "generic"

    if confidence >= 0.7:
        priority = "high"
    elif confidence >= 0.4:
        priority = "medium"
    else:
        priority = "low"

    if zones:
        if priority == "medium":
            priority = "high"
        elif priority == "low":
            priority = "medium"

    notify = category == "security" and confidence >= 0.6

    if category == "security":
        ttl_sec = 30
    elif category == "vehicle":
        ttl_sec = 90
    else:
        ttl_sec = 20

    # Away-mode threat recalibration
    away_mode = False
    away_reason = None
    try:
        if is_away():
            away_mode = True
            am_cfg = _load_away_mode_cfg()
            night_start = int(am_cfg.get("nighttime_start", 22))
            night_end = int(am_cfg.get("nighttime_end", 6))
            person_threshold = float(am_cfg.get("person_confidence_high", 0.6))
            unknown_night_high = bool(am_cfg.get("unknown_night_high", True))

            is_perimeter = camera_role in _PERIMETER_ROLES
            is_night = _is_nighttime(night_start, night_end)

            if is_perimeter:
                # Any person at perimeter at >= threshold → HIGH
                if label == "person" and confidence >= person_threshold:
                    priority = "high"
                    notify = True
                    away_reason = "Person at perimeter during absence"

                # Unknown or low-confidence at perimeter at night → HIGH
                if (
                    unknown_night_high
                    and is_night
                    and (not label or label == "generic" or confidence < 0.5)
                ):
                    priority = "high"
                    notify = True
                    away_reason = "Unidentified presence at perimeter at night during absence"
    except Exception:
        pass

    return {
        "category": category,
        "priority": priority,
        "notify": notify,
        "ttl_sec": ttl_sec,
        "away_mode": away_mode,
        "away_reason": away_reason,
        "deviation_score": round(deviation_score, 2),
        "baseline_confidence": round(baseline_confidence, 2),
    }


def make_dedup_key(event: dict, payload: dict) -> str:
    for k in ("event_id", "id", "tracking_id"):
        if k in payload:
            return f"id:{payload.get(k)}"
    zone = (event.get("zones") or ["_"])[0]
    return f"{event.get('camera')}|{event.get('label')}|{zone}"


def is_duplicate(key: str, ttl_sec: int) -> bool:
    now = time.time()
    exp = _DEDUP_CACHE.get(key)
    if exp and exp > now:
        return True
    _DEDUP_CACHE[key] = now + max(1, int(ttl_sec))
    return False


async def handle_frigate_event(payload: dict, rid: str) -> dict:
    try:
        event = parse_frigate_event(payload)
    except ValueError as e:
        return {"text": f"Invalid Frigate event: {e}", "rid": rid}

    # Look up camera role from config for away-mode threat assessment
    camera_role = ""
    try:
        cam_cfg = camera_config().get("cameras", {}).get(event.get("camera", ""), {})
        camera_role = cam_cfg.get("role", "")
    except Exception:
        pass

    hints = derive_hints(event, camera_role=camera_role)

    return {
        "text": "Frigate event received.",
        "rid": rid,
        "event": event,
        "hints": hints,
    }
