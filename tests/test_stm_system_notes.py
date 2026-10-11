"""
Tests for P105 Phase 1 — orphaned STM system-notices (fallback markers, timer-fired,
tool summary) never reaching the model.

Root cause: get_dialog_pairs() strictly filters role in ("user", "assistant") — any
add_dialog(role="system", ...) write has been silently invisible to the LLM since the
2026-05-16 multi-turn refactor (see p105-context-provenance-and-state-retention.md).

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_stm_system_notes.py -v
"""

import sys

sys.path.insert(0, "/kaare")

from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.routers.router_generate import _assemble_messages


def _messages_text(messages: list) -> str:
    """Flatten every message's content into one string for substring assertions."""
    return "\n".join(m.get("content", "") for m in messages)


def test_old_mechanism_does_not_reach_the_model():
    """Proves the diagnosis: a plain add_dialog(role="system", ...) write, replayed
    only through get_dialog_pairs(), never appears in the assembled messages — this is
    the bug, kept as a permanent regression test so nobody "fixes" get_dialog_pairs()
    into silently picking up system turns again (that would break its pairing logic)."""
    memory = ShortTermMemory(user_id="testuser")
    memory.add_dialog(role="user", text="skru på lyset", user_id="testuser")
    memory.add_dialog(role="assistant", text="Gjort!", user_id="testuser")
    memory.add_dialog(role="system", text="[RESERVEMODUS aktivert 12:00]", user_id="testuser")
    memory.add_dialog(role="user", text="takk", user_id="testuser")
    memory.add_dialog(role="assistant", text="Bare hyggelig", user_id="testuser")

    recent_pairs = memory.get_dialog_pairs(user_id="testuser", n=4)
    messages = _assemble_messages(
        context_block="",
        ltm_block="",
        recent_pairs=recent_pairs,
        current_content="ny melding",
        images=None,
        user_id="testuser",
    )

    assert "RESERVEMODUS" not in _messages_text(messages)


def test_pending_system_note_reaches_the_model():
    """The fix: a note added via add_pending_system_note() must actually appear in the
    assembled messages when folded into current_content, exactly like pending_note/
    mechanic_note/login_reminder_note already do."""
    memory = ShortTermMemory(user_id="testuser")
    memory.add_pending_system_note("[RESERVEMODUS aktivert 12:00]", user_id="testuser")

    pending = memory.get_pending_system_notes("testuser")
    assert pending == ["[RESERVEMODUS aktivert 12:00]"]

    current_content = "\n\n".join(p for p in [*pending, "ny melding"] if p)
    recent_pairs = memory.get_dialog_pairs(user_id="testuser", n=4)
    messages = _assemble_messages(
        context_block="",
        ltm_block="",
        recent_pairs=recent_pairs,
        current_content=current_content,
        images=None,
        user_id="testuser",
    )

    assert "RESERVEMODUS" in _messages_text(messages)


def test_pending_system_note_reaches_the_model_with_no_dialog_history_yet():
    """Same as above, but with an empty conversation (recent_pairs=[]) — the very first
    message ever, or right after an STM restart. Regression test for a second bug found
    while writing this test (2026-09-06): the no-history branch used to rebuild its own
    smaller subset of notes by hand instead of reusing current_content, silently dropping
    pending notices (among others) whenever there was no prior dialog yet."""
    memory = ShortTermMemory(user_id="testuser")
    memory.add_pending_system_note("[RESERVEMODUS aktivert 12:00]", user_id="testuser")

    pending = memory.get_pending_system_notes("testuser")
    current_content = "\n\n".join(p for p in [*pending, "ny melding"] if p)

    messages = _assemble_messages(
        context_block="",
        ltm_block="",
        recent_pairs=[],
        current_content=current_content,
        images=None,
        user_id="testuser",
    )

    assert len(messages) == 1
    assert "RESERVEMODUS" in _messages_text(messages)


def test_ack_clears_pending_notes_so_they_are_delivered_exactly_once():
    memory = ShortTermMemory(user_id="testuser")
    memory.add_pending_system_note("note A", user_id="testuser")
    memory.add_pending_system_note("note B", user_id="testuser")

    first_read = memory.get_pending_system_notes("testuser")
    assert first_read == ["note A", "note B"]

    memory.ack_pending_system_notes("testuser")

    assert memory.get_pending_system_notes("testuser") == []


def test_pending_notes_are_scoped_per_user():
    memory = ShortTermMemory(user_id="global")
    memory.add_pending_system_note("for alice", user_id="alice")
    memory.add_pending_system_note("for bob", user_id="bob")

    assert memory.get_pending_system_notes("alice") == ["for alice"]
    assert memory.get_pending_system_notes("bob") == ["for bob"]

    memory.ack_pending_system_notes("alice")
    assert memory.get_pending_system_notes("alice") == []
    assert memory.get_pending_system_notes("bob") == ["for bob"]


def test_pending_system_notes_survive_snapshot_roundtrip():
    """Snapshot/restore (service restart) must not silently drop an undelivered note."""
    memory = ShortTermMemory(user_id="testuser")
    memory.add_pending_system_note("[GJENOPPRETTET 12:05]", user_id="testuser")

    snapshot = memory.to_dict()

    restored = ShortTermMemory(user_id="testuser")
    restored.load_from_dict(snapshot)

    assert restored.get_pending_system_notes("testuser") == ["[GJENOPPRETTET 12:05]"]
