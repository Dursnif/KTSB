"""RAM counters for the 27B conclusion's count table (P144-s16 part B).

2026-10-28: «27B-jobben … kode som forteller x antall hendelser/episoder rundt hver bruker, samt det som er krysset
av i kamerainnstillinger … så 27B får bare en liten liste med kolonner og tall laget av kode, for større forståelse av
tid og rom». The 27B gets numbers made by code instead of the long recent-episodes text.

  - **Rows:** a named person (as the storage gate kept the name: household or guest), «unknown person» (a person event
    without a kept name — opted-out people land here), and a Frigate label (`label:person`, `label:deer` …).
  - **What is counted:** episodes, not events (one person crossing three cameras is three Frigate events, one episode).
    Each row keeps, per episode, its latest sighting (ts, camera, place) — events without an episode are not counted.
  - **RAM only in the everyday path:** `note_event()` is called by the store for every stored event (registered as a
    store listener when this module is imported — conclude imports it); nothing here
    queries frigate.db except `rebuild()`, once at start (the last REBUILD_S of events). Entries older than KEEP_S are
    pruned; the newest pruned sighting per row is kept as «last seen».
  - Names are stored as the store kept them; the caller hides opted-out names when it builds the table.
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from dataclasses import dataclass

from kaare_core.frigate_user import store

logger = logging.getLogger(__name__)

KEEP_S = 8 * 86400  # «last 7 days» + a day of margin for the window's edge
REBUILD_S = 30 * 86400  # what a start reads back from frigate.db (the «last seen» horizon after a restart)
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Sighting:
    ts: float
    episode_id: int
    camera: str
    place: str | None


_lock = threading.Lock()
_rows: dict[str, dict[int, Sighting]] = {}  # row key → episode_id → latest sighting in that episode
_older: dict[str, Sighting] = {}  # row key → newest sighting pruned out of _rows
_loaded = False


def name_key(name: str) -> str:
    return f"name:{name}"


def label_key(label: str) -> str:
    return f"label:{label}"


def row_keys(ev: store.FrigateUserEvent) -> list[str]:
    """The rows one stored event counts for."""
    keys = [label_key(ev.label)]
    if ev.label == "person":
        named = ev.person_ref and ev.person_kind in store.NAMED_KINDS
        keys.append(name_key(ev.person_ref) if named else UNKNOWN)
    return keys


def _prune(now: float) -> None:
    cutoff = now - KEEP_S
    for key, eps in _rows.items():
        old = [eid for eid, s in eps.items() if s.ts < cutoff]
        for eid in old:
            s = eps.pop(eid)
            if key not in _older or s.ts > _older[key].ts:
                _older[key] = s


def _note(ev: store.FrigateUserEvent, episode_id: int) -> None:
    s = Sighting(ts=float(ev.start_ts), episode_id=int(episode_id), camera=ev.camera, place=ev.place)
    for key in row_keys(ev):
        eps = _rows.setdefault(key, {})
        cur = eps.get(s.episode_id)
        if cur is None or s.ts > cur.ts:
            eps[s.episode_id] = s


def note_event(ev: store.FrigateUserEvent, episode_id: int | None, now: float | None = None) -> None:
    """The store calls this after every stored event (names as the storage gate kept them). Never raises."""
    if episode_id is None:
        return
    try:
        with _lock:
            _note(ev, episode_id)
            _prune(time.time() if now is None else now)
    except Exception as e:
        logger.debug("[FRIGATE_COUNTS] note skipped: %s", e)


def rebuild(now: float | None = None) -> int:
    """Read the last REBUILD_S of events back from frigate.db into RAM (at start). Returns events counted."""
    global _loaded
    now = time.time() if now is None else now
    try:
        events = store.events_between(now - REBUILD_S, now)
    except Exception as e:
        logger.warning("[FRIGATE_COUNTS] rebuild failed, counters start empty: %s", e)
        events = []
    n = 0
    with _lock:
        _rows.clear()
        _older.clear()
        for ev in events:
            if ev.episode_id is not None:
                _note(ev, ev.episode_id)
                n += 1
        _prune(now)
        _loaded = True
    logger.info("[FRIGATE_COUNTS] rebuilt from frigate.db: %d events, %d rows", n, len(_rows))
    return n


def loaded() -> bool:
    return _loaded


store.add_stored_listener(note_event)


def reset_for_tests() -> None:
    global _loaded
    with _lock:
        _rows.clear()
        _older.clear()
        _loaded = False


def known_names() -> list[str]:
    """Every name with a sighting in RAM, sorted."""
    with _lock:
        keys = set(_rows) | set(_older)
    return sorted(k.split(":", 1)[1] for k in keys if k.startswith("name:"))


@dataclass(frozen=True)
class RowCount:
    today: int  # episodes since local midnight of the reference, before it
    week: int  # episodes in the 7 days before the reference
    last: Sighting | None  # latest sighting before the reference


def count(keys: list[str], ref_ts: float, day_start_ts: float, exclude_episode: int | None) -> RowCount:
    """Episodes with any of `keys` (merged: a hidden name counts as «unknown») before `ref_ts`."""
    with _lock:
        merged: dict[int, Sighting] = {}
        for key in keys:
            for eid, s in (_rows.get(key) or {}).items():
                if eid != exclude_episode and s.ts < ref_ts and (eid not in merged or s.ts > merged[eid].ts):
                    merged[eid] = s
        older = [_older[k] for k in keys if k in _older and _older[k].ts < ref_ts]
    stamps = sorted(s.ts for s in merged.values())
    today = len(stamps) - bisect.bisect_left(stamps, day_start_ts)
    week = len(stamps) - bisect.bisect_left(stamps, ref_ts - 7 * 86400)
    candidates = list(merged.values()) + older
    last = max(candidates, key=lambda s: s.ts) if candidates else None
    return RowCount(today=today, week=week, last=last)
