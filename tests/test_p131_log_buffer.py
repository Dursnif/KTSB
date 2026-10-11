"""kaare_core.log_buffer — RAM-buffered log writer (P131)."""

import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from kaare_core import log_buffer as lb


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(lb, "get_log_flush_seconds", lambda: 2.0)
    lb.flush()
    with lb._lock:
        lb._pending.clear()
    lb._after_flush.clear()
    yield
    with lb._lock:
        lb._pending.clear()
    lb._after_flush.clear()


def _lines(p):
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_write_does_not_touch_disk_until_flush(tmp_path):
    p = tmp_path / "a.log"
    lb.write(p, {"n": 1})
    lb.write(p, {"n": 2})
    assert not p.exists()
    lb.flush(p)
    assert [json.loads(x)["n"] for x in _lines(p)] == [1, 2]


def test_one_write_call_per_file_per_flush(tmp_path, monkeypatch):
    p = tmp_path / "b.log"
    for i in range(100):
        lb.write_line(p, f"line {i}")
    calls = []
    real = os.write
    monkeypatch.setattr(lb.os, "write", lambda fd, data: calls.append(len(data)) or real(fd, data))
    lb.flush()
    assert len(calls) == 1 and len(_lines(p)) == 100


def test_flush_one_path_leaves_others_pending(tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    lb.write_line(a, "x")
    lb.write_line(b, "y")
    lb.flush(a)
    assert _lines(a) == ["x"] and not b.exists()
    lb.flush()
    assert _lines(b) == ["y"]


def test_interval_zero_writes_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, "get_log_flush_seconds", lambda: 0.0)
    p = tmp_path / "c.log"
    lb.write_line(p, "now")
    assert _lines(p) == ["now"]


def test_full_buffer_wakes_flusher_early(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, "get_log_flush_seconds", lambda: 30.0)
    monkeypatch.setattr(lb, "_MAX_PENDING_LINES", 10)
    p = tmp_path / "d.log"
    for i in range(10):
        lb.write_line(p, str(i))
    for _ in range(50):
        if len(_lines(p)) == 10:
            break
        threading.Event().wait(0.05)
    assert len(_lines(p)) == 10


def test_disk_error_keeps_lines_and_never_raises(tmp_path, monkeypatch):
    p = tmp_path / "e.log"
    lb.write_line(p, "first")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(lb, "_append", boom)
    lb.flush()  # must not raise
    monkeypatch.undo()
    monkeypatch.setattr(lb, "get_log_flush_seconds", lambda: 2.0)
    lb.write_line(p, "second")
    lb.flush()
    assert _lines(p) == ["first", "second"]  # order kept


def test_many_threads_no_line_lost(tmp_path):
    p = tmp_path / "f.log"

    def worker(n):
        for i in range(200):
            lb.write_line(p, f"{n}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lb.flush()
    assert len(_lines(p)) == 8 * 200


def test_after_flush_hook_runs(tmp_path):
    p = tmp_path / "g.log"
    seen = []
    lb.on_flush(p, lambda: seen.append(len(_lines(p))))
    lb.write_line(p, "x")
    lb.flush()
    assert seen == [1]


def test_write_never_raises_on_bad_record(tmp_path):
    lb.write(tmp_path / "h.log", {"bad": object()})  # not JSON-serialisable


def test_pending_lines_written_at_normal_exit(tmp_path):
    """A process that exits normally without calling flush() still writes its lines (atexit)."""
    p = tmp_path / "exit.log"
    code = (
        "from kaare_core import log_buffer as lb\n"
        "lb.get_log_flush_seconds = lambda: 30.0\n"
        f"lb.write_line({str(p)!r}, 'bye')\n"
    )
    subprocess.run(
        [sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": "/kaare"}
    )
    assert _lines(p) == ["bye"]


def test_locked_flushes_first_and_blocks_flush_during_rewrite(tmp_path):
    p = tmp_path / "r.log"
    lb.write_line(p, "old")
    with lb.locked(p):
        assert _lines(p) == ["old"]  # pending line written before the rewrite
        lb.write_line(p, "during")
        t = threading.Thread(target=lb.flush)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive()  # the flush waits for the rewrite to finish
        p.write_text("rewritten\n", encoding="utf-8")
    t.join(timeout=2)
    assert _lines(p) == ["rewritten", "during"]


def test_sigterm_flushes_buffered_lines(tmp_path):
    """systemd stop sends SIGTERM: install_sigterm_flush() writes the buffer before exit."""
    p = tmp_path / "term.log"
    ready = tmp_path / "ready"
    code = (
        "import time\n"
        "from kaare_core import log_buffer as lb\n"
        "lb.get_log_flush_seconds = lambda: 30.0\n"
        "lb.install_sigterm_flush()\n"
        f"lb.write_line({str(p)!r}, 'before-stop')\n"
        f"open({str(ready)!r}, 'w').close()\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": "/kaare"}
    )
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    assert _lines(p) == ["before-stop"]
