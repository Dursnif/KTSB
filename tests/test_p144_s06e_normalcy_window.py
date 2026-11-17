"""P144-s06e item 2: the normalcy baseline must cover its whole 28-day window.

Found 2026-10-27: `compute_baseline()` read only the last 5 MB of `frigate_mqtt.log` («covers ~28 days at typical
traffic»); this house writes ~5–10 MB a day, so the baseline had 2 days and every car looked «never seen». The window
is a time window, not a byte budget. Also: a per-camera day count, so a camera added last week is not judged on the
other cameras' 28 days. Tmp log/baseline/corrections only.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from kaare_core import log_buffer, normalcy
from kaare_core.storage import rawlog


def _line(ts: datetime, camera: str, label: str = "car", stage: str = "detection_end") -> str:
    return json.dumps({"ts": ts.isoformat(), "source": "frigate-mqtt", "stage": stage, "camera": camera,
                       "label": label, "score": 0.9, "event_id": f"{ts.timestamp()}-x", "message": "x" * 200})


@pytest.fixture
def paths(tmp_path, monkeypatch):
    log = tmp_path / "frigate_mqtt.log"
    monkeypatch.setitem(rawlog.LEGACY, "frigate_mqtt", log)  # P150: read through rawlog (the old single file)
    monkeypatch.setattr(normalcy, "_BASELINE_PATH", tmp_path / "normalcy_baseline.json")
    monkeypatch.setattr(normalcy, "_CORRECTIONS_PATH", tmp_path / "normalcy_corrections.json")
    monkeypatch.setattr(log_buffer, "flush", lambda *a, **k: None)
    # The old code's byte budget, made small: a log far bigger than it must still give the whole window.
    monkeypatch.setattr(normalcy, "_CHUNK_BYTES", 2_000, raising=False)
    return log


def _write(log, now: datetime):
    lines = []
    for d in range(40, 0, -1):  # 40 days back: the oldest 12 are outside the 28-day window
        day = now - timedelta(days=d)
        for h in (8, 16):
            t = day.replace(hour=h, minute=0, second=0, microsecond=0)
            lines.append(_line(t, "road", stage="detection_new"))
            lines.append(_line(t, "road"))
            if d <= 3:  # a camera added three days ago
                lines.append(_line(t, "door", label="person"))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_baseline_covers_the_whole_window_not_the_last_bytes(paths):
    now = datetime.now(UTC)
    _write(paths, now)
    assert paths.stat().st_size > 20 * 2_000  # far bigger than the old byte budget
    b = normalcy.compute_baseline()
    assert b["days_of_data"] in (27, 28)  # the window's days — not the 40 in the file, not the last few
    assert b["window_start"] == (now - timedelta(days=28)).strftime("%Y-%m-%d")


def test_baseline_counts_days_per_camera(paths):
    _write(paths, datetime.now(UTC))
    b = normalcy.compute_baseline()
    assert b["camera_days"]["door"] == 3
    assert b["camera_days"]["road"] == b["days_of_data"]


def test_baseline_days_for_cameras():
    base = {"days_of_data": 28, "camera_days": {"road": 28, "door": 3}}
    assert normalcy.baseline_days(["road"], base) == 28
    assert normalcy.baseline_days(["road", "door"], base) == 3
    assert normalcy.baseline_days(["new_cam"], base) == 0  # never seen in the window
    assert normalcy.baseline_days(["road"], {"days_of_data": 5}) == 5  # an old file without camera_days
    assert normalcy.baseline_days(["road"], {}) == 0


def test_empty_and_missing_log(paths):
    assert normalcy.compute_baseline() == {}
    paths.write_text("", encoding="utf-8")
    assert normalcy.compute_baseline()["days_of_data"] == 0
