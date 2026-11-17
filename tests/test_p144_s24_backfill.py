"""P144-s24 item 5 — backfill of missing episode frames (generic KTSB code: also after a future outage).

Found 2026-10-31: ep 1–45 list their frames in the scene JSON but have no files (before s11 part 2 stored them) — the
GUI fetches them live from Frigate until the 7-day recordings are gone (~11-03); ep 105/106 have the files on disk but no
list (the scene failed). `conclude.backfill_frames()`:
- listed but not on disk → fetched from Frigate's recordings at the listed (camera, ts), stored at the listed file;
- not listed but on disk and the code pick matches the files → the list is rebuilt from the pick, no fetch;
- not listed, nothing on disk (vehicle-only) → picked + fetched + stored + listed;
- never re-concludes (the conclusion stays as it is), never touches a waiting episode, Frigate fetches capped.
Never touches live state (tmp DBs, fake Frigate).
"""

import asyncio

import pytest

from adapters import frigate_adapter
from kaare_core.frigate_user import conclude, frames, route
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p144_s06d_scene import CAMS, T0, UTC, _leaving


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": True, "episode_gap_s": 120, "conclude": {}})
    state = {"snaps": [], "gone": set(), "live": 0, "peak": 0}

    async def fake_snap(camera, ts, height=480):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        await asyncio.sleep(0.01)
        state["live"] -= 1
        state["snaps"].append((camera, ts))
        if camera in state["gone"]:
            raise RuntimeError("404 recording gone")
        return f"{camera}-{ts:.3f}".encode()

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snap)
    yield state
    frigate_consent.invalidate()
    fstore.reset_for_tests()


DONE = {"status": "done", "by": "model", "summary": "Jeg så en person.", "attempts": 1}


def _episode(scene, conclusion=DONE):
    ep, events = _leaving()
    fstore.close_episodes(T0 + 1000, 120)
    fstore.set_episode_scene(ep.episode_id, scene, "rute")
    fstore.conclude_episode(ep.episode_id, dict(conclusion))
    return ep.episode_id, events


def _run(now=T0 + 2000):
    return asyncio.run(conclude.backfill_frames(now=now))


def test_listed_frames_missing_on_disk_are_fetched_at_their_file(env):
    listed = [{"camera": "door", "ts": T0 + 1}, {"camera": "drive", "ts": T0 + 14}, {"camera": "gate", "ts": T0 + 44}]
    ep_id, _ = _episode({"status": "done", "scene": "x", "frames": listed})
    stats = _run()
    assert sorted(env["snaps"]) == sorted((f["camera"], f["ts"]) for f in listed)
    assert fstore.stored_frame_indexes(ep_id) == [0, 1, 2]
    assert fstore.read_episode_frame(ep_id, 1) == f"drive-{T0 + 14:.3f}".encode()
    assert stats["frames_fetched"] == 3 and stats["episodes_fixed"] == 1
    ep = fstore.episode(ep_id)
    assert ep.scene["frames"] == listed and ep.conclusion == DONE  # list kept, never re-concluded
    env["snaps"].clear()
    assert _run()["frames_fetched"] == 0 and env["snaps"] == []  # idempotent


def test_files_on_disk_without_a_list_get_the_list_back_without_a_fetch(env):
    ep_id, events = _episode({"status": "failed", "reason": "empty", "look": {"status": "failed"}})
    ep = fstore.episode(ep_id)
    points = frames.pick(frames.candidates(events, (ep.start_ts, ep.end_ts)), events=events)
    fstore.save_episode_frames(ep_id, [b"old"] * len(points))
    stats = _run()
    assert env["snaps"] == [] and stats["listed_from_disk"] == 1
    scene = fstore.episode(ep_id).scene
    assert scene["status"] == "failed" and scene["reason"] == "empty"  # the rest of the scene untouched
    assert [(f["camera"], f["ts"], f["file"]) for f in scene["frames"]] == [(c, t, i) for i, (t, c) in enumerate(points)]


def test_nothing_listed_nothing_on_disk_is_picked_fetched_and_listed(env):
    ep_id, _ = _episode({"status": "skipped", "reason": "vehicles_only"},
                        {"status": "done", "by": "code", "summary": "bil"})
    stats = _run()
    scene = fstore.episode(ep_id).scene
    assert scene["reason"] == "vehicles_only" and len(scene["frames"]) == len(env["snaps"]) >= 3
    assert fstore.stored_frame_indexes(ep_id) == [f["file"] for f in scene["frames"]]
    assert stats["frames_fetched"] == len(env["snaps"])


def test_gone_recording_is_counted_and_nothing_breaks(env):
    env["gone"].add("drive")
    listed = [{"camera": "door", "ts": T0 + 1}, {"camera": "drive", "ts": T0 + 14}]
    ep_id, _ = _episode({"status": "done", "frames": listed})
    stats = _run()
    assert fstore.stored_frame_indexes(ep_id) == [0]
    assert stats["frames_failed"] == 1 and stats["frames_fetched"] == 1


def test_fetches_are_capped(env):
    listed = [{"camera": "door", "ts": T0 + i * 3} for i in range(9)]
    for _ in range(1):
        _episode({"status": "done", "frames": listed})
    _run()
    assert 1 <= env["peak"] <= frames.BACKFILL_PARALLEL


def test_waiting_episode_and_switch_off_are_left_alone(env, monkeypatch):
    ep_id, _ = _episode({"status": "waiting", "reason": "model_unreachable", "frames": [{"camera": "door", "ts": T0}]})
    _run()
    assert env["snaps"] == []
    fstore.set_episode_scene(ep_id, {"status": "done", "frames": [{"camera": "door", "ts": T0}]}, "rute")
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": False, "episode_gap_s": 120, "conclude": {}})
    _run()
    assert env["snaps"] == []


def test_older_than_the_recordings_window_is_not_fetched(env):
    _episode({"status": "done", "frames": [{"camera": "door", "ts": T0}]})
    _run(now=T0 + 8 * 86400)
    assert env["snaps"] == []


def test_a_vehicle_only_episode_gets_its_frames_at_once(env):
    # item 2: the frame set is decided by code whether a model runs or not — a vehicle-only episode (no model) too
    for i, cam in enumerate(("drive", "gate")):
        assert fstore.record_event(fstore.FrigateUserEvent(
            event_id=f"car{i}", camera=cam, label="car", start_ts=T0 + 10 * i, end_ts=T0 + 10 * i + 8, drives=True,
            path_data=[[[0.2, 0.5], T0 + 10 * i], [[0.7, 0.5], T0 + 10 * i + 6]], camera_role="driveway"))
    fstore.close_episodes(T0 + 1000, 120)
    (ep,) = fstore.episodes_between(0, T0 * 2)
    assert asyncio.run(conclude._conclude_by_code(ep))
    scene = fstore.episode(ep.episode_id).scene
    assert scene["reason"] == "vehicles_only" and len(scene["frames"]) == len(env["snaps"]) >= 2
    assert fstore.stored_frame_indexes(ep.episode_id) == [f["file"] for f in scene["frames"]]
