"""
Simple sliding-window rate limiter for Kåre API endpoints.

Per-user counters stored in memory (single-instance only — no Redis needed).
Consistent with the login rate limiter pattern from SEC-01 (router_users.py).
"""
import time
import threading
from collections import deque

_lock = threading.Lock()
_windows: dict[str, deque] = {}  # key → deque of request timestamps (monotonic)


def check_rate_limit(key: str, limit_per_minute: int) -> bool:
    """Return True if the request is allowed, False if rate limit exceeded.

    Uses a 60-second sliding window. Thread-safe.
    limit_per_minute <= 0 disables limiting for this key.
    """
    if limit_per_minute <= 0:
        return True
    now = time.monotonic()
    cutoff = now - 60.0
    with _lock:
        dq = _windows.setdefault(key, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit_per_minute:
            return False
        dq.append(now)
        return True


def get_window_count(key: str) -> int:
    """Return current request count in the last 60 seconds for a key."""
    now = time.monotonic()
    cutoff = now - 60.0
    with _lock:
        dq = _windows.get(key, deque())
        return sum(1 for ts in dq if ts >= cutoff)
