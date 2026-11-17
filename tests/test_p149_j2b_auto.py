"""P149 phase 2 / run-queue J2b (2026-11-06): Auto uses what KTSB learned once about five «Var det deg?» answers
stand behind a sensor (per camera); admin's fixed word always wins; admin can switch the learning off for all sensors or
for one (GUI, Ansiktssensorer). A faceless event claimed in chat settles that event only and teaches no sensor.
Never touches live state (users.db, frigate.db, weights.db, yaml → tmp_path). Placeholders only."""

import pytest

from kaare_core import weights
from kaare_core.frigate_user import face_votes, faces, questions
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore

NOW = 1_800_000_000.0
H = 3600


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text("faces:\n  Person A: {class: household}\n")
    monkeypatch.setattr(questions.time, "time", lambda: NOW)
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="user_a", display_name="USER A", role="adult", pin="482915")
    ustore.update_user("user_a", frigate_faces=["Person A"])
    frigate_consent.invalidate()
    yield tmp_path
    weights.flush()
    wstore.reset_for_tests()
    fstore.reset_for_tests()
    frigate_consent.invalidate()


def _teach(n, verdict="yes", camera="cam_door", sensor="det_x"):
    for i in range(n):
        weights.answer(("sensor", f"{sensor}|{camera}|day"), by_user="user_a", verdict=verdict, verbatim=True,
                       context=f"q{camera}{i}")


# ── the decision (pure) ──

def test_auto_word_waits_for_about_five_answers():
    assert face_votes.auto_word({}, {}, 4, "likely") == ("uncertain", False)
    assert face_votes.auto_word({}, {}, 5, "likely") == ("likely", True)
    assert face_votes.auto_word({}, {}, None, None) == ("uncertain", False)


def test_switches_keep_auto_uncertain():
    assert face_votes.auto_word({"auto_learning": False}, {}, 9, "likely") == ("uncertain", False)
    assert face_votes.auto_word({}, {"auto_learning": False}, 9, "likely") == ("uncertain", False)


# ── wired into sensor_weight (the fusion's one entry) ──

def test_sensor_weight_acts_after_five_answers_on_that_camera(env):
    _teach(4)
    w = face_votes.sensor_weight("det_x", "cam_door", None)
    assert w["word"] == "uncertain" and w["acting"] is False
    _teach(1, camera="cam_door_extra")  # another camera does not count for this one
    assert face_votes.sensor_weight("det_x", "cam_door", None)["acting"] is False
    weights.answer(("sensor", "det_x|cam_door|ir"), by_user="user_a", verdict="yes", verbatim=True, context="qir")
    w = face_votes.sensor_weight("det_x", "cam_door", None)
    assert w["acting"] is True and w["word"] == w["learned_word"] == "likely" and w["source"] == "auto"
    assert w["action"] == face_votes.checks.TRUST_WEIGHTS["likely"]


def test_learned_unreliable_sensor_counts_less(env):
    _teach(6, verdict="no")
    w = face_votes.sensor_weight("det_x", "cam_door", None)
    assert w["acting"] and w["word"] == "unlikely" and w["action"] < face_votes.checks.TRUST_WEIGHTS["uncertain"]


def test_admin_word_wins_over_learning(env, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text(
        "enabled: true\nface_sensors: {detectors: {det_x: {mode: admin, trust: unlikely}}}\n")
    _teach(6)
    w = face_votes.sensor_weight("det_x", "cam_door", None)
    assert w["source"] == "admin" and w["word"] == "unlikely" and w["acting"] is False


def test_house_switch_off_keeps_auto_uncertain(env, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_sensors: {auto_learning: false}\n")
    _teach(6)
    w = face_votes.sensor_weight("det_x", "cam_door", None)
    assert w["word"] == "uncertain" and w["acting"] is False and w["learned"] == 6


def test_card_rows_say_when_learning_is_used(env):
    _teach(6, sensor="frigate")
    row = {r["sensor"]: r for r in rfu._face_sensor_rows(now=NOW)}["frigate"]
    assert row["acting"] is True and row["auto_learning"] is True and row["learned_goal"] == 5
    assert rfu.api_face_sensors(_u={"sub": "boss"})["auto_learning"] is True


# ── (2) a faceless event claimed in chat teaches no sensor ──

def test_faceless_claim_settles_the_event_only(env):
    check = {"decision": "none", "ir": False, "sensors": {"relation": None, "frigate": None, "frigate_face": None,
                                                          "detectors": [{"detector": "det_x", "ref": None, "face": None}]}}
    assert fstore.record_event(fstore.FrigateUserEvent(
        event_id="f1", camera="cam_door", label="person", start_ts=NOW - H, end_ts=NOW - H + 20, person_ref=None,
        person_kind="unknown", claim_status="none", check=check))
    out = questions.answer("user_a", "f1", "yes", channel="chat")
    assert out == {"ok": True, "sensors": 0, "saved": True}  # N39: answers now say whether they were saved
    assert weights.get_store().subjects("sensor") == []
    assert fstore.event_by_id("f1").claim_status == "confirmed"
