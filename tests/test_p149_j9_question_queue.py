"""P149 / run-queue J9 — one question queue for the G17 checklist: the camera's «Var det deg?», Kåre's own camera
questions, and night-meeting themes the three-night stop parked. One budget and one willingness weight (D12); a theme
only to the user whose meeting it was, never one that names another household member; opted-out users are never asked
about the camera. Tmp files only; no model."""

import asyncio

import pytest

from kaare_core import weights
from kaare_core.frigate_user import faces, questions
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import repeat_stop as rs
from kaare_core.memory import theme_questions as tq
from kaare_core.routers import router_frigate_user as rfu
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
    monkeypatch.setattr(rfu, "_camera_cards", dict)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(questions.time, "time", lambda: NOW)
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text("faces:\n  Person A: {class: household}\n  Person B: {class: household}\n")
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="user_a", display_name="Alfa", role="adult", pin="482915")
    ustore.create_user(username="user_b", display_name="Beta", role="adult", pin="482915")
    ustore.update_user("user_a", frigate_faces=["Person A"])
    ustore.update_user("user_b", frigate_faces=["Person B"])
    frigate_consent.invalidate()
    d = tmp_path / "refl" / "user_a"
    d.mkdir(parents=True)
    for night, miss in (("2026-10-19", "Tema om drikke om kvelden."), ("2026-10-20", "Om Beta i bakgrunnen.")):
        (d / f"{night}.md").write_text(f"**[Kåre]**\nk\n\n**[Miss Kåre]**\n{miss}\n\n", encoding="utf-8")
    yield tmp_path
    weights.flush()
    wstore.reset_for_tests()
    fstore.reset_for_tests()
    frigate_consent.invalidate()


def _park(user, night, who="Miss Kåre"):
    v = rs.record(user, night, None, who)
    rs.park(user, v.chain)
    return v.chain


# ── meeting themes ──

def test_parked_theme_is_a_question_for_its_own_user_only(env):
    chain = _park("user_a", "2026-10-19")
    items = tq.candidates("user_a")
    assert [i["chain"] for i in items] == [chain] and "drikke" in items[0]["text"] and "Miss" not in items[0]["text"]
    other = env / "refl" / "user_b"  # user_b has a report that night too: still never user_a's theme
    other.mkdir()
    (other / "2026-10-19.md").write_text("**[Miss Kåre]**\nNoe annet.\n\n", encoding="utf-8")
    assert tq.candidates("user_b") == []


def test_theme_naming_another_household_member_is_never_asked(env):
    _park("user_a", "2026-10-20")  # «Om Beta i bakgrunnen.» — user_b's display name
    assert tq.candidates("user_a") == []


def test_answer_unparks_and_is_the_users_evidence(env):
    chain = _park("user_a", "2026-10-19")
    assert tq.answer("user_a", chain, "no")["ok"]
    subj = rs._subject("user_a", chain)
    assert not weights.is_parked(subj) and weights.get(subj).no == 1
    assert tq.candidates("user_a") == []
    assert tq.answer("user_a", chain, "yes")["ok"] is False  # answered: no longer a question


def test_skip_hides_it_and_lowers_willingness(env):
    chain = _park("user_a", "2026-10-19")
    assert tq.answer("user_a", chain, "skip")["ok"]
    assert tq.candidates("user_a") == [] and weights.is_parked(rs._subject("user_a", chain))
    assert weights.get(("asking", "user_a|gui", "user_a")).no == 1
    assert tq.answer("user_b", chain, "yes")["ok"] is False


# ── Kåre's own camera questions ──

def _faceless(eid):
    assert fstore.record_event(fstore.FrigateUserEvent(
        event_id=eid, camera="cam", label="person", start_ts=NOW - 3600, end_ts=NOW - 3590, person_kind="unknown",
        claim_status="none", check={"sensors": {"detectors": []}}))


def test_kare_queues_a_faceless_event_for_the_user(env):
    _faceless("f1")
    assert [q["event_id"] for q in questions.candidates("user_a")] == []
    assert questions.queue_for("user_a", "f1")["ok"]
    assert [q["event_id"] for q in questions.candidates("user_a")] == ["f1"]
    assert questions.answer("user_a", "f1", "yes", channel="gui")["ok"]
    assert fstore.event_by_id("f1").person_ref == "user_a"


def test_kare_cannot_queue_for_an_opted_out_user(env):
    _faceless("f1")
    ustore.set_frigate_tracking("user_b", False)
    frigate_consent.invalidate()
    assert questions.queue_for("user_b", "f1")["ok"] is False
    assert questions.candidates("user_b") == []


# ── one list, one budget (wired: router + camera tool) ──

def test_my_questions_holds_both_kinds_within_one_budget(env):
    chain = _park("user_a", "2026-10-19")
    _faceless("f1")
    questions.queue_for("user_a", "f1")
    items = rfu.api_my_questions(payload={"sub": "user_a"})["questions"]
    kinds = sorted(i["kind"] for i in items)
    assert kinds == ["camera", "theme"] and len(items) <= questions.budget("user_a")
    theme = next(i for i in items if i["kind"] == "theme")
    assert theme["id"] == chain and "drikke" in theme["text"]
    assert rfu.api_answer_question(rfu.QuestionAnswer(event_id=chain, answer="no", kind="theme"),
                                   payload={"sub": "user_a"})["ok"]


def test_camera_tool_ask_action(env):
    _faceless("f1")
    tool = definitions._kamera_tool("nb")["function"]["parameters"]["properties"]["action"]["enum"]
    assert "ask" in tool
    out = asyncio.run(executor_camera._kamera({"action": "ask", "event_id": "f1", "_user_id": "user_a"}, "nb"))
    assert out and [q["event_id"] for q in questions.candidates("user_a")] == ["f1"]
