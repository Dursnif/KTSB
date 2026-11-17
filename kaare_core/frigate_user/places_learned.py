"""Places KTSB learns itself: where persons appear and disappear in the middle of a camera's view (J18, from P144-s31).

s31 counted 959 person tracks over 8 days: a track that starts mid-frame (not at the frame's edge) is mostly a door, a
corner or a blind spot — «nedside vest» cell (0.2, 0.4) beside the car/wall had 22 starts, the doorbell's door 18. Such a
spot, once a human names it, gives code facts for the 27B: «kom fra garasjehjørnet», «gikk mot inngangsdøra».
  - counted from the stored events only (their path's first/last point, else J19's boxes) — no pixels are stored;
  - the counts are anonymous (camera × cell × start/end × day): nobody is counted by name, an opted-out person included
    only as «a person» like every other track;
  - a track split (the same camera, the next track starting within SPLIT_GAP_S near where the last one ended) is one
    person, not a place: neither point is counted. A hand-over (one camera's track ends, another camera's starts within
    HANDOVER_S) is counted as a cell pair; it becomes a fact only once that exact pair has been seen MIN_HANDOVERS times
    before (two people on two sides of the house at the same moment are not «the same person»);
  - a cell seen on MIN_DAYS different days and MIN_COUNT times becomes one admin question «Hva er her?» (a house matter,
    #9; at most MAX_OPEN at a time, the most seen first). The answer is a name, «ikke et sted» or «hopp over», kept in
    frigate.db (`spots`), never deleted; Frigate zones the user draws feed the same facts through Frigate's zone names.
Pure functions over events; the store does the IO.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from kaare_core.config import local_from_ts
from kaare_core.frigate_user import route, store

logger = logging.getLogger(__name__)

EDGE = 0.07  # a point this close to the frame's edge came from / went out of view, not from a spot
GRID = 5  # cells per side (s31: 0.2 of the frame)
WINDOW_DAYS = 14
MIN_COUNT = 8
MIN_DAYS = 3
MAX_OPEN = 3
SPLIT_GAP_S = 5.0
SPLIT_DIST = 0.12
HANDOVER_S = 10.0
MIN_HANDOVERS = 3
STATUSES = ("named", "not_a_place", "skip")
# J18b: a place's image is a recording frame with no active track on the camera (never a person, never an opted-out one)
EMPTY_PAD_S = 30.0  # no track this close before/after the moment
EMPTY_DAYS = 6  # look back this many days (Frigate keeps recordings ~7 days)
EMPTY_HOURS = (12, 11, 13, 10, 14, 9, 15)  # by day first (colour, not IR); the household's clock (rule 24)


def _points(ev: store.FrigateUserEvent) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """(x, y, ts) of the first and last foot point: the stored path, else J19's boxes (bottom centre)."""
    pts = []
    for p in ev.path or []:
        try:
            pts.append((float(p[0]), float(p[1]), float(p[2])))
        except (TypeError, ValueError, IndexError):
            continue
    if not pts:
        for b in ev.boxes or []:
            try:
                ts, x, y, w, h = (float(v) for v in b[:5])
            except (TypeError, ValueError):
                continue
            pts.append((x + w / 2, y + h, ts))
    return (pts[0], pts[-1]) if pts else None


def mid(x: float, y: float) -> bool:
    return EDGE <= x <= 1 - EDGE and EDGE <= y <= 1 - EDGE


def cell(x: float, y: float) -> str:
    return f"{min(GRID - 1, max(0, int(x * GRID)))},{min(GRID - 1, max(0, int(y * GRID)))}"


def cell_box(c: str) -> list[float]:
    col, row = (int(v) for v in c.split(","))
    return [col / GRID, row / GRID, 1 / GRID, 1 / GRID]


def _persons(events: list[store.FrigateUserEvent]) -> list[tuple[store.FrigateUserEvent, tuple, tuple]]:
    out = []
    for ev in events:
        if ev.label != "person":
            continue
        fl = _points(ev)
        if fl:
            out.append((ev, *fl))
    return sorted(out, key=lambda t: t[1][2])


def joins(events: list[store.FrigateUserEvent]) -> dict:
    """{splits: [(a, b)], handovers: [(a, b, (cam_a, cell_a), (cam_b, cell_b))], not_places: {(event_id, start|end)}}."""
    rows = _persons(events)
    splits, handovers, skip = [], [], set()
    for i, (a, _fa, la) in enumerate(rows):
        a_end = a.end_ts if a.end_ts is not None else la[2]
        for b, fb, _lb in rows[i + 1:]:
            gap = fb[2] - a_end
            if gap > HANDOVER_S:
                break
            if gap < -1.0:
                continue
            if b.camera == a.camera:
                if gap <= SPLIT_GAP_S and abs(fb[0] - la[0]) <= SPLIT_DIST and abs(fb[1] - la[1]) <= SPLIT_DIST:
                    splits.append((a.event_id, b.event_id))
                    skip.update({(a.event_id, "end"), (b.event_id, "start")})
            else:
                handovers.append((a.event_id, b.event_id, (a.camera, cell(la[0], la[1])), (b.camera, cell(fb[0], fb[1]))))
    return {"splits": splits, "handovers": handovers, "not_places": skip}


def counts(events: list[store.FrigateUserEvent]) -> dict[tuple[str, str], dict]:
    """(camera, cell) → {start, end, days, last_event, last_ts} over mid-frame first/last points, splits left out."""
    j = joins(events)
    out: dict[tuple[str, str], dict] = {}
    for ev, f, last in _persons(events):
        for kind, p in (("start", f), ("end", last)):
            if (ev.event_id, kind) in j["not_places"] or not mid(p[0], p[1]):
                continue
            c = out.setdefault((ev.camera, cell(p[0], p[1])), {"start": 0, "end": 0, "days": set(), "last_event": None,
                                                                 "last_ts": 0.0})
            c[kind] += 1
            c["days"].add(local_from_ts(p[2]).strftime("%Y-%m-%d"))  # the household's day (rule 24)
            if p[2] >= c["last_ts"] and ev.person_kind != "untracked":  # rule 10: never an opted-out person's image
                c["last_event"], c["last_ts"] = ev.event_id, p[2]
    return out


def questions(events: list[store.FrigateUserEvent], answered: dict[tuple[str, str], dict]) -> list[dict]:
    """The spots to ask admin about: [{kind: place, id: "camera|cell", camera, cell, box, starts, ends, days, event_id}]."""
    out = []
    for (camera, c), v in counts(events).items():
        if (camera, c) in answered or len(v["days"]) < MIN_DAYS or v["start"] + v["end"] < MIN_COUNT:
            continue
        out.append({"kind": "place", "id": f"{camera}|{c}", "camera": camera, "cell": c, "box": cell_box(c),
                    "starts": v["start"], "ends": v["end"], "days": len(v["days"])})  # J18b: no event image
    out.sort(key=lambda q: -(q["starts"] + q["ends"]))
    return out[:MAX_OPEN]


def _blocks(ev: store.FrigateUserEvent) -> bool:
    """A track that may put someone or something moving in the frame: persons (any kind, opted-out too), animals that
    were not still, a vehicle that drove. A parked car, a still bin or a still «animal» (a garden figure) is part of
    the place."""
    if ev.label == "person":
        return True
    if ev.label in store.ANIMAL_LABELS:
        return not ev.stationary  # J20: a still «animal» (the swan pot) is part of the place; a moving one is not
    return ev.label in store.VEHICLE_LABELS and bool(ev.drives) and not ev.stationary


def empty_moment(events: list[store.FrigateUserEvent], camera: str, now: float, tz) -> float | None:
    """The newest moment by day (EMPTY_HOURS, the last EMPTY_DAYS days) with no blocking track on `camera` within
    EMPTY_PAD_S; None when every candidate had one. Pure."""
    spans = [(e.start_ts - EMPTY_PAD_S, (e.end_ts if e.end_ts is not None else e.start_ts + 60) + EMPTY_PAD_S)
             for e in events if e.camera == camera and _blocks(e)]
    today = datetime.fromtimestamp(now, tz).replace(minute=0, second=0, microsecond=0)
    for d in range(EMPTY_DAYS + 1):
        for h in EMPTY_HOURS:
            ts = (today - timedelta(days=d)).replace(hour=h).timestamp()
            if ts < now - 60 and not any(a <= ts <= b for a, b in spans):
                return ts
    return None


def history(answered: dict[tuple[str, str], dict], limit: int = 20) -> list[dict]:
    """Registered spots and other answers, newest first: [{id, camera, cell, box, status, name?, ts}]."""
    rows = [(i, {"id": f"{cam}|{c}", "camera": cam, "cell": c, "box": cell_box(c), "status": a.get("status"),
                 "name": a.get("name") if a.get("status") == "named" else None, "ts": a.get("ts")})
            for i, ((cam, c), a) in enumerate(answered.items())]
    rows.sort(key=lambda r: (-(r[1]["ts"] or 0), -r[0]))  # same second: the later answer first (answers come in id order)
    return [r for _i, r in rows][:limit]


def _named(answered: dict[tuple[str, str], dict], camera: str, x: float, y: float) -> str | None:
    a = answered.get((camera, cell(x, y)))
    return a.get("name") if a and a.get("status") == "named" and a.get("name") else None


def fact_lines(episode_events: list[store.FrigateUserEvent], history: list[store.FrigateUserEvent],
               answered: dict[tuple[str, str], dict], cameras: dict, lang: str, t) -> list[str]:
    """Code facts for the 27B: «<camera>: en person kom fra <name> kl. HH:MM:SS» / «gikk mot <name>», and a hand-over
    whose cell pair is known from earlier days. `t(key, lang, **kw)` is the caller's text function. Never a name of a
    person; never raises."""
    try:
        lines = []
        j = joins(episode_events)
        for ev, f, last in _persons(episode_events):
            cam = route.camera_name(ev.camera, cameras.get(ev.camera), None, lang)
            if (ev.event_id, "start") not in j["not_places"] and mid(f[0], f[1]) and (n := _named(answered, ev.camera, *f[:2])):
                lines.append(t("spot_from", lang, camera=cam, place=n, time=route.clock(f[2])))
            if (ev.event_id, "end") not in j["not_places"] and mid(last[0], last[1]) and \
                    (n := _named(answered, ev.camera, *last[:2])):
                lines.append(t("spot_to", lang, camera=cam, place=n, time=route.clock(last[2])))
        if j["handovers"]:
            past = joins(history)["handovers"]
            seen: dict[tuple, int] = {}
            for _a, _b, pa, pb in past:
                seen[(pa, pb)] = seen.get((pa, pb), 0) + 1
            for _a, _b, pa, pb in j["handovers"]:
                n = seen.get((pa, pb), 0)
                if n >= MIN_HANDOVERS:
                    lines.append(t("spot_handover", lang,
                                   a=route.camera_name(pa[0], cameras.get(pa[0]), None, lang),
                                   b=route.camera_name(pb[0], cameras.get(pb[0]), None, lang), n=n))
        return lines
    except Exception as e:
        logger.info("[FRIGATE_PLACES] no place facts: %s", type(e).__name__)
        return []
