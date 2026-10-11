#!/usr/bin/env python3
"""
kaare_night_runner.py — starts the night sequence once per household day (N17).

Long-running service (Docker service «kaare-reflection», house unit kaare-night-runner.service).
Every _POLL_SECONDS it compares now_local() with the start time set in the GUI
(settings.yaml night_sequence.start_time, Settings → Reflection) and, when the night is due,
runs kaare_night_sequence.py as a child process and waits for it.

Rules:
  - One run per night slot. A slot is the local date D + the start time in the GUI timezone;
    a start time before midnight (e.g. 23:30) still belongs to the date it started on.
  - Catch-up: a slot is due from its start until _CATCH_UP_HOURS later, so a runner started
    late (power cut, container restart) still runs that night; later than that the night is skipped.
  - The slot is recorded in state/night_sequence_last.json BEFORE the child starts, so a crash
    mid-sequence never re-runs the whole night in a loop, and a restart does not run it twice.
  - First start ever (no state file): the latest slot that has already begun is recorded without
    running it — an upgrade or fresh install never fires a night run right away.
  - Never two runs at once: kaare_night_sequence.py itself takes NIGHT_LOCK_PATH (see
    acquire_run_lock), which also covers manual and GUI starts.

This module stays import-light: the API imports parse_start_time / is_sequence_running from it.
"""

import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, "/kaare")

from kaare_core.config import get_local_tz, now_local

log = logging.getLogger("night_runner")

_BASE = Path("/kaare")
_SETTINGS_PATH = _BASE / "configs" / "settings.yaml"
_SEQUENCE_SCRIPT = Path(__file__).resolve().with_name("kaare_night_sequence.py")
LAST_RUN_PATH = _BASE / "state" / "night_sequence_last.json"
NIGHT_LOCK_PATH = _BASE / "state" / "night_sequence.lock"

DEFAULT_START_TIME = "03:00"
_CATCH_UP_HOURS = 6
_POLL_SECONDS = 30
_LOCK_TRIES = 25  # × 0.2 s — rides out the API's brief shared-lock status probe
_LOCK_RETRY_SLEEP = 0.2


# ── Start time (GUI setting) ──────────────────────────────────────────────────


def parse_start_time(value) -> tuple[int, int] | None:
    """«HH:MM» (24 h) → (hour, minute); anything else → None."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() and 1 <= len(p) <= 2 for p in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def load_start_time(settings_path: Path = _SETTINGS_PATH) -> tuple[int, int]:
    """The GUI start time; missing or invalid → DEFAULT_START_TIME (logged)."""
    raw = None
    try:
        data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        raw = (data.get("night_sequence") or {}).get("start_time")
    except Exception as e:
        log.warning(
            "Could not read settings.yaml (%s) — using start time %s", e, DEFAULT_START_TIME
        )
    parsed = parse_start_time(raw) if raw is not None else None
    if raw is not None and parsed is None:
        log.warning("Invalid night_sequence.start_time %r — using %s", raw, DEFAULT_START_TIME)
    return parsed or parse_start_time(DEFAULT_START_TIME)


# ── Slot logic (pure — tested in tests/test_n17_night_runner.py) ──────────────


def slot_start(day: date, start: tuple[int, int], tz: ZoneInfo) -> datetime:
    """The aware start instant of the night slot that belongs to local date `day`."""
    return datetime(day.year, day.month, day.day, start[0], start[1], tzinfo=tz)


def latest_started_slot(now: datetime, start: tuple[int, int], tz: ZoneInfo) -> date:
    """The local date of the most recent slot whose start is not in the future."""
    today = now.astimezone(tz).date()
    # Compare as timestamps: aware datetimes sharing a tzinfo compare by wall clock in Python,
    # which is an hour off across a DST change.
    if slot_start(today, start, tz).timestamp() <= now.timestamp():
        return today
    return today - timedelta(days=1)


def due_slot(
    now: datetime,
    start: tuple[int, int],
    tz: ZoneInfo,
    last_slot: str | None,
    catch_up_hours: float = _CATCH_UP_HOURS,
) -> str | None:
    """The slot (ISO date) to run now, or None. Due from its start for catch_up_hours,
    once — not when last_slot already is that slot."""
    slot = latest_started_slot(now, start, tz)
    begins = slot_start(slot, start, tz).timestamp()
    if now.timestamp() >= begins + catch_up_hours * 3600:
        return None
    iso = slot.isoformat()
    return None if iso == last_slot else iso


# ── Last-run state ────────────────────────────────────────────────────────────


def read_last_slot(path: Path = LAST_RUN_PATH) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("slot")
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("Could not read %s (%s) — treating as no previous run", path, e)
        return ""


def write_last_slot(slot: str, path: Path = LAST_RUN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"slot": slot, "started_at": datetime.now(UTC).isoformat()}), encoding="utf-8"
    )
    os.replace(tmp, path)


# ── Single-run lock (taken by kaare_night_sequence.py, probed by the API) ─────


def acquire_run_lock(path: Path = NIGHT_LOCK_PATH, tries: int = _LOCK_TRIES):
    """Exclusive, non-blocking flock on the night lock. Returns the open file (keep it open for
    the whole run — the kernel releases the lock when the process ends) or None if another run
    holds it. Retries briefly so a concurrent status probe (shared lock) is not mistaken for a run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")  # noqa: SIM115 — returned to the caller; the lock lives while it is open
    for attempt in range(tries):
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()}))
            fh.flush()
            return fh
        except BlockingIOError:
            if attempt + 1 < tries:
                time.sleep(_LOCK_RETRY_SLEEP)
    fh.close()
    return None


def is_sequence_running(path: Path = NIGHT_LOCK_PATH) -> bool:
    """True while a night sequence (scheduled, manual or GUI) holds the lock — works across
    processes and containers sharing state/. Takes a shared lock for an instant only."""
    try:
        with open(path, encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False
    except BlockingIOError:  # an exclusive lock is held → a run is in progress
        return True
    except OSError:  # no lock file (FileNotFoundError) or unreadable
        return False


# ── Runner loop ───────────────────────────────────────────────────────────────


def _run_sequence(slot: str) -> None:
    log.info("Night slot %s due — starting %s", slot, _SEQUENCE_SCRIPT.name)
    t0 = time.monotonic()
    try:
        rc = subprocess.run([sys.executable, str(_SEQUENCE_SCRIPT)], cwd=str(_BASE)).returncode
    except Exception as e:
        log.error("Could not start the night sequence: %s", e)
        return
    log.info(
        "Night sequence for slot %s exited with code %s after %ds",
        slot,
        rc,
        int(time.monotonic() - t0),
    )


def tick(now: datetime | None = None) -> str | None:
    """One scheduling check. Returns the slot that was run, or None."""
    tz = get_local_tz()
    now = now or now_local()
    start = load_start_time()
    last = read_last_slot()
    if last is None:
        first = latest_started_slot(now, start, tz).isoformat()
        write_last_slot(first)
        log.info(
            "First start — slot %s recorded as done without running (next night runs normally)",
            first,
        )
        return None
    slot = due_slot(now, start, tz, last)
    if slot is None:
        return None
    write_last_slot(slot)
    _run_sequence(slot)
    return slot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    start = load_start_time()
    log.info(
        "Night runner started — start time %02d:%02d (%s), catch-up %dh, now %s",
        start[0],
        start[1],
        get_local_tz(),
        _CATCH_UP_HOURS,
        now_local().isoformat(timespec="seconds"),
    )
    while True:
        try:
            tick()
        except Exception as e:
            log.error("Night runner tick failed: %s", e)
        time.sleep(_POLL_SECONDS)


if __name__ == "__main__":
    main()
