"""J21b reader 6 — «Frigate saw …» in Kåre's situational context from frigate.db, with names only for who may get them.

- the 2026-11-12 case: a guest / unknown voice asking → the old block put Frigate's raw sub_label (a household name) into
  Kåre's context; now no name for them, the name only for a logged-in household person;
- never an opted-out person's name (untracked, or hidden since); never a still thing the stationary gate held back;
- one line per camera + label (the newest), newest first, at most three, the camera's display name;
- module 0 off → no Frigate lines at all (no fallback to the raw MQTT log).
No live state: frigate.db, users.db, yaml and the MQTT log point at tmp_path. Placeholders only."""

import json

import pytest

from kaare_core import context_builder as cb
from kaare_core.domain import frigate_store as camera_cards
from kaare_core.frigate_user import query, recent
from kaare_core.storage import rawlog
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    CAMS,
    NOW,
    H,
    _ev,
    _sensors,
    _store,
    env,
)

M = 60


@pytest.fixture
def ctx(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    monkeypatch.setattr(camera_cards, "camera_config", lambda: {"cameras": CAMS})
    monkeypatch.setattr(recent.time, "time", lambda: NOW)
    for block in ("_ha_state_block", "_zigbee_block", "_media_block"):
        monkeypatch.setattr(cb, block, lambda lang: "")
    monkeypatch.setattr(cb, "_recent_node_activity", lambda *a, **k: [])
    monkeypatch.setattr(cb, "is_away", lambda: False)
    mqtt = env / "frigate_mqtt.log"  # what the old block read: Frigate's raw name, no consent gate
    mqtt.write_text(json.dumps({"stage": "detection_end", "ts": "2999-01-01T00:00:00+00:00", "camera": "cam_door",
                                "label": "person", "score": 0.9, "sub_label": "Person A"}) + "\n")
    monkeypatch.setitem(rawlog.LEGACY, "frigate_mqtt", mqtt)  # P150: read through rawlog (the old single file)
    return env


def _named(eid, start, ref="user_a", camera="cam_door"):
    face = {"user_a": "Person A", "user_b": "Person B"}[ref]
    e = _ev(eid, start, ref=ref, claim="confirmed", camera=camera, sensors=_sensors(face, [("det_x", ref)], "agree"))
    e.end_ts = start + 20
    return e


def test_guest_on_voice_gets_no_name_household_person_does(ctx):
    _store(_named("a1", NOW - 5 * M))
    for caller in ("unknown_user", "global_user", "nobody"):
        text = cb.build_situational_context(caller, "nb")
        assert "Person A" not in text and "USER_A" not in text and "user_a" not in text, caller
        assert "person ved Inngang" in text
    assert "USER_A (person) ved Inngang" in cb.build_situational_context("user_b", "nb")


def test_opted_out_and_held_back_and_dedupe(ctx):
    held = _ev("bin", NOW - 2 * M, ref=None, kind="unknown", label="car", camera="cam_yard")
    held.check = {**held.check, "drive": {"selected": False, "why": "unchanged_stationary"}}
    _store(_named("b1", NOW - 3 * M, ref="user_b"), held, _named("a_old", NOW - 20 * M), _named("a_new", NOW - 4 * M))
    ustore.set_frigate_tracking("user_b", False)
    frigate_consent.invalidate()
    rows = recent.recent("user_a")
    assert [r["label"] for r in rows] == ["person"]  # the held-back car is out; one person row per camera+label
    assert rows[0]["name"] is None and rows[0]["minutes_ago"] == 2  # user_b (newest) opted out since → no name


def test_module_off_means_no_frigate_lines(ctx, monkeypatch):
    _store(_named("a1", NOW - 5 * M))
    monkeypatch.setattr(query, "available", lambda: False)
    text = cb.build_situational_context("user_b", "nb")
    assert "Inngang" not in text and "Person A" not in text
