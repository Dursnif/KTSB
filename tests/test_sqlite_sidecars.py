"""SQLite -wal/-shm sidecars: group-shared only where the install shares by group (BUG-log 2026-11-16, weights.db).

A process that opens a WAL database creates -wal/-shm with its own umask (often 644). If that process is not the service
user, the service can no longer write («attempt to write a readonly database»). In a setgid data folder (the install
shares by group) the opener makes its own sidecars group read/write; anywhere else nothing changes.
"""

import logging
import os
import sqlite3
import stat
from pathlib import Path

from kaare_core.frigate_user import store as fstore
from kaare_core.storage import sqlite_files
from kaare_core.weights import store as wstore


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _setgid_dir(tmp_path: Path) -> Path:
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(d, 0o2775)
    return d


def _open_wal(db: Path) -> sqlite3.Connection:
    old = os.umask(0o022)  # the stray-session case: a 644 umask
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    finally:
        os.umask(old)
    return conn


def test_setgid_folder_gets_group_writable_sidecars(tmp_path):
    db = _setgid_dir(tmp_path) / "x.db"
    conn = _open_wal(db)
    try:
        sqlite_files.share_with_group(db)
        for suffix in ("-wal", "-shm"):
            assert _mode(Path(str(db) + suffix)) & 0o060 == 0o060, suffix
    finally:
        conn.close()


def test_plain_folder_is_left_alone(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    os.chmod(d, 0o755)
    db = d / "x.db"
    conn = _open_wal(db)
    try:
        before = {s: _mode(Path(str(db) + s)) for s in ("-wal", "-shm")}
        sqlite_files.share_with_group(db)
        assert {s: _mode(Path(str(db) + s)) for s in ("-wal", "-shm")} == before
        assert all(m & 0o020 == 0 for m in before.values())
    finally:
        conn.close()


def test_missing_files_never_raise(tmp_path):
    sqlite_files.share_with_group(tmp_path / "nope" / "x.db")


def test_foreign_owner_warns_once_per_db(tmp_path, monkeypatch, caplog):
    db = _setgid_dir(tmp_path) / "x.db"
    _open_wal(db).close()
    monkeypatch.setattr(sqlite_files.os, "geteuid", lambda: os.stat(db).st_uid + 12345)
    sqlite_files._warned.clear()
    with caplog.at_level(logging.WARNING, logger=sqlite_files.__name__):
        for _ in range(3):
            sqlite_files.share_with_group(db)
    hits = [r for r in caplog.records if "owned by uid" in r.getMessage()]
    assert len(hits) == 1


def test_weight_store_shares_its_sidecars(tmp_path):
    db = _setgid_dir(tmp_path) / "weights.db"
    st = wstore.WeightStore(db)
    old = os.umask(0o022)
    try:
        conn = st._conn()  # kept open: the last close of a WAL db removes the sidecars
        st._migrate(conn)
        for suffix in ("-wal", "-shm"):
            p = Path(str(db) + suffix)
            assert p.exists() and _mode(p) & 0o060 == 0o060, suffix
        conn.close()
    finally:
        os.umask(old)


def test_frigate_store_shares_its_sidecars(tmp_path, monkeypatch):
    db = _setgid_dir(tmp_path) / "frigate.db"
    monkeypatch.setattr(fstore, "DB_PATH", db)
    old = os.umask(0o022)
    try:
        conn = fstore._connect()
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        sidecars = [Path(str(db) + s) for s in ("-wal", "-shm")]
        for p in sidecars:
            assert p.exists() and _mode(p) & 0o060 == 0o060, p.name
        conn.close()
    finally:
        os.umask(old)


def test_shared_db_file_makes_later_openers_sidecars_664(tmp_path):
    """The real fix: once the database file is 664, SQLite gives every later opener's sidecars 664 — also a process
    that never runs KTSB code (sqlite3 CLI, a stray script)."""
    db = _setgid_dir(tmp_path) / "x.db"
    _open_wal(db).close()  # service creates the DB (644 under umask 022); last close removes the sidecars
    assert _mode(db) & 0o020 == 0
    sqlite_files.share_with_group(db)
    assert _mode(db) & 0o060 == 0o060
    conn = _open_wal(db)  # a later opener, plain sqlite3, umask 022
    try:
        for suffix in ("-wal", "-shm"):
            assert _mode(Path(str(db) + suffix)) & 0o060 == 0o060, suffix
    finally:
        conn.close()
