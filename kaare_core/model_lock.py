"""
Cross-process lock for Ollama containers shared by multiple processes.

Uses fcntl.flock with LOCK_NB polling — released automatically if the process crashes.
Use as async context manager: async with lock_11445("miss_kare"):

Port 11445 is shared by:
  - Miss Kare evaluator  (kaare.service)
  - Mechanic tools       (kaare.service — in-process via job_store)
  - Miss Kare reflection (kaare-reflection.service)
  - Mechanic dev-meeting (kaare-dev-meeting.service)
"""

import asyncio
import fcntl
import logging
import os
import time
from contextlib import asynccontextmanager

log = logging.getLogger("model_lock")

_LOCK_DIR = "/kaare/runtime"
os.makedirs(_LOCK_DIR, exist_ok=True)

_LOCK_11445 = f"{_LOCK_DIR}/model_11445.lock"

try:
    _fd = os.open(_LOCK_11445, os.O_CREAT | os.O_WRONLY, 0o664)
    os.close(_fd)
except Exception:
    pass


class LockTimeout(Exception):
    pass


def _acquire(path: str, caller: str) -> object:
    """Blocking acquire with periodic progress logging. Runs in a thread."""
    fd = open(path, "w")
    t0 = time.monotonic()
    last_log = 0.0

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            waited = time.monotonic() - t0
            if waited > 2:
                log.info("[model_lock] %s acquired lock after %.0fs", caller, waited)
            return fd
        except BlockingIOError:
            elapsed = time.monotonic() - t0
            if elapsed - last_log >= 30:
                log.warning("[model_lock] %s waiting for lock (%.0fs, no timeout)", caller, elapsed)
                last_log = elapsed
            time.sleep(0.25)


def _acquire_timed(path: str, caller: str, max_wait: float) -> object | None:
    """Non-blocking acquire with timeout. Returns None if max_wait exceeded."""
    fd = open(path, "w")
    t0 = time.monotonic()
    last_log = 0.0

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            waited = time.monotonic() - t0
            if waited > 2:
                log.info("[model_lock] %s acquired lock after %.0fs", caller, waited)
            return fd
        except BlockingIOError:
            elapsed = time.monotonic() - t0
            if elapsed >= max_wait:
                log.error("[model_lock] %s: lock timeout after %.0fs — giving up", caller, max_wait)
                fd.close()
                return None
            if elapsed - last_log >= 30:
                log.warning("[model_lock] %s waiting for lock (%.0fs/%.0fs)", caller, elapsed, max_wait)
                last_log = elapsed
            time.sleep(0.25)


def _release(fd) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except Exception:
        pass


@asynccontextmanager
async def lock_11445(caller: str = "ukjent", max_wait: float | None = None):
    """
    Serialises access to Ollama port 11445 across processes.

    max_wait: if set and exceeded, raises LockTimeout instead of blocking forever.
    Usage:
        async with lock_11445("miss_kare", max_wait=60): ...
        async with lock_11445("mechanic"):             ...  # blocks forever (safe for long jobs)
    """
    if max_wait is not None:
        fd = await asyncio.to_thread(_acquire_timed, _LOCK_11445, caller, max_wait)
        if fd is None:
            raise LockTimeout(f"{caller}: could not acquire model lock within {max_wait:.0f}s")
    else:
        fd = await asyncio.to_thread(_acquire, _LOCK_11445, caller)
    try:
        yield
    finally:
        _release(fd)
