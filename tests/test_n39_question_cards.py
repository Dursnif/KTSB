"""N39 step a — every question card has its own question ID; code binds the answer, the LLM never picks an event.

Observed 2026-11-16 (rid-1794846135454): the chat question lived only in RAM, kaare.service restarted 1½ min before the
answer, Kåre got no note, called verify(episode) and picked one of ~10 event_ids himself — the wrong one.
Now:
- a card is stored (survives a restart) with a qid; the note to Kåre lists qid + camera + time, never an event_id;
- answer_question(question_id) binds to the card's own event; unknown, foreign or event_id-as-qid → refused;
- verify on an episode — or on one event of an episode — with several open events gives cards, stores nothing;
- several answers in one turn = one call per card, each bound right;
- settle() drops only the cards that were in this turn's note; the file never grows past MAX_OPEN_CARDS per user;
- chat: a card reply carries only its own cards' pictures; voice (stt): no picture lines, the note says read camera +
  time aloud, and no raw qid reaches the spoken reply (scrub_for_voice); no chat offers on voice (D12).
Tmp files only (the J11 fixture). Placeholders only."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions as cq
from kaare_core.routers import router_generate as rg
from kaare_core.tools import executor_camera
from kaare_core.users import store as ustore
from tests import test_p149_j11_chat_questions as j11

NOW = j11.NOW
env = j11.env
JPEG = b"\xff\xd8fake\xff\xd9"


@pytest.fixture
def cards(env, monkeypatch):
    monkeypatch.setattr(cq, "CARDS_PATH", env / "question_cards.json")
    monkeypatch.setattr(cq.time, "time", lambda: NOW)
    cq.reset_for_tests()
    saved = []

    def save(data, user_id, folder, ext="png"):
        saved.append(user_id)
        return f"img{len(saved)}"

    monkeypatch.setattr(cq, "save_image", save)
    return env


def _pair():
    """Two open events of one episode (60 s apart) for user_a."""
    for i, eid in enumerate(("pa", "pb")):
        assert fstore.record_event(fstore.FrigateUserEvent(
            event_id=eid, camera="cam", label="person", start_ts=NOW - 600 + 60 * i, end_ts=NOW - 600 + 60 * i + 9,
            person_ref="user_a", person_kind="household", claim_status="unverified",
            check={"ir": False, "sensors": {"relation": "disagree", "frigate": "Person A", "frigate_face": "Person A",
                                            "detectors": []}}))
    ep = fstore.event_by_id("pa").episode_id
    assert ep is not None and fstore.event_by_id("pb").episode_id == ep
    return ep


def _tool(args, uid="user_a", source="gui"):
    return asyncio.run(executor_camera._kamera({**args, "_user_id": uid, "_source": source}, "nb"))


def _qids(user="user_a"):
    return [c["qid"] for c in cq.open_cards(user)]


def _human(eid):
    return (fstore.event_by_id(eid).check or {}).get("human")


def test_offer_makes_a_stored_card_that_survives_a_restart(cards):
    assert cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    [qid] = _qids()
    assert qid.startswith("q-")
    assert json.loads((cards / "question_cards.json").read_text())["user_a"][0]["qid"] == qid
    cq.reset_for_tests()  # a restart: RAM gone, the file stays
    note = cq.pending_note("user_a", "nb")
    assert qid in note and "question_id" in note
    assert "e0" not in cq.QID_RE.sub("", note)  # never an event_id for Kåre (qids are random hex — strip them first)


def test_answer_by_question_id_binds_to_the_cards_event(cards):
    cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    [qid] = _qids()
    target = cq.open_cards("user_a")[0]["target"]
    out = _tool({"action": "answer_question", "question_id": qid, "answer": "no"})
    assert f"ref={target}" in out and _human(target)["verdict"] == "no"
    assert _qids() == []  # the card is closed


def test_unknown_foreign_or_event_id_is_refused(cards):
    ustore.create_user(username="user_b", display_name="Beta", role="adult", pin="482915")
    cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    [qid] = _qids()
    for args, uid in (({"question_id": "q-000000"}, "user_a"), ({"question_id": qid}, "user_b"),
                      ({"event_id": "e0"}, "user_a"), ({"question_id": "e0"}, "user_a")):
        out = _tool({"action": "answer_question", "answer": "yes", **args}, uid=uid)
        assert "⟦saved" not in out
    assert all(_human(e) is None for e in ("e0", "e1", "e2")) and _qids() == [qid]


def test_verify_on_an_episode_gives_cards_not_event_ids(cards):
    ep = _pair()
    out = _tool({"action": "verify", "episode_id": ep, "answer": "no"})
    assert "⟦saved" not in out and "event_id" not in out and not re.search(r"\bp[ab]\b", out)
    qids = _qids()
    assert len(qids) == 2 and all(q in out for q in qids)
    assert _human("pa") is None and _human("pb") is None


def test_verify_on_one_event_of_a_busy_episode_also_gives_cards(cards):
    _pair()
    out = _tool({"action": "verify", "event_id": "pa", "answer": "no"})
    assert "⟦saved" not in out and _human("pa") is None and len(_qids()) == 2


def test_two_answers_in_one_turn_bind_each_to_its_own_card(cards):
    ep = _pair()
    _tool({"action": "verify", "episode_id": ep, "answer": "no"})
    by_target = {c["target"]: c["qid"] for c in cq.open_cards("user_a")}
    _tool({"action": "answer_question", "question_id": by_target["pa"], "answer": "no"})
    _tool({"action": "answer_question", "question_id": by_target["pb"], "answer": "yes"})
    assert _human("pa")["verdict"] == "no" and _human("pb")["verdict"] == "yes"


def test_settle_drops_only_the_cards_in_this_turns_note(cards):
    cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    [old] = _qids()
    cq.pending_note("user_a", "nb")  # turn start: Kåre sees `old`
    ep = _pair()
    _tool({"action": "verify", "episode_id": ep, "answer": "no"})  # this turn makes two new cards
    cq.settle("user_a", [("camera", "verify")])
    left = _qids()
    assert old not in left and len(left) == 2


def test_card_file_is_capped(cards):
    for i in range(cq.MAX_OPEN_CARDS + 3):
        cq.add_card("user_a", "camera", f"x{i}", source="gui")
    assert len(_qids()) == cq.MAX_OPEN_CARDS


def test_chat_cards_carry_only_their_own_pictures(cards):
    ep = _pair()
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for e in ("pa", "pb", "e0"):
        (domain_store.SNAPSHOT_DIR / f"{e}.jpg").write_bytes(JPEG)
    out = _tool({"action": "verify", "episode_id": ep, "answer": "no"})
    assert out.count("/api/image/") == 2


def test_voice_gets_no_pictures_and_no_raw_question_id(cards):
    ep = _pair()
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (domain_store.SNAPSHOT_DIR / "pa.jpg").write_bytes(JPEG)
    out = _tool({"action": "verify", "episode_id": ep, "answer": "no"}, source="stt")
    assert "/api/image/" not in out
    qids = _qids()
    note = cq.pending_note("user_a", "nb", source="stt")
    assert all(q in note for q in qids)  # Kåre needs them to bind the spoken answer
    spoken = f"Var det deg ved kamera kl. 10:50 ({qids[0]})? Eller question_id {qids[1]}?"
    clean = cq.scrub_for_voice(spoken)
    assert not any(q in clean for q in qids) and "question_id" not in clean
    assert cq.offer("user_a", "stt", "hei", [], "nb", now=NOW) == ""  # D12: never a chat offer on voice
    by_target = {c["target"]: c["qid"] for c in cq.open_cards("user_a")}
    _tool({"action": "answer_question", "question_id": by_target["pb"], "answer": "yes"}, source="stt")
    assert _human("pb")["verdict"] == "yes"


def test_chat_loop_notes_cards_on_voice_and_scrubs_the_spoken_reply():
    src = Path(rg.__file__).read_text()
    assert 'chat_questions.pending_note(user_id, lang, source=source) if source in ("gui", "stt")' in src
    assert "chat_questions.scrub_for_voice(text_out)" in src
    assert cq.scrub_for_voice("Hei, alt vel?") == "Hei, alt vel?"  # nothing to strip → unchanged


def test_the_note_says_the_cards_own_picture_is_shown_only_when_one_is(cards):
    line = cq.t("cq_card_picture_note", "nb")
    cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)  # a camera card, but no picture stored for it
    assert line not in cq.pending_note("user_a", "nb")
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (domain_store.SNAPSHOT_DIR / f"{cq.open_cards('user_a')[0]['target']}.jpg").write_bytes(JPEG)
    assert line in cq.pending_note("user_a", "nb")  # now the card shows a picture under the question
    assert line not in cq.pending_note("user_a", "nb", source="stt")  # voice shows no picture
