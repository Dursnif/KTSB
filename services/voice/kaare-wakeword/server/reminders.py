"""Reminder scheduler with SQLite storage.

Supports voice-triggered reminders that broadcast to all speakers
and repeat until dismissed or max repeats reached.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)


def parse_reminder_time(time_str: str) -> float | None:
    """Parse LLM time output to Unix timestamp.

    Formats:
      - "HH:MM" — today (or tomorrow if already passed)
      - "+Xm" — X minutes from now
      - "tomorrow HH:MM" — tomorrow at HH:MM

    Returns Unix timestamp or None if unparseable.
    """
    if not time_str or not time_str.strip():
        return None
    time_str = time_str.strip()

    # Relative: +30m, +5m, etc.
    m = re.match(r'^\+(\d+)m$', time_str)
    if m:
        minutes = int(m.group(1))
        return time.time() + minutes * 60

    # Tomorrow HH:MM
    m = re.match(r'^tomorrow\s+(\d{1,2}):(\d{2})$', time_str, re.IGNORECASE)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        dt = datetime.datetime.combine(tomorrow, datetime.time(hour, minute))
        return dt.timestamp()

    # Absolute HH:MM
    m = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        now = datetime.datetime.now()
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt <= now:
            dt += datetime.timedelta(days=1)
        return dt.timestamp()

    return None


class ReminderScheduler:
    """Manages reminders with SQLite storage and repeat logic."""

    def __init__(self, db_path: str = "data/reminders.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                trigger_at REAL NOT NULL,
                satellite_id TEXT DEFAULT '',
                speaker TEXT DEFAULT '',
                repeat_count INTEGER DEFAULT 0,
                max_repeats INTEGER DEFAULT 3,
                repeat_interval INTEGER DEFAULT 300,
                dismissed INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        self._db.commit()

    def add_reminder(
        self,
        message: str,
        trigger_at: float,
        satellite_id: str = "",
        speaker: str = "",
        max_repeats: int = 3,
        repeat_interval: int = 300,
    ) -> int:
        """Add a new reminder. Returns the reminder ID."""
        cur = self._db.execute(
            """INSERT INTO reminders
               (message, trigger_at, satellite_id, speaker, max_repeats, repeat_interval, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (message, trigger_at, satellite_id, speaker, max_repeats, repeat_interval, time.time()),
        )
        self._db.commit()
        return cur.lastrowid

    def get_due_reminders(self) -> list[dict]:
        """Get all reminders that are due and not dismissed/expired."""
        now = time.time()
        rows = self._db.execute(
            """SELECT * FROM reminders
               WHERE trigger_at <= ? AND dismissed = 0 AND repeat_count < max_repeats
               ORDER BY trigger_at ASC""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_repeated(self, reminder_id: int) -> None:
        """Increment repeat_count and push trigger_at forward."""
        row = self._db.execute(
            "SELECT repeat_interval FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if row:
            self._db.execute(
                """UPDATE reminders
                   SET repeat_count = repeat_count + 1,
                       trigger_at = ?
                   WHERE id = ?""",
                (time.time() + row["repeat_interval"], reminder_id),
            )
            self._db.commit()

    def dismiss_reminder(self, reminder_id: int) -> None:
        """Dismiss a specific reminder."""
        self._db.execute(
            "UPDATE reminders SET dismissed = 1 WHERE id = ?",
            (reminder_id,),
        )
        self._db.commit()

    def has_active_reminder(self) -> bool:
        """Check if any reminder is actively repeating (repeat_count > 0, not dismissed)."""
        row = self._db.execute(
            """SELECT COUNT(*) as cnt FROM reminders
               WHERE dismissed = 0 AND repeat_count > 0 AND repeat_count < max_repeats""",
        ).fetchone()
        return row["cnt"] > 0

    def dismiss_most_recent(self) -> bool:
        """Dismiss the most recently created active reminder. Returns True if one was dismissed."""
        row = self._db.execute(
            """SELECT id FROM reminders
               WHERE dismissed = 0 AND repeat_count > 0 AND repeat_count < max_repeats
               ORDER BY created_at DESC LIMIT 1""",
        ).fetchone()
        if row:
            self.dismiss_reminder(row["id"])
            return True
        return False

    async def run_poll_loop(
        self,
        on_reminder,
        interval: float = 30.0,
    ) -> None:
        """Poll for due reminders every `interval` seconds.

        Args:
            on_reminder: async callback(message: str, reminder_id: int)
            interval: Seconds between polls.
        """
        log.info("Reminder poll loop started (interval=%.0fs)", interval)
        while True:
            try:
                due = self.get_due_reminders()
                for r in due:
                    try:
                        await on_reminder(r["message"], r["id"])
                        self.mark_repeated(r["id"])
                    except Exception as exc:
                        log.warning("Reminder delivery failed for #%d: %s", r["id"], exc)
            except Exception as exc:
                log.warning("Reminder poll error: %s", exc)
            await asyncio.sleep(interval)

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
