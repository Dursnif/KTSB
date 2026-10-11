"""
Tests for ShortTermMemory.mark_activity() — "day_started"/"session_started" state
anchors, added 2026-09-06 (P105) after a real conversation where Kåre couldn't tell that
his own opening greeting had scrolled out of get_dialog_pairs()'s n=4 replay window and
guessed the wrong turn instead. Unlike dialog turns, these live in `_state`, which is
always shown in full — they give the model a stable anchor even deep into a long
same-day conversation.

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_stm_activity_markers.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import time as _time
from datetime import UTC, datetime, timedelta

from kaare_core.memory.short_term import _SESSION_GAP_MINUTES, ShortTermMemory


def test_first_call_sets_both_markers():
    memory = ShortTermMemory(user_id="testuser")
    memory.mark_activity()

    assert memory.get_state("day_started") is not None
    assert memory.get_state("session_started") is not None


def test_day_marker_unchanged_within_the_same_day():
    memory = ShortTermMemory(user_id="testuser")
    memory.mark_activity()
    first_day_ts = memory.get_state("day_started").ts

    memory.mark_activity()
    second_day_ts = memory.get_state("day_started").ts

    assert first_day_ts == second_day_ts  # not rewritten — still the same calendar day


def test_session_marker_unchanged_on_quick_successive_calls():
    """Two requests seconds apart must not each look like a new session."""
    memory = ShortTermMemory(user_id="testuser")
    memory.mark_activity()
    first_session_value = memory.get_state("session_started").value

    memory.mark_activity()
    second_session_value = memory.get_state("session_started").value

    assert first_session_value == second_session_value


def test_session_marker_refreshes_after_a_real_gap():
    """ts has 1-second resolution (_utc_ts()) — sleep past that boundary so a real
    refresh is distinguishable from the "called twice in the same instant" case
    test_session_marker_unchanged_on_quick_successive_calls covers."""

    memory = ShortTermMemory(user_id="testuser")
    memory.mark_activity()
    before = memory.get_state("session_started").ts

    memory._last_activity_ts = datetime.now(UTC) - timedelta(minutes=_SESSION_GAP_MINUTES + 5)
    _time.sleep(1.1)
    memory.mark_activity()
    after = memory.get_state("session_started").ts

    assert after != before


def test_activity_markers_survive_snapshot_roundtrip():
    """A restart must not make every first request afterward look like a new session —
    this is exactly the false positive that would happen without persisting
    _last_activity_ts (see P105 Phase log)."""
    memory = ShortTermMemory(user_id="testuser")
    memory.mark_activity()
    session_value = memory.get_state("session_started").value

    snapshot = memory.to_dict()
    restored = ShortTermMemory(user_id="testuser")
    restored.load_from_dict(snapshot)

    # Immediately calling mark_activity() again right after "restart" must NOT look
    # like a new session, since barely any time has passed since the real last activity.
    restored.mark_activity()
    assert restored.get_state("session_started").value == session_value
