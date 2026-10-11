"""
N17 — the night sequence starts once per household day at the GUI start time, in Docker and at
home. Pure slot logic, the runner's tick (state file, catch-up, restart), the single-run lock,
the settings API and the GUI-button / status wiring. No model, no systemd, no real night run.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_n17_night_runner.py -v
"""

import asyncio
import inspect
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi import HTTPException

sys.path.insert(0, "/kaare")

import fcntl

import kaare_core.routers.router_meetings as rm
import kaare_core.routers.router_settings as rs
import kaare_night_runner as nr
import kaare_night_sequence as ns

# Far from the machine's own zone (Europe/Oslo, UTC+1/+2) on purpose.
TOKYO = ZoneInfo("Asia/Tokyo")  # UTC+9, no DST
LA = ZoneInfo("America/Los_Angeles")  # UTC-8/-7, DST
THREE = (3, 0)


def at(tz, y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


# ── start time parsing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("03:00", (3, 0)),
        ("3:05", (3, 5)),
        ("23:59", (23, 59)),
        (" 00:00 ", (0, 0)),
        ("24:00", None),
        ("12:60", None),
        ("0300", None),
        ("3", None),
        ("", None),
        ("ab:cd", None),
        (None, None),
        (300, None),
        ("03:00:00", None),
        ("-1:00", None),
    ],
)
def test_parse_start_time(raw, expected):
    assert nr.parse_start_time(raw) == expected


def test_missing_or_invalid_setting_uses_default(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("other: 1\n", encoding="utf-8")
    assert nr.load_start_time(p) == THREE
    p.write_text("night_sequence:\n  start_time: '25:99'\n", encoding="utf-8")
    assert nr.load_start_time(p) == THREE
    p.write_text("night_sequence: null\n", encoding="utf-8")
    assert nr.load_start_time(p) == THREE
    p.write_text("{{{ not yaml", encoding="utf-8")
    assert nr.load_start_time(p) == THREE
    assert nr.load_start_time(tmp_path / "missing.yaml") == THREE
    p.write_text("night_sequence:\n  start_time: '22:15'\n", encoding="utf-8")
    assert nr.load_start_time(p) == (22, 15)


# ── slot logic in a timezone far from the machine's ───────────────────────────


def test_due_at_start_in_tokyo_not_at_machine_time():
    # 03:00 Tokyo = 18:00 UTC the day before; machine clock (UTC/Oslo) says evening.
    now = at(TOKYO, 2026, 10, 8, 3, 0)
    assert now.astimezone(UTC).hour == 18
    assert nr.due_slot(now, THREE, TOKYO, "2026-10-07") == "2026-10-08"
    # 03:00 Oslo is 10:00 Tokyo — must not count as the start
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 2, 59), THREE, TOKYO, "2026-10-07") is None


def test_once_per_day():
    now = at(TOKYO, 2026, 10, 8, 3, 1)
    assert nr.due_slot(now, THREE, TOKYO, "2026-10-08") is None
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 8, 0), THREE, TOKYO, "2026-10-08") is None
    assert nr.due_slot(at(TOKYO, 2026, 10, 9, 3, 0), THREE, TOKYO, "2026-10-08") == "2026-10-09"


def test_catch_up_window():
    last = "2026-10-07"
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 8, 59), THREE, TOKYO, last) == "2026-10-08"
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 9, 0), THREE, TOKYO, last) is None  # 6 h passed
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 23, 0), THREE, TOKYO, last) is None
    assert (
        nr.due_slot(at(TOKYO, 2026, 10, 9, 1, 0), THREE, TOKYO, last) is None
    )  # before next start


def test_start_before_midnight_belongs_to_its_start_date():
    start = (23, 30)
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 23, 30), start, TOKYO, "2026-10-07") == "2026-10-08"
    # after midnight: still the 10-08 slot, inside catch-up
    assert nr.due_slot(at(TOKYO, 2026, 10, 9, 1, 0), start, TOKYO, "2026-10-07") == "2026-10-08"
    assert nr.due_slot(at(TOKYO, 2026, 10, 9, 1, 0), start, TOKYO, "2026-10-08") is None


def test_dst_change_uses_real_instants():
    # US DST ends 2026-11-01 02:00 → 01:00. Start 01:30 occurs twice; first occurrence counts,
    # and the second 01:30 must not trigger a second run.
    start = (1, 30)
    first = datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=1)
    assert nr.due_slot(first, start, LA, "2026-10-31") == "2026-11-01"
    assert nr.due_slot(second, start, LA, "2026-11-01") is None
    # Spring forward 2027-03-14 02:00 → 03:00: start 02:30 does not exist; Python maps it to
    # 03:30 summer time, so the night runs half an hour later that one night — never skipped.
    assert nr.due_slot(datetime(2027, 3, 14, 3, 5, tzinfo=LA), (2, 30), LA, "2027-03-13") is None
    assert (
        nr.due_slot(datetime(2027, 3, 14, 3, 30, tzinfo=LA), (2, 30), LA, "2027-03-13")
        == "2027-03-14"
    )


# ── runner tick: state file, first start, restart ─────────────────────────────


@pytest.fixture
def runner(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text("night_sequence:\n  start_time: '03:00'\n", encoding="utf-8")
    last = tmp_path / "night_sequence_last.json"
    monkeypatch.setattr(nr, "_SETTINGS_PATH", settings)
    monkeypatch.setattr(nr, "LAST_RUN_PATH", last)
    monkeypatch.setattr(nr, "get_local_tz", lambda: TOKYO)
    monkeypatch.setattr(
        nr,
        "load_start_time",
        lambda: nr.parse_start_time(
            yaml.safe_load(settings.read_text())["night_sequence"]["start_time"]
        ),
    )
    monkeypatch.setattr(nr.read_last_slot, "__defaults__", (last,))
    monkeypatch.setattr(nr.write_last_slot, "__defaults__", (last,))
    runs = []
    monkeypatch.setattr(nr, "_run_sequence", lambda slot: runs.append(slot))
    return runs, last, settings


def test_first_start_records_without_running(runner):
    runs, last, _ = runner
    assert nr.tick(at(TOKYO, 2026, 10, 8, 4, 0)) is None
    assert runs == [] and nr.read_last_slot(last) == "2026-10-08"
    assert nr.tick(at(TOKYO, 2026, 10, 8, 4, 1)) is None  # same night: nothing
    assert nr.tick(at(TOKYO, 2026, 10, 9, 3, 0)) == "2026-10-09"
    assert runs == ["2026-10-09"]


def test_restart_does_not_rerun_and_catch_up_runs(runner):
    runs, last, _ = runner
    nr.write_last_slot("2026-10-07", last)
    assert nr.tick(at(TOKYO, 2026, 10, 8, 3, 0)) == "2026-10-08"
    # «restart»: a new process reads the same state file
    assert nr.tick(at(TOKYO, 2026, 10, 8, 3, 30)) is None
    assert runs == ["2026-10-08"]
    # machine off from 02:00 to 07:00 on 10-09 → catch-up at 07:00
    assert nr.tick(at(TOKYO, 2026, 10, 9, 7, 0)) == "2026-10-09"
    # off until 10:00 on 10-10 → that night is skipped
    assert nr.tick(at(TOKYO, 2026, 10, 10, 10, 0)) is None
    assert runs == ["2026-10-08", "2026-10-09"]


def test_slot_recorded_before_the_run(runner, monkeypatch):
    _, last, _ = runner
    nr.write_last_slot("2026-10-07", last)
    seen = []
    monkeypatch.setattr(nr, "_run_sequence", lambda slot: seen.append(nr.read_last_slot(last)))
    nr.tick(at(TOKYO, 2026, 10, 8, 3, 0))
    assert seen == ["2026-10-08"]


def test_gui_change_applies_next_poll(runner):
    _runs, last, settings = runner
    nr.write_last_slot("2026-10-07", last)
    assert nr.tick(at(TOKYO, 2026, 10, 8, 1, 0)) is None
    settings.write_text("night_sequence:\n  start_time: '00:45'\n", encoding="utf-8")
    assert nr.tick(at(TOKYO, 2026, 10, 8, 1, 0)) == "2026-10-08"


def test_corrupt_state_file_counts_as_not_run(tmp_path):
    p = tmp_path / "last.json"
    p.write_text("{oops", encoding="utf-8")
    assert nr.read_last_slot(p) == ""
    assert nr.due_slot(at(TOKYO, 2026, 10, 8, 3, 0), THREE, TOKYO, "") == "2026-10-08"


# ── single-run lock (across processes) ────────────────────────────────────────

_HOLDER = """
import sys, time
sys.path.insert(0, "/kaare")
from pathlib import Path
import kaare_night_runner as nr
fh = nr.acquire_run_lock(Path(sys.argv[1]))
print("held" if fh else "none", flush=True)
time.sleep(float(sys.argv[2]))
"""


def test_lock_blocks_second_run_across_processes(tmp_path):
    lock = tmp_path / "night.lock"
    assert nr.is_sequence_running(lock) is False  # no file yet
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock), "3"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        assert nr.is_sequence_running(lock) is True
        assert nr.acquire_run_lock(lock, tries=2) is None
    finally:
        holder.wait(timeout=10)
    assert nr.is_sequence_running(lock) is False  # released when the process ends
    fh = nr.acquire_run_lock(lock, tries=1)
    assert fh is not None
    fh.close()


def test_status_probe_does_not_block_a_starting_run(tmp_path):
    lock = tmp_path / "night.lock"
    lock.write_text("", encoding="utf-8")
    probe = open(lock)  # noqa: SIM115 — closed in the finally below, after the lock assertions

    fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)  # API probe in flight
    try:
        assert nr.acquire_run_lock(lock, tries=1) is None
    finally:
        fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        probe.close()
    fh = nr.acquire_run_lock(lock, tries=3)  # retry rides it out
    assert fh is not None
    fh.close()


def test_night_sequence_entry_takes_the_lock(monkeypatch):
    monkeypatch.setattr(ns, "acquire_run_lock", lambda: None)
    monkeypatch.setattr(sys, "argv", ["kaare_night_sequence.py"])
    called = []
    monkeypatch.setattr(ns, "run_sequence", lambda: called.append(1))
    assert ns.main() == 1 and called == []


# ── wiring: GUI button, status, settings API, Docker ──────────────────────────


def test_gui_reflection_button_runs_the_night_step():
    src = inspect.getsource(rm.api_reflections_start)
    flat = " ".join(src.split())  # layout-independent: the formatter may split the argv list
    assert "kaare_night_sequence.py" in src and '"--only", "reflection"' in flat
    assert '"/kaare/kaare_reflection_runner.py"' not in src
    assert "is_sequence_running" in src


def test_status_reads_the_lock_not_systemctl():
    src = inspect.getsource(rm._night_sequence_state)
    assert "is_sequence_running()" in src and "systemctl" not in src


def test_settings_api_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "settings.yaml"
    p.write_text("night_sequence: null\nkare_language: nb\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    got = asyncio.run(rs.api_get_night_sequence(_u=None))
    assert got["start_time"] == "03:00" and got["default"] == "03:00"
    assert (
        asyncio.run(rs.api_put_night_sequence({"start_time": "4:7"}, _u=None))["start_time"]
        == "04:07"
    )
    data = yaml.safe_load(p.read_text())
    assert data["night_sequence"]["start_time"] == "04:07" and data["kare_language"] == "nb"
    assert asyncio.run(rs.api_get_night_sequence(_u=None))["start_time"] == "04:07"
    for bad in ("25:00", "", None, "noon"):
        with pytest.raises(HTTPException):
            asyncio.run(rs.api_put_night_sequence({"start_time": bad}, _u=None))


def test_template_and_compose():
    tmpl = yaml.safe_load(Path("/kaare/configs_default/settings.yaml").read_text())
    assert nr.parse_start_time(tmpl["night_sequence"]["start_time"]) == THREE
    for name in ("docker-compose.yml", "docker-compose.dev.yml"):
        svc = yaml.safe_load(Path("/kaare", name).read_text())["services"]["kaare-reflection"]
        assert "./logs:/kaare/logs" in svc["volumes"], name
    main = yaml.safe_load(Path("/kaare/docker-compose.yml").read_text())["services"][
        "kaare-reflection"
    ]
    assert main["command"] == "python kaare_night_runner.py"
