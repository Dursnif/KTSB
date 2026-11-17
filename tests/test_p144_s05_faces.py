"""Face classification accessor (P144-s05): kaare_core/frigate_user/faces.py on temporary files.
Unknown → unclassified, never raises, shipped template has no faces, bad values refused."""

from pathlib import Path

import pytest
import yaml

from kaare_core import config_migrations
from kaare_core.frigate_user import faces

TEMPLATE = Path("/kaare/configs_default/frigate_faces.yaml")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "frigate_faces.yaml"
    monkeypatch.setattr(faces, "CONFIG_PATH", p)
    return p


def _write(p, data):
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_template_ships_empty():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert data["faces"] == {}
    assert data["unclassified_name_days"] == 7


def test_faces_is_a_user_collection_for_migration():
    assert "faces" in config_migrations.USER_COLLECTIONS["frigate_faces.yaml"]


def test_missing_file_is_unclassified_and_empty_profile(cfg):
    assert faces.classify("Person A") == "unclassified"
    assert faces.attribute_profile("Person A") == {
        "beard": "unknown", "glasses": "unknown", "age_band": "unknown", "headwear_often": "unknown"
    }
    assert faces.unclassified_name_days() == 7


@pytest.mark.parametrize("name", [None, "", "Person Z", 42])
def test_unknown_or_odd_names_never_raise(cfg, name):
    _write(cfg, {"faces": {"Person A": {"class": "household"}}})
    assert faces.classify(name) == "unclassified"
    assert faces.attribute_profile(name)["beard"] == "unknown"


def test_broken_file_never_raises(cfg):
    cfg.write_text("faces: [unclosed\n", encoding="utf-8")
    assert faces.classify("Person A") == "unclassified"
    assert faces.unclassified_faces(["Person A"]) == ["Person A"]


def test_classify_and_profile_case_insensitive(cfg):
    _write(cfg, {"faces": {"Person A": {"class": "not_welcome", "attributes": {"beard": "yes", "age_band": "adult"}}}})
    assert faces.classify("person a") == "not_welcome"
    prof = faces.attribute_profile("PERSON A")
    assert prof["beard"] == "yes" and prof["age_band"] == "adult" and prof["glasses"] == "unknown"


def test_hand_edited_yaml_booleans_read_as_yes_no(cfg):
    cfg.write_text("faces:\n  Person A:\n    attributes:\n      beard: yes\n      headwear_often: no\n", encoding="utf-8")
    prof = faces.attribute_profile("Person A")
    assert prof["beard"] == "yes" and prof["headwear_often"] == "no"


def test_invalid_stored_values_fall_back(cfg):
    _write(cfg, {"faces": {"Person A": {"class": "boss", "attributes": {"beard": "maybe", "glasses": 3}}}})
    assert faces.classify("Person A") == "unclassified"
    assert faces.attribute_profile("Person A")["beard"] == "unknown"


def test_unclassified_faces_skips_classified_and_train(cfg):
    _write(cfg, {"faces": {"Person A": {"class": "household"}, "Person B": {"attributes": {"beard": "no"}}}})
    assert faces.unclassified_faces(["Person A", "Person B", "Person C", "train"]) == ["Person B", "Person C"]


def test_set_face_round_trip_and_reload(cfg):
    faces.set_face("Person A", "safe_guest", {"glasses": "sometimes"})
    assert faces.classify("Person A") == "safe_guest"
    assert faces.attribute_profile("Person A")["glasses"] == "sometimes"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["faces"]["Person A"]["attributes"]["glasses"] == "sometimes"
    faces.set_face("Person A", "safe_guest", {"glasses": "yes", "beard": "yes"})  # «new glasses»
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["faces"]["Person A"]["attributes"]["beard"] == "yes"  # stored as the string, not a boolean


def test_set_face_back_to_nothing_removes_entry(cfg):
    faces.set_face("Person A", "household", {"beard": "no"})
    faces.set_face("Person A", "unclassified", {})
    assert "Person A" not in (yaml.safe_load(cfg.read_text(encoding="utf-8"))["faces"] or {})


@pytest.mark.parametrize("cls,attrs", [
    ("friend", {}),
    ("household", {"beard": "maybe"}),
    ("household", {"glasses": "sometimes", "age_band": "toddler"}),
    ("household", {"shoe_size": "42"}),
    ("household", {"beard": True}),
])
def test_set_face_rejects_values_outside_the_sets(cfg, cls, attrs):
    with pytest.raises(ValueError):
        faces.set_face("Person A", cls, attrs)
    assert not cfg.exists()


@pytest.mark.parametrize("name", ["", "   ", "a/b", "..", "x" * 200])
def test_set_face_rejects_bad_names(cfg, name):
    with pytest.raises(ValueError):
        faces.set_face(name, "household", {})


def test_unclassified_name_days(cfg):
    faces.set_unclassified_name_days(14)
    assert faces.unclassified_name_days() == 14
    for bad in (-1, 91, "7", True):
        with pytest.raises(ValueError):
            faces.set_unclassified_name_days(bad)
