"""No test opens a database or writes a file under the house's /kaare/state/ (2026-11-16: frigate.db-wal/-shm ended up
owned by the test user, kaare.service then could not write the camera memory). The probe paths sit in a directory that
does not exist, so a missing guard fails with sqlite's own error and creates nothing."""

import sqlite3

import pytest

PROBE = "/kaare/state/__guard_probe__/x.db"


def test_sqlite_connect_under_live_state_is_blocked():
    with pytest.raises(AssertionError, match="live state"):
        sqlite3.connect(PROBE)


def test_open_for_writing_under_live_state_is_blocked():
    with pytest.raises(AssertionError, match="live state"), open("/kaare/state/__guard_probe__/x.txt", "w"):
        pass


def test_tmp_paths_and_memory_still_work(tmp_path):
    sqlite3.connect(":memory:").close()
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("CREATE TABLE a (b)")
    conn.close()
    (tmp_path / "f.txt").write_text("ok")
