"""N39 fix 1 — a receipt never says «Lagret» when the save failed (feedback_rid_and_receipts, KTSB-visjon thread 9).

Observed 2026-11-16 17:24 (rid-1794846135454): weights.db was read-only for the API, `weights.flush()` failed and kept the
rows queued, and the chat still got «⟦saved kind=camera_answer …⟧ Lagret: …». The rows were lost at the next stop.
Here the weights DB really is read-only (chmod on tmp_path) and every weights-backed receipt must say honestly that
nothing is saved yet — no `⟦saved⟧` marker, no «Lagret».
Never touches live state: everything points at tmp_path (fixture from test_p149_j2_questions). Placeholders only."""

import asyncio
import os
import stat

import pytest
from fastapi import HTTPException

from kaare_core import receipts, weights
from kaare_core.frigate_user import questions
from kaare_core.memory import chat_questions, theme_questions
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.tools import executor_camera
from kaare_core.tools.i18n import t
from kaare_core.weights import store as wstore
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    NOW,
    H,
    _ev,
    _sensors,
    _store,
    env,
)


@pytest.fixture
def readonly_weights(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    """The weights DB exists (one row saved), then the file and its folder become read-only — as on 16.11."""
    monkeypatch.setattr(wstore.WeightStore, "_schedule_retry", lambda self: None)  # no background thread in tests
    monkeypatch.setattr(receipts, "RECEIPTS_LOG", str(env / "receipts.log"))
    monkeypatch.setattr(wstore, "DB_PATH", env / "weights_only" / "weights.db")  # own folder: only weights goes read-only
    wstore.reset_for_tests()
    assert weights.record(("asking", "boss|gui", "boss"), source_class="user", source="test", context="setup")
    assert weights.flush() == 1
    db = wstore.DB_PATH
    side = [p for p in db.parent.iterdir() if p.name.startswith(db.name)]
    for p in side:
        os.chmod(p, stat.S_IRUSR | stat.S_IRGRP)
    os.chmod(db.parent, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
    yield env
    os.chmod(db.parent, stat.S_IRWXU | stat.S_IRWXG)
    for p in side:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)


def _not_saved(out: str, lang: str = "nb") -> None:
    assert receipts.parse(out) == []  # no ⟦saved⟧ marker
    assert "Lagret" not in out and "Saved" not in out
    assert t("rcpt_not_saved", lang) in out


def test_camera_answer_receipt_is_honest_when_flush_fails(readonly_weights):
    _store(_ev("e1", NOW - H))
    out = questions.verify("user_a", event_id="e1", episode_id=None, verdict="no", lang="nb")
    assert wstore.get_store().pending() > 0  # the rows really were not written
    _not_saved(out)


def test_unsure_and_queued_and_admin_receipts_are_honest(readonly_weights):
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 2 * H))
    res = questions.answer("user_a", "e1", "unsure", channel="chat")
    assert res["ok"] and res["saved"] is False
    _not_saved(questions.stored_text("user_a", "e1", "unsure", res, "nb"))
    res = questions.answer_about("boss", "user_a", "e2", channel="chat")
    assert res["ok"] and res["saved"] is False
    _not_saved(questions.stored_text("boss", "e2", "yes", res, "nb", about="user_a"))


def test_saved_answer_still_gets_its_receipt(env):  # noqa: F811
    _store(_ev("e1", NOW - H))
    res = questions.answer("user_a", "e1", "no", channel="chat")
    assert res["ok"] and res["saved"] is True
    out = questions.stored_text("user_a", "e1", "no", res, "nb")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "e1"}] and " Lagret: " in out


def test_theme_answer_reports_saved_flag(readonly_weights, monkeypatch):
    monkeypatch.setattr(theme_questions, "candidates", lambda user, base=None: [{"chain": "c1", "text": "x"}])
    res = theme_questions.answer("user_a", "c1", "yes", channel="chat")
    assert res == {"ok": True, "saved": False}


def test_executor_theme_and_ask_receipts_are_honest(readonly_weights, monkeypatch):
    monkeypatch.setattr(theme_questions, "candidates", lambda user, base=None: [{"chain": "c1", "text": "x"}])
    _store(_ev("f1", NOW - H, ref=None, kind="unknown", claim="none", sensors=_sensors(None, [("det_x", None)])))
    card = chat_questions.add_card("user_a", "theme", "c1", source="gui", text="x")  # N39 step a: by question card
    out = asyncio.run(executor_camera._kamera({"action": "answer_question", "question_id": card["qid"],
                                               "answer": "yes", "_user_id": "user_a"}, "nb"))
    _not_saved(out)
    out = asyncio.run(executor_camera._kamera({"action": "ask", "event_id": "f1", "_user_id": "user_a"}, "nb"))
    _not_saved(out)


def test_gui_endpoints_say_not_saved(readonly_weights):
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 2 * H))
    with pytest.raises(HTTPException) as e:
        rfu.api_answer_question(rfu.QuestionAnswer(event_id="e1", answer="no"), payload={"sub": "user_a"})
    assert e.value.status_code == 503 and e.value.detail == "not_saved"
    with pytest.raises(HTTPException) as e:
        rfu.api_admin_who_answer(rfu.WhoAnswer(event_id="e2", member="user_a"), u={"sub": "boss"})
    assert e.value.status_code == 503 and e.value.detail == "not_saved"
