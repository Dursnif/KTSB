"""P144-s13 — save points must carry the Frigate memory, Kåre's drawers, the watch history and the kept episode frames.

Before: `frigate/frigate.db`, `memory/learning.db`, `media/watch.db` and `frigate_snapshots/episodes/` were not in any
category, so a stranger who restores a save point lost them. Restore also wrote memory DBs with a plain file replace,
which left the house's old `-wal` beside the new file — SQLite then read the old WAL frames over the restored data.

Every test runs on a tmp state dir (router_backup._STATE_DIR is repointed); the live house is never read or written.
"""

import asyncio
import io
import os
import sqlite3
import threading
import zipfile
from pathlib import Path

import pytest

from kaare_core.routers import router_backup

MEMORY_DBS = ["memory/interactions.db", "memory/learning.db", "frigate/frigate.db", "media/watch.db"]


def _make_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    c.execute("DELETE FROM t")
    c.execute("INSERT INTO t VALUES (?)", (value,))
    c.commit()
    c.close()


def _read(path: Path) -> list[str]:
    c = sqlite3.connect(path)
    try:
        return [r[0] for r in c.execute("SELECT v FROM t")]
    finally:
        c.close()


@pytest.fixture
def state(tmp_path, monkeypatch):
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "users").mkdir()
    (sd / "users" / "users.db").write_bytes(b"users")  # ltm_database is encrypted: a save needs the user keys
    monkeypatch.setattr(router_backup, "_STATE_DIR", sd)
    return sd


def _zip_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def _restore(data: bytes, cats: set) -> dict:
    return asyncio.run(router_backup._restore_from_zip(data, cats, "admin"))


def test_save_point_carries_every_memory_db(state):
    for rel in MEMORY_DBS:
        _make_db(state / rel, f"saved:{rel}")
    data = asyncio.run(router_backup._build_backup_zip({"ltm_database", "user_keys"}))
    names = _zip_names(data)
    for arc in ("data/interactions.db", "data/learning.db", "data/frigate/frigate.db", "data/media/watch.db"):
        assert arc in names, f"{arc} missing from the save point: {names}"


def test_memory_dbs_round_trip(state):
    for rel in MEMORY_DBS:
        _make_db(state / rel, f"saved:{rel}")
    data = asyncio.run(router_backup._build_backup_zip({"ltm_database", "user_keys"}))
    for rel in MEMORY_DBS:
        _make_db(state / rel, "changed after the save")
    res = _restore(data, {"ltm_database"})
    assert res["errors"] == []
    assert res["restart_needed"] is True
    for rel in MEMORY_DBS:
        assert _read(state / rel) == [f"saved:{rel}"], rel


def test_restore_into_empty_state_creates_the_dbs(state):
    for rel in MEMORY_DBS:
        _make_db(state / rel, f"saved:{rel}")
    data = asyncio.run(router_backup._build_backup_zip({"ltm_database", "user_keys"}))
    fresh = state.parent / "fresh"
    fresh.mkdir()
    router_backup._STATE_DIR = fresh  # monkeypatch fixture restores the original afterwards
    res = _restore(data, {"ltm_database"})
    assert res["errors"] == []
    for rel in MEMORY_DBS:
        assert _read(fresh / rel) == [f"saved:{rel}"], rel
        assert not (fresh / (rel + "-wal")).exists() and not (fresh / (rel + "-shm")).exists()


@pytest.mark.parametrize("rel", ["memory/interactions.db", "frigate/frigate.db"])
def test_restore_wins_over_a_stale_wal_of_the_running_house(state, rel):
    """The service still holds its connections while a restore runs; their WAL must not shadow the restored DB."""
    _make_db(state / rel, "saved")
    data = asyncio.run(router_backup._build_backup_zip({"ltm_database", "user_keys"}))
    live = sqlite3.connect(state / rel)
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("DELETE FROM t")
    live.execute("INSERT INTO t VALUES ('house, written after the save')")
    live.commit()
    try:
        assert (state / (rel + "-wal")).exists()
        res = _restore(data, {"ltm_database"})
        assert res["errors"] == []
        assert _read(state / rel) == ["saved"]
        assert [r[0] for r in live.execute("SELECT v FROM t")] == ["saved"]
    finally:
        live.close()


def test_restore_keeps_the_file_mode(state):
    rel = "memory/learning.db"
    _make_db(state / rel, "saved")
    data = asyncio.run(router_backup._build_backup_zip({"ltm_database", "user_keys"}))
    os.chmod(state / rel, 0o660)
    _restore(data, {"ltm_database"})
    assert (state / rel).stat().st_mode & 0o777 == 0o660


def test_restore_rejects_a_broken_db_and_keeps_the_house_copy(state):
    rel = "memory/learning.db"
    _make_db(state / rel, "house")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("backup_meta.json", '{"categories": ["ltm_database"]}')
        zf.writestr("keys/users.db", b"")
        zf.writestr("data/learning.db", b"not a sqlite file" * 100)
    res = _restore(buf.getvalue(), {"ltm_database"})
    assert any("learning.db" in e for e in res["errors"])
    assert _read(state / rel) == ["house"]


def test_live_wal_db_is_copied_consistently(state):
    """Each writer transaction inserts a pair; the copy must never hold half a pair."""
    path = state / "frigate" / "frigate.db"
    path.parent.mkdir(parents=True)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE p (n INTEGER, side TEXT)")
    c.execute("CREATE TABLE pad (b BLOB)")
    c.executemany("INSERT INTO pad VALUES (?)", [(os.urandom(4000),) for _ in range(2000)])
    c.commit()
    c.close()
    stop = threading.Event()

    def writer():
        w = sqlite3.connect(path, timeout=10)
        n = 0
        while not stop.is_set():
            with w:
                w.execute("INSERT INTO p VALUES (?, 'a')", (n,))
                w.execute("INSERT INTO p VALUES (?, 'b')", (n,))
            n += 1
        w.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        copies = [router_backup._sqlite_backup_bytes(path) for _ in range(5)]
    finally:
        stop.set()
        t.join()
    for data in copies:
        m = sqlite3.connect(":memory:")
        m.deserialize(router_backup._as_rollback_image(data))
        assert m.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        rows = m.execute("SELECT side, COUNT(*) FROM p GROUP BY side").fetchall()
        counts = dict(rows)
        assert counts.get("a", 0) == counts.get("b", 0), counts
        m.close()


def test_kept_episode_frames_travel_with_images_but_detection_snapshots_do_not(state):
    ep = state / "frigate_snapshots" / "episodes" / "42"
    ep.mkdir(parents=True)
    (ep / "0.jpg").write_bytes(b"frame0")
    (ep / "1.jpg").write_bytes(b"frame1")
    (state / "frigate_snapshots" / "1786381175.255423-n222jk.jpg").write_bytes(b"detection")
    data = asyncio.run(router_backup._build_backup_zip({"images"}))
    names = _zip_names(data)
    assert "state/frigate_snapshots/episodes/42/0.jpg" in names
    assert "state/frigate_snapshots/episodes/42/1.jpg" in names
    assert not any(n.startswith("state/frigate_snapshots/1786") for n in names)

    for f in ep.iterdir():
        f.unlink()
    ep.rmdir()
    res = _restore(data, {"images"})
    assert res["errors"] == []
    assert (ep / "0.jpg").read_bytes() == b"frame0"
    assert (ep / "1.jpg").read_bytes() == b"frame1"


def test_detection_snapshots_are_never_restored_even_if_a_zip_carries_them(state):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("backup_meta.json", '{"categories": ["images"]}')
        zf.writestr("state/frigate_snapshots/x.jpg", b"detection")
    _restore(buf.getvalue(), {"images"})
    assert not (state / "frigate_snapshots" / "x.jpg").exists()
