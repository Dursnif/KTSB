"""Praise/complaint marking on the previous answer must not fire on answers or on word fragments (rid-1794859092971).

A user answered «nei, det var en annen» on a camera card → «[LTM] Feedback: negative på 3311». The word list matched
substrings («bra» in «brannalarmen», «nei» in «neida») and could not tell an answer to a question from a complaint.
November fix only: whole words, and no marking while the user is answering something. The list itself is to be replaced
in December (Kåre with full context decides praise/criticism — BUG-log 2026-11-16).
"""

import pytest

from kaare_core.memory import long_term as lt
from kaare_core.routers import router_generate as rg


@pytest.fixture
def ltm():
    obj = lt.LongTermMemory.__new__(lt.LongTermMemory)
    obj._last_id = {"user_a": 3311}
    obj._last_prompt = {"user_a": "vis kameraet"}
    return obj


@pytest.mark.parametrize("text", ["brannalarmen gikk i natt", "neida, alt i orden", "Bramming er en by"])
def test_word_fragments_do_not_count(ltm, text):
    assert ltm.detect_feedback_signal(text, "user_a") is None


def test_whole_words_still_count(ltm):
    assert ltm.detect_feedback_signal("det var feil", "user_a") == "negative"
    assert ltm.detect_feedback_signal("takk, perfekt", "user_a") == "positive"


def test_no_marking_while_answering(ltm):
    assert ltm.detect_feedback_signal("nei, det var en annen", "user_a", answering=True) is None


class _STM:
    def __init__(self, last_reply):
        self._r = last_reply

    def get_dialog_pairs(self, user_id="global", n=4, with_ts=False):
        return [("hei", self._r)] if self._r is not None else []


def test_open_camera_card_means_answering(monkeypatch):
    monkeypatch.setattr(rg.chat_questions, "open_cards", lambda user: [{"kind": "camera", "target": "e1"}])
    assert rg._is_answering(user_id="user_a", memory=_STM("Her er bildet."), pending_verification=None) is True


def test_open_verification_means_answering(monkeypatch):
    monkeypatch.setattr(rg.chat_questions, "open_cards", lambda user: [])
    assert rg._is_answering(user_id="user_a", memory=_STM("OK."), pending_verification={"id": 7}) is True


def test_question_in_last_reply_means_answering(monkeypatch):
    monkeypatch.setattr(rg.chat_questions, "open_cards", lambda user: [])
    assert rg._is_answering(user_id="user_a", memory=_STM("Var det deg?"), pending_verification=None) is True


def test_plain_statement_is_not_answering(monkeypatch):
    monkeypatch.setattr(rg.chat_questions, "open_cards", lambda user: [])
    assert rg._is_answering(user_id="user_a", memory=_STM("Lyset er på."), pending_verification=None) is False


def test_the_reported_case_gives_no_feedback(ltm, monkeypatch):
    monkeypatch.setattr(rg.chat_questions, "open_cards", lambda user: [{"kind": "camera", "target": "e1"}])
    answering = rg._is_answering(user_id="user_a", memory=_STM("Var det deg?"), pending_verification=None)
    assert ltm.detect_feedback_signal("nei, det var en annen", "user_a", answering=answering) is None
