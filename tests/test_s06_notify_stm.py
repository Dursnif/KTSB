"""A notification that goes to the chat reaches Kåre's STM (BUG-log 2026-10-08; 1940× «STM inject failed» in the log).

`notify._inject_stm` imported `get_stm` from the wrong module and called a method STM does not have, so every chat
notification was lost — and with no sound node configured that was the only way out. The failure must also never be
silent again.
"""

import logging

import pytest

import kaare_core.app_state as app_state
from kaare_core import notify


class _FakeSTM:
    def __init__(self):
        self.dialog = []
        self.notes = []

    def add_dialog(self, role, text, user_id="global"):
        self.dialog.append((role, text, user_id))

    def add_pending_system_note(self, text, user_id="global"):
        self.notes.append((text, user_id))


class _FakeRegistry:
    def __init__(self):
        self.stm = _FakeSTM()

    def get(self, user_id):
        return self.stm


@pytest.fixture
def registry(monkeypatch):
    reg = _FakeRegistry()
    reg.queued = []
    monkeypatch.setattr(app_state, "STM_REGISTRY", reg)
    monkeypatch.setattr(notify, "_get_user_lang", lambda uid: "en")
    monkeypatch.setattr(notify, "queue_chat_notification",
                        lambda uid, msg, **kw: reg.queued.append((uid, msg)))
    return reg


def test_chat_notification_lands_in_stm(registry):
    notify._inject_stm("userx", "Car in the driveway")
    assert registry.queued == [("userx", "Car in the driveway")]  # shown in the user's chat (GUI queue)
    stm = registry.stm
    assert stm.notes and stm.notes[0][1] == "userx"
    assert "Car in the driveway" in stm.notes[0][0]
    assert stm.dialog and stm.dialog[0][0] == "system" and "Car in the driveway" in stm.dialog[0][1]
    assert "Varsling" not in stm.notes[0][0]  # the user's language, not hard-coded Norwegian (rule 8)


def test_failure_is_logged_not_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(app_state, "STM_REGISTRY", None)  # e.g. a process without STM
    monkeypatch.setattr(notify, "_get_user_lang", lambda uid: "en")
    with caplog.at_level(logging.WARNING, logger=notify.logger.name):
        notify._inject_stm("userx", "hello")
    assert any("STM" in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)
