"""STM history pruning by size (P150 phase 4): state/stm_history/ keeps one folder per day; over the GUI limit
(storage_limits.stm_history_mb, Settings → Logs/Storage; 0 = off) the oldest day goes first — but only a day that is
condensed (D2). A day is condensed when the night after it left a row: a daily summary, or a recorded skip because nobody
talked to Kåre that day (stm_daily_skipped; 2026-11-13). Both are dated the night they ran, so a row dated the day or
the day after covers it. Days from before skips were recorded fall back to «no interactions that UTC day» (one scan, only
when such a day is reached). A day with conversations but no row is a real gap: logged, never pruned. If a list cannot be
read, nothing is pruned. The newest day is never pruned. Sync: call it with asyncio.to_thread.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from kaare_core.memory import long_term
from kaare_core.storage import limits, rotation

log = logging.getLogger(__name__)


class _Unreadable(Exception):
    pass


def prune(history_dir: Path) -> list[str]:
    """Remove the oldest condensed day folders until the history fits its limit. Returns the names removed."""
    max_bytes = limits.get_bytes("stm_history_mb")
    if max_bytes <= 0 or not history_dir.is_dir():
        return []
    summaries, skips = long_term.summary_dates(), long_term.skipped_dates()
    if summaries is None or skips is None:
        log.warning("STM history not pruned: summaries/skips unreadable")
        return []
    nights = summaries | skips
    talked: list[set[str]] = []  # filled at most once, only for days without a row

    def condensed(day: str) -> bool:
        try:
            nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        except ValueError:
            return False
        if day in nights or nxt in nights:
            return True
        if not talked:
            found = long_term.interaction_dates()
            if found is None:
                raise _Unreadable
            talked.append(found)
        if day in talked[0]:
            log.warning("STM history day %s kept: conversations but no summary (a real gap)", day)
            return False
        return True

    try:
        gone = rotation.prune_dirs_oldest(history_dir, max_bytes, can_delete=condensed)
    except _Unreadable:
        log.warning("STM history not pruned: interactions unreadable")
        return []
    for name in gone:
        log.info("STM history pruned (size limit): %s", name)
    return gone
