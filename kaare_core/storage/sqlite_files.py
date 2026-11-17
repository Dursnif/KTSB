"""SQLite database files: keep them writable for the whole install (BUG-log 2026-11-16, weights.db).

A WAL database gets two sidecar files (-wal/-shm), created by whichever process opens it first. SQLite gives them the
same mode as the database file itself (measured 2026-11-16). With the database at 644, a process that is not the
service user (a dev script, a second service under another user) creates 644 sidecars it owns, and the service can no
longer write: «attempt to write a readonly database».

`share_with_group(db_path)` runs after every connect:
  - Only in a setgid data folder — the sign that this install shares its state by group. Anywhere else (a stranger's
    Docker volume, a plain folder) it does nothing, so it never widens access nobody asked for.
  - Adds group read/write to the database file and its sidecars, only on files this process owns (chmod on another
    user's file is never tried). Once the database file is 664, every later opener's sidecars are 664 too.
  - Logs one warning per process and database when the opener is not the database file's owner — the stray-process
    case — so the log says who opened it. Never raises.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_SUFFIXES = ("-wal", "-shm")
_warned: set[str] = set()


def connect(database, *args, **kwargs) -> sqlite3.Connection:
    """`sqlite3.connect` with the caller's exact arguments, then `share_with_group` on the real file (N42).

    Every opener of a KTSB database uses this, so the database file is group-writable from its first open (SQLite
    creates the file at connect) and every later opener's sidecars inherit that mode. Connect errors are raised as
    before; only the file-mode step never raises.
    """
    conn = sqlite3.connect(database, *args, **kwargs)
    path = _file_path(conn)
    if path:
        share_with_group(path)
    return conn


def _file_path(conn: sqlite3.Connection) -> str | None:
    """The file SQLite actually opened (URI, Path and any redirect resolved); None for an in-memory database."""
    try:
        for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
            if name == "main":
                return file or None
    except Exception as e:  # never break a connect over file modes
        logger.debug("sqlite_files: database_list failed: %s", e)
    return None


def share_with_group(db_path: Path | str) -> None:
    """Give this process's own database file and sidecars group read/write when the folder is setgid. Never raises."""
    try:
        db = Path(db_path)
        if not db.parent.stat().st_mode & stat.S_ISGID:
            return
        euid = os.geteuid()
        _warn_foreign_opener(db, euid)
        for f in (db, *(Path(str(db) + s) for s in SIDECAR_SUFFIXES)):
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            if st.st_uid != euid or st.st_mode & 0o060 == 0o060:
                continue
            os.chmod(f, stat.S_IMODE(st.st_mode) | 0o060)
    except Exception as e:  # never break a database connect over file modes
        logger.debug("share_with_group(%s) skipped: %s", db_path, e)


def _warn_foreign_opener(db: Path, euid: int) -> None:
    try:
        owner = db.stat().st_uid
    except FileNotFoundError:
        return
    key = str(db)
    if owner == euid or key in _warned:
        return
    _warned.add(key)
    logger.warning(
        "%s opened by uid %d but owned by uid %d (pid %d) - a script or service other than the database owner is "
        "using this live database", db, euid, owner, os.getpid())


def sidecar_report(db_path: Path | str) -> str:
    """Owner and mode of the database file and its sidecars, for an error message. Never raises."""
    parts = []
    for f in (Path(db_path), *(Path(str(db_path) + s) for s in SIDECAR_SUFFIXES)):
        try:
            st = f.stat()
            parts.append(f"{f.name} uid={st.st_uid} mode={stat.S_IMODE(st.st_mode):o}")
        except Exception:
            parts.append(f"{f.name} missing")
    return ", ".join(parts) + " - fix: chmod g+w on the files as their owner (or remove the stray process's sidecars)"
