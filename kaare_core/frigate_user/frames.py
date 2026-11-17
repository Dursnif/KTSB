"""Which frames the vision model sees for one episode (P144-s06d; rule measured in s09 `frames.py`).

Candidates are even points over each event that moved plus Frigate's path points (people and animals over the whole
event, vehicles over their active window — a parked car's hours are not activity; a person on the road camera still is;
a non-driving event clipped to the episode window — P144-s20 item 3). The first point on each distinct camera is always
taken (every camera the episode touched is shown), then one point inside each person/animal event not yet shown, then
the point nearest each of N evenly spaced times, more than MIN_SPACING_S from any chosen point. Frames come from Frigate's recordings at those times (480 p). s09: ~9 separate
frames in one call beat a grid and 4 frames on both models (0 inventions on the 9B).
"""

import asyncio
import logging
from dataclasses import dataclass

from adapters import frigate_adapter
from kaare_core.frigate_user import change_detect, chapters, store
from kaare_core.frigate_user.episode_state import Track

log = logging.getLogger(__name__)

N_FRAMES = 9  # s09: 4 frames too few, 9 best
MIN_SPACING_S = 2.0
EVENT_CAP_S = 600  # one event never spreads the frames over more than this
SPREAD_KEEP = 3  # frames kept for the even spread after «one per person/animal event» (P144-s20)
FRAME_HEIGHT = 480
_FETCH_TIMEOUT_S = 15
BACKFILL_PARALLEL = 4  # P144-s24: Frigate fetches at once while filling in missing frames (Frigate's node is not flooded)
REUSE_S = 1.0  # P144-s26: a part's kept frame this close to a merged pick point (same camera) is reused, not fetched
MERGED_FILE_BLOCK = 100  # P144-s26: a merged pick is saved from the next hundred up — the survivor's old files stay


@dataclass
class Frame:
    ts: float
    camera: str
    jpeg: bytes


def candidates(events: list[store.FrigateUserEvent],
               window: tuple[float, float] | None = None) -> list[tuple[float, str]]:
    """(ts, camera) points of the events that moved — driving ones, and ones on an ignored camera role that still moved
    (all events if none did) — sorted and unique. P144-s20 item 3: a person/animal counts over its whole event (one
    standing still is still there), a vehicle over its active window; even points about every MIN_SPACING_S over each
    event plus Frigate's path points (the path alone bunches up — ep 87 had a 41 s hole); nothing before the episode
    `window` starts, and a non-driving event stops at its end (frames up to 72 s after the episode came from other cameras)."""
    movers = [e for e in events if e.drives or e.path] or events
    pts: set[tuple[float, str]] = set()
    for e in movers:
        if e.label in store.VEHICLE_LABELS:
            a0 = e.active_start if e.active_start is not None else e.start_ts
            a1 = e.active_end if e.active_end is not None else (e.end_ts if e.end_ts is not None else a0)
        else:
            a0, a1 = e.start_ts, e.end_ts if e.end_ts is not None else e.start_ts
        a1 = min(max(a1, a0), a0 + EVENT_CAP_S)
        if window is not None:
            # never before the episode; a driving person/animal may stand on after its active end (ep 89), anything
            # else stops at the episode's end
            a0 = max(a0, window[0])
            a1 = a1 if e.drives else min(a1, window[1])
            if a1 < a0:
                continue
        if a1 - a0 <= 0.6:
            pts.add((a0 + (a1 - a0) / 2, e.camera))
            continue
        lo, hi = a0 + 0.3, a1 - 0.3
        k = max(1, min(N_FRAMES - 1, int((hi - lo) // MIN_SPACING_S)))
        pts.update((round(lo + (hi - lo) * i / k, 3), e.camera) for i in range(k + 1))
        for point in e.path or []:
            try:
                ts = float(point[2])
            except (TypeError, ValueError, IndexError):
                continue
            if lo <= ts <= hi:
                pts.add((ts, e.camera))
    return sorted(pts)


def pick(points: list[tuple[float, str]], n: int = N_FRAMES,
         events: list[store.FrigateUserEvent] | None = None) -> list[tuple[float, str]]:
    """First point per camera; then (with `events`) one point inside each person/animal event not yet shown, keeping
    room for SPREAD_KEEP even frames; then the point nearest each of n even times — all more than MIN_SPACING_S apart."""
    if not points:
        return []
    chosen: list[tuple[float, str]] = []
    seen: set[str] = set()
    for p in points:
        if p[1] not in seen:
            chosen.append(p)
            seen.add(p[1])
    chosen = chosen[:n]

    def free(p: tuple[float, str]) -> bool:
        return all(abs(p[0] - c[0]) > MIN_SPACING_S for c in chosen)

    beings = sorted((e for e in events or [] if e.label not in store.VEHICLE_LABELS and (e.drives or e.path)),
                    key=lambda e: e.start_ts)
    for e in beings:
        if len(chosen) >= n - SPREAD_KEEP:
            break
        end = e.end_ts if e.end_ts is not None else e.start_ts
        mine = [p for p in points if p[1] == e.camera and e.start_ts - 0.5 <= p[0] <= end + 0.5]
        if not mine or any(c in mine for c in chosen):
            continue
        ok = [p for p in mine if free(p)]
        if ok:
            chosen.append(min(ok, key=lambda p: abs(p[0] - (e.start_ts + end) / 2)))
    t0, t1 = points[0][0], points[-1][0]
    targets = [t0 + (t1 - t0) * i / (n - 1) for i in range(n)] if n > 1 else [t0]
    # Biggest hole first: the target farthest from every chosen frame (in time order, the last targets were never
    # reached once the per-camera frames had filled the slots — P144-s20).
    while targets and len(chosen) < n:
        target = max(targets, key=lambda t: min((abs(t - c[0]) for c in chosen), default=float("inf")))
        targets.remove(target)
        spare = [p for p in points if free(p)]
        if spare:
            chosen.append(min(spare, key=lambda p: abs(p[0] - target)))
    return sorted(chosen)


async def _one(ts: float, camera: str) -> Frame | None:
    try:
        jpeg = await asyncio.wait_for(
            frigate_adapter.fetch_recording_snapshot(camera, ts, height=FRAME_HEIGHT), timeout=_FETCH_TIMEOUT_S)
    except Exception as e:
        log.info("[FRIGATE_FRAMES] no recording frame %s @ %.1f (%s)", camera, ts, type(e).__name__)
        return None
    return Frame(ts=ts, camera=camera, jpeg=jpeg) if jpeg else None


async def fetch_frames(picked: list[tuple[float, str]]) -> list[Frame]:
    """The picked frames from Frigate's recordings, in time order; missing ones are dropped (fewer than N is fine)."""
    got = await asyncio.gather(*(_one(ts, cam) for ts, cam in picked))
    return [f for f in got if f is not None]


async def plan_chapters(t0: float, t1: float, tracks: list[Track], sem: asyncio.Semaphore | None = None) -> dict | None:
    """Chapters and frame budget for a long episode (P144-s27): Frigate's tracks give the first split, a slow scan of small
    recording frames on the cameras that saw someone gives the keyframes (where the scene settled into something new), and
    those give the calm chapters and where the frames go. None when the episode keeps the grid (short, or no tracks).
    Returns {chapters: view, picks: [(ts, camera)], scan_frames: n}. Never calls a model; a missing scan frame is skipped."""
    if not chapters.wants_chapters(t0, t1, tracks):
        return None
    sem = sem or asyncio.Semaphore(BACKFILL_PARALLEL)
    first = chapters.split(tracks, t0, t1, {})
    wanted: dict[str, set[float]] = {}
    for part in first:
        if part.kind == "active":
            for camera in part.cameras:
                wanted.setdefault(camera, set()).update(chapters.scan_times(part.start, part.end))

    async def one(camera: str, ts: float) -> tuple[str, float, object] | None:
        async with sem:
            try:
                jpeg = await asyncio.wait_for(
                    frigate_adapter.fetch_recording_snapshot(camera, ts, height=chapters.SCAN_HEIGHT),
                    timeout=_FETCH_TIMEOUT_S)
            except Exception:
                return None
        frame = await asyncio.to_thread(change_detect.load_frame, jpeg, chapters.SCAN_HEIGHT) if jpeg else None
        return (camera, ts, frame) if frame is not None else None

    got = await asyncio.gather(*(one(camera, ts) for camera, times in wanted.items() for ts in sorted(times)))
    by_camera: dict[str, list[tuple[float, object]]] = {}
    for item in got:
        if item is not None:
            by_camera.setdefault(item[0], []).append((item[1], item[2]))
    keys = {}
    for camera, seq in by_camera.items():
        seq.sort(key=lambda x: x[0])
        keys[camera] = await asyncio.to_thread(chapters.keyframes, seq)
    parts = chapters.split(tracks, t0, t1, keys)
    return {"chapters": chapters.to_view(parts), "picks": chapters.budget(parts, keys, tracks, t0, t1),
            "scan_frames": sum(len(v) for v in by_camera.values())}


async def fetch_plan(ep: store.Episode, picks: list[tuple[float, str]], kept_frames: list[tuple[int, Frame]] | None = None,
                     sem: asyncio.Semaphore | None = None) -> list[tuple[int, Frame]]:
    """The planned frames as (file, frame) in time order, stored before any model sees them (s24). A frame already kept on
    the same camera within REUSE_S is reused; new ones go under the next hundred, so earlier files stay (s26)."""
    sem = sem or asyncio.Semaphore(BACKFILL_PARALLEL)
    pool = list(kept_frames or [])
    used: set[int] = set()
    out: list[tuple[int, Frame]] = []
    to_fetch: list[tuple[float, str]] = []
    for ts, camera in picks:
        near = sorted((p for p in pool if p[1].camera == camera and abs(p[1].ts - ts) <= REUSE_S and p[0] not in used),
                      key=lambda p: abs(p[1].ts - ts))
        if near:
            used.add(near[0][0])
            out.append(near[0])
        else:
            to_fetch.append((ts, camera))

    async def one(ts: float, camera: str) -> Frame | None:
        async with sem:
            return await _one(ts, camera)

    got = [g for g in await asyncio.gather(*(one(ts, cam) for ts, cam in to_fetch)) if g is not None]
    if got:
        stored = await asyncio.to_thread(store.stored_frame_indexes, ep.episode_id)
        base = (max(stored, default=-1) // MERGED_FILE_BLOCK + 1) * MERGED_FILE_BLOCK if stored else 0
        files = [base + i for i in range(len(got))]
        await asyncio.to_thread(store.save_episode_frames, ep.episode_id, [g.jpeg for g in got], files)
        out.extend(zip(files, got, strict=True))
    return sorted(out, key=lambda x: x[1].ts)


def _point_box(point) -> list[float] | None:
    try:
        return [float(point[0]) - 0.05, float(point[1]) - 0.25, 0.1, 0.27]
    except (TypeError, ValueError, IndexError):
        return None


def _near_boxes(camera: str, tracks: list[Track], events: list[store.FrigateUserEvent]) -> list:
    """Where something tracked has been on this camera: Frigate's boxes and path points (feet → a person-sized box)."""
    boxes = [m[2] for k in tracks if k.camera == camera and change_detect.is_mover(k.label) for m in k.marks if m[2]]
    for e in events:
        if e.camera == camera and change_detect.is_mover(e.label):
            boxes.extend(b for b in (_point_box(p) for p in e.path or []) if b)
    return boxes


AFTER_CANDIDATES_S = (5.0, 15.0, 30.0, 60.0, 120.0)  # J15: after frames from the recording, past Frigate's linger


def quiet_after_times(t1: float, camera: str, tracks: list[Track]) -> list[float]:
    """J15: the first two candidate times after the episode with no active track on that camera ([] if fewer)."""
    quiet = [t1 + dt for dt in AFTER_CANDIDATES_S
             if not any(k.camera == camera and k.covers(t1 + dt) for k in tracks)]
    return quiet[:2] if len(quiet) >= 2 else []


async def change_regions(t0: float, t1: float, tracks: list[Track], events: list[store.FrigateUserEvent],
                         sem: asyncio.Semaphore | None = None) -> dict:
    """What looks different after the episode than before it, per camera that saw something move (P144-s27, #5): two
    reference frames, two after frames, Frigate's motion masks, `change_detect.detect`. {regions: [{camera, box, cells}],
    guards: {camera: why}, crops: [{after, before}] parallel to regions — never stored}.
    Frigate unreachable or frames missing → nothing, never an error (rule 24)."""
    sem = sem or asyncio.Semaphore(BACKFILL_PARALLEL)
    cameras = list(dict.fromkeys(k.camera for k in tracks if change_detect.is_mover(k.label)))
    if not cameras:
        return {"regions": [], "guards": {}, "crops": []}
    config = await frigate_adapter.fetch_camera_configs()

    async def one(camera: str, ts: float) -> bytes | None:
        async with sem:
            f = await _one(ts, camera)
        return f.jpeg if f else None

    async def per_camera(camera: str) -> tuple[str, dict, list[bytes]]:
        # J15: after frames = the first two quiet moments (no active track on this camera) of end +5/+15/+30/+60/+120 s
        after_times = quiet_after_times(t1, camera, tracks)
        if not after_times:
            return camera, {"regions": [], "guard": "no_quiet_after"}, []
        got = await asyncio.gather(*(one(camera, t0 - dt) for dt in change_detect.BEFORE_S),
                                   *(one(camera, t) for t in after_times))
        if any(g is None for g in got):
            return camera, {"regions": [], "guard": "no_frames"}, []
        n = len(change_detect.BEFORE_S)
        skip = [k.box_at(ts) for k in tracks if k.camera == camera for ts in after_times if k.covers(ts) and k.box_at(ts)]
        result = await asyncio.to_thread(change_detect.detect, list(got[:n]), list(got[n:]),
                                         _near_boxes(camera, tracks, events),
                                         change_detect.masks_from_camera(config.get(camera)), skip)
        result["after_ts"] = after_times[0]
        return camera, result, list(got)

    regions, guards, crops, after_frames = [], {}, [], []
    for camera, result, jpegs in await asyncio.gather(*(per_camera(c) for c in cameras)):
        if result.get("guard"):
            guards[camera] = result["guard"]
        if result.get("regions") and jpegs:  # J15: keep the first after frame of a camera that changed (GUI only)
            after_frames.append({"camera": camera, "ts": result.get("after_ts"),
                                 "jpeg": jpegs[len(change_detect.BEFORE_S)]})
        regions.extend({"camera": camera, "box": list(r["box"]), "cells": r["cells"], "_jpegs": jpegs}
                       for r in result.get("regions") or [])
    regions.sort(key=lambda r: -r["cells"])
    for r in regions:
        jpegs = r.pop("_jpegs")
        n = len(change_detect.BEFORE_S)
        before, after = await asyncio.gather(asyncio.to_thread(change_detect.crop, jpegs[n - 1], r["box"]),
                                             asyncio.to_thread(change_detect.crop, jpegs[n], r["box"]))
        crops.append({"before": before, "after": after})
    return {"regions": regions, "guards": guards, "crops": crops, "after_frames": after_frames}


def kept(ep: store.Episode) -> list[tuple[int, Frame]]:
    """(file, frame) for each frame the episode lists that is still on disk, in list order (P144-s24: a retry or a
    re-analysis runs on these — no Frigate needed). File = the list entry's `file`, else its position (before s24)."""
    out = []
    for pos, f in enumerate((ep.scene or {}).get("frames") or []):
        try:
            file, ts, camera = int(f.get("file", pos)), float(f["ts"]), str(f["camera"])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        jpeg = store.read_episode_frame(ep.episode_id, file)
        if jpeg:
            out.append((file, Frame(ts=ts, camera=camera, jpeg=jpeg)))
    return out


async def ensure(ep: store.Episode, events: list[store.FrigateUserEvent],
                 sem: asyncio.Semaphore) -> tuple[list[dict] | None, int, int]:
    """Fill in an episode's missing frames (P144-s24 item 5; also after any outage). Returns (the new frame list, or
    None when the list is unchanged, frames fetched, frames that could not be fetched). Never calls a model.
      - listed, some files missing → those fetched at their listed (camera, ts), stored at their file number;
      - not listed, files on disk that match the code pick one to one → the list rebuilt from the pick, no fetch;
      - not listed otherwise → the code pick fetched, stored and listed."""
    listing = (ep.scene or {}).get("frames") or []
    stored = set(store.stored_frame_indexes(ep.episode_id))

    async def one(ts: float, camera: str) -> Frame | None:
        async with sem:
            return await _one(ts, camera)

    if listing:
        missing = []
        for pos, f in enumerate(listing):
            try:
                file, ts, camera = int(f.get("file", pos)), float(f["ts"]), str(f["camera"])
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if file not in stored:
                missing.append((file, ts, camera))
        if not missing:
            return None, 0, 0
        got = await asyncio.gather(*(one(ts, cam) for _file, ts, cam in missing))
        ok = [(m[0], g) for m, g in zip(missing, got, strict=True) if g is not None]
        if ok:
            await asyncio.to_thread(store.save_episode_frames, ep.episode_id, [g.jpeg for _f, g in ok],
                                    [f for f, _g in ok])
        return None, len(ok), len(missing) - len(ok)
    if not events:
        return None, 0, 0
    points = pick(candidates(events, (ep.start_ts, ep.end_ts)), events=events)
    if not points:
        return None, 0, 0
    if stored == set(range(len(points))):
        return [{"camera": cam, "ts": ts, "file": i} for i, (ts, cam) in enumerate(points)], 0, 0
    got = await asyncio.gather(*(one(ts, cam) for ts, cam in points))
    ok = [(i, g) for i, g in enumerate(got) if g is not None]
    if ok:
        await asyncio.to_thread(store.save_episode_frames, ep.episode_id, [g.jpeg for _i, g in ok], [i for i, _g in ok])
    listed = [{"camera": g.camera, "ts": g.ts, "file": i} for i, g in ok]
    return listed or None, len(ok), len(points) - len(ok)


async def repick_merged(ep: store.Episode, events: list[store.FrigateUserEvent], parts: dict[int, list[dict]],
                        sem: asyncio.Semaphore | None = None) -> tuple[list[dict] | None, int, int, int]:
    """Frames for an episode made from merged parts (P144-s26 item 4): the code pick over the merged window; a part's
    kept frame on the same camera within REUSE_S of a pick point is reused (its own time kept), the rest fetched from
    Frigate's recordings. Saved under new file numbers (from the next MERGED_FILE_BLOCK up), so the survivor's earlier
    frames stay on disk. Returns (the frame list in time order or None, reused, fetched, missing). Never calls a model."""
    points = pick(candidates(events, (ep.start_ts, ep.end_ts)), events=events)
    if not points:
        return None, 0, 0, 0
    pool = []
    for part_id, listing in (parts or {}).items():
        for pos, f in enumerate(listing or []):
            try:
                pool.append((int(part_id), int(f.get("file", pos)), float(f["ts"]), str(f["camera"])))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
    sem = sem or asyncio.Semaphore(BACKFILL_PARALLEL)
    used: set[tuple[int, int]] = set()
    chosen: list[tuple[float, str, bytes, int | None]] = []  # (ts, camera, jpeg, from part)
    to_fetch: list[tuple[float, str]] = []
    for ts, camera in points:
        near = sorted((p for p in pool if p[3] == camera and abs(p[2] - ts) <= REUSE_S and (p[0], p[1]) not in used),
                      key=lambda p: abs(p[2] - ts))
        jpeg = None
        for part_id, file, f_ts, _cam in near:
            jpeg = await asyncio.to_thread(store.read_episode_frame, part_id, file)
            if jpeg:
                used.add((part_id, file))
                chosen.append((f_ts, camera, jpeg, part_id))
                break
        if jpeg is None:
            to_fetch.append((ts, camera))

    async def one(ts: float, camera: str) -> Frame | None:
        async with sem:
            return await _one(ts, camera)

    got = await asyncio.gather(*(one(ts, cam) for ts, cam in to_fetch))
    fetched = [g for g in got if g is not None]
    chosen.extend((g.ts, g.camera, g.jpeg, None) for g in fetched)
    if not chosen:
        return None, 0, 0, len(to_fetch)
    chosen.sort(key=lambda c: c[0])
    base = (max(store.stored_frame_indexes(ep.episode_id), default=-1) // MERGED_FILE_BLOCK + 1) * MERGED_FILE_BLOCK
    files = [base + i for i in range(len(chosen))]
    await asyncio.to_thread(store.save_episode_frames, ep.episode_id, [c[2] for c in chosen], files)
    listing = [{"camera": cam, "ts": ts, "file": f, **({"from_episode": part} if part is not None else {})}
               for f, (ts, cam, _j, part) in zip(files, chosen, strict=True)]
    reused = len(chosen) - len(fetched)
    return listing, reused, len(fetched), len(to_fetch) - len(fetched)
