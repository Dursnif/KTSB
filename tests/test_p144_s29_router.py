"""P144-s29: the episode detail's sensor line carries one vote per detector by the user's label, and a person who has
since opted out is never shown by name there. Pure function; no live DB (hidden names are passed in)."""

from kaare_core.frigate_user import face_votes, store
from kaare_core.routers import router_frigate_user as r


def _check():
    return {"review": True, "sensors": {
        "provider": "double_take", "relation": "disagree", "frigate": None, "frigate_kind": None,
        "double_take": "persona", "double_take_kind": "household",
        "detectors": [{"detector": "det_if", "label": "InsightFace", "ref": "persona", "kind": "household",
                       "weight": 1.0, "split": False, "discarded": 0},
                      {"detector": "det_cp", "label": "CPAI", "ref": "personb", "kind": "household",
                       "weight": 0.3, "split": False, "discarded": 1}]}}


def test_each_detector_is_listed_by_label(monkeypatch):
    monkeypatch.setattr(r, "_person_name", lambda ref, kind: ref.title())
    out = r._sensors_summary(_check(), set())
    assert [(d["label"], d["name"]) for d in out["detectors"]] == [("InsightFace", "Persona"), ("CPAI", "Personb")]
    assert out["detectors"][1]["discarded"] == 1


def test_an_opted_out_detector_name_is_never_shown(monkeypatch):
    monkeypatch.setattr(r, "_person_name", lambda ref, kind: ref.title())
    out = r._sensors_summary(_check(), {"personb"})
    cp = out["detectors"][1]
    assert cp["name"] is None and cp["kind"] == "untracked"
    assert "personb" not in str(out).casefold()


# ── P144-s29b: the «Ansiktssensorer» card's rows ────────────────────────────


def _ev(sensors):
    return store.FrigateUserEvent(event_id="e", camera="inngang", label="person", start_ts=1.0,
                                  check={"sensors": sensors})


def test_sensor_rows_are_generic_and_carry_no_names(monkeypatch):
    monkeypatch.setattr(face_votes, "status", lambda: {"detectors": [
        {"detector": "made_up_det", "label": "made_up_det", "configured": False, "last_seen_ts": 5.0}]})
    monkeypatch.setattr(r.pipe_settings, "face_sensors", lambda: {
        "frigate": {"mode": "admin", "trust": "likely", "label": ""}, "detectors": {}})
    events = [
        _ev({"relation": "agree", "frigate": "persona", "detectors": [
            {"detector": "made_up_det", "ref": "persona", "kind": "household"}]}),
        _ev({"relation": "disagree", "frigate": "persona", "detectors": [
            {"detector": "made_up_det", "ref": "personb", "kind": "household"}]}),
    ]
    monkeypatch.setattr(r.fstore, "events_between", lambda *a, **k: events)
    rows = r._face_sensor_rows(now=10.0)
    by = {x["sensor"]: x for x in rows}
    assert set(by) == {"frigate", "made_up_det"}
    assert by["frigate"]["mode"] == "admin" and by["frigate"]["trust"] == "likely"
    assert by["made_up_det"]["mode"] == "auto" and by["made_up_det"]["learned"] is None
    assert by["made_up_det"]["counts"] == {"votes": 2, "agree": 1, "disagree": 1}
    assert by["frigate"]["counts"] == {"votes": 2, "agree": 1, "disagree": 1}
    assert "persona" not in str(rows) and "personb" not in str(rows)
