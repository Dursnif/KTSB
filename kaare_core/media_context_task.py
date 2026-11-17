"""
Background task: keeps state/plex_cache.json up to date.

P140: the 60 s Plex poller is replaced by the Plex listener (kaare_core/media/plex_listener.py) — one websocket,
pushed updates, plus KTSB's own watch index. This module stays as the entry point kaare_api spawns.
MPD status is checked synchronously in context_builder._media_block() — no task needed.
"""

import logging

from kaare_core.media.plex_listener import run_plex_listener, set_user_lookup
from kaare_core.users.store import get_username_for_plex_account

logger = logging.getLogger("media_context_task")


async def start_media_context_task() -> None:
    """Long-running background task. Idle while Plex is not configured; never raises."""
    set_user_lookup(get_username_for_plex_account)
    logger.info("[media_context] Plex listener started")
    await run_plex_listener()
