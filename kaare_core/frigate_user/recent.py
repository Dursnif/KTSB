"""«Frigate saw …» in Kåre's situational context, from frigate.db (run-queue J21b reader 6, main session 2026-11-12 (b)).

Before: `context_builder` tailed `frigate_mqtt.log` and put Frigate's raw sub_label (no consent gate, no check of who is
asking) into every turn. Now: module 0's stored events of the last minutes, newest first, one per camera + label, never one
the stationary gate held back (a parked car / bin that did not change); a person's name only when the caller may get names
(`query.names_allowed` — a guest or an unknown voice never does), the storage gate named them and they have not opted out
since. Module 0 off → nothing (no fallback to the raw log). Read only; never raises.
"""

from __future__ import annotations

import logging
import time

from adapters import frigate_adapter
from kaare_core.domain import frigate_store as camera_cards
from kaare_core.frigate_user import query, store

log = logging.getLogger(__name__)

HELD_BACK = ("unchanged_stationary", "stationary_unknown")


def recent(user_id: str | None, minutes: int = 30, max_lines: int = 3, now: float | None = None) -> list[dict]:
    """[{label, camera, name | None, minutes_ago}], newest first."""
    try:
        if not query.available():
            return []
        now = time.time() if now is None else now
        evs = [e for e in store.events_between(now - minutes * 60, now)
               if ((e.check or {}).get("drive") or {}).get("why") not in HELD_BACK]
        latest: dict[tuple[str, str], store.FrigateUserEvent] = {}
        for e in evs:
            k = (e.camera, e.label)
            if k not in latest or (e.end_ts or e.start_ts) >= (latest[k].end_ts or latest[k].start_ts):
                latest[k] = e
        picked = sorted(latest.values(), key=lambda e: -(e.end_ts or e.start_ts))[:max_lines]
        if not picked:
            return []
        names = query.names_allowed(user_id)
        refs = {e.person_ref for e in picked if e.person_ref}
        hidden = {n.casefold() for n in store.hidden_names(refs)} if names and refs else set()
        users = query._users_by_name() if names and refs else {}
        try:
            cards = camera_cards.camera_config().get("cameras") or {}
        except Exception:
            cards = {}
        out = []
        for e in picked:
            ref = e.person_ref if (names and e.person_ref and e.person_kind != "untracked"
                                   and e.person_ref.casefold() not in hidden) else None
            out.append({"label": e.label, "camera": frigate_adapter.camera_display_name(e.camera, cards, fetch=False),
                        "name": query._display(ref, users) if ref else None,
                        "minutes_ago": max(0, int((now - (e.end_ts or e.start_ts)) / 60))})
        return out
    except Exception as ex:
        log.debug("[FRIGATE_RECENT] %s", ex)
        return []
