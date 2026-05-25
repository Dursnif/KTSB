"""
In-memory session key cache for Kåre per-user encryption.

The private key for each user lives in RAM only during an active JWT session.
It is loaded on PIN login and revoked on logout or token expiry.

Thread-safe via asyncio.Lock.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _SessionEntry:
    private_key: bytes
    expires_at: float  # Unix timestamp


_sessions: dict[str, _SessionEntry] = {}
_lock = asyncio.Lock()


async def store_session_key(user_id: str, private_key: bytes, expires_at: float) -> None:
    """Cache a user's private key in RAM for the duration of their session."""
    async with _lock:
        _sessions[user_id] = _SessionEntry(private_key=private_key, expires_at=expires_at)
    logger.info(f"[SESSION_KEYS] stored key for {user_id}, expires {expires_at:.0f}")


async def get_session_key(user_id: str) -> bytes | None:
    """Return the user's private key if their session is still valid, else None."""
    async with _lock:
        entry = _sessions.get(user_id)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del _sessions[user_id]
            logger.info(f"[SESSION_KEYS] expired key for {user_id} removed")
            return None
        return entry.private_key


def get_session_key_sync(user_id: str) -> bytes | None:
    """Synchronous variant — safe to call from non-async context."""
    entry = _sessions.get(user_id)
    if entry is None:
        return None
    if time.time() > entry.expires_at:
        _sessions.pop(user_id, None)
        return None
    return entry.private_key


async def revoke_session_key(user_id: str) -> None:
    """Remove a user's private key from RAM (logout / token expiry)."""
    async with _lock:
        if user_id in _sessions:
            del _sessions[user_id]
            logger.info(f"[SESSION_KEYS] revoked key for {user_id}")


def active_sessions() -> list[str]:
    """Return list of user_ids with active (non-expired) sessions."""
    now = time.time()
    return [uid for uid, e in _sessions.items() if e.expires_at > now]
