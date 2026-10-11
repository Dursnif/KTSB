"""
Kåre's knowledge store (P105 Phase 3).

Shared structured replacement for world.md / personality_self.md / notater.json —
rows with type, source, importance, and last-validated metadata, instead of N
independent flat-file conventions each with its own ad-hoc limit (or none at all).

Database file: /kaare/state/memory/knowledge.db (its own file — see P105 Phase 3,
"Storage location, revised 2026-09-07" for why not interactions.db).

Schema:
  state_facts — one row per fact/note, grouped by domain ('world' | 'personality' | 'notes')
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("/kaare/state/memory/knowledge.db")

DOMAINS = ("world", "personality", "notes", "lessons")  # lessons: P133 D7 — not in the weekly pass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT    NOT NULL,
    section         TEXT    DEFAULT '',
    content         TEXT    NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'kare',
    importance      REAL    DEFAULT 0.5,
    valid_from      TEXT    NOT NULL,
    valid_until     TEXT,
    last_validated  TEXT    NOT NULL,
    important_tag   INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_state_facts_domain_importance
    ON state_facts(domain, importance DESC);
CREATE INDEX IF NOT EXISTS idx_state_facts_domain_status
    ON state_facts(domain, status);
"""


def _utc_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _get_conn() -> sqlite3.Connection:
    """Opens a connection in WAL mode — same pattern as long_term.py."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to an existing database without losing data.

    No migrations needed at v1 — this function exists from day one so future
    schema changes follow the same pattern as long_term.py, instead of being
    invented from scratch when the first one is actually needed.
    """


def _init_db() -> None:
    conn = _get_conn()
    try:
        _migrate(conn)
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


_initialized = False


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        _init_db()
        _initialized = True


def add_fact(
    *,
    domain: str,
    content: str,
    section: str = "",
    source: str = "kare",
    importance: float = 0.5,
    valid_from: str | None = None,
) -> int | None:
    """Insert a new fact row. Returns the new row id, or None on failure."""
    if domain not in DOMAINS:
        logger.warning("knowledge_store: invalid domain '%s'", domain)
        return None
    _ensure_init()
    conn = _get_conn()
    try:
        now = _utc_ts()
        cur = conn.execute(
            """INSERT INTO state_facts
               (domain, section, content, source, importance, valid_from, last_validated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (domain, section or "", content, source or "kare", importance, valid_from or now, now),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        logger.warning("knowledge_store: add_fact failed: %s", e)
        return None
    finally:
        conn.close()


def update_fact(
    fact_id: int,
    *,
    content: str | None = None,
    section: str | None = None,
    importance: float | None = None,
) -> bool:
    """Update fields on an existing row. Only non-None fields are changed."""
    _ensure_init()
    conn = _get_conn()
    try:
        sets, params = [], []
        if content is not None:
            sets.append("content=?")
            params.append(content)
        if section is not None:
            sets.append("section=?")
            params.append(section)
        if importance is not None:
            sets.append("importance=?")
            params.append(importance)
        if not sets:
            return False
        sets.append("last_validated=?")
        params.append(_utc_ts())
        params.append(fact_id)
        cur = conn.execute(f"UPDATE state_facts SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning("knowledge_store: update_fact failed: %s", e)
        return False
    finally:
        conn.close()


def retire_fact(fact_id: int) -> bool:
    """Soft-delete: mark a row as retired instead of hard-deleting it.

    Deliberate behavior change vs. the old flat-file delete (which removed the
    line outright) — keeps a recoverable trail. See P105 Fase 3 build notes.
    """
    _ensure_init()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE state_facts SET status='retired', valid_until=? WHERE id=?",
            (_utc_ts(), fact_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning("knowledge_store: retire_fact failed: %s", e)
        return False
    finally:
        conn.close()


def find_facts(domain: str, fragment: str) -> list[dict]:
    """Find active rows in a domain whose content contains fragment (case-insensitive)."""
    _ensure_init()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, section, content, importance FROM state_facts
               WHERE domain=? AND status='active' AND LOWER(content) LIKE ?
               ORDER BY id""",
            (domain, f"%{fragment.lower()}%"),
        ).fetchall()
        return [{"id": r[0], "section": r[1], "content": r[2], "importance": r[3]} for r in rows]
    finally:
        conn.close()


def get_all_facts(domain: str) -> list[dict]:
    """All active rows in a domain, grouped naturally by section then id."""
    _ensure_init()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, section, content, importance, important_tag, valid_from, last_validated
               FROM state_facts
               WHERE domain=? AND status='active'
               ORDER BY section, id""",
            (domain,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "section": r[1],
                "content": r[2],
                "importance": r[3],
                "important_tag": bool(r[4]),
                "valid_from": r[5],
                "last_validated": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_fact_by_section(domain: str, section: str) -> dict | None:
    """Exact-match single-row lookup, for callers that embed a unique key in
    `section` (e.g. executor_world.py's 'var::{key}' / '{category}::{field}'
    addressing) — the upsert primitive that keeps repeated writes from piling
    up duplicate rows the way the old flat files never did either."""
    _ensure_init()
    conn = _get_conn()
    try:
        row = conn.execute(
            """SELECT id, section, content, importance, source, valid_from FROM state_facts
               WHERE domain=? AND section=? AND status='active'
               ORDER BY id DESC LIMIT 1""",
            (domain, section),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "section": row[1],
            "content": row[2],
            "importance": row[3],
            "source": row[4],
            "valid_from": row[5],
        }
    finally:
        conn.close()


def find_by_section_prefix(domain: str, prefix: str) -> list[dict]:
    """Active rows whose section starts with `prefix` — e.g. all 'var::*' rows."""
    _ensure_init()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, section, content, importance FROM state_facts
               WHERE domain=? AND status='active' AND section LIKE ?
               ORDER BY section, id""",
            (domain, f"{prefix}%"),
        ).fetchall()
        return [{"id": r[0], "section": r[1], "content": r[2], "importance": r[3]} for r in rows]
    finally:
        conn.close()


def retire_all(domain: str, section: str | None = None) -> int:
    """Soft-delete every active row in a domain (optionally scoped to one section).
    Returns the count retired. Bulk equivalent of retire_fact()."""
    _ensure_init()
    conn = _get_conn()
    try:
        now = _utc_ts()
        if section is not None:
            cur = conn.execute(
                "UPDATE state_facts SET status='retired', valid_until=? "
                "WHERE domain=? AND section=? AND status='active'",
                (now, domain, section),
            )
        else:
            cur = conn.execute(
                "UPDATE state_facts SET status='retired', valid_until=? "
                "WHERE domain=? AND status='active'",
                (now, domain),
            )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning("knowledge_store: retire_all failed: %s", e)
        return 0
    finally:
        conn.close()


def get_top_facts(
    domain: str, limit: int = 20, exclude_section_prefixes: list[str] | None = None
) -> list[dict]:
    """Ranked rows for prompt injection — replaces the old raw[:2000] byte-offset slice.

    Ordered by importance DESC, ties broken by id DESC (most recent first — matches
    the old "keep the newest observations" behavior for rows that don't yet have a
    differentiated importance score, e.g. before the weekly reflection job or
    Migration A's LLM-assigned importance has run). Only rows still valid
    (valid_until NULL or in the future).

    `exclude_section_prefixes` filters out rows whose section starts with any of
    the given prefixes — e.g. executor_world.py's 'var::' rows, which were never
    part of the passive prompt block even in the old file-based world.md/
    world_vars.json split and shouldn't silently start being one now.
    """
    _ensure_init()
    conn = _get_conn()
    try:
        now = _utc_ts()
        rows = conn.execute(
            """SELECT id, section, content, importance FROM state_facts
               WHERE domain=? AND status='active'
                 AND (valid_until IS NULL OR valid_until > ?)
               ORDER BY importance DESC, id DESC
               LIMIT ?""",
            (domain, now, limit * 3 if exclude_section_prefixes else limit),
        ).fetchall()
        result = [{"id": r[0], "section": r[1], "content": r[2], "importance": r[3]} for r in rows]
        if exclude_section_prefixes:
            result = [
                r
                for r in result
                if not any(r["section"].startswith(p) for p in exclude_section_prefixes)
            ]
            result = result[:limit]
        return result
    finally:
        conn.close()


def set_important_tag(fact_id: int, important: bool = True) -> bool:
    """Flag a row as important — forces the next reflection-meeting cycle to run
    the weekly re-validation pass early instead of waiting out the week."""
    _ensure_init()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE state_facts SET important_tag=? WHERE id=?",
            (1 if important else 0, fact_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning("knowledge_store: set_important_tag failed: %s", e)
        return False
    finally:
        conn.close()


def touch_all_validated(domain: str | None = None) -> int:
    """Bulk-stamp last_validated=now on every active row (optionally scoped to
    one domain) — called after a reflection pass reviews everything, so rows
    the pass left unchanged still record that they were actually looked at,
    not just the ones that got retired/reimportanced. Returns count touched."""
    _ensure_init()
    conn = _get_conn()
    try:
        now = _utc_ts()
        if domain is not None:
            cur = conn.execute(
                "UPDATE state_facts SET last_validated=? WHERE domain=? AND status='active'",
                (now, domain),
            )
        else:
            cur = conn.execute(
                "UPDATE state_facts SET last_validated=? WHERE status='active'", (now,)
            )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning("knowledge_store: touch_all_validated failed: %s", e)
        return 0
    finally:
        conn.close()


def clear_all_important_tags() -> int:
    """Clear important_tag on every row — called at the end of a reflection
    pass since all pending escalations (weekly or tag-forced) have now been
    reviewed. Returns count cleared."""
    _ensure_init()
    conn = _get_conn()
    try:
        cur = conn.execute("UPDATE state_facts SET important_tag=0 WHERE important_tag=1")
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning("knowledge_store: clear_all_important_tags failed: %s", e)
        return 0
    finally:
        conn.close()


def has_unprocessed_important_tag() -> bool:
    """True if any active row has important_tag set — used by the reflection-meeting
    job's frequency check ('has a week passed, OR does an important-tagged note exist')."""
    _ensure_init()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM state_facts WHERE status='active' AND important_tag=1 LIMIT 1"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_stats() -> dict:
    """Simple stats — useful for debugging/verification."""
    _ensure_init()
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM state_facts").fetchone()[0]
        by_domain = dict(
            conn.execute(
                "SELECT domain, COUNT(*) FROM state_facts WHERE status='active' GROUP BY domain"
            ).fetchall()
        )
        return {"total": total, "by_domain": by_domain}
    finally:
        conn.close()
