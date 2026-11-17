"""P144-s14 item 4: no test writes under the live /kaare/logs.

- importing kaare_night_sequence attaches no handler (the FileHandler is attached when the night sequence runs:
  `attach_file_log()` from main());
- the conftest guard catches a write-mode open() under /kaare/logs and a FileHandler pointing there.
"""

import logging

import conftest
import pytest

import kaare_night_sequence as ns


def test_import_attaches_no_live_handler():
    assert conftest.live_log_handlers() == []


def test_attach_file_log_writes_where_told(tmp_path):
    root = logging.getLogger()
    target = tmp_path / "night_sequence.log"
    h = ns.attach_file_log(target)
    try:
        assert h is not None and h in root.handlers
        logging.getLogger("night_sequence").warning("hello from the test")
        h.flush()
        assert "hello from the test" in target.read_text()
    finally:
        root.removeHandler(h)
        h.close()


def test_guard_blocks_write_open_under_live_logs():
    with pytest.raises(AssertionError, match="live log"):
        open("/kaare/logs/p144_s14_guard_probe.log", "a")  # noqa: SIM115


def test_guard_lets_reads_through():
    with open("/kaare/logs/night_sequence.log", "rb") as f:  # reading is fine
        f.read(1)


def test_guard_sees_a_live_handler():
    h = logging.FileHandler.__new__(logging.FileHandler)  # never opened: no file is touched
    h.baseFilename = "/kaare/logs/p144_s14_guard_probe.log"
    logging.Handler.__init__(h)
    root = logging.getLogger()
    root.addHandler(h)
    try:
        assert conftest.live_log_handlers() == ["root → /kaare/logs/p144_s14_guard_probe.log"]
    finally:
        root.removeHandler(h)
