"""N39 step c — «vet ikke» (unsure) is its own answer, separate from «hopp over» (skip).

- «vet ikke» closes the card for that person (not asked again), moves no Validated anywhere (P149 D1: a human who is
  unsure stays unknown), teaches no sensor and writes nothing on the event in frigate.db;
- unlike skip it counts as engagement: the asking willingness goes up, not down;
- it is reachable from the GUI endpoint, the chat tool (answer_question / verify) and the tool schema, and the receipt
  says «vet ikke»; the person's own history shows it.
Never touches live state: everything points at tmp_path (fixture from test_p149_j2_questions). Placeholders only."""

from kaare_core import receipts, weights
from kaare_core.frigate_user import questions
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.tools import definitions
from kaare_core.tools.i18n import t
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    NOW,
    H,
    _ev,
    _ids,
    _learned,
    _store,
    env,
)


def _asking(user, channel="gui"):
    return weights.get(("asking", f"{user}|{channel}", user))


def test_unsure_closes_the_card_and_moves_nothing(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    monkeypatch.setattr(receipts, "RECEIPTS_LOG", str(env / "receipts.log"))
    _store(_ev("e1", NOW - H))
    res = questions.answer("user_a", "e1", "unsure")
    assert res == {"ok": True, "sensors": 0, "saved": True}
    assert "e1" not in _ids("user_a")  # closed: not asked again
    w = weights.get(("event", "e1", "user_a"))
    assert w.validated is None and w.learned_n == 0  # D1: unsure stays unknown
    assert _learned("det_x").learned_n == 0 and _learned("det_y").learned_n == 0  # no sensor learns
    assert not (fstore.event_by_id("e1").check or {}).get("human")  # nothing on the event
    assert questions.answer("user_a", "e1", "yes")["ok"] is False  # answered once (correction is step b)


def test_unsure_raises_willingness_skip_lowers_it(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 3 * H))
    assert questions.answer("user_a", "e1", "unsure")["ok"]
    assert _asking("user_a").yes == 1 and _asking("user_a").no == 0
    assert questions.answer("user_a", "e2", "skip")["ok"]
    assert _asking("user_a").no == 1


def test_unsure_in_history_and_receipt(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H))
    out = questions.verify("user_a", event_id="e1", episode_id=None, verdict="unsure", lang="nb")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "e1"}]
    assert "vet ikke" in out
    assert [h["answer"] for h in questions.history("user_a")] == ["unsure"]


def test_unsure_from_chat_card_and_gui(env):  # noqa: F811  (env: the shared fixture imported above)
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 3 * H))
    card = chat_questions.add_card("user_a", "camera", "e1", source="gui")  # N39 step a: by question card
    assert chat_questions.answer("user_a", card["qid"], "unsure")["ok"]
    assert rfu.api_answer_question(rfu.QuestionAnswer(event_id="e2", answer="unsure"), payload={"sub": "user_a"})["ok"]
    assert _ids("user_a") == []


def test_tool_schema_offers_unsure():
    for lang in ("nb", "en", "de"):
        props = definitions._kamera_tool(lang)["function"]["parameters"]["properties"]
        assert "unsure" in props["answer"]["enum"]


def test_unsure_receipt_never_says_it_was_not_the_user(env):  # noqa: F811  (env: the shared fixture imported above)
    """DT04-S05k: the «vet ikke» receipt said «ingen navn er lagret, bare at det ikke var brukeren» — the «no» text. The
    stored answer was right (unsure); the receipt claimed something nobody said. Every language."""
    _store(_ev("e1", NOW - H), _ev("e2", NOW - 2 * H), _ev("e3", NOW - 3 * H))
    for ev_id, lang in (("e1", "nb"), ("e2", "en"), ("e3", "de")):
        res = questions.answer("user_a", ev_id, "unsure")
        out = questions.stored_text("user_a", ev_id, "unsure", res, lang)
        assert t("fv_stored_no_name", lang) not in out, (lang, out)
        unsure = t("fv_stored_unsure", lang)
        assert unsure != "fv_stored_unsure" and unsure in out, (lang, out)
