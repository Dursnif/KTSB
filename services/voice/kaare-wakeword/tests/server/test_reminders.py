"""Tests for reminders module."""
from __future__ import annotations

import time

import pytest

from server.reminders import parse_reminder_time


class TestParseReminderTime:
    def test_absolute_time_today(self):
        """'23:59' should return today at 23:59 (unless it's already past)."""
        import datetime
        result = parse_reminder_time("23:59")
        assert result is not None
        dt = datetime.datetime.fromtimestamp(result)
        assert dt.hour == 23
        assert dt.minute == 59

    def test_absolute_time_past_rolls_to_tomorrow(self):
        """'00:01' at any reasonable hour should roll to tomorrow."""
        import datetime
        result = parse_reminder_time("00:01")
        assert result is not None
        assert result > time.time()

    def test_relative_time(self):
        """+30m should be ~30 minutes from now."""
        before = time.time()
        result = parse_reminder_time("+30m")
        assert result is not None
        assert abs(result - (before + 30 * 60)) < 2

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
        due = sched.get_due_reminders()
        assert len(due) == 0
        sched.close()

    def test_max_repeats_stops(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        rid = sched.add_reminder("Stopp", time.time() - 10, max_repeats=1)
        sched.mark_repeated(rid)
        due = sched.get_due_reminders()
        assert len(due) == 0
        sched.close()

    def test_has_active_reminder(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        assert sched.has_active_reminder() is False
        rid = sched.add_reminder("Aktiv", time.time() - 10)
        sched.mark_repeated(rid)
        assert sched.has_active_reminder() is True
        sched.close()

    def test_dismiss_most_recent(self, tmp_path):
        from server.reminders import ReminderScheduler
        db = str(tmp_path / "test.db")
        sched = ReminderScheduler(db_path=db)
        sched.add_reminder("Første", time.time() - 20)
        sched.add_reminder("Andre", time.time() - 10)
        for r in sched.get_due_reminders():
            sched.mark_repeated(r["id"])
        dismissed = sched.dismiss_most_recent()
        assert dismissed is True
        sched.close()


class TestReminderIntegration:
    def test_set_and_dismiss_flow(self, tmp_path):
        """Full flow: parse time -> add -> query due -> dismiss."""
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
