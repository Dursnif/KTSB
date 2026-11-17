"""Stationary objects are never analysed unless the pixels changed (P144 R138, run-queue J3).

A parked car, a trailer or the waste bins are re-detected by Frigate again and again (tonight 17 waste_bin events → 9
bin-only episodes, 8 of them concluded by the 27B, and 17 old-path analyses — nothing in the scene had changed). Frigate's
own `stationary` flag and the track's path do not tell it apart: the bins' tracks jump ~0.11 of the frame between boxes,
over the episode rule's 0.08. So code looks at the pixels: the object's box (Frigate's snapshot box) in two recording frames
from before the event against two at the snapshot time, with s27's structure measure (`change_detect.changed_blocks`, not
brightness). Unchanged → the object was already standing there → no episode, no model, no old-path analysis. A vehicle whose
track moved is never judged (`moving`). Measured 2026-11-06 (run-queue J3 log): 0 of 25 passing cars held back, 6 of 9
parked cars with readable frames and the bins held back.

  - only for objects that do not move by themselves: never persons or animals, never urgent labels (the caller decides);
  - «unknown» (no frames, colour ↔ IR, burnt-out IR blobs, unreadable) always lets the event through — the gate may only
    ever save work, never hide something;
  - one verdict per Frigate event, shared by the new pipe and the old responder (an in-flight future), kept a while in RAM;
  - read-only toward Frigate (REST event + recording frames, GETs only). Blocking image work runs in a thread (rule 10).
"""

from __future__ import annotations

import asyncio
import logging
import time

from adapters import frigate_adapter
from kaare_core.frigate_user import change_detect, settings, store

log = logging.getLogger(__name__)

BEFORE_S = (45.0, 15.0)  # references before the event (as s27's change detector)
SNAP_PAIR_S = 0.25  # the second «during» frame, just after the snapshot (the box belongs to Frigate's snapshot frame)
BOX_PAD = 0.02  # of the frame, each side
CHANGED_SHARE = 0.15  # more than this share of the box's blocks changed in every comparison → the object is new or moved
MIN_BOX_BLOCKS = 4  # a box smaller than this many blocks cannot be judged → unknown
FETCH_TIMEOUT_S = 10
HEIGHT = change_detect.ANALYSE_HEIGHT
CACHE_S = 600.0

_cache: dict[str, tuple[float, asyncio.Future]] = {}


URGENT_LABELS = ("fire", "smoke")  # never held back by anything (as the pipe's _ALWAYS_URGENT_LABELS)


def applies(label: str) -> bool:
    """Not persons, not animals, never urgent labels. Vehicles are included (a parked car re-detected is the same case)
    but only judged when Frigate's track did not move (see `moving`)."""
    try:
        urgent = {*URGENT_LABELS, *(settings.conclude().get("urgent_labels") or [])}
    except Exception:
        urgent = set(URGENT_LABELS)
    return label != "person" and label not in store.ANIMAL_LABELS and label not in urgent


def moving(label: str, data: dict, stationary: bool | None = None) -> bool:
    """A vehicle whose track moved (path span ≥ the episode rule's minimum movement) is in motion — pixels from two
    moments can never call it «unchanged» (measured 2026-11-06: 3 of 25 passing cars looked pixel-unchanged). Frigate's
    own stationary flag wins. Objects that cannot move by themselves (bins …) are judged on pixels only: their tracks
    jump between neighbouring boxes (~0.11), not because they move."""
    if label not in store.VEHICLE_LABELS:
        return False
    if stationary is None:
        st = (data or {}).get("stationary")
        stationary = st if isinstance(st, bool) else None
    if stationary:
        return False
    span = path_span((data or {}).get("path_data"))
    return span is None or span >= settings.episode_min_movement()


def path_span(path) -> float | None:
    try:
        xs = [float(p[0][0]) for p in path]
        ys = [float(p[0][1]) for p in path]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not xs:
        return None
    return max(max(xs) - min(xs), max(ys) - min(ys))


def judge(before: list[bytes | None], during: list[bytes | None], box) -> str:
    """«unchanged» | «changed» | «unknown» for the object's box. Pure; never raises."""
    try:
        frames = [change_detect.load_frame(j) if j else None for j in (*before, *during)]
        if len(frames) != 4 or any(f is None for f in frames):
            return "unknown"
        if any(change_detect._is_ir_with_blobs(f) for f in frames) or len({change_detect._is_ir(f) for f in frames}) > 1:
            return "unknown"
        a0, a1, b0, b1 = (f[..., 0] for f in frames)
        grid = None
        for a in (a0, a1):
            for b in (b0, b1):
                part = change_detect.changed_blocks(a, b, change_detect.STRUCT_MAX)
                grid = part if grid is None else grid & part
        if int(change_detect._boxes_grid([box], grid.shape, 0.0).sum()) < MIN_BOX_BLOCKS:
            return "unknown"  # judged on the box itself, before padding
        zone = change_detect._boxes_grid([box], grid.shape, BOX_PAD)
        n = int(zone.sum())
        share = float((grid & zone).sum()) / n
        return "changed" if share > CHANGED_SHARE else "unchanged"
    except Exception as e:
        log.info("[FRIGATE_GATE] judge failed: %s", type(e).__name__)
        return "unknown"


def _box(data: dict):
    box = (data or {}).get("box")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            vals = [float(v) for v in box]
        except (TypeError, ValueError):
            return None
        if all(0.0 <= v <= 1.0 for v in vals) and vals[2] > 0 and vals[3] > 0:
            return vals
    return None


async def _frame(camera: str, ts: float) -> bytes | None:
    try:
        return await asyncio.wait_for(frigate_adapter.fetch_recording_snapshot(camera, ts, HEIGHT),
                                      timeout=FETCH_TIMEOUT_S)
    except Exception as e:
        log.debug("[FRIGATE_GATE] camera %s: no recording frame at %.1f (%s)", camera, ts, type(e).__name__)
        return None


FRAME_RETRY_S = 10.0  # J13: Frigate writes the recording a little after the event ends — one more try after this


async def _frames_with_retry(camera: str, times: list[float]) -> list[bytes | None]:
    """The four frames; a frame that is not written yet is fetched once more after FRAME_RETRY_S (J13: 16 bin events
    were «unknown» live and «unchanged» an hour later)."""
    frames = list(await asyncio.gather(*(_frame(camera, t) for t in times)))
    missing = [i for i, f in enumerate(frames) if f is None]
    if missing:
        await asyncio.sleep(FRAME_RETRY_S)
        again = await asyncio.gather(*(_frame(camera, times[i]) for i in missing))
        for i, f in zip(missing, again, strict=True):
            frames[i] = f
    return frames


def hold_unknown(label: str) -> bool:
    """J13 (main session's design): a non-living thing that does not drive by itself (not a vehicle) whose verdict is
    «unknown» is held back — it never opens an episode or an analysis alone. Vehicles may be driving: always through."""
    return applies(label) and label not in store.VEHICLE_LABELS


async def _verdict(event_id: str, camera: str, label: str, rest: dict | None) -> str:
    if rest is None:
        try:
            rest = await asyncio.wait_for(frigate_adapter.get_event(event_id), timeout=FETCH_TIMEOUT_S)
        except Exception as e:
            log.debug("[FRIGATE_GATE] event %s: no REST data (%s)", event_id, type(e).__name__)
            return "unknown"
    if not isinstance(rest, dict):
        return "unknown"
    data = rest.get("data") if isinstance(rest.get("data"), dict) else {}
    box = _box(data)
    try:
        start = float(rest.get("start_time"))
        snap = float(data.get("snapshot_frame_time"))
    except (TypeError, ValueError):
        return "unknown"
    if box is None or moving(label, data):
        return "unknown"
    frames = await _frames_with_retry(camera, [start - BEFORE_S[0], start - BEFORE_S[1], snap, snap + SNAP_PAIR_S])
    return await asyncio.to_thread(judge, list(frames[:2]), list(frames[2:]), box)


async def verdict(event_id: str, camera: str, label: str, rest: dict | None = None) -> str:
    """The gate's verdict for one Frigate event — computed once, shared by every caller. «unknown» for a label the gate
    does not apply to. Never raises."""
    if not event_id or not camera or not applies(label):
        return "unknown"
    now = time.monotonic()
    for k in [k for k, (t, _) in _cache.items() if now - t > CACHE_S]:
        _cache.pop(k, None)
    hit = _cache.get(event_id)
    if hit is None:
        fut = asyncio.ensure_future(_verdict(event_id, camera, label, rest))
        _cache[event_id] = (now, fut)
    else:
        fut = hit[1]
    try:
        out = await asyncio.shield(fut)
    except Exception as e:
        log.info("[FRIGATE_GATE] event %s: verdict failed (%s)", event_id, type(e).__name__)
        return "unknown"
    if out == "unchanged":
        log.info("[FRIGATE_GATE] event %s camera=%s label=%s: unchanged stationary object — not analysed",
                 event_id, camera, label)
    return out


KEY_GRID = 10  # the object's place in the key: its box centre on a 10 × 10 grid of the frame


def object_key(camera: str, label: str, box) -> str | None:
    """A stable key for one stationary thing on one camera: camera + label + rough place (P144 R139: a later job links
    the keys of several cameras to one thing through `thing_id`; nothing links them now)."""
    if not box:
        return None
    x, y, w, h = box
    return f"{camera}|{label}|{min(KEY_GRID - 1, int((x + w / 2) * KEY_GRID))},{min(KEY_GRID - 1, int((y + h / 2) * KEY_GRID))}"


def record(verdict_word: str, camera: str, label: str, data: dict | None) -> dict:
    """What the event stores about the gate: {verdict, key, thing_id} — `thing_id` stays None until R139 is built."""
    return {"verdict": verdict_word, "key": object_key(camera, label, _box(data or {})), "thing_id": None}


def reset_for_tests() -> None:
    _cache.clear()


__all__ = ["applies", "judge", "object_key", "record", "reset_for_tests", "verdict"]
