# Frigate adapter — HTTP API + event handling
# Reads config from configs/settings.yaml and configs/frigate.env

import base64
import logging
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc

log = logging.getLogger("frigate_adapter")

_DEDUP_CACHE: dict[str, float] = {}


# ── Config loading ────────────────────────────────────────────────────────────

def _frigate_url() -> str:
    return _svc("frigate", "url") or "http://127.0.0.1:5000"


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
        log.warning("Could not fetch cameras from Frigate API: %s — using settings.yaml fallback", e)
        return [
            {"api_name": k, "friendly_name": v}
            for k, v in _camera_map().items()
        ]


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
        return r.json()


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


async def fetch_event_snapshot(event_id: str) -> bytes:
    """Fetches the snapshot JPEG for a specific event."""
    url = f"{_frigate_url()}/api/events/{event_id}/snapshot.jpg"
    async with httpx.AsyncClient(timeout=_snapshot_timeout()) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


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


def derive_hints(event: dict) -> dict:
    label = event.get("label")
    confidence = event.get("confidence", 0.0)
    zones = event.get("zones", [])

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

    return {
        "category": category,
        "priority": priority,
        "notify": notify,
        "ttl_sec": ttl_sec,
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
        hints = derive_hints(event)
    except ValueError as e:
        return {"text": f"Invalid Frigate event: {e}", "rid": rid}

    return {
        "text": "Frigate event received.",
        "rid": rid,
        "event": event,
        "hints": hints,
    }
