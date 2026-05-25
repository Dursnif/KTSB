# Reminders Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add voice-triggered reminders to Kåre that broadcast to all speakers and repeat until dismissed.

**Architecture:** Asyncio scheduler polls SQLite every 30s for due reminders. NLU parses `set_reminder` action with time+message. Delivery via broadcast (satellites + Sonos) + HA phone notify. Voice dismiss via pre-NLU pattern match.

**Tech Stack:** Python asyncio, SQLite (aiosqlite not needed — sync polling in thread), existing TTS/broadcast infrastructure.

---

### Task 1: Time parsing — write failing tests

**Files:**
- Create: `tests/server/test_reminders.py`

**Step 1: Write the failing tests**

```python
"""Tests for reminders module."""
from __future__ import annotations

import time

import pytest

from server.reminders import parse_reminder_time


class TestParseReminderTime:
    def test_absolute_time_today(self):
        """'10:00' should return today at 10:00 if not yet passed."""
        import datetime
        now = datetime.datetime.now()
        # Use a time far in the future to ensure it's today
        result = parse_reminder_time("23:59")
        assert result is not None
        dt = datetime.datetime.fromtimestamp(result)
        assert dt.hour == 23
        assert dt.minute == 59
        assert dt.date() == now.date()

    def test_absolute_time_past_rolls_to_tomorrow(self):
        """'00:01' at any reasonable hour should roll to tomorrow."""
        import datetime
        result = parse_reminder_time("00:01")
        assert result is not None
        dt = datetime.datetime.fromtimestamp(result)
        # Should be in the future
        assert result > time.time()

    def test_relative_time(self):
        """+30m should be ~30 minutes from now."""
        before = time.time()
        result = parse_reminder_time("+30m")
        assert result is not None
        assert abs(result - (before + 30 * 60)) < 2  # within 2s tolerance

    def test_tomorrow_time(self):
        """'tomorrow 09:00' should be tomorrow at 09:00."""
        import datetime
        result = parse_reminder_time("tomorrow 09:00")
        assert result is not None
        dt = datetime.datetime.fromtimestamp(result)
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).date()
        assert dt.date() == tomorrow
        assert dt.hour == 9
        assert dt.minute == 0

    def test_invalid_returns_none(self):
        """Unparseable time string returns None."""
        assert parse_reminder_time("whenever") is None
        assert parse_reminder_time("") is None
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_reminders.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.reminders'`

---

### Task 2: Time parsing — implement

**Files:**
- Create: `server/reminders.py`

**Step 1: Write minimal implementation**

```python
"""Reminder scheduler with SQLite storage.

Supports voice-triggered reminders that broadcast to all speakers
and repeat until dismissed or max repeats reached.
"""
from __future__ import annotations

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
```

**Step 2: Run tests to verify they pass**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_reminders.py::TestParseReminderTime -v`
Expected: All 5 PASS

---

### Task 3: SQLite storage + scheduler — write failing tests

**Files:**
- Modify: `tests/server/test_reminders.py`

**Step 1: Add scheduler tests**

Append to `tests/server/test_reminders.py`:

```python
class TestReminderScheduler:
    def test_add_reminder(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        rid = sched.add_reminder("Gå ut med søppla", time.time() + 600)
        assert rid is not None
        assert isinstance(rid, int)
        sched.close()

    def test_get_due_reminders(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        # Add a reminder that's already due
        sched.add_reminder("Test påminnelse", time.time() - 10)
        due = sched.get_due_reminders()
        assert len(due) == 1
        assert due[0]["message"] == "Test påminnelse"
        sched.close()

    def test_dismiss_reminder(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        rid = sched.add_reminder("Dismiss meg", time.time() - 10)
        sched.dismiss_reminder(rid)
        due = sched.get_due_reminders()
        assert len(due) == 0
        sched.close()

    def test_increment_repeat(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        rid = sched.add_reminder("Gjenta", time.time() - 10, repeat_interval=300)
        due = sched.get_due_reminders()
        assert len(due) == 1
        sched.mark_repeated(rid)
        # After marking repeated, trigger_at should be in the future
        due = sched.get_due_reminders()
        assert len(due) == 0
        sched.close()

    def test_max_repeats_stops(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        rid = sched.add_reminder("Stopp", time.time() - 10, max_repeats=1)
        sched.mark_repeated(rid)
        # After 1 repeat with max_repeats=1, should not show as due
        due = sched.get_due_reminders()
        assert len(due) == 0
        sched.close()

    def test_has_active_reminder(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        assert sched.has_active_reminder() is False
        rid = sched.add_reminder("Aktiv", time.time() - 10)
        sched.mark_repeated(rid)  # now it's actively repeating
        assert sched.has_active_reminder() is True
        sched.close()

    def test_dismiss_most_recent(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        sched.add_reminder("Første", time.time() - 20)
        rid2 = sched.add_reminder("Andre", time.time() - 10)
        # Mark both as repeated (actively repeating)
        for r in sched.get_due_reminders():
            sched.mark_repeated(r["id"])
        dismissed = sched.dismiss_most_recent()
        assert dismissed is True
        sched.close()
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_reminders.py::TestReminderScheduler -v`
Expected: FAIL with `ImportError: cannot import name 'ReminderScheduler'`

---

### Task 4: SQLite storage + scheduler — implement

**Files:**
- Modify: `server/reminders.py`

**Step 1: Add ReminderScheduler class**

Append to `server/reminders.py`:

```python
class ReminderScheduler:
    """Manages reminders with SQLite storage and repeat logic.

    Args:
        db_path: Path to SQLite database file.
    """

    def __init__(self, db_path: str = "data/reminders.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path)
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
            "SELECT repeat_interval, repeat_count FROM reminders WHERE id = ?",
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

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
```

**Step 2: Run tests to verify they pass**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_reminders.py -v`
Expected: All 12 PASS (5 time + 7 scheduler)

**Step 3: Commit**

```bash
git add server/reminders.py tests/server/test_reminders.py
git commit -m "feat(reminders): add time parsing and SQLite scheduler"
```

---

### Task 5: Add set_reminder to NLU system prompt

**Files:**
- Modify: `server/nlu.py:65-69` (before the broadcast action)
- Modify: `tests/server/test_nlu.py`

**Step 1: Write failing test**

Add to `tests/server/test_nlu.py`:

```python
class TestReminders:
    def test_set_reminder_parsed(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "set_reminder", "message": "Gå ut med søppla", "time": "10:00", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.action == "set_reminder"
        assert result.entities.get("message") == "Gå ut med søppla"
        assert result.entities.get("time") == "10:00"

    def test_system_prompt_includes_set_reminder(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "set_reminder" in engine._system_prompt
```

**Step 2: Run to verify test_system_prompt_includes_set_reminder fails**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_nlu.py::TestReminders -v`
Expected: `test_set_reminder_parsed` PASS (JSON parsing already works), `test_system_prompt_includes_set_reminder` FAIL

**Step 3: Add set_reminder to system prompt**

In `server/nlu.py`, add before the broadcast action line (before `If the user asks you to broadcast`):

```
If the user asks to be reminded of something at a specific time:
{{"action": "set_reminder", "message": "what to remind about", "time": "10:00", "confidence": 5}}
Time formats: "HH:MM" (today/tomorrow), "+Xm" (relative minutes), "tomorrow HH:MM".
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_nlu.py::TestReminders -v`
Expected: Both PASS

**Step 5: Commit**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "feat(reminders): add set_reminder action to NLU prompt"
```

---

### Task 6: Wire set_reminder + dismiss into server pipeline

**Files:**
- Modify: `server/server.py`

**Step 1: Add imports and scheduler init**

In `server/server.py`, add import at top (after other server imports):

```python
from server.reminders import ReminderScheduler, parse_reminder_time
```

In `ServerPipeline.__init__`, after `self._session_log = SessionLogger()`:

```python
self._reminders = ReminderScheduler()
```

**Step 2: Add dismiss pattern (pre-NLU shortcut)**

In `server/server.py`, add after `_DATE_PATTERN`:

```python
_DISMISS_PATTERN = re.compile(
    r'\b(ok(?:ay)?|stopp|mottatt|slutt|dismiss)\b',
    re.IGNORECASE,
)
```

In `_process_transcript`, after the date shortcut block and before `nlu_result = self._nlu.process_local(...)`:

```python
# Pre-NLU shortcut: dismiss active reminder
if _DISMISS_PATTERN.search(text_lower) and self._reminders.has_active_reminder():
    dismissed = self._reminders.dismiss_most_recent()
    if dismissed:
        response = "Påminnelsen er avvist."
        log.info("Pre-NLU shortcut: dismiss reminder")
        sl.log(satellite_id, "shortcut", {"type": "dismiss_reminder"})
        nlu_result = NLUResult(
            action="answer", entities={}, response_text=response,
            confidence=1.0, source="shortcut",
        )
        tts_audio = self._synthesize_and_play(response)
        return PipelineResult(transcript=transcript, nlu=nlu_result, tts_audio=tts_audio)
```

**Step 3: Handle set_reminder action**

In `_process_transcript`, after the broadcast handler block (after `if nlu_result.action == "broadcast": ...`), add:

```python
# Handle set_reminder action
if nlu_result.action == "set_reminder":
    message = nlu_result.entities.get("message", "")
    time_str = nlu_result.entities.get("time", "")
    trigger_at = parse_reminder_time(time_str)
    if trigger_at and message:
        self._reminders.add_reminder(
            message=message,
            trigger_at=trigger_at,
            satellite_id=satellite_id,
        )
        import datetime
        dt = datetime.datetime.fromtimestamp(trigger_at)
        response = f"Jeg minner deg på det klokka {dt.strftime('%H:%M')}."
        sl.log(satellite_id, "reminder_set", {
            "message": message, "trigger_at": trigger_at,
        })
    else:
        response = "Beklager, jeg forstod ikke tidspunktet."
    nlu_result.response_text = response
    tts_audio = self._synthesize_and_play(response)
    return PipelineResult(transcript=transcript, nlu=nlu_result, tts_audio=tts_audio)
```

**Step 4: Run existing tests to verify nothing breaks**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/ -v`
Expected: All existing tests PASS

**Step 5: Commit**

```bash
git add server/server.py
git commit -m "feat(reminders): wire set_reminder + dismiss into pipeline"
```

---

### Task 7: Async reminder delivery loop

**Files:**
- Modify: `server/reminders.py`
- Modify: `server/server.py`

**Step 1: Add async poll method to ReminderScheduler**

In `server/reminders.py`, add at top:

```python
import asyncio
```

Add method to `ReminderScheduler`:

```python
async def run_poll_loop(
    self,
    on_reminder,
    interval: float = 30.0,
) -> None:
    """Poll for due reminders every `interval` seconds.

    Args:
        on_reminder: async callback(message: str, reminder_id: int)
            Called for each due reminder.
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
```

**Step 2: Start poll loop in VoiceServer.start()**

In `server/server.py`, in `VoiceServer.start()`, after the model preload block, add:

```python
# Start reminder poll loop if pipeline has reminders
if self._pipeline and hasattr(self._pipeline, '_reminders'):
    async def _on_reminder(message: str, reminder_id: int) -> None:
        """Deliver reminder via broadcast."""
        text = f"Påminnelse: {message}"
        tts_audio = self._pipeline._tts.synthesize(text)
        # Broadcast to all satellites
        if self._registry:
            self._registry.broadcast_audio(tts_audio)
        # Broadcast to Sonos
        if self._pipeline._sonos:
            self._pipeline._sonos.broadcast(text)
        # HA phone notification
        if self._pipeline._ha_tool:
            try:
                self._pipeline._ha_tool.handle({
                    "method": "POST",
                    "path": "/api/services/notify/notify",
                    "body": {"message": text, "title": "Kåre påminnelse"},
                })
            except Exception as exc:
                log.warning("HA notify failed: %s", exc)
        log.info("Reminder delivered: %s", message)

    asyncio.create_task(
        self._pipeline._reminders.run_poll_loop(_on_reminder)
    )
```

**Step 3: Run all tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add server/reminders.py server/server.py
git commit -m "feat(reminders): async poll loop with broadcast + HA notify delivery"
```

---

### Task 8: Integration test — full flow

**Files:**
- Modify: `tests/server/test_reminders.py`

**Step 1: Add integration-style test**

Append to `tests/server/test_reminders.py`:

```python
class TestReminderIntegration:
    def test_set_and_dismiss_flow(self, tmp_path):
        """Full flow: parse time → add → query due → dismiss."""
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "integration.db")
        sched = ReminderScheduler(db_path=db)

        # Set a reminder for "now" (already due)
        trigger_at = parse_reminder_time("+0m")
        assert trigger_at is not None
        rid = sched.add_reminder("Ta ut søppla", trigger_at)

        # Should be due
        due = sched.get_due_reminders()
        assert len(due) == 1
        assert due[0]["message"] == "Ta ut søppla"

        # First repeat
        sched.mark_repeated(rid)
        assert sched.has_active_reminder() is True

        # Dismiss by voice
        assert sched.dismiss_most_recent() is True
        assert sched.has_active_reminder() is False

        # No more due
        due = sched.get_due_reminders()
        assert len(due) == 0
        sched.close()
```

**Step 2: Run all reminder tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/server/test_reminders.py -v`
Expected: All 13 PASS

**Step 3: Run full test suite**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && uv run pytest tests/ -v`
Expected: All pass (except 2 pre-existing failures)

**Step 4: Commit**

```bash
git add tests/server/test_reminders.py
git commit -m "test(reminders): add integration test for full set-dismiss flow"
```
