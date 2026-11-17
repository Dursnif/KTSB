"""A still thing a person was at: handled (came back the same) or moved — with the time span (J17, from P144-s31 B1).

s31 measured on real frames: the household car's box (ep 218, night) was stable at 0.00–0.01, rose to 0.13–0.23 in exactly
the 13 s someone stood at the open boot, and was 0.00 again after → «handled». The bin's box (ep 266, dawn) was 0.00 until
the person reached it, 0.47–0.76 while it was pulled away, and stayed 0.33–0.48 after → «moved». Frigate's own tracks said
1.7 s and 1.5 s. This module turns that into code facts for the 27B and the episode state — never for the 9B:
  - things = still, non-living objects with a stationary-gate key (J3) on the camera in the hours before the episode
    (vehicles only when parked); the thing's box = its last stored Frigate box (J19) or Frigate's REST box (GET);
  - touched = a person's box over the thing's box (J19 boxes; else the path's foot points; else the snapshot box);
  - measure = s27's structure measure (`change_detect.changed_blocks`) inside the thing's box, once per second over the
    touch, against two references before the episode, plus two quiet frames after the person left;
  - «unknown» on IR blobs, colour ↔ IR, a reference pair that disagrees (light changed), or missing frames — never a guess.
Which end of a car is the boot is not code's to say. Read-only toward Frigate (GETs); image work in a thread (rule 10).
"""
from __future__ import annotations

import asyncio
import logging

from adapters import frigate_adapter
from kaare_core.frigate_user import change_detect, stationary_gate, store

log = logging.getLogger(__name__)

BEFORE_S = (45.0, 15.0)  # references before the first touch (as the gate and s27)
AFTER_S = (15.0, 30.0)  # quiet frames after the last touch
TOUCH_PAD = 0.02  # of the frame, around the thing's box
CHANGED_SECOND = 0.10  # share of the box's blocks that differ from both references → this second the thing looked different
SAME_AFTER = 0.06  # after the person left: at most this → back as before (s31: 0.00–0.04)
MOVED_AFTER = 0.10  # …at least this in both after frames → moved or gone (J17 on ep 266's frames: a smooth bin gave 0.11)
REF_STABLE = 0.06  # the two references must agree inside the box (else the light changed)
MIN_CHANGED_S = 2  # seconds that must look different before «handled» is said
MAX_SECONDS = 60  # frames measured per touch at most (1/s); longer touches are thinned evenly
MIN_BOX_BLOCKS = 4
FRESH_S = 1800.0  # a thing counts only if Frigate saw it this recently before the episode (a stale box made «handled» of a person standing where a car had been)
PARALLEL = 4  # Frigate fetches at once (as frames.BACKFILL_PARALLEL)
FETCH_TIMEOUT_S = 10.0


def _overlap(a, b, pad: float = 0.0) -> bool:
    ax0, ay0, ax1, ay1 = a[0] - pad, a[1] - pad, a[0] + a[2] + pad, a[1] + a[3] + pad
    return ax0 < b[0] + b[2] and b[0] < ax1 and ay0 < b[1] + b[3] and b[1] < ay1


def _box(v) -> list[float] | None:
    try:
        b = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return b if len(b) == 4 and b[2] > 0 and b[3] > 0 and all(0.0 <= x <= 1.0 for x in b) else None


def _is_thing(ev: store.FrigateUserEvent) -> bool:
    gate = (ev.check or {}).get("gate") if isinstance(ev.check, dict) else None
    if not isinstance(gate, dict) or not gate.get("key") or not stationary_gate.applies(ev.label):
        return False
    return ev.label not in store.VEHICLE_LABELS or bool(ev.stationary) or not ev.drives


def things(before: list[store.FrigateUserEvent], camera: str, since: float | None = None) -> list[dict]:
    """One entry per gate key on `camera` (the latest event of it): {key, label, event_id, box | None}. `since`: only
    things Frigate saw (event start or end) at or after this time."""
    out: dict[str, dict] = {}
    for ev in sorted(before, key=lambda e: e.start_ts):
        if ev.camera != camera or not _is_thing(ev):
            continue
        seen = max(ev.start_ts, ev.end_ts if ev.end_ts is not None else ev.start_ts)
        if since is not None and seen < since:
            continue
        last = ev.boxes[-1] if ev.boxes else None
        out[ev.check["gate"]["key"]] = {"key": ev.check["gate"]["key"], "label": ev.label, "event_id": ev.event_id,
                                        "box": _box(last[1:5]) if last else None}
    return list(out.values())


def person_boxes(ev: store.FrigateUserEvent) -> list[tuple[float, list[float]]]:
    """(ts, box) over a person's track: J19 boxes; else the path's foot points as a thin box; never raises."""
    pts = []
    for p in ev.boxes or []:
        b = _box(p[1:5]) if isinstance(p, (list, tuple)) and len(p) >= 5 else None
        if b:
            pts.append((float(p[0]), b))
    if pts:
        return pts
    for p in ev.path or []:
        try:
            x, y, ts = float(p[0]), float(p[1]), float(p[2])
        except (TypeError, ValueError, IndexError):
            continue
        pts.append((ts, [max(0.0, x - 0.01), max(0.0, y - 0.02), 0.02, 0.02]))
    return pts


def touch_span(persons: list[store.FrigateUserEvent], box: list[float]) -> tuple[float, float] | None:
    """First and last moment a person's box was over the thing's box (padded), or None."""
    hits = [ts for ev in persons for ts, b in person_boxes(ev) if _overlap(b, box, TOUCH_PAD)]
    return (min(hits), max(hits)) if hits else None


def seconds(t0: float, t1: float) -> list[float]:
    n = int(t1 - t0) + 1
    ts = [t0 + i for i in range(n)]
    if len(ts) > MAX_SECONDS:
        step = (len(ts) - 1) / (MAX_SECONDS - 1)
        ts = [ts[round(i * step)] for i in range(MAX_SECONDS)]
    return ts


def _share(a, b, zone) -> float:
    grid = change_detect.changed_blocks(a, b, change_detect.STRUCT_MAX)
    return float((grid & zone).sum()) / max(1, int(zone.sum()))


def judge(refs: list[bytes | None], during: list[tuple[float, bytes | None]], after: list[bytes | None],
          box: list[float]) -> dict:
    """{outcome: handled | moved | near | unknown, changed_s, first_changed, after_share, why}. Pure; never raises.
    near = a person was at it and nothing changed in its box."""
    try:
        ref = [change_detect.load_frame(j) if j else None for j in refs]
        aft = [change_detect.load_frame(j) if j else None for j in after]
        if any(f is None for f in (*ref, *aft)) or len(ref) != 2 or len(aft) != 2:
            return {"outcome": "unknown", "why": "no_frames"}
        frames = [*ref, *aft]
        if any(change_detect._is_ir_with_blobs(f) for f in frames) or len({change_detect._is_ir(f) for f in frames}) > 1:
            return {"outcome": "unknown", "why": "ir"}
        grid_shape = change_detect.changed_blocks(ref[0][..., 0], ref[1][..., 0], change_detect.STRUCT_MAX).shape
        if int(change_detect._boxes_grid([box], grid_shape, 0.0).sum()) < MIN_BOX_BLOCKS:
            return {"outcome": "unknown", "why": "small"}
        zone = change_detect._boxes_grid([box], grid_shape, 0.0)
        r0, r1 = ref[0][..., 0], ref[1][..., 0]
        if _share(r0, r1, zone) > REF_STABLE:
            return {"outcome": "unknown", "why": "light"}

        def vs_refs(f) -> float:
            return min(_share(r0, f[..., 0], zone), _share(r1, f[..., 0], zone))

        changed = []
        for ts, j in during:
            f = change_detect.load_frame(j) if j else None
            if f is not None and not change_detect._is_ir_with_blobs(f) and vs_refs(f) >= CHANGED_SECOND:
                changed.append(ts)
        after_share = min(vs_refs(f) for f in aft)
        out = {"changed_s": len(changed), "first_changed": changed[0] if changed else None,
               "after_share": round(after_share, 3)}
        if after_share >= MOVED_AFTER:
            return {**out, "outcome": "moved", "why": ""}
        if after_share <= SAME_AFTER:
            return {**out, "outcome": "handled" if len(changed) >= MIN_CHANGED_S else "near", "why": ""}
        return {**out, "outcome": "unknown", "why": "between"}
    except Exception as e:
        log.info("[FRIGATE_TOUCH] judge failed: %s", type(e).__name__)
        return {"outcome": "unknown", "why": "error"}


async def _frame(camera: str, ts: float, sem: asyncio.Semaphore) -> bytes | None:
    async with sem:
        try:
            return await asyncio.wait_for(frigate_adapter.fetch_recording_snapshot(camera, ts, change_detect.ANALYSE_HEIGHT),
                                          timeout=FETCH_TIMEOUT_S)
        except Exception as e:
            log.debug("[FRIGATE_TOUCH] %s: no frame at %.1f (%s)", camera, ts, type(e).__name__)
            return None


async def _thing_box(thing: dict) -> list[float] | None:
    if thing.get("box"):
        return thing["box"]
    try:
        rest = await asyncio.wait_for(frigate_adapter.get_event(thing["event_id"]), timeout=FETCH_TIMEOUT_S)
    except Exception:
        return None
    data = rest.get("data") if isinstance(rest, dict) and isinstance(rest.get("data"), dict) else {}
    return _box(data.get("box"))


async def for_episode(ep: store.Episode, events: list[store.FrigateUserEvent],
                      before: list[store.FrigateUserEvent]) -> list[dict]:
    """[{camera, label, key, start, end, near_s, outcome, changed_s, first_changed, why}] for every still thing a person
    of the episode touched. `before`: stored events of the hours before the episode (the things). Never raises."""
    try:
        out = []
        sem = asyncio.Semaphore(PARALLEL)
        persons = [e for e in events if e.label == "person"]
        for camera in dict.fromkeys(e.camera for e in persons):
            here = [e for e in persons if e.camera == camera]
            for thing in things([e for e in before if e.start_ts < ep.start_ts], camera, since=ep.start_ts - FRESH_S):
                box = await _thing_box(thing)
                span = touch_span(here, box) if box else None
                if not span:
                    continue
                t0, t1 = span
                times = seconds(t0, t1)
                got = await asyncio.gather(
                    *(_frame(camera, t0 - dt, sem) for dt in BEFORE_S),
                    *(_frame(camera, t, sem) for t in times),
                    *(_frame(camera, t1 + dt, sem) for dt in AFTER_S))
                refs, during, after = list(got[:2]), list(zip(times, got[2:2 + len(times)], strict=True)), list(got[-2:])
                verdict = await asyncio.to_thread(judge, refs, during, after, box)
                out.append({"camera": camera, "label": thing["label"], "key": thing["key"], "start": round(t0, 1),
                            "end": round(t1, 1), "near_s": round(t1 - t0, 1), **verdict})
                log.info("[FRIGATE_TOUCH] episode %s camera=%s %s: %s (%.0f s, %s changed)", ep.episode_id, camera,
                         thing["label"], verdict["outcome"], t1 - t0, verdict.get("changed_s"))
        return out
    except Exception as e:
        log.info("[FRIGATE_TOUCH] episode %s skipped: %s", getattr(ep, "episode_id", "?"), type(e).__name__)
        return []
