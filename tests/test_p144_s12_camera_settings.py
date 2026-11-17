"""P144-s12: the camera settings (Integrations → Frigate, per camera: ticked objects + min. confidence) decide which
Frigate events make episodes and get the models in the Frigate user's shadow pipe; the rest is stored as a plain event.
Plus the recording frame as snapshot fallback, and licence plates as vehicle claims (Part B).

- a ticked label at/above its min_confidence drives; an unticked label or one below the threshold is stored, gets no
  episode (not even an open one) and no model call; the road camera never opens an episode even with a tick;
- a camera missing from the config keeps the old behaviour; a configured camera without the label = not ticked (as
  the GUI shows it, and as the old responder reads it);
- fire/smoke and not-welcome faces are never filtered out;
- a short person event without a Frigate snapshot gets a recording frame for L3;
- plates: stored as a claim (known name / raw plate), never as a person; a plate event joins an open episode but never
  opens one; unknown plates are dropped after the unclassified-name days; schema v4.
Never touches live state: frigate.db / users.db / configs point at tmp_path (s06a's fixture); camera config is faked.
"""

import asyncio
import json
import logging
import sqlite3

import pytest

from adapters import frigate_adapter, mqtt_adapter
from kaare_core.frigate_user import conclude, pipe
from kaare_core.frigate_user import store as fstore
from tests import test_p144_s06a_pipe as s06a

T0 = s06a.T0
REAL_LOAD_SNAPSHOT = pipe._load_snapshot  # s06a's fixture fakes it; the fallback tests need the real one

env = s06a.env  # tmp DBs/configs, fake Frigate REST + LLM, fake urgent handler

WALK = [[[0.2, 0.5], T0], [[0.7, 0.5], T0 + 5]]  # moves half the frame


def _label(analyze, conf=0.65):
    return {"analyze": analyze, "min_confidence": conf, "announce": False}


CAMERAS = {
    "inngang": {"role": "front_door", "labels": {"person": _label(True), "car": _label(False), "deer": _label(True),
                                                  "fire": _label(False)}},
    "innkjorsel": {"role": "driveway", "labels": {"person": _label(True), "car": _label(False),
                                                  "license_plate": _label(False)}},
    "veg": {"role": "road_facing", "labels": {"person": _label(False), "car": _label(False),
                                              "license_plate": _label(True)}},
}


@pytest.fixture
def cams(env, monkeypatch):
    monkeypatch.setattr(pipe.frigate_store, "camera_config", lambda: {"cameras": CAMERAS})
    return env


def _ev(event_id, camera="inngang", label="person", score=0.9, path=WALK, **kw):
    ev = s06a._event(event_id, camera=camera, label=label)
    ev.update(score=score, top_score=score, motion={"stationary": False, "path_data": path}, **kw)
    return ev


# ── rule 1: the ticks + min_confidence decide ───────────────────────────────────


def test_unticked_label_is_stored_without_episode_or_model(cams):
    s06a._process(_ev("c1", label="car"))
    e = s06a._stored("c1")
    assert e.episode_id is None and e.drives is False
    assert e.check["drive"] == {"selected": False, "why": "label_not_selected"}
    assert cams["llm_calls"] == []


def test_below_min_confidence_is_stored_without_episode_or_model(cams):
    s06a._process(_ev("p1", score=0.5))
    e = s06a._stored("p1")
    assert e.episode_id is None and e.check["drive"]["why"] == "below_min_confidence"
    assert cams["llm_calls"] == []


def test_ticked_label_above_threshold_drives_and_gets_the_model(cams):
    s06a._process(_ev("p2", score=0.9))
    e = s06a._stored("p2")
    assert e.episode_id is not None and e.drives is True
    assert e.check["drive"] == {"selected": True, "why": "selected"}
    assert len(cams["llm_calls"]) == 1


def test_threshold_is_inclusive(cams):
    s06a._process(_ev("p3", score=0.65))
    assert s06a._stored("p3").episode_id is not None


def test_road_camera_with_plate_ticked_does_not_open(cams):
    s06a._process(_ev("r1", camera="veg", label="license_plate"))
    e = s06a._stored("r1")
    assert e.episode_id is None and e.drives is False
    assert e.check["drive"] == {"selected": True, "why": "episodes_off"}  # P144-s14 (was «ignored_role»)


def test_unselected_event_does_not_join_an_open_episode(cams):
    s06a._process(_ev("p4"))
    opened = s06a._stored("p4").episode_id
    assert opened is not None
    s06a._process(_ev("c2", label="car"))  # same time, same place — but not ticked
    assert s06a._stored("c2").episode_id is None
    assert fstore.episode(opened).event_count == 1


def test_camera_missing_from_config_keeps_old_behaviour(cams, caplog):
    caplog.set_level(logging.INFO, logger=pipe.log.name)
    s06a._process(_ev("x1", camera="ukjent", label="car"))
    e = s06a._stored("x1")
    assert e.episode_id is not None and e.drives is True  # a moving car opened, as before s12
    assert e.check["drive"] == {"selected": True, "why": "no_camera_config"}
    assert "no_camera_config" in caplog.text


def test_camera_without_labels_keeps_old_behaviour(cams, monkeypatch):
    monkeypatch.setattr(pipe.frigate_store, "camera_config", lambda: {"cameras": {"inngang": {"role": "front_door"}}})
    s06a._process(_ev("x2", label="car"))
    assert s06a._stored("x2").episode_id is not None


def test_configured_camera_without_the_label_is_not_ticked(cams):
    s06a._process(_ev("b1", label="bird"))  # the GUI shows a missing label unticked; the old responder skips it too
    e = s06a._stored("b1")
    assert e.episode_id is None and e.check["drive"]["why"] == "label_not_selected"


def test_broken_camera_config_never_crashes(cams, monkeypatch):
    def boom():
        raise RuntimeError("yaml")
    monkeypatch.setattr(pipe.frigate_store, "camera_config", boom)
    s06a._process(_ev("x3"))
    assert s06a._stored("x3").check["drive"]["why"] == "no_camera_config"


def test_fire_is_never_filtered(cams):
    s06a._process(_ev("f1", label="fire", score=0.3))  # unticked and below threshold
    e = s06a._stored("f1")
    assert e.episode_id is not None and e.check["drive"] == {"selected": True, "why": "urgent_label"}


def test_not_welcome_face_is_never_filtered(cams, tmp_path):
    (tmp_path / "frigate_faces.yaml").write_text("faces:\n  Person X: {class: not_welcome}\n")
    s06a._process(_ev("n1", camera="veg", label="person"), "Person X", 0.9)  # person unticked on the road camera
    e = s06a._stored("n1")
    assert e.check["drive"] == {"selected": True, "why": "not_welcome"}


def test_face_claim_on_an_unticked_camera_is_still_checked(cams, tmp_path):
    # a claimed name is checked before it may be stored — never skipped to save a call
    s06a._process(_ev("n2", camera="veg", label="person"), "Person A", 0.95)
    e = s06a._stored("n2")
    assert e.episode_id is None and e.check["drive"]["why"] == "label_not_selected"
    assert len(cams["llm_calls"]) == 1 and "l3" in e.check


# ── item 3: recording frame as snapshot fallback ────────────────────────────────


@pytest.fixture
def frames(cams, monkeypatch, tmp_path):
    state = {"snapshot_calls": 0, "recording_calls": []}

    async def no_snapshot(event_id, height=None):  # P144-s25: the pipe asks for a capped height
        state["snapshot_calls"] += 1
        raise RuntimeError("404")

    async def recording(camera, ts, height=480):
        state["recording_calls"].append((camera, ts, height))
        return s06a._jpeg()

    monkeypatch.setattr(pipe, "_load_snapshot", REAL_LOAD_SNAPSHOT)
    monkeypatch.setattr(pipe.frigate_store, "SNAPSHOT_DIR", tmp_path / "snaps")
    monkeypatch.setattr(pipe.frigate_adapter, "fetch_event_snapshot", no_snapshot)
    monkeypatch.setattr(pipe.frigate_adapter, "fetch_recording_snapshot", recording)
    monkeypatch.setattr(pipe, "_SNAPSHOT_RETRY_S", 0)
    monkeypatch.setattr(pipe, "_RECORDING_RETRY_S", 0)
    return state


def test_short_person_event_without_snapshot_gets_a_recording_frame(frames, cams):
    s06a._process(_ev("s1", has_snapshot=False), "Person A", 0.95)
    assert frames["snapshot_calls"] == 0  # Frigate said it has none — no 2 s retry for nothing
    assert len(frames["recording_calls"]) == 1
    camera, ts, height = frames["recording_calls"][0]
    assert camera == "inngang" and T0 <= ts <= T0 + 10 and height == 480
    assert len(cams["llm_calls"]) == 1 and cams["llm_calls"][0]["images"]
    e = s06a._stored("s1")
    assert e.check["image"] == "recording" and "l3" in e.check


def test_missing_snapshot_falls_back_too(frames, cams):
    s06a._process(_ev("s2"))  # has_snapshot True, but Frigate answers 404
    assert frames["snapshot_calls"] == 2 and len(frames["recording_calls"]) == 1
    assert s06a._stored("s2").check["image"] == "recording"


def test_no_recording_frame_for_an_unselected_event_without_claim(frames, cams):
    s06a._process(_ev("s3", camera="veg", has_snapshot=False))
    assert frames["recording_calls"] == [] and cams["llm_calls"] == []


def test_snapshot_still_wins(cams):
    s06a._process(_ev("s4"))
    assert s06a._stored("s4").check["image"] == "snapshot"


# ── Part B: plates as vehicle claims ────────────────────────────────────────────


@pytest.fixture
def capture(monkeypatch):
    other, frigate_user = [], []

    async def other_hook(ev):
        other.append(ev)

    async def frigate_user_hook(ev, raw_name, raw_score):
        frigate_user.append((ev, raw_name))

    monkeypatch.setattr(mqtt_adapter, "_write_event_log", lambda *a, **kw: None)
    monkeypatch.setattr(mqtt_adapter, "_END_EVENT_HOOKS", [other_hook])
    monkeypatch.setattr(mqtt_adapter, "_FRIGATE_USER_HOOKS", [frigate_user_hook])
    return other, frigate_user


@pytest.mark.parametrize("raw", ["CE7434", ["CE7434", 0.998]])
def test_plate_reaches_the_frigate_user_hook_only(capture, raw):
    other, frigate_user = capture
    before = {"id": "1800000000.1-abc", "camera": "veg", "label": "car", "top_score": 0.9, "start_time": 1.0,
              "end_time": 5.0, "has_snapshot": True, "sub_label": ["Tilhenger", 0.99],
              "recognized_license_plate": raw, "recognized_license_plate_score": 0.998}

    async def main():
        await mqtt_adapter._process_frigate_event(json.dumps({"type": "end", "before": before, "after": before}).encode())
        await asyncio.sleep(0)

    asyncio.run(main())
    ev, raw_name = frigate_user[0]
    assert ev["plate"] == {"plate": "CE7434", "score": 0.998} and raw_name == "Tilhenger"
    assert "plate" not in other[0]


def _rest_with_plate(monkeypatch, env, plate, score=0.99):
    async def rest(event_id):
        return {"start_time": T0, "end_time": T0 + 10,
                "data": {"attributes": env["face"], "path_data": WALK, "recognized_license_plate": plate,
                         "recognized_license_plate_score": score}}
    monkeypatch.setattr(pipe.frigate_adapter, "get_event", rest)


def test_known_plate_is_a_claim_never_a_person(cams, monkeypatch):
    _rest_with_plate(monkeypatch, cams, "CE7434")
    s06a._process(_ev("k1", camera="innkjorsel", label="car"), "Toyota Auris", 0.99)
    e = s06a._stored("k1")
    assert e.plate_claim == "Toyota Auris" and e.plate_known is True
    assert e.person_ref is None and e.person_kind == "unknown"


def test_unknown_plate_keeps_the_raw_plate(cams, monkeypatch):
    _rest_with_plate(monkeypatch, cams, "AB12345")
    s06a._process(_ev("k2", camera="innkjorsel", label="car"))
    e = s06a._stored("k2")
    assert e.plate_claim == "AB12345" and e.plate_known is False


def test_sub_label_without_a_plate_is_not_a_plate(cams):
    s06a._process(_ev("k3", camera="innkjorsel", label="car"), "something", 0.9)
    e = s06a._stored("k3")
    assert e.plate_claim is None and e.plate_known is None


def test_mqtt_plate_used_when_rest_is_missing(cams, monkeypatch):
    async def no_rest(event_id):
        raise RuntimeError("down")
    monkeypatch.setattr(pipe.frigate_adapter, "get_event", no_rest)
    s06a._process(_ev("k4", camera="innkjorsel", label="car", plate={"plate": "CE7434", "score": 0.99}),
                  "Tilhenger", 0.99)
    assert s06a._stored("k4").plate_claim == "Tilhenger"


def test_plate_event_joins_an_open_episode_but_never_opens_one(cams, monkeypatch):
    _rest_with_plate(monkeypatch, cams, "CE7434")
    s06a._process(_ev("k5", camera="innkjorsel", label="car"), "Toyota Auris", 0.99)
    alone = s06a._stored("k5")
    assert alone.episode_id is None and alone.check["drive"]["why"] == "plate_join_only"

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", s06a_rest(cams))
    s06a._process(_ev("p9"))  # a person opens an episode
    opened = s06a._stored("p9").episode_id
    _rest_with_plate(monkeypatch, cams, "CE7434")
    s06a._process(_ev("k6", camera="innkjorsel", label="car"), "Toyota Auris", 0.99)
    k6 = s06a._stored("k6")
    assert k6.episode_id == opened and k6.drives is False


def s06a_rest(env):
    async def rest(event_id):
        return {"start_time": T0, "end_time": T0 + 10, "data": {"attributes": env["face"], "path_data": WALK}}
    return rest


def test_unknown_plates_expire_known_plates_stay(cams, monkeypatch):
    _rest_with_plate(monkeypatch, cams, "AB12345")
    s06a._process(_ev("k7", camera="innkjorsel", label="car"))
    _rest_with_plate(monkeypatch, cams, "CE7434")
    s06a._process(_ev("k8", camera="innkjorsel", label="car"), "Toyota Auris", 0.99)
    assert fstore.expire_unknown_plates(days=7, now=T0 + 6 * 86400) == 0
    assert fstore.expire_unknown_plates(days=7, now=T0 + 8 * 86400) == 1
    k7, k8 = s06a._stored("k7"), s06a._stored("k8")
    assert k7.plate_claim is None and k7.plate_known is False  # «unknown vehicle»: the fact a plate was read stays
    assert k8.plate_claim == "Toyota Auris"


def test_conclusion_prompt_names_a_known_plate_and_hides_an_unknown(cams):
    known = fstore.FrigateUserEvent(event_id="a", camera="innkjorsel", label="car", start_ts=T0,
                                    plate_claim="Toyota Auris", plate_known=True)
    unknown = fstore.FrigateUserEvent(event_id="b", camera="innkjorsel", label="car", start_ts=T0,
                                      plate_claim="AB12345", plate_known=False)
    for lang in ("nb", "en", "de"):
        k, u = conclude._event_line(known, lang), conclude._event_line(unknown, lang)
        assert "Toyota Auris" in k
        assert "AB12345" not in u and u != conclude._event_line(
            fstore.FrigateUserEvent(event_id="c", camera="innkjorsel", label="car", start_ts=T0), lang)


def test_migration_v4_from_v3(tmp_path, monkeypatch):
    path = tmp_path / "frigate" / "frigate.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    for v in (1, 2, 3):
        conn.executescript(fstore._MIGRATIONS[v])
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '3')")
    conn.execute("INSERT INTO events (event_id, camera, label, start_ts, person_kind, claim_status, created_ts, "
                 "updated_ts) VALUES ('old', 'veg', 'car', 1, 'unknown', 'none', 1, 1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(fstore, "DB_PATH", path)
    fstore.reset_for_tests()
    try:
        assert fstore.schema_version() == fstore.SCHEMA_VERSION  # v3 → v4 and on (s07 added v5)
        old = next(e for e in fstore.events_between(0, 2) if e.event_id == "old")
        assert old.plate_claim is None and old.plate_known is None
    finally:
        fstore.reset_for_tests()


# ── item 5: Frigate's camera type (LPR) — read from Frigate, never a KTSB setting ──────────────────────────────

FRIGATE_CONFIG = {"cameras": {"veg": {"type": "lpr"}, "inngang": {"type": "generic"}, "gammel": {}}}


def test_camera_types_from_frigate_config():
    assert frigate_adapter.camera_types_from_config(FRIGATE_CONFIG) == {"veg": "lpr", "inngang": "generic"}
    assert frigate_adapter.camera_types_from_config({"cameras": None}) == {}


def test_camera_types_cached_and_frigate_absent_never_breaks(monkeypatch):
    monkeypatch.setattr(frigate_adapter, "_camera_types", {})
    monkeypatch.setattr(frigate_adapter, "_camera_types_ts", 0.0)

    class Down:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            raise RuntimeError("frigate down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(frigate_adapter.httpx, "AsyncClient", Down)
    assert asyncio.run(frigate_adapter.camera_types()) == {}
    frigate_adapter.camera_types_from_config(FRIGATE_CONFIG)  # the camera settings GET refreshed it
    assert asyncio.run(frigate_adapter.camera_types())["veg"] == "lpr"  # served from the cache, Frigate still down
