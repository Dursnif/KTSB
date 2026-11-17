"""N39 step b — a person can correct their own earlier camera answer; nothing is deleted, the weights follow.

- fold(): an answer row is skipped when the same subject holds a *user* evidence row «supersede:<by_user>:<pointer>»
  (P149 D1: only the human who answered can withdraw it — a machine/self marker or another user's marker moves nothing);
  rows without a marker fold exactly as before (also checked on a copy of the real weights.db, scratchpad script);
- questions.correct(): the old rows stay (append-only), new rows get their own pointer, sensors are counted again;
  «vet ikke»/«privat» take the human answer off the event (kept in check.human_history), «ja»/«nei» replace it;
- in chat it goes through cards (action='correct' → one card per own recent answer; answer_question(question_id) →
  correct) with a receipt `camera_answer_corrected` — the LLM never picks the event.
Tmp files only. Placeholders only."""

import asyncio
import re

from kaare_core import receipts, weights
from kaare_core.frigate_user import questions
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions as cq
from kaare_core.tools import executor_camera
from kaare_core.weights import model as wmodel
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    NOW,
    H,
    _ev,
    _learned,
    _store,
    env,
)

TS = "2026-11-16T16:00:00+00:00"


def _a(verdict, by="user_a", ptr="frigate:e1", ctx="frigate:e1", ts=TS):
    return {"ts": ts, "by_user": by, "verdict": verdict, "verbatim": 1, "context": ctx, "pointer": ptr}


def _sup(by, ptr, cls="user"):
    return {"ts": TS, "source_class": cls, "source": "chat", "context": "correct", "polarity": 0,
            "pointer": f"supersede:{by}:{ptr}"}


# ── fold ──

def test_fold_skips_a_superseded_answer_and_counts_the_new_one():
    w = wmodel.fold([_sup("user_a", "frigate:e1")],
                    [_a("no"), _a("yes", ptr="frigate:e1#r1", ts="2026-11-16T17:00:00+00:00")], kind="event")
    assert w.yes == 1 and w.no == 0 and w.learned_n == 1


def test_fold_ignores_a_machine_marker_and_another_users_marker():
    base = wmodel.fold([], [_a("no")], kind="event")
    for marker in (_sup("user_a", "frigate:e1", cls="self"), _sup("user_a", "frigate:e1", cls="world"),
                   _sup("user_b", "frigate:e1")):
        w = wmodel.fold([marker], [_a("no")], kind="event")
        assert (w.yes, w.no, w.learned_n, w.validated) == (base.yes, base.no, base.learned_n, base.validated)


def test_fold_without_markers_is_unchanged():
    answers = [_a("yes"), _a("no", by="user_b", ctx="frigate:e2", ptr="frigate:e2")]
    ev = [{"ts": TS, "source_class": "world", "source": "x", "context": "d1", "polarity": 1, "pointer": "p"}]
    w = wmodel.fold(ev, answers, kind="sensor")
    assert (w.yes, w.no, w.learned_n, w.angles) == (1, 1, 2, 1)


# ── questions.correct ──

def test_correct_no_to_unsure_withdraws_the_answer_and_the_sensor_steps(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H))
    assert questions.answer("user_a", "e1", "no")["ok"]
    assert _learned("det_x").no == 1
    res = questions.correct("user_a", "e1", "unsure")
    assert res["ok"] and res["before"] == "no" and res["saved"] is True
    assert weights.get(("event", "e1", "user_a")).learned_n == 0
    assert _learned("det_x").learned_n == 0  # the sensor's «wrong» step is gone again
    rows = weights.get_store().rows(("event", "e1", "user_a"))
    assert any(r["verdict"] == "no" for r in rows["answers"])  # nothing deleted
    ev = fstore.event_by_id("e1")
    assert not (ev.check or {}).get("human") and ev.check["human_history"][-1]["verdict"] == "no"
    assert "e1" not in [q["event_id"] for q in questions.candidates("user_a", now=NOW)]  # still closed (unsure)


def test_correct_no_to_yes_flips_the_sensors_and_names_the_event(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H))
    questions.answer("user_a", "e1", "no")
    res = questions.correct("user_a", "e1", "yes")
    assert res["ok"] and res["sensors"] == 3  # frigate's own face guess + det_x + det_y
    assert _learned("det_x").yes == 1 and _learned("det_x").no == 0  # named me: now right
    assert _learned("det_y").no == 1  # named someone else: wrong
    ev = fstore.event_by_id("e1")
    assert ev.check["human"]["verdict"] == "yes" and ev.person_ref == "user_a"
    assert ev.check["human_history"][-1]["verdict"] == "no"


def test_correct_twice_keeps_only_the_newest(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H))
    questions.answer("user_a", "e1", "yes")
    questions.correct("user_a", "e1", "no")
    questions.correct("user_a", "e1", "yes")
    assert _learned("det_x").yes == 1 and _learned("det_x").no == 0 and _learned("det_x").learned_n == 1
    assert weights.get(("event", "e1", "user_a")).learned_n == 1


def test_correct_unsure_to_no_and_refusals(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 2 * H))
    questions.answer("user_a", "e1", "unsure")
    assert questions.correct("user_a", "e1", "no")["ok"]
    assert _learned("det_x").no == 1
    assert questions.correct("user_a", "e2", "yes") == {"ok": False, "error": "not_answered"}  # nothing to correct
    assert questions.correct("user_b", "e1", "yes")["ok"] is False  # not their answer
    assert questions.correct("user_a", "e1", "skip")["ok"] is False  # a correction is an answer


# ── chat: through cards ──

def _tool(args, uid="user_a"):
    return asyncio.run(executor_camera._kamera({**args, "_user_id": uid, "_source": "gui"}, "nb"))


def test_chat_correction_goes_through_a_card_with_a_receipt(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    monkeypatch.setattr(cq, "CARDS_PATH", env / "cards.json")
    cq.reset_for_tests()
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 3 * H))
    questions.answer("user_a", "e1", "no")
    questions.answer("user_a", "e2", "yes")
    out = _tool({"action": "correct"})
    text = "\n".join(ln for ln in out.splitlines() if not ln.startswith("/api/image/"))  # image ids are random hex
    assert not re.search(r"\be[12]\b", text) and "⟦saved" not in out
    cards = cq.open_cards("user_a")
    assert {c["target"] for c in cards} == {"e1", "e2"} and all(c["kind"] == "correct" for c in cards)
    qid = next(c["qid"] for c in cards if c["target"] == "e1")
    out = _tool({"action": "answer_question", "question_id": qid, "answer": "unsure"})
    assert receipts.parse(out) == [{"kind": "camera_answer_corrected", "ref": "e1"}]
    assert "nei" in out and "vet ikke" in out
    assert not (fstore.event_by_id("e1").check or {}).get("human")
    assert fstore.event_by_id("e2").check["human"]["verdict"] == "yes"  # the other card untouched


def test_a_withdrawn_answer_does_not_come_back_when_the_pipe_rewrites_the_event(env):  # noqa: F811
    _store(_ev("e1", NOW - H))
    questions.answer("user_a", "e1", "yes")
    questions.correct("user_a", "e1", "unsure")
    _store(_ev("e1", NOW - H))  # the camera pipe writes the event again (its own fields, no human answer)
    ev = fstore.event_by_id("e1")
    assert not (ev.check or {}).get("human") and ev.check["human_history"][-1]["verdict"] == "yes"
