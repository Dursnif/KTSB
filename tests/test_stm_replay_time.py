"""
Tests for P105 Phase 6 — replayed STM turns carry their time, and a long gap since the
user's previous message is stated explicitly.

Real bug (BUG-log 2026-09-28): Stian's first message of the day got "the weather is still
overcast, nothing new since earlier today" with no tool call. The replayed turns were from
5 days earlier, but get_dialog_pairs() handed the model bare text with no date, so they
read as the ongoing conversation.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_stm_replay_time.py -v
"""

import sys

sys.path.insert(0, "/kaare")

from datetime import UTC, datetime, timedelta

from adapters.llm_adapter import _build_system, _build_system_fallback, time_block
from kaare_core.memory import knowledge_reflection
from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.routers import router_generate
from kaare_core.routers.router_generate import _assemble_messages, _gap_note, _stamp_pairs


def _memory_with_one_pair(user_ts: str) -> ShortTermMemory:
    memory = ShortTermMemory(user_id="testuser")
    memory.add_dialog(role="user", text="kan du sjekke været for meg", user_id="testuser")
    memory.add_dialog(role="assistant", text="Overskyet, regn på vei.", user_id="testuser")
    memory._dialog[-2].ts = user_ts  # pin the user turn's time
    return memory


def test_default_pairs_unchanged_two_tuples():
    """Existing callers (and test_stm_system_notes.py) keep getting 2-tuples."""
    memory = _memory_with_one_pair("2026-09-23T13:55:52+00:00")
    pairs = memory.get_dialog_pairs(user_id="testuser", n=4)
    assert pairs == [("kan du sjekke været for meg", "Overskyet, regn på vei.")]


def test_with_ts_returns_user_turn_time():
    memory = _memory_with_one_pair("2026-09-23T13:55:52+00:00")
    pairs = memory.get_dialog_pairs(user_id="testuser", n=4, with_ts=True)
    assert pairs == [
        ("kan du sjekke været for meg", "Overskyet, regn på vei.", "2026-09-23T13:55:52+00:00")
    ]


def test_only_user_turns_are_stamped():
    stamped = _stamp_pairs([("hei", "Hei!", "2026-09-23T13:55:52+00:00")], "nb")
    user_text, assistant_text = stamped[0]
    assert user_text.startswith("[23.09 kl. ") and user_text.endswith("] hei")
    assert assistant_text == "Hei!"  # never stamped — the model would imitate it


def test_assembled_messages_carry_the_stamp():
    messages = _assemble_messages(
        context_block="CTX",
        ltm_block="",
        recent_pairs=[
            ("første", "svar 1", "2026-09-21T18:18:09+00:00"),
            ("andre", "svar 2", "2026-09-23T13:55:52+00:00"),
        ],
        current_content="hei.. hvordan står det til?",
        images=None,
        user_id="testuser",
    )
    assert "[21.09 kl. " in messages[0]["content"] and "første" in messages[0]["content"]
    assert messages[2]["content"].startswith("[23.09 kl. ")
    assert messages[1]["content"] == "svar 1" and messages[3]["content"] == "svar 2"
    assert messages[-1]["content"] == "hei.. hvordan står det til?"


def test_unparseable_ts_leaves_text_untouched():
    assert _stamp_pairs([("hei", "Hei!", "not-a-date")], "nb") == [("hei", "Hei!")]


def test_gap_note_after_five_days():
    now = datetime(2026, 9, 28, 9, 32, tzinfo=UTC)
    previous = now - timedelta(days=5)
    note = _gap_note(previous, "nb", now=now)
    assert "for 5 dager siden" in note
    assert "sjekk ferske data først" in note
    assert (
        "vær" not in note and "systemstatus" not in note
    )  # no example list priming status reports


def test_gap_note_in_hours_below_two_days():
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    note = _gap_note(now - timedelta(hours=10), "en", now=now)
    assert "10 hours ago" in note


def test_no_gap_note_within_threshold():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert _gap_note(now - timedelta(hours=1), "nb", now=now) == ""


def test_no_gap_note_on_first_ever_activity():
    assert _gap_note(None, "nb") == ""


def test_threshold_is_read_from_settings(monkeypatch):
    monkeypatch.setattr(
        router_generate, "get_settings", lambda: {"stm": {"replay_gap_note_hours": 0.5}}
    )
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert _gap_note(now - timedelta(hours=1), "nb", now=now) != ""


def test_mark_activity_returns_previous_time():
    memory = ShortTermMemory(user_id="testuser")
    assert memory.mark_activity() is None  # first ever activity
    earlier = datetime.now(UTC) - timedelta(days=5)
    memory._last_activity_ts = earlier
    assert memory.mark_activity() == earlier


# ── P105 Phase 6b: time sits right before the new message in the chat path ──────────────


def test_system_prompt_keeps_time_by_default():
    """Every non-chat caller (ask_llm, cloud brief, knowledge_reflection, Jang) is unchanged."""
    header = time_block("nb").splitlines()[0]
    assert header in _build_system("", user_id="")
    assert header in _build_system_fallback(user_id="")


def test_chat_path_system_prompt_without_time():
    header = time_block("nb").splitlines()[0]
    assert header not in _build_system("", user_id="", include_time=False)
    assert header not in _build_system_fallback(user_id="", include_time=False)


def test_time_block_is_current_local_time():
    assert "Nå er det" in time_block("nb")
    assert "It is" in time_block("en")


# ── Name bug: knowledge_reflection acting user from config, not hardcoded ───────────────


def test_acting_user_read_from_settings(monkeypatch):
    monkeypatch.setattr(
        knowledge_reflection,
        "get_settings",
        lambda: {"knowledge_reflection": {"acting_user": "someone"}},
    )
    assert knowledge_reflection._acting_user() == "someone"


def test_acting_user_blank_means_household_context(monkeypatch):
    monkeypatch.setattr(knowledge_reflection, "get_settings", dict)
    assert knowledge_reflection._acting_user() == ""
