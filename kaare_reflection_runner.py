#!/usr/bin/env python3
"""
Reflection runner — runs as a long-lived Docker service.

Reads kare_reflection.enabled from settings.yaml every cycle.
When enabled: runs the reflection meeting once per night (03:45–04:15 window).
When disabled: sleeps and re-checks every 5 minutes.
Never exits cleanly so docker restart: on-failure does not trigger on disable.
"""

import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, "/kaare")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reflection_runner")

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_RUN_HOUR = 4       # target hour (local time)
_RUN_WINDOW_MIN = 30  # minutes around target hour that count as "in window"
_CHECK_INTERVAL = 300  # seconds between config checks when disabled
_SLEEP_INTERVAL = 60   # seconds between time checks when enabled


def _load_enabled() -> bool:
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return bool(data.get("kare_reflection", {}).get("enabled", False))
    except Exception:
        return False


def _in_run_window() -> bool:
    now = datetime.now()
    minutes_from_target = abs(now.hour * 60 + now.minute - _RUN_HOUR * 60)
    return minutes_from_target <= _RUN_WINDOW_MIN


def _get_active_users() -> list[str]:
    from kaare_core.users.store import list_users
    return [
        u["username"]
        for u in list_users()
        if u["is_active"] and u["username"] != "admin"
    ]


async def _run_reflection_for_all() -> None:
    from kaare_reflection import main as reflection_main

    users = _get_active_users()
    if not users:
        log.info("No active users — skipping reflection meeting.")
        return

    log.info("Starting reflection meeting for %d user(s): %s", len(users), ", ".join(users))
    lock = Path("/kaare/state/meeting_active.lock")
    lock.touch()
    try:
        for user_id in users:
            log.info("=== Meeting for: %s ===", user_id)
            try:
                await reflection_main(user_id=user_id)
            except Exception as e:
                log.error("Meeting for %s failed: %s", user_id, e)
            log.info("=== Done: %s ===", user_id)
    finally:
        lock.unlink(missing_ok=True)


async def main_loop() -> None:
    ran_today: str = ""  # date string "YYYY-MM-DD" of last run

    while True:
        enabled = _load_enabled()

        if not enabled:
            log.info("Reflection disabled via settings — sleeping %ds.", _CHECK_INTERVAL)
            await asyncio.sleep(_CHECK_INTERVAL)
            continue

        today = datetime.now().strftime("%Y-%m-%d")

        if _in_run_window() and ran_today != today:
            log.info("In run window — starting nightly reflection.")
            ran_today = today
            try:
                await _run_reflection_for_all()
            except Exception as e:
                log.error("Reflection loop error: %s", e)
            # Sleep past the window so we don't re-run
            await asyncio.sleep(_RUN_WINDOW_MIN * 60 + 60)
        else:
            await asyncio.sleep(_SLEEP_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
