#!/usr/bin/env python3
"""
Kjøres kl. 04:00 via systemd timer.
Henter alle aktive brukere (unntatt admin) og kjører refleksjonsmøtet
sekvensielt for hver.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/kaare")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reflection_runner")


def _get_active_users() -> list[str]:
    from kaare_core.users.store import list_users
    return [
        u["username"]
        for u in list_users()
        if u["is_active"] and u["username"] != "admin"
    ]


async def run_all() -> None:
    from kaare_reflection import main as reflection_main

    users = _get_active_users()
    if not users:
        log.warning("Ingen aktive brukere å kjøre refleksjon for.")
        return

    log.info("Refleksjonsmøte for %d bruker(e): %s", len(users), ", ".join(users))

    for user_id in users:
        log.info("=== Starter møte for: %s ===", user_id)
        try:
            await reflection_main(user_id=user_id)
        except Exception as e:
            log.error("Møte for %s feilet: %s", user_id, e)
        log.info("=== Ferdig: %s ===", user_id)


if __name__ == "__main__":
    _lock = Path("/kaare/state/meeting_active.lock")
    _lock.touch()
    try:
        asyncio.run(run_all())
    finally:
        _lock.unlink(missing_ok=True)
