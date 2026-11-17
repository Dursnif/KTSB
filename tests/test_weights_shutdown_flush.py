"""Queued weight rows are never lost silently (BUG-log 2026-11-16 «weights: queued rows lost at a clean stop»).

2 learning rows from a camera answer failed to flush («readonly database»), stayed queued in RAM, and were gone after a
clean restart with no log line. Guarantees here: a failed flush keeps the rows and logs; a retry saves them without any
new write; the API's shutdown flushes the store and says what happened; a failure on the way down is logged.
"""

import ast
import logging
import sqlite3
import time
from pathlib import Path

import pytest

from kaare_core.weights import store as wstore

KAARE_API = Path(__file__).resolve().parent.parent / "kaare_api.py"


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    s = wstore.get_store()
    yield s
    wstore.reset_for_tests()


def _count(db: Path) -> int:
    if not db.exists():
        return 0
    c = sqlite3.connect(str(db))
    try:
        return c.execute("SELECT count(*) FROM evidence").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        c.close()


def _readonly(monkeypatch):
    real = sqlite3.connect

    def boom(*a, **k):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(wstore.sqlite3, "connect", boom)
    return real


def _queue_one(s) -> None:
    assert s.record(("sensor", "test.probe"), source_class="world", source="test")


def test_connect_failure_keeps_rows_and_logs(st, monkeypatch, caplog):
    _queue_one(st)
    _readonly(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=wstore.__name__):
        assert st.flush() == 0  # must not raise
    assert len(st._pending) == 1
    assert any("flush failed" in r.getMessage() for r in caplog.records)


def test_shutdown_flush_saves_rows_left_by_a_failed_flush(st, monkeypatch, caplog):
    _queue_one(st)
    real = _readonly(monkeypatch)
    st.flush()
    monkeypatch.setattr(wstore.sqlite3, "connect", real)  # the file is writable again (chmod)
    with caplog.at_level(logging.INFO, logger=wstore.__name__):
        assert st.shutdown_flush() == 1
    assert _count(st.path) == 1
    assert any("shutdown" in r.getMessage() for r in caplog.records)


def test_failure_on_the_way_down_is_logged_not_swallowed(st, monkeypatch, caplog):
    _queue_one(st)
    _readonly(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=wstore.__name__):
        st._flush_at_exit()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("1 queued row" in m and "lost" in m for m in msgs), msgs


def test_retry_saves_rows_without_a_new_write(st, monkeypatch):
    monkeypatch.setattr(wstore, "RETRY_S", 0.05)
    _queue_one(st)
    real = _readonly(monkeypatch)
    st.flush()
    monkeypatch.setattr(wstore.sqlite3, "connect", real)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and st._pending:
        time.sleep(0.05)
    assert not st._pending
    assert _count(st.path) == 1


def test_no_retry_thread_when_healthy(st):
    _queue_one(st)
    assert st.flush() == 1
    assert st._retry_thread is None


def test_api_shutdown_flushes_the_weight_store():
    tree = ast.parse(KAARE_API.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "_shutdown")
    calls = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "shutdown_flush" in calls
