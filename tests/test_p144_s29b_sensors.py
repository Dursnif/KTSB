"""P144-s29b: face sensors as words (2026-11-05) — every face sensor (Frigate + each detector a provider reports)
has «Auto – KTSB lærer» or a word admin sets (høyst sannsynlig / sannsynlig / usikkert / lite sannsynlig / teller ikke).
The fusion reads them through one function, `face_votes.sensor_weight(sensor, camera, light)` (where KTSB's weight
system plugs in later); agreement never changes a sensor's value (echo rule); an admin word decides the word for a name
one sensor alone stands behind. Nothing here reaches a broker, Double Take, live users.db / frigate.db / configs."""

import json

import httpx
import pytest

from kaare_core.frigate_user import checks, face_votes, faces, query
from kaare_core.frigate_user import settings as fsettings
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: true}\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n"
        "  Person A: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
        "  Person B: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
    )
    ustore.init_db()
    for name in ("persona", "personb"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    ustore.update_user("personb", frigate_faces=["Person B"])
    frigate_consent.invalidate()
    face_votes.reset_for_tests()

    class _NoHttp:
        def __init__(self, *a, **k):
            raise AssertionError("a test reached for a real HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", _NoHttp)
    yield tmp_path
    face_votes.reset_for_tests()
    frigate_consent.invalidate()


def _sensors(tmp_path, frigate=None, detectors=None):
    fs = {"frigate": frigate or {"mode": "auto"}, "detectors": detectors or {}}
    (tmp_path / "frigate_user.yaml").write_text(
        "enabled: true\nface_votes: {enabled: true}\nface_sensors: " + json.dumps(fs) + "\n")


def _mqtt(event_id, detector, name, conf=75.0, filename="img1.jpg"):
    m = {"name": name, "confidence": conf, "match": True, "type": "snapshot", "detector": detector,
         "filename": filename, "box": {"top": 100, "left": 100, "width": 80, "height": 100}}
    return face_votes.on_message(f"double-take/matches/{name}",
                                 json.dumps({"id": event_id, "camera": "inngang", "match": m}).encode())


def _fuse(reading, frigate_ref="persona", frigate_face="Person A"):
    return checks.fuse_second_sensor(frigate_ref=frigate_ref, frigate_face=frigate_face, frigate_status="unverified",
                                     votes=[], attributes={}, reading=reading, profile_of=faces.attribute_profile)


# ── settings ────────────────────────────────────────────────────────────────


def test_default_is_auto_for_every_sensor(tmp_path, monkeypatch):
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "none.yaml")
    fs = fsettings.face_sensors()
    assert fs["frigate"]["mode"] == "auto" and fs["detectors"] == {}


def test_face_sensor_settings_validate(env):
    fsettings.update({"face_sensors": {"frigate": {"mode": "admin", "trust": "likely"},
                                       "detectors": {"det_x": {"label": "X", "mode": "admin", "trust": "off"}}}})
    fs = fsettings.face_sensors()
    # J2b: every sensor also carries `auto_learning` (on by default)
    assert fs["frigate"] == {"mode": "admin", "trust": "likely", "label": "", "auto_learning": True}
    assert fs["detectors"]["det_x"] == {"mode": "admin", "trust": "off", "label": "X", "auto_learning": True}
    for bad in ({"frigate": {"trust": "verified"}}, {"frigate": {"mode": "learned"}}, {"auto_learning": "yes"},
                {"frigate": {"auto_learning": 1}},
                {"detectors": {"x": {"trust": 0.5}}}, {"detectors": {"": {}}}, {"what": 1},
                {"detectors": {"x": {"label": "y" * 41}}}):
        with pytest.raises(ValueError):
            fsettings.update({"face_sensors": bad})


def test_verified_is_never_a_sensor_value(env):
    with pytest.raises(ValueError):
        fsettings.update({"face_sensors": {"detectors": {"x": {"mode": "admin", "trust": "verified"}}}})


# ── the one place the fusion reads a sensor's value ─────────────────────────


def test_sensor_weight_auto_is_cautious_and_says_so(env):
    w = face_votes.sensor_weight("frigate", "inngang", None)
    assert w["source"] == "auto" and w["word"] == "uncertain" and w["learned"] is None
    assert w["action"] == checks.TRUST_WEIGHTS["uncertain"]
    assert "attention" in w  # kept apart from action for the weight system (None until it exists)


def test_sensor_weight_admin_word_wins(env, tmp_path):
    _sensors(tmp_path, detectors={"det_cp": {"mode": "admin", "trust": "unlikely"}})
    w = face_votes.sensor_weight("det_cp", "inngang", "ir")
    assert w["source"] == "admin" and w["word"] == "unlikely" and w["action"] == checks.TRUST_WEIGHTS["unlikely"]


def test_an_unknown_detector_is_auto(env):
    assert face_votes.sensor_weight("never_seen", None, None)["source"] == "auto"


def test_the_fusion_reads_weights_only_through_sensor_weight(env, tmp_path, monkeypatch):
    _sensors(tmp_path, detectors={"det_cp": {"mode": "admin", "trust": "off"}})
    seen = []
    real = face_votes.sensor_weight

    def spy(sensor, camera, light):
        seen.append((sensor, camera))
        return real(sensor, camera, light)
    monkeypatch.setattr(face_votes, "sensor_weight", spy)
    _mqtt("ev1", "det_cp", "person b")
    r = face_votes.reading("ev1")
    assert ("det_cp", "inngang") in seen and ("frigate", "inngang") in seen
    assert face_votes.single_vote(r) is None  # «teller ikke»


# ── words in the fusion ─────────────────────────────────────────────────────


def test_the_likelier_detector_wins_a_disagreement(env, tmp_path):
    _sensors(tmp_path, detectors={"det_if": {"mode": "admin", "trust": "likely"},
                                  "det_cp": {"mode": "admin", "trust": "unlikely"}})
    _mqtt("ev1", "det_if", "person a", 60.0)
    _mqtt("ev1", "det_cp", "person b", 77.0)
    assert face_votes.single_vote(face_votes.reading("ev1"))["ref"] == "persona"


def test_all_auto_behaves_as_before(env):
    _mqtt("ev1", "det_if", "person a")
    _mqtt("ev1", "det_cp", "person b")
    assert face_votes.reading("ev1")["split"] is True
    face_votes.reset_for_tests()
    _mqtt("ev2", "det_if", "person a")
    out = _fuse(face_votes.reading("ev2"))
    # J14 (ep 266): was «confirmed» (as before s29b); two Auto («uncertain») sensors agreeing no longer confirm
    assert out["status"] == "unverified"


def test_frigate_rated_unlikely_loses_to_two_agreeing_detectors(env, tmp_path):
    _sensors(tmp_path, frigate={"mode": "admin", "trust": "unlikely"})
    _mqtt("ev1", "det_if", "person b")
    _mqtt("ev1", "det_cp", "person b", filename="img2.jpg")
    out = _fuse(face_votes.reading("ev1"))
    assert out["sensors"]["relation"] == "disagree"
    assert out["person_ref"] == "personb" and out["sensors"]["lean_why"] == "weight"
    assert out["status"] == "unverified"  # a lean is never above «likely»


def test_a_light_frigate_agreeing_never_confirms(env, tmp_path):
    _sensors(tmp_path, frigate={"mode": "admin", "trust": "unlikely"})
    _mqtt("ev1", "det_if", "person a")
    out = _fuse(face_votes.reading("ev1"))
    assert out["sensors"]["relation"] == "agree" and out["status"] == "unverified"


def test_agreement_never_changes_a_sensors_value(env, tmp_path):
    """Echo rule (P115): sensors agreeing with each other never move any sensor towards confirmed/verified."""
    before = {s: face_votes.sensor_weight(s, "inngang", None) for s in ("frigate", "det_if")}
    cfg_before = (tmp_path / "frigate_user.yaml").read_text()
    for i in range(20):
        _mqtt(f"ev{i}", "det_if", "person a", filename=f"i{i}.jpg")
        _fuse(face_votes.reading(f"ev{i}"))
    assert {s: face_votes.sensor_weight(s, "inngang", None) for s in before} == before
    assert (tmp_path / "frigate_user.yaml").read_text() == cfg_before


# ── the word Kåre says for a name one sensor alone stands behind ────────────


def _check(trust, relation="frigate_only", **extra):
    c = {"decision": "unverified", "claim_trust": trust, **extra}
    if relation:
        c["sensors"] = {"relation": relation}
    return c


def test_admin_word_decides_a_one_sensor_name(env):
    assert query.certainty(_check({"word": "unlikely", "source": "admin"}), "unverified") == "unlikely"
    assert query.certainty(_check({"word": "very_likely", "source": "admin"}), "unverified") == "very_likely"
    assert query.certainty(_check({"word": "uncertain", "source": "admin"}, relation=None), "unverified") == "uncertain"


def test_auto_keeps_todays_word(env):
    assert query.certainty(_check({"word": "uncertain", "source": "auto"}), "unverified") == "likely"


def test_code_doubt_still_lowers_an_admin_word(env):
    c = _check({"word": "very_likely", "source": "admin"}, doubt={"strong": [{}]})
    assert query.certainty(c, "unverified") == "uncertain"
    c = _check({"word": "unlikely", "source": "admin"}, doubt={"strong": [{}]})
    assert query.certainty(c, "unverified") == "unlikely"


def test_admin_word_does_not_touch_two_sensors(env):
    c = {"decision": "confirmed", "claim_trust": {"word": "unlikely", "source": "admin"}, "sensors": {"relation": "agree"}}
    # J14 / P149 D1: was «very_likely»; machine «confirmed» says at most «likely» (only a person's «ja» goes higher)
    assert query.certainty(c, "confirmed") == "likely"


def test_fusion_marks_which_sensor_stood_alone(env, tmp_path):
    _sensors(tmp_path, detectors={"det_if": {"mode": "admin", "trust": "likely"}})
    _mqtt("ev1", "det_if", "person a")
    r = {**face_votes.reading("ev1"), "claim_source": "double_take"}
    out = _fuse(r)
    assert out["sensors"]["relation"] == "double_take_only"
    assert out["claim_trust"] == {"word": "likely", "source": "admin"}


# ── consent with Frigate + two detectors ────────────────────────────────────


def test_opted_out_person_stays_unnamed_whatever_the_words(env, tmp_path):
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    _sensors(tmp_path, frigate={"mode": "admin", "trust": "unlikely"},
             detectors={"det_if": {"mode": "admin", "trust": "very_likely"},
                        "det_cp": {"mode": "admin", "trust": "very_likely"}})
    _mqtt("ev1", "det_if", "person b")
    _mqtt("ev1", "det_cp", "person b", filename="img2.jpg")
    r = face_votes.reading("ev1")
    stored = face_votes.mask_for_storage(_fuse(r), r)
    assert "personb" not in json.dumps({"s": stored["sensors"], "l2": stored["l2"]}).casefold()
    assert "person b" not in json.dumps({"s": stored["sensors"], "l2": stored["l2"]}).casefold()
