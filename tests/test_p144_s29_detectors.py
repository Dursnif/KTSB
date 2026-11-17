"""P144-s29: Double Take per detector — a vote and a weight per detector, Double Take's REST read (GET only) that
restores the detector Double Take's MQTT merge hides, the face-box sanity check (a torso-sized «face» that no other
detector saw is not a vote), consent with two detectors, and the sensor line. Nothing here reaches a broker, Double Take,
live users.db / frigate.db / configs: the REST read is patched, and any real HTTP client fails the test."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from kaare_core.frigate_user import checks, face_votes, faces, query, store
from kaare_core.frigate_user import settings as fsettings
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

CFG = "enabled: true\nface_votes: {enabled: true, rest_url: 'http://dt.invalid:3000'%s}\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text(CFG % "")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n"
        "  Person A: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
        "  Person B: {class: household, attributes: {beard: full, age_band: adult}}\n"
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


def _words(tmp_path, detectors: dict):
    """s29b: how much a detector counts is a word in `face_sensors` (was a 0–1 weight in s29)."""
    fs = {"frigate": {"mode": "auto"}, "detectors": {n: {"mode": "admin", **d} for n, d in detectors.items()}}
    (tmp_path / "frigate_user.yaml").write_text((CFG % "") + "face_sensors: " + json.dumps(fs) + "\n")


def _res(name, conf=75.0, match=True, box=(100, 100, 80, 100)):
    top, left, w, h = box
    return {"name": name, "confidence": conf, "match": match, "box": {"top": top, "left": left, "width": w, "height": h}}


def _mqtt(event_id, detector, name, conf=75.0, filename="img1.jpg", box=(100, 100, 80, 100)):
    """`double-take/matches/<name>` as Double Take sends it: the winning detector per name, with box + filename."""
    m = {**_res(name, conf, True, box), "type": "snapshot", "duration": 0.3, "detector": detector,
         "filename": filename, "base64": None}
    return json.dumps({"id": event_id, "camera": "inngang", "zones": [], "match": m}).encode()


def _row(filename, response, size=(1000, 1000), row_id=1, created="2026-11-05T10:00:00.000Z"):
    """One Double Take REST `/api/match` row (the fields KTSB reads)."""
    return {"id": row_id, "camera": "inngang", "type": "snapshot", "createdAt": created,
            "file": {"filename": filename, "width": size[0], "height": size[1]},
            "response": [{"detector": d, "duration": 0.2, "attempt": 1, "results": rs, "filename": filename}
                         for d, rs in response.items()]}


def _patch_rest(monkeypatch, pages: list[list[dict]], calls: list):
    async def fake(base, page):
        calls.append((base, page))
        return pages[page - 1] if page - 1 < len(pages) else []
    monkeypatch.setattr(face_votes, "_fetch_page", fake)


def _det(reading, name):
    return next(d for d in reading["detectors"] if d["detector"] == name)


# ── settings ────────────────────────────────────────────────────────────────


def test_default_sensor_words_are_generic(tmp_path, monkeypatch):
    # s29b: was «default_weight 1.0 + detectors {}»; now every sensor is «auto» by default.
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "none.yaml")
    fs = fsettings.face_sensors()
    assert fs["frigate"]["mode"] == "auto" and fs["detectors"] == {}
    assert "default_weight" not in fsettings.face_votes() and "detectors" not in fsettings.face_votes()


def test_configs_default_carries_the_sensor_words():
    raw = yaml.safe_load(Path("configs_default/frigate_user.yaml").read_text())
    assert raw["face_sensors"]["frigate"]["mode"] == "auto" and raw["face_sensors"]["detectors"] == {}


# ── separate votes + weights ────────────────────────────────────────────────


def test_each_detector_gets_its_own_vote(env):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_if", "person a", filename="a.jpg"))
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b", filename="a.jpg"))
    r = face_votes.reading("ev1")
    assert [n["ref"] for n in _det(r, "det_if")["names"]] == ["persona"]
    assert [n["ref"] for n in _det(r, "det_cp")["names"]] == ["personb"]


def test_equal_weights_disagreeing_is_split_as_before(env):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_if", "person a"))
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b"))
    r = face_votes.reading("ev1")
    assert r["split"] is True and face_votes.single_vote(r) is None


def test_the_heavier_detector_wins_a_disagreement(env, tmp_path):
    _words(tmp_path, {"det_if": {"label": "InsightFace", "trust": "likely"}, "det_cp": {"label": "CPAI", "trust": "unlikely"}})
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_if", "person a", 60.0))
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b", 77.0))
    r = face_votes.reading("ev1")
    assert r["split"] is False and face_votes.single_vote(r)["ref"] == "persona"
    assert _det(r, "det_cp")["label"] == "CPAI" and _det(r, "det_cp")["trust"] == "unlikely"


def test_weight_zero_is_shown_but_never_counts(env, tmp_path):
    _words(tmp_path, {"det_cp": {"label": "CPAI", "trust": "off"}})
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b"))
    r = face_votes.reading("ev1")
    assert face_votes.single_vote(r) is None and r["names"] == []
    assert [n["ref"] for n in _det(r, "det_cp")["names"]] == ["personb"]


def test_an_unknown_detector_is_auto(env):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "somebody_new", "person a"))
    r = face_votes.reading("ev1")
    assert _det(r, "somebody_new")["source"] == "auto" and _det(r, "somebody_new")["label"] == "somebody_new"
    assert _det(r, "somebody_new")["weight"] == checks.TRUST_WEIGHTS["uncertain"]


def test_a_light_vote_alone_never_confirms(env, tmp_path):
    _words(tmp_path, {"det_cp": {"label": "CPAI", "trust": "unlikely"}})
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a"))
    r = face_votes.reading("ev1")
    out = checks.fuse_second_sensor(frigate_ref="persona", frigate_face="Person A", frigate_status="unverified",
                                    votes=[], attributes={}, reading=r, profile_of=faces.attribute_profile)
    assert out["sensors"]["relation"] == "agree" and out["status"] == "unverified"


def test_a_full_vote_agreeing_still_confirms(env):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_if", "person a"))
    r = face_votes.reading("ev1")
    out = checks.fuse_second_sensor(frigate_ref="persona", frigate_face="Person A", frigate_status="unverified",
                                    votes=[], attributes={}, reading=r, profile_of=faces.attribute_profile)
    # J14 (ep 266): a full vote of an Auto («uncertain») detector agreeing no longer confirms (was: «confirmed»);
    # one agreeing sensor must stand at «likely» or higher — tests/test_p144_j14_weak_agree.py covers that path
    assert out["status"] == "unverified"


# ── Double Take's REST read (GET only) ──────────────────────────────────────


def test_rest_restores_the_detector_mqtt_merged_away(env, monkeypatch):
    # MQTT: both detectors said Person A, Double Take sent only the stronger one.
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a", 80.0, "a.jpg"))
    calls = []
    _patch_rest(monkeypatch, [[_row("a.jpg", {"det_cp": [_res("person a", 80.0)], "det_if": [_res("person a", 70.0)]})]],
                calls)
    asyncio.run(face_votes.enrich("ev1"))
    r = face_votes.reading("ev1")
    assert [n["ref"] for n in _det(r, "det_if")["names"]] == ["persona"]
    assert calls == [("http://dt.invalid:3000", 1)]


def test_rest_is_read_once_per_image(env, monkeypatch):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a", 80.0, "a.jpg"))
    calls = []
    _patch_rest(monkeypatch, [[_row("a.jpg", {"det_cp": [_res("person a")]})]], calls)
    asyncio.run(face_votes.enrich("ev1"))
    asyncio.run(face_votes.enrich("ev1"))
    assert len(calls) == 1


def test_rest_reads_at_most_two_pages(env, monkeypatch):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a", 80.0, "gone.jpg"))
    calls = []
    _patch_rest(monkeypatch, [[_row("x.jpg", {})], [_row("y.jpg", {})], [_row("gone.jpg", {})]], calls)
    asyncio.run(face_votes.enrich("ev1"))
    assert [p for _b, p in calls] == [1, 2]


def test_no_rest_url_means_no_read(env, monkeypatch, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: true}\n")
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a"))
    calls = []
    _patch_rest(monkeypatch, [[]], calls)
    asyncio.run(face_votes.enrich("ev1"))
    assert calls == [] and face_votes.reading("ev1")["names"][0]["ref"] == "persona"


def test_rest_failure_keeps_the_mqtt_reading(env, monkeypatch):
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a"))

    async def boom(base, page):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(face_votes, "_fetch_page", boom)
    asyncio.run(face_votes.enrich("ev1"))
    assert face_votes.reading("ev1")["names"][0]["ref"] == "persona"


# ── face-box sanity check ──────────────────────────────────────────────────


def test_torso_sized_face_no_other_detector_saw_is_not_a_vote(env, monkeypatch):
    # Measured (s29): a box > 0.40 of the image height where the other detector found no face = a torso.
    torso = (460, 300, 400, 500)  # height 500 of 1000
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b", 74.0, "t.jpg", torso))
    _patch_rest(monkeypatch, [[_row("t.jpg", {"det_cp": [_res("person b", 74.0, box=torso)], "det_if": []})]], [])
    asyncio.run(face_votes.enrich("ev1"))
    r = face_votes.reading("ev1")
    assert face_votes.single_vote(r) is None
    assert _det(r, "det_cp")["names"] == [] and _det(r, "det_cp")["discarded"] == 1
    assert _det(r, "det_if")["face_found"] is False


def test_large_face_the_other_detector_also_saw_is_kept(env, monkeypatch):
    close = (50, 700, 340, 570)  # a doorbell close-up: 0.57 of the image, both detectors see it
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a", 73.0, "c.jpg", close))
    _patch_rest(monkeypatch, [[_row("c.jpg", {"det_cp": [_res("person a", 73.0, box=close)],
                                              "det_if": [_res("person a", 70.0, box=close)]})]], [])
    asyncio.run(face_votes.enrich("ev1"))
    r = face_votes.reading("ev1")
    assert face_votes.single_vote(r)["ref"] == "persona" and _det(r, "det_cp")["discarded"] == 0


def test_with_one_detector_the_box_check_does_not_apply(env, monkeypatch):
    big = (50, 700, 340, 570)
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_cp", "person a", 73.0, "c.jpg", big))
    _patch_rest(monkeypatch, [[_row("c.jpg", {"det_cp": [_res("person a", 73.0, box=big)]})]], [])
    asyncio.run(face_votes.enrich("ev1"))
    assert face_votes.single_vote(face_votes.reading("ev1"))["ref"] == "persona"


# ── consent with two detectors ──────────────────────────────────────────────


def test_opted_out_person_named_by_one_detector_is_never_stored_by_name(env, tmp_path):
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    _words(tmp_path, {"det_if": {"label": "InsightFace", "trust": "likely"}, "det_cp": {"label": "CPAI", "trust": "unlikely"}})
    face_votes.on_message("double-take/matches/person a", _mqtt("ev1", "det_if", "person a", 60.0))
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b", 77.0))
    r = face_votes.reading("ev1")
    out = checks.fuse_second_sensor(frigate_ref="persona", frigate_face="Person A", frigate_status="unverified",
                                    votes=[], attributes={}, reading=r, profile_of=faces.attribute_profile)
    stored = face_votes.mask_for_storage(out, r)
    text = json.dumps(stored).casefold()
    assert "personb" not in text and "person b" not in text
    cp = next(d for d in stored["sensors"]["detectors"] if d["detector"] == "det_cp")
    assert cp["ref"] is None and cp["kind"] == "untracked"


def test_opted_out_person_named_by_both_detectors_stays_unnamed(env):
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_if", "person b"))
    face_votes.on_message("double-take/matches/person b", _mqtt("ev1", "det_cp", "person b", filename="b.jpg"))
    r = face_votes.reading("ev1")
    out = checks.fuse_second_sensor(frigate_ref=None, frigate_face=None, frigate_status="unverified", votes=[],
                                    attributes={}, reading={**r, "claim_source": "frigate"},
                                    profile_of=faces.attribute_profile)
    stored = face_votes.mask_for_storage(out, r)
    assert "personb" not in json.dumps(stored).casefold()
    assert stored["sensors"]["double_take_kind"] == "untracked"


# ── what Kåre and the GUI read ──────────────────────────────────────────────


def _event(sensors):
    return store.FrigateUserEvent(event_id="ev1", camera="inngang", label="person", start_ts=1.0,
                                  check={"sensors": sensors, "decision": "unverified"})


def test_sensor_line_names_each_detector(env):
    sensors = {"provider": "double_take", "relation": "disagree", "frigate": "persona", "frigate_kind": "household",
               "double_take": "personb", "double_take_kind": "household",
               "detectors": [{"detector": "det_if", "label": "InsightFace", "ref": "persona", "kind": "household",
                              "weight": 1.0, "split": False, "discarded": 0},
                             {"detector": "det_cp", "label": "CPAI", "ref": "personb", "kind": "household",
                              "weight": 0.3, "split": False, "discarded": 0}]}
    text = query.sensors_text(_event(sensors), {}, "en")
    assert text.startswith("Frigate: ") and "InsightFace: " in text and "CPAI: " in text
    assert "Double Take:" not in text


def test_sensor_line_hides_an_opted_out_detector_name(env):
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    sensors = {"provider": "double_take", "relation": "split", "frigate": "persona", "frigate_kind": "household",
               "detectors": [{"detector": "det_cp", "label": "CPAI", "ref": "personb", "kind": "household",
                              "weight": 1.0, "split": False, "discarded": 0}]}
    text = query.sensors_text(_event(sensors), {}, "en")
    assert "personb" not in text.casefold() and "CPAI: " in text
