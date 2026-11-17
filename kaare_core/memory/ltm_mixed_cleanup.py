"""
Find and mark the LTM episodes the old cross-user nightjob loop mixed — once per install, by itself (P146-s01).

Before P146 the nightjob compressed the next 20 rows in id order regardless of user and filed the episode under the
user with most rows. P146 fixed the writer and the readers skip episodes whose status is not 'ok'; this step marks the
old damage on every install that upgrades. It runs from the nightjob (never from the API start-up path: it reads every
old episode range and talks to Qdrant).

Rules (one checkable answer per episode, no model):
  - Only episodes written by the OLD loop are judged: ts_created earlier than the P146 floor row in ltm_cursors
    (written once, the first time P146 code opened the database, before any per-user episode existed). A new
    per-user episode's range legitimately holds other users' rows and is never judged.
  - An old episode is 'mixed' if its from_id..to_id range holds a row (outcome success/llm_fallback, the old loop's
    selection) of any user_id other than its owner — 'global', 'kare', 'frigate' and strangers count as other users.
  - An old 'global' episode written by the global (Jing) loop is never judged: that loop takes only global rows, so
    its range may hold person rows it never used. It is recognised by interaction_count == the global rows in range.
Then every person with rows in a newly marked episode is re-queued for the per-user loop (option (b)): the cursor
moves back to just before the first affected row, and the person's own 'ok' episodes from there on become
'superseded' (their rows are compressed again). A person whose encrypted rows the night cannot read waits.
Nothing is deleted. SQLite is backed up (backup API) before the first write; Qdrant gets the same status by
set_payload; a missing point is counted, Qdrant down is retried next night. Logs carry ids and counts only.
"""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from kaare_core.memory import long_term, semantic_memory
from kaare_core.memory.long_term import (
    EPISODE_MIXED,
    EPISODE_OK,
    EPISODE_SUPERSEDED,
    LTM_CURSOR_FLOOR,
    USER_GLOBAL,
)
from kaare_core.memory.memory_embed_models import MODELS
from kaare_core.storage import sqlite_files

logger = logging.getLogger(__name__)

STATE_NAME = "ltm_mixed_cleanup.json"  # next to interactions.db
BACKUP_SUBDIR = ("backups", "ltm_mixed_mark")  # under state/ — mounted in every Docker service, unlike /kaare/backups
OLD_LOOP_OUTCOMES = ("success", "llm_fallback")
_UNREADABLE = "[kryptert]"
_QDRANT_CHUNK = 256


def _parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _old_loop_cutoff(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT ts_updated FROM ltm_cursors WHERE user_id = ?", (LTM_CURSOR_FLOOR,)).fetchone()
    return _parse_ts(row[0]) if row else None


def _range_users(conn: sqlite3.Connection, from_id: int, to_id: int) -> dict[str, list[int]]:
    """user_id → row ids in the range, with the old loop's outcome filter."""
    out: dict[str, list[int]] = {}
    cur = conn.execute(
        "SELECT id, user_id FROM interactions WHERE id BETWEEN ? AND ? AND outcome IN (?, ?) ORDER BY id",
        (from_id, to_id, *OLD_LOOP_OUTCOMES),
    )
    for row_id, uid in cur:
        out.setdefault(uid, []).append(row_id)
    return out


def _is_global_loop_episode(conn: sqlite3.Connection, from_id: int, to_id: int, count: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE id BETWEEN ? AND ? AND user_id = ? AND COALESCE(repaired, 0) = 0",
        (from_id, to_id, USER_GLOBAL),
    ).fetchone()
    return row[0] == count


def find_mixed_old_episodes(conn: sqlite3.Connection) -> list[int]:
    """Ids of old-loop episodes that hold rows of anyone but their owner (any status). Read-only."""
    cutoff = _old_loop_cutoff(conn)
    if cutoff is None:
        return []
    found = []
    cur = conn.execute("SELECT id, ts_created, user_id, from_id, to_id, interaction_count FROM episodes ORDER BY id")
    for ep_id, ts, owner, from_id, to_id, count in cur.fetchall():
        created = _parse_ts(ts)
        if created is None or created >= cutoff:
            continue
        if owner == USER_GLOBAL and _is_global_loop_episode(conn, from_id, to_id, count or 0):
            continue
        if set(_range_users(conn, from_id, to_id)) - {owner}:
            found.append(ep_id)
    return found


def _affected_persons(
    conn: sqlite3.Connection, ep_ids: list[int], non_person: tuple[str, ...]
) -> dict[str, int]:
    """person → first own row id inside the given (mixed) episodes."""
    first: dict[str, int] = {}
    for ep_id in ep_ids:
        from_id, to_id = conn.execute("SELECT from_id, to_id FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        for uid, ids in _range_users(conn, from_id, to_id).items():
            if uid not in non_person:
                first[uid] = min(first.get(uid, ids[0]), ids[0])
    return first


def _person_readable(conn: sqlite3.Connection, user_id: str) -> bool:
    """False if the person's rows are encrypted and the night cannot resolve the key (re-compression would see
    only placeholders)."""
    row = conn.execute(
        "SELECT prompt FROM interactions WHERE user_id = ? AND prompt LIKE ? LIMIT 1",
        (user_id, long_term._ENC_PREFIX + "%"),
    ).fetchone()
    if row is None:
        return True
    return long_term._dec(user_id, row[0], allow_system_key=True) != _UNREADABLE


def _cursor(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute("SELECT last_id FROM ltm_cursors WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT last_id FROM ltm_cursors WHERE user_id = ?", (LTM_CURSOR_FLOOR,)).fetchone()
    return row[0] if row else 0


def _requeue(conn: sqlite3.Connection, user_id: str, first_row: int) -> dict:
    """Move the person's cursor to just before first_row (never forward); their 'ok' episodes from there on →
    superseded. Caller commits."""
    start = first_row
    row = conn.execute(
        "SELECT MIN(from_id) FROM episodes WHERE user_id = ? AND status = ? AND to_id >= ?",
        (user_id, EPISODE_OK, first_row),
    ).fetchone()
    if row and row[0] is not None:
        start = min(start, row[0])  # an own episode that straddles first_row must not lose its earlier rows
    new_cursor = min(start - 1, _cursor(conn, user_id))
    superseded = conn.execute(
        "UPDATE episodes SET status = ? WHERE user_id = ? AND status = ? AND to_id > ?",
        (EPISODE_SUPERSEDED, user_id, EPISODE_OK, new_cursor),
    ).rowcount
    conn.execute(
        """INSERT INTO ltm_cursors (user_id, last_id, ts_updated) VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET last_id = excluded.last_id, ts_updated = excluded.ts_updated""",
        (user_id, new_cursor, datetime.now(UTC).isoformat(timespec="seconds")),
    )
    rows = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE user_id = ? AND id > ? AND outcome IN (?, ?)",
        (user_id, new_cursor, *OLD_LOOP_OUTCOMES),
    ).fetchone()[0]
    return {"cursor": new_cursor, "superseded": superseded, "rows": rows}


def _backup(conn: sqlite3.Connection, db_path: Path) -> Path:
    out_dir = db_path.parent.parent.joinpath(*BACKUP_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_path = out_dir / f"interactions_before_mark_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
    dst = sqlite_files.connect(str(dst_path))
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return dst_path


def _sync_qdrant(conn: sqlite3.Connection) -> dict:
    """Give every Qdrant point of a non-ok episode the episode's status. Only differing points are written."""
    marked = conn.execute("SELECT id, status FROM episodes WHERE status != ?", (EPISODE_OK,)).fetchall()
    if not marked:
        return {}
    status_of = dict(marked)
    client = semantic_memory._get_client(write=True)
    existing = {c.name for c in client.get_collections().collections}
    result = {}
    for name in sorted({m["collection"] for m in MODELS.values()} & existing):
        ids = sorted(status_of)
        present = 0
        to_set: dict[str, list[int]] = {}
        for i in range(0, len(ids), _QDRANT_CHUNK):
            for p in client.retrieve(name, ids=ids[i : i + _QDRANT_CHUNK], with_payload=["status"]):
                present += 1
                want = status_of[int(p.id)]
                if (p.payload or {}).get("status", EPISODE_OK) != want:
                    to_set.setdefault(want, []).append(int(p.id))
        for status, point_ids in to_set.items():
            client.set_payload(name, payload={"status": status}, points=point_ids, wait=True)
        result[name] = {
            "present": present,
            "missing": len(ids) - present,
            "set": sum(len(v) for v in to_set.values()),
        }
    return result


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("LTM mixed cleanup: unreadable state file (%s) — starting over (idempotent).", e)
        return {}


def _save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)


def run_once(db_path: Path, non_person: tuple[str, ...]) -> dict:
    """The one-time step (nightjob). Returns the state; every later call is a no-op once it is complete.

    State file (next to the DB): sqlite_done (marking + re-queue written), waiting (persons whose key cannot be
    read yet — re-queued on a later night), qdrant_done. Never raises for Qdrant trouble."""
    state_path = db_path.parent / STATE_NAME
    state = _load_state(state_path)
    if state.get("sqlite_done") and state.get("qdrant_done") and not state.get("waiting"):
        return state
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn = sqlite_files.connect(str(db_path))
    backups: list[str] = []

    def before_write() -> None:
        if not backups:  # one SQLite backup per run, before its first write
            backups.append(str(_backup(conn, db_path)))
            state.setdefault("backups", []).append(backups[0])

    try:
        if "marked" not in state:
            # Plan first, saved before any write: if the run dies after the commit, the next run re-applies the
            # same plan (both steps are idempotent) instead of re-detecting — re-detection would see the episodes
            # as already marked and forget whom to re-queue.
            mixed = find_mixed_old_episodes(conn)
            marks = ",".join("?" * len(mixed))
            ok_ids = (
                [r[0] for r in conn.execute(f"SELECT id FROM episodes WHERE status = ? AND id IN ({marks})",
                                            (EPISODE_OK, *mixed))]
                if mixed
                else []
            )
            affected = _affected_persons(conn, ok_ids, non_person) if ok_ids else {}
            logger.info(
                "LTM mixed cleanup: %d old mixed episodes found, %d not yet marked, %d persons affected",
                len(mixed), len(ok_ids), len(affected),
            )
            state.update(marked=ok_ids, waiting=affected, requeued={})
            _save_state(state_path, state)
        if not state.get("sqlite_done"):
            if state["marked"]:
                before_write()
                marks = ",".join("?" * len(state["marked"]))
                conn.execute(f"UPDATE episodes SET status = ? WHERE status = ? AND id IN ({marks})",
                             (EPISODE_MIXED, EPISODE_OK, *state["marked"]))
                logger.info("LTM mixed cleanup: marked %d episodes mixed: ids %s", len(state["marked"]), state["marked"])
            state["sqlite_done"] = now
        for user_id, first_row in sorted(dict(state.get("waiting") or {}).items()):
            if not _person_readable(conn, user_id):
                logger.info("LTM mixed cleanup: user=%s waits — rows not readable with the system key yet", user_id)
                continue
            before_write()
            info = _requeue(conn, user_id, first_row)
            state.setdefault("requeued", {})[user_id] = info
            del state["waiting"][user_id]
            state["qdrant_done"] = None  # new superseded episodes → Qdrant again
            logger.info(
                "LTM mixed cleanup: user=%s re-queued from id %d (%d rows, %d own episodes superseded)",
                user_id, info["cursor"] + 1, info["rows"], info["superseded"],
            )
        conn.commit()
        _save_state(state_path, state)
        if not state.get("qdrant_done"):
            try:
                result = _sync_qdrant(conn)
                state["qdrant_done"] = now
                state["qdrant"] = result
                logger.info("LTM mixed cleanup: Qdrant %s", result or "nothing to mark")
            except Exception as e:
                logger.warning("LTM mixed cleanup: Qdrant not reachable (%s) — retrying next night.", e)
            _save_state(state_path, state)
    finally:
        conn.close()
    return state
