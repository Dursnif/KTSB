"""A long episode as chapters, and how many frames it deserves (P144-s27, Design today #20 «mellomhendelser»).

An episode had ~9 frames on a time grid whatever its length (Halloween, 6:38: one frame every ~45 s). Here code finds where
the situation really changes and spends frames there. Pure code, no model. Measured in the s27 log; no single signal won, so:

  - **no being anywhere** for GAP_S → that stretch is a chapter of its own (ep 102: 1:43 between two visits);
  - **a new place**: a camera outside the chapter's group gets a track that holds (ep 115: up the drive → door → down);
  - **calm**: a slow scan of the frames (structure against the last keyframe, must persist) sees nothing for CALM_S → its own
    chapter (ep 108: sits for 88 s). The same keyframes decide where the frames go: much happens → more; nothing → few.

Frigate's motion activity, track edges and stationary flags alone were measured and left out (many extras, see log). One
module with `change_detect` would mix a per-frame scan with a per-episode comparison; they share only the structure measure.
Episodes under MIN_EPISODE_S keep today's grid. Constants, not settings: rule 25 applies if users ever need them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise

from kaare_core.frigate_user import change_detect, store
from kaare_core.frigate_user.episode_state import Track

MIN_EPISODE_S = 60.0
GAP_S = 15.0
HOLD_S = 8.0  # a track on a new camera must last this long, or another track there must follow within it
GATHER_S = 12.0  # cameras joining this soon after a chapter starts are the same place (one person seen by several)
MERGE_S = 5.0
CALM_S = 30.0
CALM_BREAK_CELLS = 50  # small keyframes (25–42 blocks) jitter with the scan's phase, big ones do not (ep 108, four offsets)
SCAN_STEP_S = 4.0
SCAN_MAX_PER_CAMERA = 80  # a longer chapter is scanned more coarsely (fetches stay bounded)
SCAN_HEIGHT = 270
SCAN_BLOCK = 8
SCAN_STRUCT_MAX = 0.45
KEY_CELLS = 25  # blocks of 33 × 60 that must differ from the last keyframe, in two scan frames in a row
N_MIN = 9  # never fewer allowed than today's grid; an episode under ~2 min never gets more
CAP_PER_MIN = 4
CAP_EPISODE = 24
CALM_FRAMES = 2
# J5 / R63 (ep 108): when a calm chapter would run to the episode's end, a track that ends inside it is someone leaving —
# the calm breaks this long before that track's last sighting (getting up, walking off), so the leaving gets frames.
TRACK_END_LEAD_S = 8.0
EMPTY_FRAMES = 1
MIN_SPACING_S = 2.0
GONE_AFTER_S = 2.0  # the frame showing that someone is gone (s25: ep 108 #9)


@dataclass
class Chapter:
    start: float
    end: float
    kind: str  # active | calm | empty
    signal: str  # what started it: start | gap_end | gap_start | new_place | calm_start | calm_end
    cameras: list[str] = field(default_factory=list)


def _usable(tracks: list[Track]) -> list[Track]:
    """Persons and animals; vehicles only when nothing else was there."""
    beings = [k for k in tracks if k.label not in store.VEHICLE_LABELS and k.label in ("person", *store.ANIMAL_LABELS)]
    return sorted(beings or [k for k in tracks if k.label in store.VEHICLE_LABELS], key=lambda k: k.start)


def wants_chapters(t0: float, t1: float, tracks: list[Track]) -> bool:
    return t1 - t0 >= MIN_EPISODE_S and bool(_usable(tracks))


def _gaps(tracks: list[Track], t0: float, t1: float) -> list[tuple[float, float]]:
    """Stretches with no track at all, longer than GAP_S, inside the episode."""
    out = []
    end = None
    for k in tracks:
        if end is not None and k.start - end >= GAP_S:
            out.append((end, k.start))
        end = k.last_seen if end is None else max(end, k.last_seen)
    if end is not None and t1 - end >= GAP_S:
        out.append((end, t1))
    return out


def boundaries(tracks: list[Track], t0: float, t1: float) -> list[tuple[float, str]]:
    """(time, signal) where a new chapter starts — from Frigate's tracks only, in time order."""
    used = _usable(tracks)
    if not used:
        return []
    gaps = _gaps(used, t0, t1)
    bounds: list[tuple[float, str]] = []
    if used[0].start - t0 >= GAP_S:  # ep 25: cars on the road first, the situation starts when someone comes
        bounds.append((used[0].start, "gap_end"))
    for a, b in gaps:
        bounds.append((a, "gap_start"))
        if b < t1 - 1:
            bounds.append((b, "gap_end"))
    resets = sorted(b for _a, b in gaps)
    group: set[str] = set()
    start = used[0].start
    for k in used:
        while resets and k.start >= resets[0]:
            start = resets.pop(0)
            group = set()
        if not group or k.camera in group or k.start - start < GATHER_S:
            group.add(k.camera)
            continue
        holds = k.last_seen - k.start >= HOLD_S or any(
            j.camera == k.camera and k.start < j.start <= k.start + HOLD_S for j in used)
        if holds:
            bounds.append((k.start, "new_place"))
            start, group = k.start, {k.camera}
    bounds.sort()
    merged: list[tuple[float, str]] = []
    for ts, sig in bounds:
        if not merged or ts - merged[-1][0] > MERGE_S:
            merged.append((ts, sig))
    return merged


def scan_times(t0: float, t1: float) -> list[float]:
    """When the slow scan looks: every SCAN_STEP_S, coarser on a long chapter so one camera stays under the cap."""
    step = max(SCAN_STEP_S, (t1 - t0) / max(1, SCAN_MAX_PER_CAMERA - 1))
    return [t0 + i * step for i in range(int((t1 - t0) / step) + 1)]


def scan_compare(a, b) -> int:
    """Blocks whose structure differs between two scan frames (`change_detect.load_frame` at SCAN_HEIGHT)."""
    return int(change_detect.changed_blocks(a[..., 0], b[..., 0], SCAN_STRUCT_MAX, SCAN_BLOCK).sum())


def keyframes(seq: list[tuple[float, object]], compare: Callable = scan_compare) -> list[tuple[float, int]]:
    """(time, blocks) of the frames where the scene settled into something different from the last keyframe. The change
    must hold in the next scan frame too: a passing headlight or someone walking through alone is not a keyframe."""
    out: list[tuple[float, int]] = []
    if len(seq) < 3:
        return out
    key = seq[0][1]
    for i in range(1, len(seq) - 1):
        n = compare(key, seq[i][1])
        if n >= KEY_CELLS and compare(key, seq[i + 1][1]) >= KEY_CELLS:
            key = seq[i + 1][1]
            out.append((seq[i + 1][0], n))
    return out


def _track_cameras(tracks: list[Track], a: float, b: float) -> list[str]:
    return list(dict.fromkeys(k.camera for k in tracks if k.start <= b and k.last_seen >= a))


def split(tracks: list[Track], t0: float, t1: float, keys: dict | None) -> list[Chapter]:
    """The chapters of one episode; `keys` = {camera: keyframes} from the scan ({} = tracks only). Ep 108: a stretch of at
    least CALM_S inside an active chapter with no keyframe on any camera is a calm chapter of its own."""
    used = _usable(tracks)
    edges = boundaries(used, t0, t1)
    gap_starts = {ts for ts, sig in edges if sig == "gap_start"}
    points = [(t0, "start"), *edges, (t1, "")]
    parts: list[Chapter] = []
    for (a, sig), (b, _s) in pairwise(points):
        if b <= a:
            continue
        head = sig == "start" and bool(used) and used[0].start - t0 >= GAP_S
        kind = "empty" if a in gap_starts or head else "active"
        parts.append(Chapter(a, b, kind, sig, _track_cameras(used, a, b) or _track_cameras(tracks, a, b)))
    all_keys = sorted(ts for v in (keys or {}).values() for ts, n in v if n >= CALM_BREAK_CELLS)
    out: list[Chapter] = []
    for p in parts:
        if p.kind != "active" or not all_keys:
            out.append(p)
            continue
        inside = [p.start, *[t for t in all_keys if p.start < t < p.end]]
        cur = p.start
        sig = p.signal
        for a, b in zip(inside, [*inside[1:], p.end], strict=True):
            if b - a < CALM_S:
                continue
            if a > cur:
                out.append(Chapter(cur, a, "active", sig, _track_cameras(used, cur, a)))
                sig = "calm_start"
            out.append(Chapter(a, b, "calm", sig, _track_cameras(used, a, b)))
            cur, sig = b, "calm_end"
        if cur < p.end:
            out.append(Chapter(cur, p.end, "active", sig, _track_cameras(used, cur, p.end)))
        _break_calm_at_track_end(out, used)
    return out


def _break_calm_at_track_end(out: list[Chapter], used: list[Track]) -> None:
    """R63: the last chapter is calm and ends where its active chapter ended — split it at the latest track end inside
    it (minus the lead) when at least CALM_S of calm stays before it."""
    if not out or out[-1].kind != "calm":
        return
    last = out[-1]
    cuts = [t.last_seen - TRACK_END_LEAD_S for t in used
            if last.start + CALM_S <= t.last_seen - TRACK_END_LEAD_S and t.last_seen < last.end]
    if not cuts:
        return
    cut = max(cuts)
    out[-1] = Chapter(last.start, cut, "calm", last.signal, _track_cameras(used, last.start, cut))
    out.append(Chapter(cut, last.end, "active", "track_end", _track_cameras(used, cut, last.end)))


def _cap(t0: float, t1: float) -> int:
    return min(CAP_EPISODE, max(N_MIN, math.ceil((t1 - t0) / 60 * CAP_PER_MIN)))


def budget(parts: list[Chapter], keys: dict | None, tracks: list[Track], t0: float, t1: float) -> list[tuple[float, str]]:
    """The frames: (ts, camera) in time order. Every chapter is seen; every camera of an active chapter once; then the
    strongest keyframes as far as the cap allows; a calm chapter stays at CALM_FRAMES, an empty one at EMPTY_FRAMES (the
    frame just after the people left, which is how the run learns they are gone)."""
    used = _usable(tracks)
    must: list[tuple[float, str]] = []
    optional: list[tuple[int, float, str]] = []

    def first_on(camera: str, a: float, b: float) -> float | None:
        starts = [max(k.start, a) for k in used if k.camera == camera and k.start <= b and k.last_seen >= a]
        return min(starts) + 0.5 if starts else None

    for p in parts:
        if p.kind == "empty":
            cams = _track_cameras(used, p.start - GONE_AFTER_S, p.start) or p.cameras
            if cams:
                must += [(p.start + GONE_AFTER_S, cams[-1])][:EMPTY_FRAMES]
            continue
        cams = p.cameras or _track_cameras(used, p.start, p.end)
        if p.kind == "calm":
            if cams:
                must.append((p.start + 1.0, cams[0]))
                if CALM_FRAMES > 1 and len(cams) > 1:
                    must.append((p.end - 1.0, cams[1]))
                elif CALM_FRAMES > 1:
                    must.append((p.end - 1.0, cams[0]))
            continue
        for cam in cams:
            ts = first_on(cam, p.start, p.end)
            if ts is not None:
                must.append((min(ts, p.end), cam))
                ends = [k.last_seen for k in used if k.camera == cam and k.start <= p.end and k.last_seen >= p.start]
                last = min(max(ends), p.end) - 0.5
                optional.append((0, last, cam))
        for cam in cams:
            for ts, n in (keys or {}).get(cam) or []:
                if p.start <= ts < p.end:
                    optional.append((n, ts, cam))
    last_seen = max((k.last_seen for k in used), default=None)
    if last_seen is not None and t1 - last_seen < GAP_S:
        # the last person's camera just after they left (no empty chapter at the end to show it)
        cam = max((k for k in used), key=lambda k: k.last_seen).camera
        must.append((last_seen + GONE_AFTER_S, cam))
    chosen: list[tuple[float, str]] = []

    def free(ts: float) -> bool:
        return all(abs(ts - c[0]) >= MIN_SPACING_S for c in chosen)

    cap = _cap(t0, t1)
    for ts, cam in sorted(must):
        if len(chosen) < cap and free(ts):
            chosen.append((round(ts, 3), cam))
    # Rounds over (chapter, camera): each takes its strongest remaining frame in turn, so a person posing close to one
    # camera does not take every slot from the camera that shows what they do (ep 102: the jacket is on the far camera).
    queues: dict[tuple[int, str], list[tuple[int, float]]] = {}
    for n, ts, cam in optional:
        queues.setdefault((chapter_of(to_view(parts), ts) or 0, cam), []).append((n, ts))
    for q in queues.values():
        q.sort(key=lambda o: (-o[0], o[1]))
    while len(chosen) < cap and any(queues.values()):
        for q in queues.values():
            while q and len(chosen) < cap:
                _n, ts = q.pop(0)
                if free(ts):
                    chosen.append((round(ts, 3), next(c for (i, c), v in queues.items() if v is q)))
                    break
    return sorted(chosen)


def to_view(parts: list[Chapter]) -> list[dict]:
    return [{"start": p.start, "end": p.end, "kind": p.kind, "signal": p.signal, "cameras": list(p.cameras)} for p in parts]


def chapter_of(view: list[dict], ts: float) -> int | None:
    """Index of the chapter a time lies in (the last one that started at or before it)."""
    found = None
    for i, c in enumerate(view or []):
        if c["start"] <= ts:
            found = i
    return found
