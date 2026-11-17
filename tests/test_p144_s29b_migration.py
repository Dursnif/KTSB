"""P144-s29b: the config migration step that turns s29's 0–1 detector weights into the sensor words. Pure dict tests —
no files of the house are read or written."""

from kaare_core import config_migrations as cm


def _step():
    return next(s for s in cm.STEPS if s.name == "face_sensor_words")


def test_a_set_weight_becomes_the_nearest_word_and_admin_mode():
    data = {"face_votes": {"enabled": True, "default_weight": 1.0,
                           "detectors": {"det_if": {"label": "InsightFace", "weight": 1.0},
                                         "det_cp": {"label": "CPAI", "weight": 0.3},
                                         "det_x": {"label": "", "weight": 0.0}}}}
    lines = _step().fn(data, {})
    d = data["face_sensors"]["detectors"]
    assert d["det_if"] == {"mode": "admin", "trust": "very_likely", "label": "InsightFace"}
    assert d["det_cp"] == {"mode": "admin", "trust": "unlikely", "label": "CPAI"}
    assert d["det_x"]["trust"] == "off"
    assert lines and _step().filename == "frigate_user.yaml"
    assert data["face_votes"]["detectors"]["det_cp"]["weight"] == 0.3  # the old keys stay (never deleted by a migration)


def test_nothing_set_writes_nothing():
    data = {"face_votes": {"enabled": True, "default_weight": 1.0, "detectors": {}}}
    assert _step().fn(data, {}) == [] and "face_sensors" not in data


def test_an_admin_word_already_there_is_never_overwritten():
    data = {"face_votes": {"detectors": {"det_cp": {"label": "CPAI", "weight": 1.0}}},
            "face_sensors": {"detectors": {"det_cp": {"mode": "admin", "trust": "unlikely", "label": "CPAI"}}}}
    _step().fn(data, {})
    assert data["face_sensors"]["detectors"]["det_cp"]["trust"] == "unlikely"


def test_it_is_step_11_and_the_steps_are_numbered_in_order():
    # Changed in DT04 R0.8: was «it is the latest step» (number == cm.LATEST) — true only until the next step was added
    # (step 12, cors_same_host). What matters: this step keeps its number and the list has no gap.
    assert _step().number == 11
    assert [s.number for s in cm.STEPS] == list(range(1, cm.LATEST + 1))
