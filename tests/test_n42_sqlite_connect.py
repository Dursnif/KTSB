"""N42: every SQLite opener goes through `sqlite_files.connect()` so a live database file is group-writable from the start.

SQLite gives -wal/-shm the mode of the database file. If the file is 644, a process that is not the service user leaves
sidecars the service cannot write («attempt to write a readonly database» — frigate.db lost 528 writes 2026-11-16). The
helper = `sqlite3.connect` with the caller's exact arguments + `share_with_group` on the real file. No-op outside a
setgid folder (Docker: one uid, nothing changes).
"""

import ast
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from kaare_core.storage import sqlite_files

ROOT = Path(__file__).resolve().parent.parent
_SKIP_PARTS = {"venv", "node_modules", "tests", "backups", "frontend", "__pycache__", ".cache"}


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _setgid_dir(tmp_path: Path) -> Path:
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(d, 0o2775)
    return d


def test_new_db_in_setgid_folder_and_its_sidecars_are_group_writable(tmp_path):
    db = _setgid_dir(tmp_path) / "x.db"
    old = os.umask(0o022)  # the stray-session umask
    try:
        conn = sqlite_files.connect(str(db), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        for f in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
            assert f.exists() and _mode(f) & 0o060 == 0o060, f.name
        conn.close()
    finally:
        os.umask(old)


def test_read_only_uri_shares_the_real_file(tmp_path):
    db = _setgid_dir(tmp_path) / "x.db"
    old = os.umask(0o022)
    try:
        c = sqlite3.connect(str(db))
        c.execute("CREATE TABLE t (x)")
        c.commit()
        c.close()
    finally:
        os.umask(old)
    assert _mode(db) & 0o020 == 0
    conn = sqlite_files.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    finally:
        conn.close()
    assert _mode(db) & 0o060 == 0o060


def test_plain_folder_is_left_alone(tmp_path):
    """Docker / a stranger's volume: no setgid → modes stay exactly as the umask made them."""
    d = tmp_path / "plain"
    d.mkdir()
    os.chmod(d, 0o755)
    db = d / "x.db"
    old = os.umask(0o022)
    try:
        conn = sqlite_files.connect(str(db))
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
        conn.close()
    finally:
        os.umask(old)
    assert _mode(db) == 0o644


def test_memory_and_path_objects(tmp_path):
    sqlite_files.connect(":memory:").close()
    sqlite_files.connect(_setgid_dir(tmp_path) / "p.db").close()  # a Path, as argus_size passes it


def test_arguments_are_passed_through_unchanged(tmp_path, monkeypatch):
    seen = {}
    real = sqlite3.connect

    def spy(database, *args, **kwargs):
        seen["call"] = (database, args, kwargs)
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    db = str(tmp_path / "x.db")
    sqlite_files.connect(db, check_same_thread=False, timeout=7.5).close()
    assert seen["call"] == (db, (), {"check_same_thread": False, "timeout": 7.5})


def test_connect_errors_are_not_swallowed(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        sqlite_files.connect(f"file:{tmp_path / 'missing.db'}?mode=ro", uri=True)


def _production_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if _SKIP_PARTS & set(rel.parts) or rel.parts[0] in {"intent_server", "semantic_embed"}:
            continue
        yield p


def _bare_connects(path: Path) -> list[int]:
    """Lines with `sqlite3.connect(...)` whose first argument is not the literal ':memory:'."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "connect" and isinstance(f.value, ast.Name) \
                and f.value.id in {"sqlite3", "aiosqlite"}:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and first.value == ":memory:":
                continue
            hits.append(node.lineno)
    return hits


def test_every_production_opener_goes_through_the_helper():
    helper = ROOT / "kaare_core" / "storage" / "sqlite_files.py"
    found = {str(p.relative_to(ROOT)): lines for p in _production_files() if p != helper
             for lines in [_bare_connects(p)] if lines}
    assert found == {}, f"use kaare_core.storage.sqlite_files.connect() instead of sqlite3.connect: {found}"


def test_guard_sees_a_bare_connect(tmp_path):
    """The guard itself: shown red against a known-bad file."""
    bad = tmp_path / "bad.py"
    bad.write_text("import sqlite3\nc = sqlite3.connect('/x/y.db')\nm = sqlite3.connect(':memory:')\n")
    assert _bare_connects(bad) == [2]
