"""N37: no test may queue a line for a live log through log_buffer (BUG-log 2026-11-06, sideøkt 2).

log_buffer writes from a background thread with os.open — the conftest open() guard never sees it, and a line queued
during a test is flushed after the test (or at process exit) into the house's file. Every suite run left one fake
`rid-test-refl` row in logs/llm_calls.log (40 rows by 2026-11-06). The probe never touches the disk: _append is faked,
so a red run only shows where the line *would* have gone.
"""

from pathlib import Path

from kaare_core import log_buffer

LIVE = Path("/kaare/logs").resolve()


def test_a_line_for_a_live_log_lands_outside_logs(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(log_buffer, "_append", lambda key, lines: written.append(key))
    log_buffer.write(LIVE / "llm_calls.log", {"rid": "rid-test-n37-probe"})
    log_buffer.flush()
    assert written, "the probe line was never flushed"
    assert not any(Path(k).resolve().is_relative_to(LIVE) for k in written), written


def test_a_tmp_path_log_is_left_alone(monkeypatch, tmp_path):
    written: list[str] = []
    monkeypatch.setattr(log_buffer, "_append", lambda key, lines: written.append(key))
    log_buffer.write(tmp_path / "mine.log", {"x": 1})
    log_buffer.flush()
    assert written == [str(tmp_path / "mine.log")]
