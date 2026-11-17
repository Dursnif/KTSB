"""P144-s06d Part 1 — episodes that actually close (P144 bug «episodes never close on this house»).

- a camera whose role is in `episode_ignore_roles` (default road_facing) never opens or extends an episode; its
  events are stored and may join an episode another camera opened;
- a vehicle (or anything Frigate calls stationary) drives an episode only with a real moving burst in its path_data;
  a 42 h «parked, then drove off» event counts only its drive-off;
- people/animals that move a little still open an episode; unknown movement (no path) keeps the old behaviour;
- schema v2 migrates a v1 file; the MQTT payload's motion fields reach the Frigate user's hook only.
Never touches live state: frigate.db / users.db / frigate_user.yaml point at tmp_path; no live logs.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from adapters import mqtt_adapter
from kaare_core.config_migrations import fill_missing_keys
from kaare_core.frigate_user import pipe
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests import test_p144_s06a_pipe as s06a

T0 = 1_800_000_000.0
G = 120


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _path(*points):
    """Frigate path_data: [[[x, y], ts], …] from (x, y, ts) tuples."""
    return [[[x, y], ts] for x, y, ts in points]


WALK = _path((0.2, 0.5, T0), (0.4, 0.5, T0 + 3), (0.6, 0.5, T0 + 6))  # moves 40 % of the frame
JITTER = _path((0.50, 0.3, T0), (0.52, 0.3, T0 + 2))  # a parked car's box hop


def _ev(event_id, *, camera="inngang", label="person", start=T0, end=None, path=None, role=None,
        stationary=None, **kw):
    return fstore.FrigateUserEvent(
        event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 10 if end is None else end,
        person_kind="unknown", claim_status="none", path_data=path, camera_role=role, stationary=stationary,
        # what the pipe sets since P144-s14: the camera card's switch, here its role's default
        camera_episodes=None if role is None else fsettings.camera_makes_episodes({"role": role}), **kw)


def _get(event_id):
    return next(e for e in fstore.events_between(0, T0 * 3) if e.event_id == event_id)


# ── settings ────────────────────────────────────────────────────────────────────


def test_settings_defaults(db):
    s = fsettings.get_settings()
    assert "episode_ignore_roles" not in s  # retired in P144-s14: «Kan lage episoder» per camera card
    assert s["episode_min_movement"] == 0.08


def test_settings_update_validates(db):
    assert fsettings.update({"episode_min_movement": 0.15})["episode_min_movement"] == 0.15
    with pytest.raises(ValueError):  # retired in P144-s14
        fsettings.update({"episode_ignore_roles": ["road_facing"]})
    with pytest.raises(ValueError):
        fsettings.update({"episode_min_movement": 2})


def test_template_has_the_new_keys_and_switch_on():
    tpl = yaml.safe_load(Path("/kaare/configs_default/frigate_user.yaml").read_text(encoding="utf-8"))
    assert "episode_ignore_roles" not in tpl and tpl["episode_min_movement"] == 0.08  # retired in P144-s14
    assert tpl["enabled"] is True  # DT04 R0.6b: ships on (heavy modules off); was False


# ── the episode rule ────────────────────────────────────────────────────────────


def test_road_facing_event_does_not_open_an_episode(db):
    assert fstore.record_event(_ev("road", camera="cam_road", label="person", path=WALK, role="road_facing"))
    assert _get("road").episode_id is None
    assert fstore.episodes_between(0, T0 * 3) == []


def test_road_facing_event_joins_an_episode_another_camera_opened(db):
    fstore.record_event(_ev("door", path=WALK, role="front_door"))
    fstore.record_event(_ev("road", camera="cam_road", label="car", start=T0 + 5, path=WALK, role="road_facing"))
    door, road = _get("door"), _get("road")
    assert road.episode_id == door.episode_id
    ep = fstore.episode(door.episode_id)
    assert ep.end_ts == pytest.approx(T0 + 6)  # the road car did not extend it (its walk ends at T0 + 6 too)
    assert ep.event_count == 2


def test_stationary_car_does_not_extend_an_episode(db):
    fstore.record_event(_ev("door", path=WALK, role="front_door"))
    # a parked car flickers back in 100 s later and stays 30 min: joins, but must not keep the episode open
    fstore.record_event(_ev("car", camera="park", label="car", start=T0 + 100, end=T0 + 1900,
                            path=_path((0.50, 0.3, T0 + 100), (0.52, 0.3, T0 + 1800)), role="driveway"))
    ep = fstore.episode(_get("door").episode_id)
    assert ep.end_ts == pytest.approx(T0 + 6)
    assert [e.episode_id for e in fstore.close_episodes(now=T0 + 6 + G + 1, gap_s=G)] == [ep.episode_id]


def test_stationary_flag_alone_never_opens(db):
    fstore.record_event(_ev("still", label="person", path=JITTER, stationary=True, role="front_door"))
    assert _get("still").episode_id is None


def test_parked_42h_car_counts_only_its_drive_off(db):
    start, leave = T0, T0 + 42 * 3600
    path = _path((0.75, 0.28, start), (0.40, 0.30, start + 6),          # arrives
                 (0.30, 0.32, start + 30000), (0.38, 0.32, start + 30001),  # box hops while parked
                 (0.38, 0.30, leave), (0.70, 0.28, leave + 4), (0.95, 0.42, leave + 6))  # drives off
    fstore.record_event(_ev("car42", camera="park", label="car", start=start, end=leave + 7, path=path,
                            role="driveway"))
    ep = fstore.episode(_get("car42").episode_id)
    assert ep.start_ts == pytest.approx(leave)  # not 42 h earlier
    assert ep.end_ts - ep.start_ts < 60


def test_person_with_a_short_path_still_opens(db):
    fstore.record_event(_ev("cat", label="cat", start=T0, end=T0 + 2,
                            path=_path((0.3, 0.3, T0), (0.33, 0.3, T0 + 2)), role="driveway"))
    assert _get("cat").episode_id is not None


def test_unknown_movement_keeps_old_behaviour(db):
    fstore.record_event(_ev("a", label="car"))  # no path, no role, no stationary flag: as before s06d
    assert _get("a").episode_id is not None


def test_episode_closes_after_gap_of_real_activity(db):
    fstore.record_event(_ev("p1", path=WALK, role="front_door"))
    for i in range(5):  # road traffic every 60 s for 5 minutes
        fstore.record_event(_ev(f"r{i}", camera="cam_road", label="car", start=T0 + 60 * (i + 1),
                                path=WALK, role="road_facing"))
    closed = fstore.close_episodes(now=T0 + 6 + G + 1, gap_s=G)
    assert len(closed) == 1 and closed[0].end_ts == pytest.approx(T0 + 6)


def test_activity_window_is_stored(db):
    fstore.record_event(_ev("p", path=WALK, role="front_door"))
    e = _get("p")
    assert e.drives is True and e.active_start == pytest.approx(T0) and e.active_end == pytest.approx(T0 + 6)


# ── migration ───────────────────────────────────────────────────────────────────


def test_migration_v2_from_v1(db, tmp_path):
    path = tmp_path / "frigate" / "frigate.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(fstore._MIGRATIONS[1])
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO episodes (start_ts, end_ts, status) VALUES (?, ?, 'closed')", (T0, T0 + 10))
    conn.execute(
        "INSERT INTO events (event_id, camera, label, start_ts, end_ts, person_kind, claim_status, created_ts, "
        "updated_ts, episode_id) VALUES ('old', 'inngang', 'person', ?, ?, 'unknown', 'none', ?, ?, 1)",
        (T0, T0 + 10, T0, T0))
    conn.commit()
    conn.close()
    fstore.reset_for_tests()
    assert fstore.schema_version() == fstore.SCHEMA_VERSION  # v1 → v2 → … (s06e added v3)
    old = _get("old")
    assert old.episode_id == 1 and old.drives is True and old.active_start is None
    ep = fstore.episode(1)
    assert ep.scene is None and ep.route is None


def test_episode_scene_and_route_round_trip(db):
    fstore.record_event(_ev("p", path=WALK, role="front_door"))
    ep_id = _get("p").episode_id
    assert fstore.set_episode_scene(ep_id, {"persons": 1, "scene": "a person walks to the door"}, "route text")
    ep = fstore.episode(ep_id)
    assert ep.scene == {"persons": 1, "scene": "a person walks to the door"} and ep.route == "route text"


# ── MQTT: motion fields to the Frigate user's hook only ─────────────────────────


@pytest.fixture
def capture(monkeypatch):
    other, frigate_user = [], []

    async def other_hook(ev):
        other.append(ev)

    async def frigate_user_hook(ev, raw_name, raw_score):
        frigate_user.append(ev)

    monkeypatch.setattr(mqtt_adapter, "_write_event_log", lambda *a, **kw: None)
    monkeypatch.setattr(mqtt_adapter, "_END_EVENT_HOOKS", [other_hook])
    monkeypatch.setattr(mqtt_adapter, "_FRIGATE_USER_HOOKS", [frigate_user_hook])
    return other, frigate_user


def test_motion_fields_reach_frigate_user_hook_only(capture):
    other, frigate_user = capture
    before = {"id": "1800000000.1-abc", "camera": "park", "label": "car", "top_score": 0.9, "start_time": 1.0,
              "end_time": 5.0, "has_snapshot": True, "stationary": True, "active": False, "motionless_count": 4179,
              "position_changes": 1, "path_data": [[[0.5, 0.3], 1.0], [[0.52, 0.3], 2.0]]}

    async def main():
        await mqtt_adapter._process_frigate_event(json.dumps({"type": "end", "before": before, "after": before}).encode())
        await asyncio.sleep(0)

    asyncio.run(main())
    motion = frigate_user[0]["motion"]
    assert motion == {"stationary": True, "active": False, "motionless_count": 4179, "position_changes": 1,
                      "path_data": [[[0.5, 0.3], 1.0], [[0.52, 0.3], 2.0]]}
    assert "motion" not in other[0]


# ── pipe: passes the camera role, Frigate's stationary flag and path_data to the store ─────────────────────────

env = s06a.env  # s06a's pipe fixture: tmp DBs/configs, fake Frigate + LLM


def _roles(monkeypatch, roles):
    monkeypatch.setattr(pipe.frigate_store, "camera_config", lambda: {"cameras": {c: {"role": r} for c, r in roles.items()}})


def test_pipe_road_camera_event_has_no_episode(env, monkeypatch):
    _roles(monkeypatch, {"veg": "road_facing"})
    ev = s06a._event("r1", camera="veg", label="car")
    ev["motion"] = {"stationary": False, "path_data": [[[0.1, 0.5], s06a.T0], [[0.6, 0.5], s06a.T0 + 4]]}
    s06a._process(ev)
    e = s06a._stored("r1")
    assert e.episode_id is None and e.drives is False


def test_pipe_parked_car_flag_reaches_store(env, monkeypatch):
    _roles(monkeypatch, {"park": "driveway"})
    ev = s06a._event("c1", camera="park", label="car")
    ev["motion"] = {"stationary": True, "path_data": [[[0.50, 0.3], s06a.T0], [[0.51, 0.3], s06a.T0 + 4]]}
    s06a._process(ev)
    e = s06a._stored("c1")
    assert e.stationary is True and e.drives is False


def test_pipe_rest_path_data_wins_over_mqtt(env, monkeypatch):
    _roles(monkeypatch, {"inngang": "front_door"})
    walk = [[[0.2, 0.5], s06a.T0], [[0.7, 0.5], s06a.T0 + 5]]

    async def rest(event_id):
        return {"start_time": s06a.T0, "end_time": s06a.T0 + 10, "data": {"attributes": env["face"], "path_data": walk}}

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", rest)
    ev = s06a._event("p1")
    ev["motion"] = {"stationary": False, "path_data": []}
    s06a._process(ev, "Person A", 0.95)
    e = s06a._stored("p1")
    assert e.drives is True and e.active_end == s06a.T0 + 5 and e.episode_id is not None


def test_migration_adds_the_new_keys_to_an_existing_install():
    tpl = yaml.safe_load(Path("/kaare/configs_default/frigate_user.yaml").read_text(encoding="utf-8"))
    old = {k: v for k, v in tpl.items() if k != "episode_min_movement"}
    added = fill_missing_keys(old, tpl)
    assert "episode_min_movement" in set(added)
    assert old["episode_min_movement"] == 0.08
