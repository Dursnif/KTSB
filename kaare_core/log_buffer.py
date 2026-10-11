"""
RAM-buffered append-only log writer (P131 — CLAUDE.md rule 10, no unnecessary I/O-wait).

write()/write_line() put a line in memory and return at once — no disk access, no await, safe from
sync and async code and from any thread. A background thread writes everything pending with one
write() per file every get_log_flush_seconds() (GUI: Settings → General → Logs and data, default
2 s), earlier when a file's buffer passes _MAX_PENDING_LINES, and at process exit. Interval 0 writes
every line at once (the old behaviour, for live debugging).

Readers in the same process call flush(path) before reading, so they see the latest lines. Other
processes see a line at most one interval late. On a hard stop (power cut, kill -9) at most one
interval of lines is lost — the accepted trade-off (P131 D3). Security/audit logs and data files do
not use this module.
"""

import atexit
import json
import os
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from kaare_core.config import get_log_flush_seconds

_MAX_PENDING_LINES = 5000  # per file: flush early (memory guard)
_MAX_RETAINED_LINES = 50_000  # per file while the disk keeps failing; oldest dropped beyond this

_lock = threading.Lock()  # guards _pending
_io_lock = threading.RLock()  # one flush/rewrite at a time, so lines reach each file in order
_pending: dict[str, list[str]] = {}
_after_flush: dict[str, Callable[[], None]] = {}
_wake = threading.Event()
_flusher: threading.Thread | None = None


def write(path: str | Path, record: dict) -> None:
    """Queue one JSON line. Never raises, never touches the disk (unless the interval is 0)."""
    try:
        write_line(path, json.dumps(record, ensure_ascii=False))
    except Exception:
        pass


def write_line(path: str | Path, line: str) -> None:
    """Queue one text line (a newline is added). Never raises."""
    try:
        key = str(path)
        if get_log_flush_seconds() <= 0:
            _append(key, [line])
            return
        with _lock:
            buf = _pending.setdefault(key, [])
            buf.append(line)
            full = len(buf) >= _MAX_PENDING_LINES
        _ensure_flusher()
        if full:
            _wake.set()
    except Exception:
        pass


def on_flush(path: str | Path, hook: Callable[[], None]) -> None:
    """Run hook after each flush of path (e.g. trimming a file) — inside the I/O lock, no race."""
    _after_flush[str(path)] = hook


def flush(path: str | Path | None = None) -> None:
    """Write pending lines now — all files, or only path. Never raises."""
    try:
        with _io_lock:
            with _lock:
                keys = [str(path)] if path is not None else list(_pending)
                batches = {k: _pending.pop(k) for k in keys if _pending.get(k)}
            for key, lines in batches.items():
                try:
                    _append(key, lines)
                except Exception:
                    _requeue(key, lines)
                    continue
                hook = _after_flush.get(key)
                if hook:
                    try:
                        hook()
                    except Exception:
                        pass
    except Exception:
        pass


@contextmanager
def locked(path: str | Path) -> Iterator[None]:
    """Hold off all flushes while the caller rewrites path (trim/cleanup): pending lines for path
    are written first, and no batch can land between the caller's read and write."""
    with _io_lock:
        flush(path)
        yield


def _append(key: str, lines: list[str]) -> None:
    """One O_APPEND write per batch: another process's batch never lands in the middle of it."""
    Path(key).parent.mkdir(parents=True, exist_ok=True)
    data = ("\n".join(lines) + "\n").encode("utf-8")
    fd = os.open(key, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def _requeue(key: str, lines: list[str]) -> None:
    """Disk write failed: keep the lines (in order, ahead of newer ones) for the next try."""
    with _lock:
        merged = lines + _pending.get(key, [])
        _pending[key] = merged[-_MAX_RETAINED_LINES:]


def _run() -> None:
    while True:
        _wake.wait(timeout=max(get_log_flush_seconds(), 0.5))
        _wake.clear()
        flush()


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return
    with _lock:
        if _flusher is None or not _flusher.is_alive():
            _flusher = threading.Thread(target=_run, name="log-buffer-flush", daemon=True)
            _flusher.start()


def install_sigterm_flush() -> None:
    """For plain scripts/services without their own shutdown hook: flush on SIGTERM (systemd stop),
    then exit as SIGTERM would. Call once at startup from the main thread."""

    def _handler(signum, frame):
        flush()
        raise SystemExit(128 + signum)

    try:
        signal.signal(signal.SIGTERM, _handler)
    except Exception:
        pass


atexit.register(flush)
