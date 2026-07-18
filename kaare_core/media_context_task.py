"""
Background task: keeps state/plex_cache.json up to date.

Polls Plex for active sessions every _POLL_INTERVAL seconds.
No-op if Plex is not configured (url empty).
MPD status is checked synchronously in context_builder._media_block() — no task needed.
"""

import asyncio
import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("media_context_task")

_PLEX_CACHE_PATH = Path("/kaare/state/plex_cache.json")
_POLL_INTERVAL   = 60  # seconds


def _plex_cfg() -> dict:
    try:
        from kaare_core.config import get_service
        return get_service("media", "plex") or {}
    except Exception:
        return {}


def _plex_token() -> str:
    try:
        token_file = Path("/kaare/configs/plex_token.env")
        for line in token_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("PLEX_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _write_cache(data: dict) -> None:
    try:
        _PLEX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLEX_CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.debug("[media_context] cache write error: %s", e)


async def _poll_plex() -> None:
    cfg   = _plex_cfg()
    url   = cfg.get("url", "")
    token = _plex_token()
    if not url or not token:
        return

    try:
        headers = {"X-Plex-Token": token, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/sessions", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return

    metadata = data.get("MediaContainer", {}).get("Metadata") or []
    sessions = []
    for item in metadata:
        state = (item.get("Player") or {}).get("state", "")
        if state not in ("playing", "buffering"):
            continue
        title = item.get("title", "")
        show  = item.get("grandparentTitle", "")
        display = f"{show} – {title}" if show else title
        sessions.append({"title": display, "state": state})

    _write_cache({"sessions": sessions})


async def start_media_context_task() -> None:
    """Long-running background task. No-op if Plex not configured."""
    cfg = _plex_cfg()
    if not cfg.get("url"):
        logger.info("[media_context] Plex not configured — task idle")
        return

    logger.info("[media_context] Plex poller started (every %ds)", _POLL_INTERVAL)
    while True:
        try:
            await _poll_plex()
        except Exception as e:
            logger.debug("[media_context] Poll error: %s", e)
        await asyncio.sleep(_POLL_INTERVAL)
