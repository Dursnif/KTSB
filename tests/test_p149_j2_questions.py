"""P149 phase 2 / P144 #9 — «Var det deg?»: questions from uncertain camera events, answers into KTSB's weight system.

- only the person a sensor named is asked; an opted-out person never; only about the last week; confirmed events never;
- «ja»/«privat»: sensors that named the person were right, sensors that named someone else wrong; «nei»: only «not you»;
- one answer settles the event; «ja»/«nei» are stored on the event itself with who and when (and survive a re-write by
  the pipe); «privat» changes nothing on the event; «hopp over» lowers how many questions the person gets;
- a question can only be answered by the person it is for (fail closed); admin never sees anyone's questions;
- the chat channel (camera action='verify') answers ONE event; an episode with several open events → ask which;
- «sist sett» mentions a later faceless episode and offers it, never one holding an opted-out person's event;
- sensor_weight shows what was learned but still acts as «uncertain» (shadow).
Never touches live state: users.db, frigate.db, weights.db and the yaml files point at tmp_path. Placeholders only."""

from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from kaare_core import receipts, weights
from kaare_core.frigate_user import face_votes, faces, query, questions
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.tools import definitions, executor_camera
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore

OSLO = ZoneInfo("Europe/Oslo")
NOW = 1_800_000_000.0
H = 3600
CAMS = {"cam_door": {"display_name": "Inngang", "role": "front_door"},
        "cam_yard": {"display_name": "Gårdsplass", "role": "driveway"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nretention_dry_run: false\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n  Person A: {class: household}\n  Person B: {class: household}\n  Person C: {class: household}\n")
    monkeypatch.setattr(query, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(fstore, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(query, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(rfu, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(questions.time, "time", lambda: NOW)
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    for name, face in (("user_a", "Person A"), ("user_b", "Person B"), ("user_c", "Person C")):
        ustore.create_user(username=name, display_name=name.upper(), role="adult", pin="482915")
        ustore.update_user(name, frigate_faces=[face])
    ustore.set_frigate_tracking("user_c", False)
    frigate_consent.invalidate()
    yield tmp_path
    weights.flush()
    wstore.reset_for_tests()
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _sensors(frigate=None, dets=(), relation="disagree"):
    return {"provider": "double_take", "relation": relation, "frigate_face": frigate, "frigate": frigate,
            "detectors": [{"detector": d, "ref": r, "face": r} for d, r in dets]}


def _ev(event_id, start, *, ref="user_a", kind="household", claim="unverified", sensors=None, camera="cam_door",
        ir=False, label="person"):
    check = {"decision": claim, "ir": ir, "sensors": sensors if sensors is not None else
             _sensors("Person A", [("det_x", "user_a"), ("det_y", "user_b")])}
    return fstore.FrigateUserEvent(event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 20,
                                   person_ref=ref, person_kind=kind, claim_status=claim, claim_score=0.8 if ref else None,
                                   check=check, place=None)


def _store(*events):
    for e in events:
        assert fstore.record_event(e)


def _ids(user, channel="gui"):
    return [q["event_id"] for q in questions.candidates(user, channel, now=NOW)]


# ── who is asked ──

def test_the_named_person_is_asked_and_only_them(env):
    _store(_ev("e1", NOW - 2 * H))
    assert _ids("user_a") == ["e1"]
    assert _ids("user_b") == ["e1"]  # det_y guessed user_b: asked too, without any detail (#9)
    assert _ids("boss") == []


def test_opted_out_person_is_never_asked(env):
    # an uncertain event where one detector guessed the opted-out person
    _store(_ev("e1", NOW - H, sensors=_sensors("Person A", [("det_x", "user_c")])))
    assert _ids("user_a") == ["e1"] and _ids("user_c") == []
    assert questions.answer("user_c", "e1", "yes")["ok"] is False


def test_certain_and_old_events_are_not_asked(env):
    _store(_ev("ok", NOW - H, claim="confirmed", sensors=_sensors("Person A", [("det_x", "user_a")], "agree")),
           _ev("old", NOW - 8 * 24 * H))
    assert _ids("user_a") == []


def test_off_switch_means_no_questions(env):
    _store(_ev("e1", NOW - H))
    (env / "frigate_user.yaml").write_text("enabled: false\n")
    assert _ids("user_a") == []


# ── what an answer teaches ──

def _learned(sensor, cam="cam_door", light="day"):
    return weights.get(("sensor", f"{sensor}|{cam}|{light}"))


def test_yes_settles_event_and_moves_each_sensor_one_step(env):
    _store(_ev("e1", NOW - H))
    assert questions.answer("user_a", "e1", "yes")["ok"]
    assert weights.get(("event", "e1", "user_a")).validated > 0.5
    assert _learned("det_x").yes == 1 and _learned("det_x").no == 0  # named me: right
    assert _learned("det_y").no == 1 and _learned("det_y").yes == 0  # named someone else: wrong
    assert _learned("frigate").yes == 1
    ev = fstore.event_by_id("e1")
    assert ev.claim_status == "confirmed" and ev.person_ref == "user_a"
    assert ev.check["human"]["by"] == "user_a" and ev.check["human"]["verdict"] == "yes" and ev.check["human"]["ts"]
    assert _ids("user_a") == [] and _ids("user_b") == []  # settled once — and user_b is not asked about it now?


def test_no_only_teaches_not_you(env):
    _store(_ev("e1", NOW - H))
    assert questions.answer("user_a", "e1", "no")["ok"]
    assert _learned("det_x").no == 1
    assert _learned("det_y").learned_n == 0  # says nothing about who it was
    ev = fstore.event_by_id("e1")
    assert ev.claim_status == "rejected" and ev.person_ref is None and ev.check["human"]["verdict"] == "no"


def test_private_teaches_sensors_and_leaves_event_alone(env):
    _store(_ev("e1", NOW - H))
    before = fstore.event_by_id("e1")
    assert questions.answer("user_a", "e1", "private")["ok"]
    assert _learned("det_x").yes == 1
    after = fstore.event_by_id("e1")
    assert after.claim_status == before.claim_status and "human" not in after.check


def test_ir_events_learn_in_their_own_cell(env):
    _store(_ev("e1", NOW - H, ir=True))
    questions.answer("user_a", "e1", "yes")
    assert _learned("det_x", light="ir").yes == 1 and _learned("det_x", light="day").learned_n == 0


def test_answer_twice_is_refused(env):
    _store(_ev("e1", NOW - H))
    assert questions.answer("user_a", "e1", "yes")["ok"]
    assert questions.answer("user_a", "e1", "no")["ok"] is False
    assert _learned("det_x").learned_n == 1


def test_someone_elses_question_is_refused(env):
    _store(_ev("e1", NOW - H, sensors=_sensors(None, [("det_x", "user_a")])))
    assert questions.answer("user_b", "e1", "yes") == {"ok": False, "error": "not_a_question"}
    assert questions.answer("user_a", "nope", "yes")["ok"] is False
    assert questions.answer("user_a", "e1", "maybe")["ok"] is False


def test_human_answer_survives_the_pipe_writing_again(env):
    _store(_ev("e1", NOW - H))
    questions.answer("user_a", "e1", "yes")
    _store(_ev("e1", NOW - H))  # the pipe writes the event again with the sensors' view
    ev = fstore.event_by_id("e1")
    assert ev.claim_status == "confirmed" and ev.person_ref == "user_a" and ev.check["human"]["by"] == "user_a"


# ── how many questions (D12) ──

def test_skip_removes_question_and_lowers_the_budget(env):
    _store(*[_ev(f"e{i}", NOW - (i + 1) * H) for i in range(8)])
    assert len(_ids("user_a")) == questions.BUDGET["uncertain"]
    for i in range(4):
        assert questions.answer("user_a", f"e{i}", "skip")["ok"]
    assert len(_ids("user_a")) == questions.BUDGET["unlikely"]
    assert all(_learned(s).learned_n == 0 for s in ("det_x", "det_y", "frigate"))  # skipping teaches no sensor


def test_least_learned_sensor_first(env):
    _store(_ev("a", NOW - 3 * H, camera="cam_door"), _ev("b", NOW - 2 * H, camera="cam_yard"),
           _ev("c", NOW - 1 * H, camera="cam_door"))
    questions.answer("user_a", "a", "yes")
    assert _ids("user_a")[0] == "b"  # cam_yard has no answers yet


# ── sensor_weight in shadow ──

def test_sensor_weight_shows_learning_and_acts_after_five(env):
    # J2b (2026-11-06): was «shadow — still acts uncertain»; Auto now uses the learned word from ~5 answers
    _store(*[_ev(f"e{i}", NOW - (i + 1) * H) for i in range(5)])
    for i in range(4):
        questions.answer("user_a", f"e{i}", "yes")
    assert face_votes.sensor_weight("det_x", "cam_door", None)["word"] == "uncertain"
    questions.answer("user_a", "e4", "yes")
    w = face_votes.sensor_weight("det_x", None, None)
    assert w["learned"] == 5 and w["learned_word"] == "likely"
    assert w["word"] == "likely" and w["acting"] is True and w["source"] == "auto"
    assert face_votes.sensor_weight("det_x", "cam_door", "day")["learned"] == 5
    assert face_votes.sensor_weight("det_x", "cam_door", "ir")["learned"] is None


def test_admin_card_rows_carry_learning(env):
    _store(_ev("e1", NOW - H))
    questions.answer("user_a", "e1", "yes")
    rows = {r["sensor"]: r for r in rfu._face_sensor_rows(now=NOW)}
    assert rows["frigate"]["learned"] == 1 and rows["frigate"]["learned_goal"] == 5


# ── GUI endpoints (G17) ──

def test_my_questions_are_mine_only(env):
    _store(_ev("e1", NOW - H, sensors=_sensors(None, [("det_x", "user_a")])))
    mine = rfu.api_my_questions(payload={"sub": "user_a"})["questions"]
    assert [q["event_id"] for q in mine] == ["e1"] and mine[0]["camera"] == "Inngang"
    # J9: items also carry kind + id (one list for camera and meeting questions) — still no image, no name, no detail
    assert set(mine[0]) == {"kind", "id", "event_id", "camera", "ts"} and mine[0]["kind"] == "camera"
    assert rfu.api_my_questions(payload={"sub": "boss"})["questions"] == []


def test_answer_endpoint_fails_closed(env):
    _store(_ev("e1", NOW - H, sensors=_sensors(None, [("det_x", "user_a")])))
    with pytest.raises(HTTPException) as e:
        rfu.api_answer_question(rfu.QuestionAnswer(event_id="e1", answer="yes"), payload={"sub": "user_b"})
    assert e.value.status_code == 404
    assert rfu.api_answer_question(rfu.QuestionAnswer(event_id="e1", answer="yes"), payload={"sub": "user_a"})["ok"]


# ── chat channel (camera action='verify') ──

def test_verify_in_chat_saves_one_event(env):
    _store(_ev("e1", NOW - H))
    out = questions.verify("user_a", event_id="e1", episode_id=None, verdict="yes", lang="nb")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "e1"}] and " Lagret: " in out  # J22 part 4: the receipt
    assert fstore.event_by_id("e1").check["human"]["channel"] == "chat"


def test_verify_episode_with_several_events_asks_which(env):
    _store(_ev("e1", NOW - 2 * H + 0), _ev("e2", NOW - 2 * H + 60))
    ep = fstore.event_by_id("e1").episode_id
    assert ep is not None and fstore.event_by_id("e2").episode_id == ep
    out = questions.verify("user_a", event_id=None, episode_id=ep, verdict="yes", lang="nb")
    assert "e1" in out and "e2" in out and "Lagret" not in out
    assert weights.get(("event", "e1", "user_a")).learned_n == 0  # nothing merged, nothing saved


def test_verify_episode_with_one_open_event_saves_it(env):
    _store(_ev("e1", NOW - 2 * H))
    ep = fstore.event_by_id("e1").episode_id
    out = questions.verify("user_a", event_id=None, episode_id=ep, verdict="no", lang="en")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "e1"}] and " Saved: " in out  # J22 part 4


def test_verify_refuses_guests_and_opted_out(env):
    _store(_ev("e1", NOW - H))
    for uid in ("global", "user_c", ""):
        assert "Ingenting lagret" in questions.verify(uid, event_id="e1", episode_id=None, verdict="yes", lang="nb")
    assert "krever" in questions.verify("user_a", event_id=None, episode_id=None, verdict="yes", lang="nb")


def test_faceless_event_can_be_claimed_in_chat_only(env):
    _store(_ev("f1", NOW - H, ref=None, kind="unknown", claim="none", sensors=_sensors(None, [("det_x", None)])))
    assert _ids("user_a") == []  # never in the GUI list: no sensor named anyone
    assert questions.answer("user_a", "f1", "yes", channel="gui")["ok"] is False
    out = questions.verify("user_a", event_id="f1", episode_id=None, verdict="yes", lang="nb")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "f1"}]  # J22 part 4: the receipt
    ev = fstore.event_by_id("f1")
    assert ev.claim_status == "confirmed" and ev.person_ref == "user_a"


def test_camera_tool_has_verify_with_answer_enum():
    tool = definitions._kamera_tool("nb")["function"]["parameters"]["properties"]
    # J11 added «skip» for answer_question (verify still refuses it, see questions.verify); N39 c added «unsure»
    assert "verify" in tool["action"]["enum"] and tool["answer"]["enum"] == ["yes", "no", "private", "unsure", "skip"]


# ── «sist sett» ──

def test_last_seen_offers_a_later_faceless_episode(env):
    _store(_ev("n1", NOW - 3 * H, claim="confirmed", sensors=_sensors("Person A", [("det_x", "user_a")], "agree")),
           _ev("f1", NOW - 3 * H + 600, ref=None, kind="unknown", claim="none", sensors=_sensors(None, [])))
    fstore.close_visits(now=NOW)
    ep = fstore.event_by_id("f1").episode_id
    out = query.last_seen("user_a", names=True, lang="nb", now=NOW)
    assert f"episode #{ep}" in out and "action='verify'" in out


def test_last_seen_never_offers_an_episode_with_an_opted_out_person(env):
    _store(_ev("n1", NOW - 3 * H, claim="confirmed", sensors=_sensors("Person A", [("det_x", "user_a")], "agree")),
           _ev("f1", NOW - 3 * H + 600, ref=None, kind="unknown", claim="none", sensors=_sensors(None, [])),
           _ev("u1", NOW - 3 * H + 620, ref=None, kind="untracked", claim="none", sensors=_sensors(None, [])))
    fstore.close_visits(now=NOW)
    assert "Senere" not in query.last_seen("user_a", names=True, lang="nb", now=NOW)
