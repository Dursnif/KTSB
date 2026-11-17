"""P144 R138 / run-queue J3 — stationary objects are not analysed unless the pixels changed.

- same pixels in the object's box before the event and at Frigate's snapshot → «unchanged»; something new in the box →
  «changed»; colour ↔ IR, burnt-out IR blobs, a missing or unreadable frame, a tiny box → «unknown» (always let through);
- a vehicle whose track moved is never judged (measured: 3 of 25 passing cars looked pixel-unchanged); Frigate's
  stationary flag wins; bins are judged on pixels (their tracks jump between neighbouring boxes);
- never persons, animals or urgent labels; one verdict per event, shared by every caller.
Synthetic frames only; Frigate is faked (no HTTP)."""

import asyncio
import io

import numpy as np
import pytest
from PIL import Image

from kaare_core.frigate_user import pipe
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import stationary_gate as g

BOX = [0.3, 0.3, 0.3, 0.3]


def _jpeg(seed: int, patch_seed: int | None = None, gray: bool = False) -> bytes:
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    img = np.kron(img[::8, ::8], np.ones((8, 8, 1), dtype=np.uint8))  # blocky texture survives JPEG
    if patch_seed is not None:
        p = np.random.default_rng(patch_seed).integers(0, 255, (18, 24, 3), dtype=np.uint8)
        img[144:288, 192:384] = np.kron(p, np.ones((8, 8, 1), dtype=np.uint8))
    if gray:
        img[..., :] = img.mean(axis=2, keepdims=True).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    g.reset_for_tests()
    yield
    g.reset_for_tests()


# ── judge (pure) ──

def test_same_scene_is_unchanged():
    f = _jpeg(1)
    assert g.judge([f, f], [f, f], BOX) == "unchanged"


def test_something_new_in_the_box_is_changed():
    before, after = _jpeg(1), _jpeg(1, patch_seed=7)
    assert g.judge([before, before], [after, after], BOX) == "changed"


def test_change_outside_the_box_does_not_count():
    before, after = _jpeg(1), _jpeg(1, patch_seed=7)
    assert g.judge([before, before], [after, after], [0.0, 0.85, 0.1, 0.1]) == "unchanged"


def test_colour_to_ir_is_unknown():
    assert g.judge([_jpeg(1), _jpeg(1)], [_jpeg(1, gray=True)] * 2, BOX) == "unknown"


def test_missing_or_unreadable_frame_is_unknown():
    f = _jpeg(1)
    assert g.judge([f, None], [f, f], BOX) == "unknown"
    assert g.judge([f, b"not a jpeg"], [f, f], BOX) == "unknown"


def test_tiny_box_is_unknown():
    f = _jpeg(1)
    assert g.judge([f, f], [f, f], [0.5, 0.5, 0.005, 0.005]) == "unknown"


# ── who the gate applies to, and motion ──

def test_never_persons_animals_or_urgent():
    assert g.applies("waste_bin") and g.applies("car")
    assert not g.applies("person") and not g.applies("dog") and not g.applies("fire") and not g.applies("smoke")


def test_a_moving_vehicle_is_never_judged():
    path = [[[0.1, 0.5], 1.0], [[0.3, 0.5], 2.0]]
    assert g.moving("car", {"path_data": path})
    assert not g.moving("car", {"path_data": path, "stationary": True})  # Frigate's flag wins
    assert not g.moving("car", {"path_data": [[[0.1, 0.5], 1.0], [[0.11, 0.5], 2.0]]})
    assert g.moving("car", {})  # no path: cannot tell → treated as moving (never «unchanged»)
    assert not g.moving("waste_bin", {"path_data": path})  # bins: pixels only


# ── verdict: shared, REST-driven, never raises ──

def _rest(label="waste_bin", path=None, box=BOX, snap=1000.0):
    return {"camera": "cam", "label": label, "start_time": 990.0, "end_time": 1010.0,
            "data": {"box": box, "snapshot_frame_time": snap, "path_data": path or []}}


def test_verdict_unchanged_and_computed_once(monkeypatch):
    calls = []

    async def frame(camera, ts):
        calls.append(ts)
        return _jpeg(1)

    monkeypatch.setattr(g, "_frame", frame)

    async def run():
        return await asyncio.gather(g.verdict("e1", "cam", "waste_bin", rest=_rest()),
                                    g.verdict("e1", "cam", "waste_bin", rest=_rest()))

    assert asyncio.run(run()) == ["unchanged", "unchanged"]
    assert len(calls) == 4  # one set of frames for both callers
    assert sorted(calls) == [945.0, 975.0, 1000.0, 1000.25]  # two before the start, two at the snapshot


def test_verdict_moving_car_is_unknown_without_fetching(monkeypatch):
    calls = []

    async def frame(camera, ts):  # identical frames: pixels alone would say «unchanged»
        calls.append(ts)
        return _jpeg(1)

    monkeypatch.setattr(g, "_frame", frame)
    rest = _rest(label="car", path=[[[0.1, 0.5], 1.0], [[0.4, 0.5], 2.0]])
    assert asyncio.run(g.verdict("e2", "cam", "car", rest=rest)) == "unknown" and calls == []
    parked = _rest(label="car", path=[[[0.1, 0.5], 1.0], [[0.101, 0.5], 2.0]])
    assert asyncio.run(g.verdict("e2b", "cam", "car", rest=parked)) == "unchanged"


def test_verdict_without_snapshot_time_or_box_is_unknown(monkeypatch):
    async def frame(camera, ts):
        return _jpeg(1)

    monkeypatch.setattr(g, "_frame", frame)
    assert asyncio.run(g.verdict("e3", "cam", "waste_bin", rest=_rest(snap=None))) == "unknown"
    assert asyncio.run(g.verdict("e4", "cam", "waste_bin", rest=_rest(box=None))) == "unknown"


def test_verdict_person_is_unknown_and_never_fetches(monkeypatch):
    async def frame(camera, ts):
        raise AssertionError("never for a person")

    monkeypatch.setattr(g, "_frame", frame)
    assert asyncio.run(g.verdict("e5", "cam", "person", rest=_rest(label="person"))) == "unknown"


def test_verdict_rest_failure_is_unknown(monkeypatch):
    async def boom(event_id):
        raise RuntimeError("frigate down")

    monkeypatch.setattr(g.frigate_adapter, "get_event", boom)
    assert asyncio.run(g.verdict("e6", "cam", "waste_bin")) == "unknown"


# ── stable key per thing (R139 groundwork) ──

def test_object_key_is_stable_and_thing_id_empty():
    a = g.record("unchanged", "cam_e", "waste_bin", {"box": [0.017, 0.27, 0.08, 0.28]})
    b = g.record("unchanged", "cam_e", "waste_bin", {"box": [0.013, 0.273, 0.079, 0.28]})  # tonight's jitter
    assert a["key"] == b["key"] == "cam_e|waste_bin|0,4" and a["thing_id"] is None
    assert g.record("unknown", "cam_e", "waste_bin", {})["key"] is None


# ── wiring: the new pipe and the old responder share the verdict ──

def _fake_verdict(monkeypatch, word, seen):
    async def verdict(event_id, camera, label, rest=None):
        seen.append((event_id, label))
        return word
    monkeypatch.setattr(g, "verdict", verdict)


def test_pipe_unchanged_object_gets_no_episode(monkeypatch):
    seen = []
    _fake_verdict(monkeypatch, "unchanged", seen)
    out = asyncio.run(pipe._stationary_check("e1", "cam", "waste_bin", {"box": BOX}, "normal", True, "selected"))
    assert out[:3] == ("none", False, "unchanged_stationary") and out[3]["verdict"] == "unchanged" and seen


def test_pipe_never_asks_for_persons_urgent_or_unselected(monkeypatch):
    seen = []
    _fake_verdict(monkeypatch, "unchanged", seen)
    assert asyncio.run(pipe._stationary_check("e1", "cam", "person", {}, "normal", True, "selected"))[:3] == \
        ("normal", True, "selected")
    assert asyncio.run(pipe._stationary_check("e2", "cam", "fire", {}, "normal", True, "urgent_label"))[:3] == \
        ("normal", True, "urgent_label")
    assert asyncio.run(pipe._stationary_check("e3", "cam", "waste_bin", {}, "none", False, "label_not_selected"))[:3] \
        == ("none", False, "label_not_selected")
    assert seen == []


def test_pipe_changed_or_unknown_passes(monkeypatch):
    for word in ("changed", "unknown"):
        _fake_verdict(monkeypatch, word, [])
        out = asyncio.run(pipe._stationary_check("e1", "cam", "car", {"box": BOX}, "normal", True, "selected"))
        assert out[:3] == ("normal", True, "selected") and out[3]["verdict"] == word


# ── J13 (2026-11-06, the real bin cases 05:30–13:48): «unknown» must not open an episode for a non-living still thing ──

def test_frames_not_written_yet_are_fetched_once_more(monkeypatch):
    """16 bin events were «unknown» live and «unchanged» an hour later: Frigate had not written the recording yet."""
    calls = []

    async def frame(camera, ts):
        calls.append(ts)
        return None if len(calls) <= 4 else _jpeg(1)  # first round: nothing written yet

    async def no_sleep(s):
        return None

    monkeypatch.setattr(g, "_frame", frame)
    monkeypatch.setattr(g.asyncio, "sleep", no_sleep)
    assert asyncio.run(g.verdict("r1", "cam", "waste_bin", rest=_rest())) == "unchanged"
    assert len(calls) == 8


def test_pipe_holds_an_unknown_still_thing_but_never_a_vehicle_or_person(monkeypatch):
    """31 bin events had no REST event (Frigate kept none, 404) → no box → «unknown» → 16 opened an episode."""
    _fake_verdict(monkeypatch, "unknown", [])
    held = asyncio.run(pipe._stationary_check("e404", "cam", "waste_bin", {}, "normal", True, "selected"))
    assert held[:3] == ("none", False, "stationary_unknown") and held[3]["verdict"] == "unknown"
    car = asyncio.run(pipe._stationary_check("c1", "cam", "car", {}, "normal", True, "selected"))
    assert car[:3] == ("normal", True, "selected")  # a vehicle may be driving: always through
    person = asyncio.run(pipe._stationary_check("p1", "cam", "person", {}, "normal", True, "selected"))
    assert person[:3] == ("normal", True, "selected")
