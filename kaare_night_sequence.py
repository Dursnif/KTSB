#!/usr/bin/env python3
"""
kaare_night_sequence.py — sequential night orchestrator.

Replaces three separate systemd timers with one ordered sequence:

  03:00  Step 1 — kaare_nightjob         (LTM compression, STM summary, Jing, Qdrant)
         Step 2 — kaare_miss_kare_nightjob (per-user portrait synthesis) [Fase 2]
         Step 3 — reflection meeting       (per-user, under meeting_active.lock)
         Step 4 — dev meeting              (under meeting_active.lock)

Failures or timeouts in one step do not stop subsequent steps.
Log: /kaare/logs/night_sequence.log
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, "/kaare")

from kaare_core.users.store import list_users
from kaare_nightjob import run_nightjob
from kaare_reflection import main as reflection_main
from kaare_dev_meeting import main as dev_meeting_main

try:
    from kaare_miss_kare_nightjob import run as miss_kare_portrait_run
    _HAS_PORTRAIT_JOB = True
except ImportError:
    miss_kare_portrait_run = None  # type: ignore[assignment]
    _HAS_PORTRAIT_JOB = False

# ── Logging ───────────────────────────────────────────────────────────────────
# kaare_nightjob's module-level basicConfig runs first during import and
# configures the root logger. We add our own file handler on top so all
# module loggers (nightjob, reflection, dev_meeting) also write here.
_LOG_PATH = Path("/kaare/logs/night_sequence.log")
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

try:
    _file_handler = logging.FileHandler(str(_LOG_PATH), encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"))
    logging.getLogger().addHandler(_file_handler)
except PermissionError:
    pass  # Running as non-service user; stdout only

log = logging.getLogger("night_sequence")

# ── Constants ─────────────────────────────────────────────────────────────────
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_LOCK_PATH = Path("/kaare/state/meeting_active.lock")

_TIMEOUT_NIGHTJOB    = 3600   # 60 min
_TIMEOUT_PORTRAIT    = 1800   # 30 min
_TIMEOUT_REFLECTION  = 5400   # 90 min
_TIMEOUT_DEV_MEETING = 3600   # 60 min


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        return yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("Could not load settings.yaml: %s", e)
        return {}


def _reflection_enabled(settings: dict) -> bool:
    return bool(settings.get("kare_reflection", {}).get("enabled", False))


def _dev_meeting_enabled(settings: dict) -> bool:
    return bool(settings.get("dev_meeting", {}).get("enabled", True))


def _get_active_users() -> list[str]:
    try:
        return [
            u["username"]
            for u in list_users()
            if u["is_active"] and u["username"] != "admin"
        ]
    except Exception as e:
        log.warning("Could not fetch active users: %s", e)
        return []


async def _step(name: str, coro, timeout: int) -> bool:
    """Run coro with timeout. Returns True on success, False on timeout/error."""
    log.info("=== [%s] starting (timeout=%ds) ===", name, timeout)
    t_start = datetime.now()
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        elapsed = int((datetime.now() - t_start).total_seconds())
        log.info("=== [%s] done in %ds ===", name, elapsed)
        return True
    except asyncio.TimeoutError:
        elapsed = int((datetime.now() - t_start).total_seconds())
        log.error("=== [%s] TIMED OUT after %ds ===", name, elapsed)
        return False
    except Exception as e:
        elapsed = int((datetime.now() - t_start).total_seconds())
        log.error("=== [%s] FAILED after %ds: %s ===", name, elapsed, e)
        return False


async def _run_reflection_all_users(users: list[str]) -> None:
    for user_id in users:
        log.info("--- reflection for user: %s ---", user_id)
        try:
            await reflection_main(user_id=user_id)
        except Exception as e:
            log.error("Reflection for %s failed: %s", user_id, e)
        log.info("--- reflection done: %s ---", user_id)


# ── Main sequence ─────────────────────────────────────────────────────────────

async def run_sequence() -> None:
    log.info("====== Night sequence starting ======")
    settings = _load_settings()

    # Step 1: LTM compression, STM daily summary, Jing ingestion, Qdrant indexing
    await _step("nightjob", run_nightjob(), _TIMEOUT_NIGHTJOB)

    # Step 2: Miss Kåre portrait synthesis (Fase 2 — skipped until file exists)
    if _HAS_PORTRAIT_JOB:
        await _step("miss_kare_portrait", miss_kare_portrait_run(), _TIMEOUT_PORTRAIT)
    else:
        log.info("[miss_kare_portrait] module not yet installed — skipping (Fase 2).")

    # Steps 3+4 under meeting_active.lock
    run_reflection = _reflection_enabled(settings)
    run_dev_meeting = _dev_meeting_enabled(settings)

    if not run_reflection and not run_dev_meeting:
        log.info("Both reflection and dev meeting disabled — skipping meeting block.")
        log.info("====== Night sequence complete ======")
        return

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_PATH.touch()
    log.info("meeting_active.lock set")
    try:
        # Step 3: Reflection meeting (per active user)
        if run_reflection:
            users = _get_active_users()
            if not users:
                log.info("No active users — skipping reflection.")
            else:
                log.info("Active users: %s", ", ".join(users))
                await _step(
                    "reflection",
                    _run_reflection_all_users(users),
                    _TIMEOUT_REFLECTION,
                )
        else:
            log.info("Reflection disabled in settings — skipping.")

        # Step 4: Developer meeting
        if run_dev_meeting:
            await _step("dev_meeting", dev_meeting_main(), _TIMEOUT_DEV_MEETING)
        else:
            log.info("Dev meeting disabled in settings — skipping.")
    finally:
        _LOCK_PATH.unlink(missing_ok=True)
        log.info("meeting_active.lock removed")

    log.info("====== Night sequence complete ======")


if __name__ == "__main__":
    asyncio.run(run_sequence())
