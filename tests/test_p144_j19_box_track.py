"""J19 (from P144-s31): Frigate's per-update box kept on the event — only on change, ≤ 1/s, ≤ MAX_POINTS, in frigate.db,
cleared with the event's path by the normal retention. Never in frigate_mqtt.log. No real MQTT/HTTP (tmp DBs, fakes)."""
import asyncio
import inspect
import json

import pytest

from adapters import mqtt_adapter
from kaare_core.frigate_user import box_track, pipe
from kaare_core.frigate_user import store as fstore
from tests import test_p144_s06a_pipe as s06a

W, H = 2304.0, 1296.0


@pytest.fixture(autouse=True)
def clean():
    box_track._reset_for_tests()
    yield
    box_track._reset_for_tests()


def _after(ts, x1=100, y1=200, x2=160, y2=400, stationary=False, motionless=0, ratio=0.3, cam="cam1"):
    return {"camera": cam, "frame_time": ts, "box": [x1, y1, x2, y2], "ratio": ratio, "stationary": stationary,
            "motionless_count": motionless}


# ── the collector ───────────────────────────────────────────────────────────────────────────────────────────────

def test_kept_only_on_change_and_at_most_once_per_second():
    box_track._sizes["cam1"] = (W, H)
    box_track.note("e1", _after(10.0), now=10.0)
    box_track.note("e1", _after(10.4, x1=200, x2=260), now=10.4)  # moved, but < 1 s after the last kept → dropped
    box_track.note("e1", _after(12.0), now=12.0)  # 2 s later, same box → dropped
    box_track.note("e1", _after(13.0, motionless=40), now=13.0)  # only motionless_count grew → dropped
    box_track.note("e1", _after(14.0, x1=130, x2=190), now=14.0)  # moved 30 px of 2304 (1.3 %) → kept
    box_track.note("e1", _after(15.0, x1=131, x2=191), now=15.0)  # moved 1 px → dropped (jitter)
    box_track.note("e1", _after(16.0, x1=131, x2=191, stationary=True, motionless=80), now=16.0)  # flag flip → kept
    pts = box_track.pop("e1", end=_after(30.0, x1=131, x2=191, stationary=True, motionless=200))  # end: always kept
    assert [p[0] for p in pts] == [10.0, 14.0, 16.0, 30.0]
    assert pts[-2][6:] == [80, 1] and pts[-1][6:] == [200, 1]
    assert box_track.pop("e1") == []  # gone from RAM


def test_points_are_capped_evenly_first_and_last_kept():
    box_track._sizes["cam1"] = (W, H)
    for i in range(999):  # 2 px/s drift: under MOVE_MIN per second, so only every ~6th second is a change
        box_track.note("e2", _after(100.0 + i, x1=10 + 2 * i, x2=70 + 2 * i), now=100.0 + i)
    end = _after(1099.0, x1=10 + 2 * 999, x2=70 + 2 * 999)
    stored = box_track.normalised(box_track.pop("e2", end=end), (W, H))
    assert len(stored) <= box_track.MAX_POINTS
    assert stored[0][0] == 100.0 and stored[-1][0] == 1099.0


def test_normalised_is_the_rest_form_relative_x_y_w_h():
    stored = box_track.normalised([[5.0, 230.4, 129.6, 460.8, 518.4, 0.5, 3, 0]], (W, H))
    assert stored == [[5.0, 0.1, 0.1, 0.1, 0.3, 0.5, 3, 0]]


def test_unknown_detect_size_keeps_nothing():
    assert box_track.normalised([[5.0, 1, 2, 3, 4, None, 0, 0]], None) == []


def test_broken_updates_never_raise_and_are_skipped():
    for bad in ({}, {"box": None}, {"box": [1, 2]}, {"box": ["a", 2, 3, 4]}, {"box": [10, 10, 5, 5]}, None, "x"):
        box_track.note("e3", bad, now=1.0)
    assert box_track.pop("e3") == []


def test_ram_is_bounded_and_stale_tracks_go(monkeypatch):
    monkeypatch.setattr(box_track, "MAX_TRACKS", 3)
    for i in range(3):
        box_track.note(f"old{i}", _after(1.0), now=1.0)
    box_track.note("new", _after(1.0), now=1.0 + box_track.STALE_S + 1)  # full → the stale ones are dropped first
    assert set(box_track._tracks) == {"new"}


def test_detect_size_from_frigate_config_cached(monkeypatch):
    calls = []

    async def cfg():
        calls.append(1)
        return {"cam1": {"detect": {"width": 2304, "height": 1296}}, "cam2": {"detect": {}}}

    monkeypatch.setattr(box_track.frigate_adapter, "fetch_camera_configs", cfg)

    async def main():
        return await box_track.detect_size("cam1"), await box_track.detect_size("cam1")

    assert asyncio.run(main()) == ((2304.0, 1296.0), (2304.0, 1296.0))
    assert len(calls) == 1


# ── the store ───────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    yield tmp_path
    fstore.reset_for_tests()


def test_store_keeps_boxes_and_reads_them_back(db):
    boxes = [[1.0, 0.1, 0.1, 0.05, 0.2, 0.25, 0, 0], [2.0, 0.12, 0.1, 0.05, 0.2, 0.25, 0, 0]]
    ev = fstore.FrigateUserEvent(event_id="b1", camera="cam1", label="person", start_ts=1.0, end_ts=2.0, boxes=boxes)
    assert fstore.record_event(ev)
    got = next(e for e in fstore.events_between(0, 10) if e.event_id == "b1")
    assert got.boxes == boxes


def test_slimming_clears_boxes_with_the_path(db):
    """«Gone with the event's normal retention»: the step that clears path clears boxes too."""
    src = fstore.FrigateUserEvent.__dataclass_fields__
    assert "boxes" in src
    sql = inspect.getsource(fstore)
    assert "path='[]', boxes='[]'" in sql


# ── wiring: MQTT update → collector; end → the Frigate user's hook; pipe → store ─────────────────────────────────

@pytest.fixture
def capture(monkeypatch):
    got = []

    async def hook(ev, raw_name, raw_score):
        got.append(ev)

    monkeypatch.setattr(mqtt_adapter, "_write_event_log", lambda *a, **kw: None)
    monkeypatch.setattr(mqtt_adapter, "_END_EVENT_HOOKS", [])
    monkeypatch.setattr(mqtt_adapter, "_FRIGATE_USER_HOOKS", [hook])
    return got


def test_mqtt_updates_feed_the_collector_and_end_hands_the_points_on(capture):
    base = {"id": "m1", "camera": "cam1", "label": "person", "top_score": 0.9, "start_time": 1.0, "has_snapshot": True}

    async def main():
        for ts, x in ((1.0, 100), (2.5, 200), (4.0, 300)):
            msg = {"type": "update", "before": base, "after": {**base, **_after(ts, x1=x, x2=x + 60)}}
            await mqtt_adapter._process_frigate_event(json.dumps(msg).encode())
        end = {**base, **_after(5.0, x1=300, x2=360), "end_time": 5.0}
        await mqtt_adapter._process_frigate_event(json.dumps({"type": "end", "before": end, "after": end}).encode())
        await asyncio.sleep(0)

    asyncio.run(main())
    assert [p[0] for p in capture[0]["boxes"]] == [1.0, 2.5, 4.0, 5.0]  # the end box always last
    assert "boxes" not in capture[0]["motion"]  # the motion dict stays as it was (s06d test)
    assert "m1" not in box_track._tracks


env = s06a.env


def test_pipe_stores_the_normalised_boxes(env, monkeypatch):
    async def size(camera):
        return (W, H)

    monkeypatch.setattr(pipe.box_track, "detect_size", size)
    ev = s06a._event("p1")
    ev["boxes"] = [[s06a.T0, 230.4, 129.6, 460.8, 518.4, 0.5, 0, 0]]
    s06a._process(ev, "Person A", 0.95)
    assert s06a._stored("p1").boxes == [[s06a.T0, 0.1, 0.1, 0.1, 0.3, 0.5, 0, 0]]
