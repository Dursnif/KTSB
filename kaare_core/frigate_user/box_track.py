"""Frigate's per-update box kept on the event (J19, from P144-s31).

Frigate sends a box with every MQTT `new` / `update` of a tracked object; REST and the timeline keep only a few (the
snapshot box and 2–4 timeline entries). s31 measured that posture, dwell and «at the car for N s» need the box over time.
This module keeps it — in RAM while the track is open, on the stored event after `end`:
  - a point is kept only when the box moved or changed size (≥ MOVE_MIN of the frame) or Frigate's `stationary` flag
    flipped, and at least MIN_INTERVAL_S after the last kept point; `ratio` and `motionless_count` ride along, never
    trigger (motionless_count grows every frame while a thing stands still);
  - at most MAX_POINTS per stored event (evenly thinned, first and last kept — like the store's PATH_MAX_POINTS);
  - at most MAX_TRACKS open tracks in RAM; a track not updated for STALE_S is dropped (Frigate lost the end).
Frigate's MQTT box is pixels [x1, y1, x2, y2] of the camera's detect stream; `normalised` turns it into the REST form
(relative x, y, w, h) with the detect size from Frigate's /api/config (cached). No pixels, no image, never a name.
Stored shape (store column `events.boxes`): [[ts, x, y, w, h, ratio, motionless_count, stationary], …].
"""
from __future__ import annotations

import logging
import threading
import time

from adapters import frigate_adapter

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 1.0
MOVE_MIN = 0.005  # share of the frame's width/height a box edge must move to count as a change
MAX_POINTS = 120  # per stored event
MAX_RAW_POINTS = 2 * MAX_POINTS  # per open track in RAM — thinned to half when full
MAX_TRACKS = 2000
STALE_S = 2 * 3600
CONFIG_TTL_S = 3600

_lock = threading.Lock()
_tracks: dict[str, dict] = {}  # event_id → {"camera", "seen", "points": [[ts, x1, y1, x2, y2, ratio, motionless, stat]]}
_sizes: dict[str, tuple[float, float]] = {}
_sizes_ts = 0.0


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _thin(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def _changed(last: list, box: list[float], stationary, frame_wh: tuple[float, float] | None) -> bool:
    if bool(last[7]) != bool(stationary):
        return True
    w, h = frame_wh or (max(box[2], last[3], 1.0), max(box[3], last[4], 1.0))  # unknown size: the box's own scale
    return any(abs(a - b) / s >= MOVE_MIN for a, b, s in zip(last[1:5], box, (w, h, w, h), strict=True))


def note(event_id: str, after: dict, now: float | None = None, force: bool = False) -> None:
    """One MQTT new/update `after` block. Cheap and sync (the MQTT loop); never raises. `force`: the end block — kept
    whenever it is later than the last kept point, changed or not (it says until when the thing stood there)."""
    try:
        if not event_id or not isinstance(after, dict):
            return
        box = after.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return
        box = [_num(v) for v in box]
        if any(v is None for v in box) or box[2] <= box[0] or box[3] <= box[1]:
            return
        ts = _num(after.get("frame_time")) or (time.time() if now is None else now)
        wall = time.time() if now is None else now
        stationary = bool(after.get("stationary"))
        point = [round(ts, 2), *(round(v, 1) for v in box), _num(after.get("ratio")),
                 int(_num(after.get("motionless_count")) or 0), int(stationary)]
        camera = str(after.get("camera") or "")
        with _lock:
            t = _tracks.get(event_id)
            if t is None:
                if len(_tracks) >= MAX_TRACKS:
                    _drop_stale(wall)
                    if len(_tracks) >= MAX_TRACKS:
                        return
                _tracks[event_id] = {"camera": camera, "seen": wall, "points": [point]}
                return
            t["seen"] = wall
            last = t["points"][-1]
            if force:
                if ts <= last[0]:
                    return
            elif ts - last[0] < MIN_INTERVAL_S or not _changed(last, box, stationary, _sizes.get(t["camera"])):
                return
            t["points"].append(point)
            if len(t["points"]) > MAX_RAW_POINTS:
                t["points"] = _thin(t["points"], MAX_POINTS)
    except Exception as e:
        log.debug("[FRIGATE_BOXES] note skipped: %s", type(e).__name__)


def _drop_stale(wall: float) -> None:
    for k in [k for k, t in _tracks.items() if wall - t["seen"] > STALE_S]:
        _tracks.pop(k, None)


def pop(event_id: str, end: dict | None = None) -> list:
    """The raw points of a finished track (the `end` block's box always appended last); the track leaves RAM."""
    if end:
        note(event_id, end, force=True)
    with _lock:
        t = _tracks.pop(event_id, None)
        _drop_stale(time.time())
    return list(t["points"]) if t else []


async def detect_size(camera: str) -> tuple[float, float] | None:
    """(width, height) of the camera's detect stream from Frigate's /api/config, cached CONFIG_TTL_S. None = unknown."""
    global _sizes_ts
    if time.time() - _sizes_ts > CONFIG_TTL_S or camera not in _sizes:
        cfg = await frigate_adapter.fetch_camera_configs()
        sizes = {}
        for cam, block in (cfg or {}).items():
            det = block.get("detect") if isinstance(block, dict) else None
            w, h = (_num(det.get("width")), _num(det.get("height"))) if isinstance(det, dict) else (None, None)
            if w and h:
                sizes[cam] = (w, h)
        if sizes:
            with _lock:
                _sizes.clear()
                _sizes.update(sizes)
            _sizes_ts = time.time()
    return _sizes.get(camera)


def normalised(points: list, frame_wh: tuple[float, float] | None) -> list:
    """Raw pixel points → stored [[ts, x, y, w, h, ratio, motionless, stationary], …] relative, ≤ MAX_POINTS.
    Unknown detect size → [] (a pixel box without its frame means nothing to a reader)."""
    if not points or not frame_wh or not frame_wh[0] or not frame_wh[1]:
        return []
    fw, fh = frame_wh
    out = []
    for p in points:
        try:
            ts, x1, y1, x2, y2, ratio, motionless, stat = p
            out.append([round(float(ts), 2), round(x1 / fw, 4), round(y1 / fh, 4), round((x2 - x1) / fw, 4),
                        round((y2 - y1) / fh, 4), None if ratio is None else round(float(ratio), 3), int(motionless),
                        int(stat)])
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return _thin(out, MAX_POINTS)


async def for_event(points: list, camera: str) -> list:
    """The stored form for one finished event; never raises (Frigate unreachable → [])."""
    try:
        return normalised(points, await detect_size(camera)) if points else []
    except Exception as e:
        log.info("[FRIGATE_BOXES] boxes for %s not kept: %s", camera, type(e).__name__)
        return []


def _reset_for_tests() -> None:
    global _sizes_ts
    with _lock:
        _tracks.clear()
        _sizes.clear()
        _sizes_ts = 0.0
