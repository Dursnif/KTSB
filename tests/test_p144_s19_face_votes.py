"""P144-s19: Double Take as the second face sensor — settings, payload parsing, name mapping, consent and the pure
fusion rules (P144 decision 2026-10-27: one sensor → likely, two agree → very likely, disagree → uncertain + code
attribute test against both profiles, a lean never above likely). Nothing here reaches a broker, Double Take, live
users.db / frigate.db / configs."""

import json

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
        "  Person B: {class: household, attributes: {beard: full, age_band: adult}}\n"
        "  Guest C: {class: safe_guest}\n"
    )
    ustore.init_db()
    for name in ("persona", "personb"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    ustore.update_user("personb", frigate_faces=["Person B"])
    frigate_consent.invalidate()
    face_votes.reset_for_tests()
    yield tmp_path
    face_votes.reset_for_tests()
    frigate_consent.invalidate()


def _match(event_id, name, confidence=95.0, camera="inngang", match=True, kind="mqtt"):
    """A Double Take `double-take/matches/<name>` payload (fields as Double Take sends them; base64 ignored)."""
    return json.dumps({"id": event_id, "duration": 0.4, "timestamp": "2026-10-30T10:00:00Z", "attempts": 3,
                       "camera": camera, "zones": [],
                       "match": {"name": name, "confidence": confidence, "match": match, "box": {}, "type": kind,
                                 "detector": "deepstack", "base64": "AAAA"}}).encode()


# ── settings ────────────────────────────────────────────────────────────────


def test_default_is_off_and_generic(tmp_path, monkeypatch):
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "none.yaml")
    fv = fsettings.face_votes()
    assert fv == {"enabled": False, "provider": "double_take", "topic_prefix": "double-take", "rest_url": "",
                  "min_confidence": 0.0}  # s29 added weights here; s29b moved them to `face_sensors` (words)


def test_update_validates_face_votes(env):
    fsettings.update({"face_votes": {"enabled": False, "topic_prefix": "dt", "min_confidence": 80}})
    assert fsettings.face_votes()["topic_prefix"] == "dt"
    assert fsettings.face_votes()["min_confidence"] == 80
    for bad in ({"provider": "nope"}, {"min_confidence": 101}, {"topic_prefix": "a/#"}, {"rest_url": "ftp://x"},
                {"what": 1}):
        with pytest.raises(ValueError):
            fsettings.update({"face_votes": bad})


def test_topics_only_when_on(env, tmp_path):
    assert face_votes.topics() == ["double-take/matches/#", "double-take/cameras/+", "double-take/available"]
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: false}\n")
    assert face_votes.topics() == []
    (tmp_path / "frigate_user.yaml").write_text("enabled: false\nface_votes: {enabled: true}\n")
    assert face_votes.topics() == []  # the Frigate user is off → no second sensor either


# ── payload + mapping ───────────────────────────────────────────────────────


def test_match_payload_is_buffered_by_frigate_event_id(env):
    assert face_votes.on_message("double-take/matches/person a", _match("ev1", "person a", 93.5)) == "ev1"
    r = face_votes.reading("ev1")
    assert [(n["face"], n["ref"], n["kind"], n["confidence"]) for n in r["names"]] == [
        ("Person A", "persona", "household", 93.5)]
    assert r["split"] is False and r["unmapped"] == 0


def test_base64_never_kept(env):
    face_votes.on_message("double-take/matches/person a", _match("ev1", "person a"))
    assert "AAAA" not in json.dumps(face_votes.reading("ev1"), default=str)


def test_duplicate_attempts_are_one_vote(env):
    for kind, conf in (("mqtt", 81.0), ("latest", 88.0), ("snapshot", 85.0)):
        face_votes.on_message("double-take/matches/person a", _match("ev1", "person a", conf, kind=kind))
    r = face_votes.reading("ev1")
    assert len(r["names"]) == 1 and r["names"][0]["confidence"] == 88.0
    assert r["types"] == ["latest", "mqtt", "snapshot"]


def test_two_different_names_are_split(env):
    face_votes.on_message("double-take/matches/person a", _match("ev1", "person a"))
    face_votes.on_message("double-take/matches/person b", _match("ev1", "person b"))
    assert face_votes.reading("ev1")["split"] is True


def test_unknown_name_is_never_a_guessed_user(env):
    face_votes.on_message("double-take/matches/zed", _match("ev1", "zed"))
    r = face_votes.reading("ev1")
    assert r["names"] == [] and r["unmapped"] == 1


def test_guest_maps_to_face_spelling(env):
    face_votes.on_message("double-take/matches/guest c", _match("ev1", "guest c"))
    n = face_votes.reading("ev1")["names"][0]
    assert (n["face"], n["ref"], n["kind"]) == ("Guest C", "Guest C", "safe_guest")


def test_below_min_confidence_is_not_a_vote(env, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: true, min_confidence: 80}\n")
    face_votes.on_message("double-take/matches/person a", _match("ev1", "person a", 72.0))
    r = face_votes.reading("ev1")
    assert r["names"] == [] and r["no_match"][0]["ref"] == "persona"


def test_camera_payload_misses_are_raw_not_votes(env):
    payload = json.dumps({"id": "ev1", "camera": "inngang", "matches": [],
                          "misses": [{"name": "person b", "confidence": 70.1, "match": False}],
                          "unknowns": [{"name": "unknown", "confidence": 0, "match": False}]}).encode()
    face_votes.on_message("double-take/cameras/inngang", payload)
    r = face_votes.reading("ev1")
    assert r["names"] == [] and r["no_match"] == [{"ref": "personb", "kind": "household", "confidence": 70.1}]
    assert r["unknown_faces"] == 1


def test_count_topic_and_junk_are_ignored(env):
    assert face_votes.on_message("double-take/cameras/inngang/person", b"2") is None
    assert face_votes.on_message("double-take/matches/x", b"not json") is None
    assert face_votes.on_message("frigate/events", _match("ev1", "person a")) is None
    assert face_votes.reading("ev1") is None


def test_available_is_the_health(env):
    assert face_votes.status()["available"] == "unknown"
    face_votes.on_message("double-take/available", b"online")
    assert face_votes.status()["available"] == "online"
    face_votes.on_message("double-take/available", b"offline")
    assert face_votes.status()["available"] == "offline"


def test_off_buffers_nothing(env, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: false}\n")
    assert face_votes.on_message("double-take/matches/person a", _match("ev1", "person a")) is None
    assert face_votes.reading("ev1") is None


# ── fusion (pure) ───────────────────────────────────────────────────────────

ATTRS_NO_BEARD = {"persons": 1, "beard": "no", "glasses": "unknown", "age_band": "adult", "headwear": "no"}


def _reading(*names, split=False):
    return {"provider": "double_take", "names": [
        {"face": f, "ref": r, "kind": k, "confidence": 90.0} for f, r, k in names], "split": split, "unmapped": 0,
        "no_match": [], "unknown_faces": 0, "types": ["mqtt"]}


def _fuse(frigate_ref, status, reading, attrs=ATTRS_NO_BEARD, votes=()):
    return checks.fuse_second_sensor(frigate_ref=frigate_ref, frigate_face=frigate_ref and {
        "persona": "Person A", "personb": "Person B"}.get(frigate_ref, frigate_ref), frigate_status=status,
        votes=list(votes), attributes=attrs, reading=reading, profile_of=faces.attribute_profile)


def test_agree_confirms(env):
    # J14 (ep 266): was «both on Auto agree → confirmed → very_likely»; now Auto sensors agreeing stay unverified
    out = _fuse("persona", "unverified", _reading(("Person A", "persona", "household")))
    assert out["status"] == "unverified" and out["sensors"]["relation"] == "agree"
    assert query.certainty({"sensors": out["sensors"], "decision": out["status"]}, out["status"]) != "very_likely"


def test_agree_never_beats_a_strong_contradiction(env):
    strong = checks.Vote("l3", "no", True, "beard full≠no", [{"layer": "l3", "field": "beard"}])
    out = _fuse("persona", "unverified", _reading(("Person A", "persona", "household")), votes=[strong])
    assert out["status"] == "unverified"


def test_disagree_without_lean_is_uncertain(env):
    attrs = {"persons": 1, "beard": "unknown", "glasses": "unknown", "age_band": "unknown", "headwear": "no"}
    out = _fuse("persona", "unverified", _reading(("Person B", "personb", "household")), attrs=attrs)
    assert out["sensors"]["relation"] == "disagree" and out["sensors"]["lean"] is None
    assert out["person_ref"] == "persona"  # the first sensor's claim stays the stored one
    assert query.certainty({"sensors": out["sensors"]}, out["status"]) == "uncertain"
    assert out["review"] is True


def test_disagree_leans_by_attributes_but_never_above_likely(env):
    # Image: no beard. Person B's profile has a full beard → strongly contradicted; Person A's is not → lean A.
    out = _fuse("personb", "unverified", _reading(("Person A", "persona", "household")))
    assert out["sensors"]["lean"] == "persona"
    assert out["person_ref"] == "persona" and out["person_kind"] == "household" and out["source"] == "double_take"
    assert out["status"] == "unverified"
    assert query.certainty({"sensors": out["sensors"]}, out["status"]) == "likely"


def test_split_is_uncertain(env):
    r = _reading(("Person A", "persona", "household"), ("Person B", "personb", "household"), split=True)
    out = _fuse("persona", "unverified", r)
    assert out["sensors"]["relation"] == "split"
    assert query.certainty({"sensors": out["sensors"]}, out["status"]) == "uncertain"


def test_double_take_only_is_one_sensor(env):
    out = _fuse(None, "unverified", _reading(("Person A", "persona", "household")))
    assert out["sensors"]["relation"] == "double_take_only"
    assert query.certainty({"sensors": out["sensors"]}, out["status"]) == "likely"


def test_frigate_only_is_unchanged(env):
    out = _fuse("persona", "unverified", _reading())
    assert out["sensors"]["relation"] == "frigate_only" and out["status"] == "unverified"
    assert query.certainty({"sensors": out["sensors"]}, out["status"]) == "likely"


def test_no_reading_changes_nothing(env):
    assert _fuse("persona", "unverified", None) is None


def test_opted_out_double_take_name_is_masked_in_what_is_stored(env):
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    attrs = {"persons": 1, "beard": "unknown", "glasses": "unknown", "age_band": "unknown", "headwear": "no"}
    out = _fuse("persona", "unverified", _reading(("Person B", "personb", "household")), attrs=attrs)
    stored = face_votes.mask_for_storage(out)
    text = json.dumps(stored)
    assert "personb" not in text and "Person B" not in text
    assert stored["sensors"]["double_take"] is None and stored["sensors"]["double_take_kind"] == "untracked"


def test_opted_out_frigate_name_is_masked_in_what_is_stored(env):
    ustore.set_frigate_tracking("persona", False)
    frigate_consent.invalidate()
    out = _fuse("persona", "unverified", _reading(("Person B", "personb", "household")))
    stored = face_votes.mask_for_storage(out)
    assert "persona" not in json.dumps(stored["sensors"]) and "Person A" not in json.dumps(stored["sensors"])


def test_rejected_frigate_name_is_not_stored_in_the_sensor_block(env):
    attrs = {"persons": 1, "beard": "unknown", "glasses": "unknown", "age_band": "unknown", "headwear": "no"}
    out = _fuse("persona", "rejected", _reading(("Person B", "personb", "household")), attrs=attrs)
    stored = face_votes.mask_for_storage(out)
    assert stored["sensors"]["frigate"] is None and stored["sensors"]["frigate_rejected"] is True


def test_unknown_faces_are_not_counted_twice(env):
    """Double Take sends the same unknowns on `matches/unknown` and `cameras/<camera>` (its mqtt.util.js)."""
    unknowns = [{"name": "unknown", "confidence": 0, "match": False, "type": "latest"}]
    face_votes.on_message("double-take/matches/unknown", json.dumps(
        {"id": "ev1", "camera": "inngang", "unknown": unknowns[0], "unknowns": unknowns}).encode())
    face_votes.on_message("double-take/cameras/inngang", json.dumps(
        {"id": "ev1", "camera": "inngang", "matches": [], "misses": [], "unknowns": unknowns}).encode())
    assert face_votes.reading("ev1")["unknown_faces"] == 1


def test_votes_seen_counts_events_not_messages(env):
    for kind in ("mqtt", "latest", "snapshot"):
        face_votes.on_message("double-take/matches/person a", _match("ev1", "person a", kind=kind))
    face_votes.on_message("double-take/matches/person b", _match("ev2", "person b"))
    assert face_votes.status()["votes_seen"] == 2
