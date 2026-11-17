"""P144 / run-queue J14 — ep 266 (2026-11-06 07:16:37, doorbell): Frigate (uncertain) and facebox (uncertain, 0.5)
both said one name, deepstack (very likely) said nothing there and another name on two other cameras; the result was
«confirmed» and the 27B wrote «høyst sannsynlig» — it was someone else (the user's truth).

(1) sensors that are all «uncertain» or lower never make «confirmed» (Frigate and facebox often read the same image —
    they are not independent); (2) machine «confirmed» needs one agreeing sensor at «likely» or higher, and even then the
    words to the 27B and users say at most «sannsynlig» (P149 D1: machines alone ≤ 0.5) — «høyst sannsynlig» needs a
    human «ja»; (4) such an event becomes a «Var det deg?» question. Placeholders only."""

import pytest

from kaare_core import weights
from kaare_core.frigate_user import checks, faces, query, questions
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore


def _reading(det_weight, support, frigate_action=0.5):
    return {"names": [{"ref": "user_n", "face": "Person N", "kind": "household", "confidence": 80.0}],
            "split": False, "support": support, "total_weight": support, "claim_source": "frigate",
            "frigate_trust": {"action": frigate_action, "word": "uncertain", "source": "admin"},
            "detectors": [{"detector": "facebox", "weight": det_weight, "names": [{"ref": "user_n", "face": "Person N",
                                                                                   "kind": "household"}]},
                          {"detector": "deepstack", "weight": 1.0, "names": []}]}


def _fuse(reading):
    return checks.fuse_second_sensor(frigate_ref="user_n", frigate_face="Person N", frigate_status="unverified",
                                     votes=[], attributes={}, reading=reading, profile_of=lambda f: {},
                                     frigate_kind="household")


def test_two_uncertain_sensors_agreeing_never_confirm():
    out = _fuse(_reading(0.5, 0.5))  # the ep 266 doorbell event
    assert out["sensors"]["relation"] == "agree" and out["status"] == "unverified"


def test_one_likely_sensor_agreeing_may_confirm():
    assert _fuse(_reading(0.75, 0.75))["status"] == "confirmed"
    assert _fuse(_reading(0.5, 0.5, frigate_action=0.75))["status"] == "confirmed"


def test_machine_confirmed_says_at_most_likely_a_human_yes_says_very_likely():
    check = {"decision": "confirmed", "sensors": {"relation": "agree"}}
    assert query.certainty(check, "confirmed") == "likely"
    human = {**check, "human": {"by": "user_n", "verdict": "yes", "channel": "gui", "ts": 1.0}}
    assert query.certainty(human, "confirmed") == "very_likely"


# ── (4) such events become «Var det deg?» questions (J2) ──

NOW = 1_800_000_000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    monkeypatch.setattr(questions.time, "time", lambda: NOW)
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text("faces:\n  Person N: {class: household}\n  Person P: {class: household}\n")
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    for name, face in (("user_n", "Person N"), ("user_p", "Person P")):
        ustore.create_user(username=name, display_name=name, role="adult", pin="482915")
        ustore.update_user(name, frigate_faces=[face])
    frigate_consent.invalidate()
    yield
    weights.flush()
    wstore.reset_for_tests()
    fstore.reset_for_tests()


def test_ep266_like_events_ask_the_named_people(env):
    door = {"decision": "unverified", "sensors": {"relation": "agree", "frigate": "user_n", "frigate_face": "Person N",
                                                  "detectors": [{"detector": "facebox", "ref": "user_n"},
                                                                {"detector": "deepstack", "ref": None}]}}
    side = {"decision": "unverified", "sensors": {"relation": "double_take_only",
                                                  "detectors": [{"detector": "deepstack", "ref": "user_p"},
                                                                {"detector": "facebox", "ref": "user_n"}]}}
    for eid, cam, ref, chk in (("d", "door", "user_n", door), ("s", "side", "user_p", side)):
        assert fstore.record_event(fstore.FrigateUserEvent(
            event_id=eid, camera=cam, label="person", start_ts=NOW - 600, end_ts=NOW - 590, person_ref=ref,
            person_kind="household", claim_status="unverified", check=chk))
    assert sorted(q["event_id"] for q in questions.candidates("user_n")) == ["d", "s"]
    assert [q["event_id"] for q in questions.candidates("user_p")] == ["s"]
