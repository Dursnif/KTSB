"""Does a user need a reflection meeting tonight? (DT04 R0.9c)

A meeting about a user with nothing new since that user's last meeting only re-reads old material — on 2026-11-15
five such meetings took 31 minutes of the main model. The night sequence asks here first, per user:

- **activity** = the user's own interactions (the rows the portrait step reads: outcome `success` / `llm_fallback`)
  newer than the user's newest meeting file; a user who never had a meeting needs one as soon as there is any;
- something a person asked the meeting to take up counts too: the user's comment on the last meeting, the user's
  topic, the admin's topic;
- what the cameras saw does **not** count — it belongs to the household, not to one user.

Read-only and sync (the caller is the night sequence, off the hot path). Never raises: when the interaction store
cannot be read the answer is «yes» — a broken check must not silence the meetings.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from kaare_core.storage import sqlite_files

log = logging.getLogger(__name__)

INTERACTIONS_DB = Path("/kaare/state/memory/interactions.db")
REFLECTIONS_BASE = Path("/kaare/state/memory/reflections")
COMMENTS_BASE = Path("/kaare/state/meeting_comments")
USER_TOPICS_BASE = Path("/kaare/state/meeting_topics_user")
ADMIN_TOPICS_FILE = Path("/kaare/state/meeting_topics.json")
_COUNTED_OUTCOMES = ("success", "llm_fallback")  # the same rows as the portrait step (kaare_miss_kare_nightjob)


def last_meeting_ts(user_id: str) -> float | None:
    """When the user's newest dated meeting file was written (epoch seconds), or None. `latest.md` is a copy."""
    try:
        files = [f for f in (REFLECTIONS_BASE / user_id).glob("*.md") if f.stem[:4].isdigit()]
        return max(f.stat().st_mtime for f in files) if files else None
    except OSError:
        return None


def _has_text(path: Path) -> bool:
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _asked_for(user_id: str) -> str:
    """What a person asked this meeting to take up, as a short reason — '' when nothing."""
    try:
        if any(_has_text(f) for f in (COMMENTS_BASE / "reflection" / user_id).glob("*.txt")):
            return "the user's comment on the last meeting"
    except OSError:
        pass
    if _has_text(USER_TOPICS_BASE / f"{user_id}.txt"):
        return "the user's topic"
    try:
        if str(json.loads(ADMIN_TOPICS_FILE.read_text(encoding="utf-8")).get("reflection") or "").strip():
            return "the admin's topic"
    except (OSError, ValueError, AttributeError):
        pass
    return ""


def _interactions_since(user_id: str, since_ts: float | None) -> int:
    marks = ",".join("?" * len(_COUNTED_OUTCOMES))
    sql = f"SELECT COUNT(*) FROM interactions WHERE user_id = ? AND outcome IN ({marks})"
    args: list = [user_id, *_COUNTED_OUTCOMES]
    if since_ts is not None:
        sql += " AND ts >= ?"
        args.append(datetime.fromtimestamp(since_ts, UTC).isoformat(timespec="seconds"))
    conn = sqlite_files.connect(f"file:{INTERACTIONS_DB}?mode=ro", uri=True, timeout=5)
    try:
        return int(conn.execute(sql, args).fetchone()[0])
    finally:
        conn.close()


def needs_meeting(user_id: str) -> tuple[bool, str]:
    """(hold the meeting?, why) — the reason is an English log text (no names beyond the user id the caller logs)."""
    asked = _asked_for(user_id)
    if asked:
        return True, asked
    last = last_meeting_ts(user_id)
    try:
        n = _interactions_since(user_id, last)
    except sqlite3.Error as e:
        log.warning("[MEETING] could not read the interactions of %s (%s) — the meeting runs", user_id, type(e).__name__)
        return True, "could not read the interactions"
    if n:
        return True, f"{n} new interaction(s)"
    if last is None:
        return False, "no new activity: no interactions at all"
    when = datetime.fromtimestamp(last, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return False, f"no new activity since the last meeting ({when})"
