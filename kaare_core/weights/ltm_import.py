"""Read the existing chat verifications (LTM `verifications` in interactions.db) as low-weight human evidence (P149
phase 1, D12 note).

What a verification is today: after a number of unverified interactions Kåre asks for a check and the user's reply
marks a whole *range* of interactions confirmed/denied. That is a human's vague, range-wide answer → one `answers` row
per verification, `verbatim=False` (VAGUE_WEIGHT), subject ("chat_answers", user) scoped to that user. The old
mechanism keeps running unchanged; nothing here writes to interactions.db (opened read-only).
Idempotent: a verification already imported (same pointer) is skipped.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from kaare_core.memory import long_term
from kaare_core.storage import sqlite_files
from kaare_core.weights.store import WeightStore, get_store

logger = logging.getLogger(__name__)

_VERDICT = {"confirmed": "yes", "denied": "no"}


def import_ltm_verifications(db_path: Path | None = None, store: WeightStore | None = None) -> int:
    """Copy answered verifications into the weight store as vague answers. Returns how many were new. Never raises."""
    path = Path(db_path or long_term.DB_PATH)
    store = store or get_store()
    if not path.exists():
        return 0
    try:
        conn = sqlite_files.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        logger.warning("weights: cannot open %s read-only: %s", path, e)
        return 0
    new = 0
    try:
        rows = conn.execute(
            "SELECT id, ts_asked, ts_answered, user_id, verdict FROM verifications "
            "WHERE verdict IN ('confirmed', 'denied') ORDER BY id"
        ).fetchall()
        for vid, ts_asked, ts_answered, user_id, verdict in rows:
            pointer = f"ltm.verifications:{vid}"
            if store.has_pointer("answers", pointer):
                continue
            uid = user_id or long_term.USER_GLOBAL
            if store.answer(("chat_answers", uid, uid), by_user=uid, verdict=_VERDICT[verdict], verbatim=False,
                            context=pointer, pointer=pointer, ts=ts_answered or ts_asked):
                new += 1
    except sqlite3.Error as e:
        logger.warning("weights: reading verifications failed: %s", e)
    finally:
        conn.close()
    store.flush()
    logger.info("weights: imported %d LTM verifications (shadow)", new)
    return new
