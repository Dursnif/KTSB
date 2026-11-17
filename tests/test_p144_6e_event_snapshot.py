"""P144 6e — the pipe keeps the event snapshot the GUI shows (the old responder was its only writer), and reads the
label's «min. varighet» (`min_duration_seconds`) like the old path did.

- a selected person: the snapshot the pipe already fetched is written to `<SNAPSHOT_DIR>/<event>.jpg` — no extra GET;
- another selected label: exactly one Frigate GET (at L3 height), then written; not selected → nothing, no GET;
- `storage.snapshots_max_mb: 0` → nothing kept; the rotation runs at most once per SNAPSHOT_CLEANUP_S;
- `_drive` drops a selected label shorter than its min_duration_seconds; the pipe's two call sites are wired.
No live state: the snapshot folder is tmp_path, Frigate is faked."""

import asyncio
import inspect

import pytest

from kaare_core.domain import frigate_store
from kaare_core.frigate_user import pipe, sealing

JPEG = b"\xff\xd8\xff\xe0jpegdata"


@pytest.fixture
def snaps(tmp_path, monkeypatch):
    monkeypatch.setattr(frigate_store, "SNAPSHOT_DIR", tmp_path / "snaps")
    monkeypatch.setattr(frigate_store, "camera_config", lambda: {"storage": {"snapshots_max_mb": 500}, "cameras": {
        "cam": {"labels": {"car": {"analyze": True, "min_confidence": 0.5, "min_duration_seconds": 5}}}}})
    monkeypatch.setattr(pipe, "_last_snapshot_cleanup", 0.0)
    gets, cleanups = [], []

    async def fetch(event_id, height=None):
        gets.append((event_id, height))
        return JPEG

    monkeypatch.setattr(pipe.frigate_adapter, "fetch_event_snapshot", fetch)
    monkeypatch.setattr(frigate_store, "cleanup_snapshots", lambda mb: cleanups.append(mb))
    return tmp_path / "snaps", gets, cleanups


def _keep(*a):
    return asyncio.run(pipe._keep_event_snapshot(*a))


def test_person_reuses_the_fetched_snapshot(snaps):
    d, gets, _ = snaps
    assert _keep("e1", "person", True, JPEG, "snapshot", True)
    assert sealing.read_picture(d / "e1.jpg") == JPEG and gets == []  # sealed at rest since DT04 R0.6a


def test_other_label_one_get_and_unselected_nothing(snaps):
    d, gets, _ = snaps
    assert _keep("e2", "car", True, None, None, True)
    assert gets == [("e2", pipe.L3_SNAPSHOT_HEIGHT)] and (d / "e2.jpg").exists()
    assert not _keep("e3", "car", False, None, None, True)
    assert gets == [("e2", pipe.L3_SNAPSHOT_HEIGHT)] and not (d / "e3.jpg").exists()
    assert not _keep("e4", "person", True, b"frame", "recording", True)  # a recording frame is not the event snapshot


def test_cap_zero_keeps_nothing_and_rotation_is_throttled(snaps, monkeypatch):
    d, _gets, cleanups = snaps
    _keep("e5", "person", True, JPEG, "snapshot", True)
    _keep("e6", "person", True, JPEG, "snapshot", True)
    assert cleanups == [500]  # once per SNAPSHOT_CLEANUP_S, not per event
    monkeypatch.setattr(frigate_store, "camera_config", lambda: {"storage": {"snapshots_max_mb": 0}})
    assert not _keep("e7", "person", True, JPEG, "snapshot", True) and not (d / "e7.jpg").exists()


def test_min_duration_is_read_by_the_pipe(snaps):
    assert pipe._drive("cam", "car", 0.9, "unknown", False, duration=2.0)[1:] == (False, "below_min_duration")
    assert pipe._drive("cam", "car", 0.9, "unknown", False, duration=9.0)[1] is True


def test_the_pipe_is_wired():
    assert "_keep_event_snapshot(" in inspect.getsource(pipe._finish)
    assert "duration=duration" in inspect.getsource(pipe._arrive)
