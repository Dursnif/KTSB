"""P149 D12 / run-queue J11 — one question from the user's list in chat, now and then.

Only in chat (never voice), only after a light turn (no tool, short text), one open at a time, a gap between, a daily
number from the chat willingness (ignored or skipped → fewer, down to none), switch in the GUI (`questions.chat`).
The answer goes through the camera tool's `answer_question` into the same stores as the GUI. Tmp files only."""

import asyncio

import pytest

from kaare_core import weights
from kaare_core.frigate_user import faces, questions
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions as cq
from kaare_core.memory import repeat_stop as rs
from kaare_core.tools import definitions, executor_camera
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore

NOW = 1_800_000_000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    monkeypatch.setattr(rs, "REFLECTIONS_BASE", tmp_path / "refl")
    monkeypatch.setattr(cq.query, "_camera_cards", dict)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(questions.time, "time", lambda: NOW)
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text("faces:\n  Person A: {class: household}\n")
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    cq.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="user_a", display_name="Alfa", role="adult", pin="482915")
    ustore.update_user("user_a", frigate_faces=["Person A"])
    frigate_consent.invalidate()
    for i in range(3):
        assert fstore.record_event(fstore.FrigateUserEvent(
            event_id=f"e{i}", camera="cam", label="person", start_ts=NOW - 3600 * (i + 1), end_ts=NOW - 3600 * (i + 1) + 9,
            person_ref="user_a", person_kind="household", claim_status="unverified",
            check={"ir": False, "sensors": {"relation": "disagree", "frigate": "Person A", "frigate_face": "Person A",
                                            "detectors": []}}))
    yield tmp_path
    weights.flush()
    cq.reset_for_tests()
    wstore.reset_for_tests()
    fstore.reset_for_tests()
    frigate_consent.invalidate()


def test_light_chat_turn_gets_one_question(env):
    line = cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    assert line and "var det deg" in line
    assert cq.offer("user_a", "gui", "hei", [], "nb", now=NOW + 7200) == ""  # one open at a time


def test_never_voice_never_after_a_command_never_long_text(env):
    assert cq.offer("user_a", "stt", "hei", [], "nb", now=NOW) == ""
    assert cq.offer("user_a", "gui", "slå på lyset", ["ha_control"], "nb", now=NOW) == ""
    assert cq.offer("user_a", "gui", "x" * 500, [], "nb", now=NOW) == ""


def test_switch_off(env):
    (env / "frigate_user.yaml").write_text("enabled: true\nquestions: {chat: false}\n")
    assert cq.offer("user_a", "gui", "hei", [], "nb", now=NOW) == ""


def test_gap_and_daily_number(env):
    assert cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    cq.settle("user_a", [])  # ignored
    assert cq.offer("user_a", "gui", "hei", [], "nb", now=NOW + 60) == ""  # gap
    # one ignore lowers willingness: the daily number is reached
    assert cq.offer("user_a", "gui", "hei", [], "nb", now=NOW + 2 * 3600) == ""


def test_ignoring_again_and_again_stops_asking(env):
    for d in range(6):
        cq.offer("user_a", "gui", "hei", [], "nb", now=NOW + d * 86400)
        cq.pending_note("user_a", "nb")  # N39: the next turn sees the card …
        cq.settle("user_a", [])  # … and ignores it
    assert cq.per_day("user_a") == 0


def test_answer_question_stores_through_the_same_path(env):
    cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    note = cq.pending_note("user_a", "nb")
    [card] = cq.open_cards("user_a")  # N39: the note names the card's question_id, never the event_id
    assert "answer_question" in note and card["qid"] in note and card["target"] == "e0"
    assert "e0" not in cq.QID_RE.sub("", note)  # qids are random hex — strip them before looking for the event id
    assert cq.answer("user_a", card["qid"], "yes")["ok"]
    cq.settle("user_a", [("camera", "answer_question")])
    assert fstore.event_by_id("e0").check["human"]["channel"] == "chat"
    assert weights.get(("asking", "user_a|chat", "user_a")).yes == 1


def test_setting_validates(env):
    fsettings.update({"questions": {"chat": False}})
    assert fsettings.questions_chat() is False
    with pytest.raises(ValueError):
        fsettings.update({"questions": {"chat": "yes"}})


# ── wired ──

def test_camera_tool_answer_question(env):
    tool = definitions._kamera_tool("nb")["function"]["parameters"]["properties"]
    assert "answer_question" in tool["action"]["enum"] and tool["kind"]["enum"] == ["camera", "theme"]
    card = cq.add_card("user_a", "camera", "e1", source="gui")  # N39: answer_question binds by question card
    out = asyncio.run(executor_camera._kamera({"action": "answer_question", "question_id": card["qid"],
                                               "answer": "no", "_user_id": "user_a"}, "nb"))
    assert out and fstore.event_by_id("e1").check["human"]["verdict"] == "no"


def test_chat_loop_offers_and_settles():
    # PLC0415: imports the whole chat stack — only this test needs it
    from kaare_core.routers import router_generate as rg  # noqa: PLC0415
    with open(rg.__file__) as f:
        src = f.read()
    assert "chat_questions.offer(" in src and "chat_questions.settle(" in src and "chat_questions.pending_note(" in src
    assert "enforcer.ran_calls()" in src


def test_enforcer_ran_calls_gives_the_turn_facts():
    # PLC0415: kept local to this one test (imports the enforcer stack)
    from kaare_core.model_enforcement import CALL_RAN, CALL_REFUSED, ModelEnforcer  # noqa: PLC0415
    e = ModelEnforcer.for_profile("chat", "nb")
    e.record_call("camera", {"action": "answer_question"}, CALL_RAN)
    e.record_call("ha_control", {"action": "turn_on"}, CALL_REFUSED)
    assert e.ran_calls() == [("camera", "answer_question")]
