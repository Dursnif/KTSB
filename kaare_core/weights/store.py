"""The weight store (P149 phase 1): an append-only ledger in its own SQLite file, read from RAM.

Database file: /kaare/state/memory/weights.db (own file, P149 Q10 — its migration never touches knowledge.db or
learning.db; `pointer` links to rows there).

Rules this module enforces (P149 invariants):
  - Nothing is deleted or rewritten: evidence, answers and park/unpark are appended rows. There is no delete path.
  - Validated is only moved by `answer()` — a human's answer. `record()` never touches it.
  - Self rows without a pointer are kept but move nothing (echo rule, in `model.fold`).
  - An opted-out person is never stored: `record(about=…)`/`answer(about=…)` for an untracked face writes nothing.
  - `get()` reads RAM; writes are queued and flushed in one transaction (`flush()`), never inside `get()`.
Phase 1 is shadow: no consumer acts on these numbers yet.
"""

from __future__ import annotations

import atexit
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from kaare_core import receipts
from kaare_core.storage import sqlite_files
from kaare_core.users import frigate_consent
from kaare_core.weights import model

logger = logging.getLogger(__name__)

DB_PATH = Path("/kaare/state/memory/weights.db")
SUBJECT_KINDS = ("sensor", "belief", "person_fact", "baseline", "rule", "wondering", "topic", "chat_answers", "event",
                 "asking", "thing_pair")
FLUSH_BATCH = 50
RETRY_S = 60.0  # after a failed flush only: one background retry per minute until the queue is empty
FLUSH_AGE_S = 5.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    subject_kind  TEXT    NOT NULL,
    subject_key   TEXT    NOT NULL,
    scope         TEXT    NOT NULL DEFAULT 'household',
    source_class  TEXT    NOT NULL CHECK (source_class IN ('user', 'world', 'self')),
    source        TEXT    NOT NULL DEFAULT '',
    actor         TEXT    NOT NULL DEFAULT '',
    context       TEXT    NOT NULL DEFAULT '',
    polarity      INTEGER NOT NULL DEFAULT 0,
    worry         REAL,
    pointer       TEXT    NOT NULL DEFAULT '',
    valid_from    TEXT,
    valid_to      TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_subject ON evidence(subject_kind, subject_key, scope);

CREATE TABLE IF NOT EXISTS answers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    subject_kind  TEXT    NOT NULL,
    subject_key   TEXT    NOT NULL,
    scope         TEXT    NOT NULL DEFAULT 'household',
    by_user       TEXT    NOT NULL,
    verdict       TEXT    NOT NULL CHECK (verdict IN ('yes', 'no', 'correct', 'private', 'skip')),
    verbatim      INTEGER NOT NULL DEFAULT 0,
    context       TEXT    NOT NULL DEFAULT '',
    pointer       TEXT    NOT NULL DEFAULT '',
    valid_from    TEXT,
    valid_to      TEXT
);
CREATE INDEX IF NOT EXISTS idx_answers_subject ON answers(subject_kind, subject_key, scope);
CREATE INDEX IF NOT EXISTS idx_answers_pointer ON answers(pointer);

CREATE TABLE IF NOT EXISTS parked (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    subject_kind  TEXT    NOT NULL,
    subject_key   TEXT    NOT NULL,
    scope         TEXT    NOT NULL DEFAULT 'household',
    action        TEXT    NOT NULL CHECK (action IN ('park', 'unpark')),
    reason        TEXT    NOT NULL DEFAULT ''
);
"""

_TABLE_COLS = {
    "evidence": ("ts", "subject_kind", "subject_key", "scope", "source_class", "source", "actor", "context",
                 "polarity", "worry", "pointer", "valid_from", "valid_to"),
    "answers": ("ts", "subject_kind", "subject_key", "scope", "by_user", "verdict", "verbatim", "context", "pointer",
                "valid_from", "valid_to"),
    "parked": ("ts", "subject_kind", "subject_key", "scope", "action", "reason"),
}


def _utc_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")  # orders rows written in the same second


def _subject(subject) -> tuple[str, str, str]:
    """(kind, key) or (kind, key, scope) → normalised triple. Raises ValueError on an unknown kind."""
    if not isinstance(subject, (tuple, list)) or len(subject) not in (2, 3):
        raise ValueError(f"subject must be (kind, key[, scope]), got {subject!r}")
    kind, key = str(subject[0]), str(subject[1])
    scope = str(subject[2]) if len(subject) == 3 and subject[2] else "household"
    if kind not in SUBJECT_KINDS:
        raise ValueError(f"unknown subject kind {kind!r}")
    if not key:
        raise ValueError("subject key is empty")
    return kind, key, scope


def _opted_out(about) -> bool:
    """Fail closed: when consent cannot be read, `untracked_faces()` keeps the last known set."""
    if not about:
        return False
    names = about if isinstance(about, (list, tuple, set, frozenset)) else [about]
    return any(frigate_consent.is_untracked(str(n)) for n in names)


class WeightStore:
    """One ledger file. All rows live in RAM after `load()`; `get()` folds them; writes queue until `flush()`."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._loaded = False
        self._rows: dict[tuple, dict[str, list]] = {}
        self._first_ts: dict[tuple[str, str], datetime] = {}
        self._domain_answers: dict[tuple[str, str], int] = {}
        self._pointers: set[str] = set()
        self._pending: list[tuple[str, dict]] = []
        self._pending_since = 0.0
        self._seq = 0
        self._retry_thread: threading.Thread | None = None
        atexit.register(self._flush_at_exit)

    def shutdown_flush(self) -> int:
        """Flush on the way down (API shutdown, then atexit as a backstop). Says what happened — never silent."""
        try:
            n_queued = len(self._pending)
            if not n_queued:
                return 0
            n = self.flush()
            if n == n_queued:
                logger.info("weights: saved %d queued row(s) on shutdown", n)
            else:
                logger.warning("weights: %d queued row(s) could not be saved on shutdown and are lost [%s]",
                               len(self._pending), sqlite_files.sidecar_report(self.path))
            return n
        except Exception as e:  # never crash a process on its way out — but say it
            logger.warning("weights: shutdown flush failed, %d queued row(s) lost: %s", len(self._pending), e)
            return 0

    def _flush_at_exit(self) -> None:
        self.shutdown_flush()

    def _schedule_retry(self) -> None:
        """Start one background retry loop after a failed flush; it ends when the queue is empty. No thread, timer or
        IO while flushes succeed (rule 10)."""
        if self._retry_thread is not None and self._retry_thread.is_alive():
            return
        self._retry_thread = threading.Thread(target=self._retry_loop, name="weights-retry", daemon=True)
        self._retry_thread.start()

    def _retry_loop(self) -> None:
        while self._pending:
            time.sleep(RETRY_S)
            try:
                if self.flush():
                    logger.info("weights: retry saved queued rows")
            except Exception as e:  # keep trying; flush() itself logs the failure
                logger.debug("weights: retry error: %s", e)

    # ── IO ──
    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite_files.connect(str(self.path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        sqlite_files.share_with_group(self.path)
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)  # P149 phase 1 = first schema; later columns are added here with ALTER TABLE
        conn.commit()

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            if self.path.exists():
                conn = self._conn()
                try:
                    self._migrate(conn)
                    conn.row_factory = sqlite3.Row
                    for table in ("evidence", "answers", "parked"):
                        for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):  # fixed table names
                            self._add_ram(table, dict(row))
                finally:
                    conn.close()
            self._loaded = True

    def flush(self) -> int:
        """Write queued rows in one transaction. Returns rows written; on error the rows stay queued."""
        with self._lock:
            if not self._pending:
                return 0
            batch = list(self._pending)
            conn = None
            try:
                conn = self._conn()  # inside the try: a failed connect keeps the rows queued and is logged
                self._migrate(conn)
                with conn:
                    for table, row in batch:
                        cols = _TABLE_COLS[table]
                        conn.execute(
                            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            tuple(row.get(c) for c in cols),
                        )
            except Exception as e:
                logger.warning("weights: flush failed (%d rows kept queued, retry every %ds): %s [%s]", len(batch),
                               int(RETRY_S), e, sqlite_files.sidecar_report(self.path))
                self._schedule_retry()
                return 0
            finally:
                if conn is not None:
                    conn.close()
            del self._pending[: len(batch)]
            counts: dict[str, int] = {}
            for table, _row in batch:
                counts[table] = counts.get(table, 0) + 1
            receipts.log_saved("weights_flush", where="weights.db", refs=[f"{k}:{v}" for k, v in sorted(counts.items())],
                               n=len(batch))
            return len(batch)

    def flush_due(self) -> bool:
        return bool(self._pending) and (len(self._pending) >= FLUSH_BATCH
                                        or time.monotonic() - self._pending_since >= FLUSH_AGE_S)

    # ── RAM ──
    def _bucket(self, kind: str, key: str, scope: str) -> dict[str, list]:
        return self._rows.setdefault((kind, key, scope), {"evidence": [], "answers": [], "parked": []})

    def _add_ram(self, table: str, row: dict) -> None:
        b = self._bucket(row["subject_kind"], row["subject_key"], row["scope"])
        self._seq += 1
        row["_seq"] = self._seq  # RAM only: ties in ts keep write order
        b[table].append(row)
        dom = (row["subject_kind"], row["scope"])
        ts = model.parse_ts(row.get("ts"))
        if table != "parked" and ts and (dom not in self._first_ts or ts < self._first_ts[dom]):
            self._first_ts[dom] = ts
        if table == "answers" and row.get("verdict") != "skip":
            self._domain_answers[dom] = self._domain_answers.get(dom, 0) + 1
        if row.get("pointer"):
            self._pointers.add(f"{table}:{row['pointer']}")

    def _append(self, table: str, row: dict) -> None:
        with self._lock:
            self.load()
            self._add_ram(table, row)
            if not self._pending:
                self._pending_since = time.monotonic()
            self._pending.append((table, row))
            if len(self._pending) >= FLUSH_BATCH:
                self.flush()

    def has_pointer(self, table: str, pointer: str) -> bool:
        self.load()
        return f"{table}:{pointer}" in self._pointers

    # ── The interface (P149 § Interface) ──
    def record(self, subject, *, source_class: str, source: str = "", actor: str = "", context: str = "",
               polarity: int = 0, worry: float | None = None, pointer: str = "", ts: str | None = None,
               valid_from: str | None = None, valid_to: str | None = None, about=None) -> bool:
        """Append one piece of evidence. Never moves Validated. Returns False when nothing was written (opted-out
        person, bad input) — never raises."""
        try:
            kind, key, scope = _subject(subject)
            if source_class not in model.SOURCE_CLASSES:
                raise ValueError(f"unknown source class {source_class!r}")
        except ValueError as e:
            logger.warning("weights: record refused: %s", e)
            return False
        if _opted_out(about):
            return False  # W22: «ikke lagre om meg» — no row that names or describes the person
        self._append("evidence", {
            "ts": ts or _utc_ts(), "subject_kind": kind, "subject_key": key, "scope": scope,
            "source_class": source_class, "source": source, "actor": actor, "context": context,
            "polarity": (polarity > 0) - (polarity < 0), "worry": worry, "pointer": pointer,
            "valid_from": valid_from, "valid_to": valid_to,
        })
        return True

    def answer(self, subject, *, by_user: str, verdict: str, verbatim: bool, context: str = "", pointer: str = "",
               ts: str | None = None, valid_from: str | None = None, valid_to: str | None = None,
               about=None) -> bool:
        """A human's answer — the only thing that moves Validated. `by_user` must be a real user (D1: humans only)."""
        try:
            kind, key, scope = _subject(subject)
            if verdict not in model.VERDICTS:
                raise ValueError(f"unknown verdict {verdict!r}")
            if not by_user:
                raise ValueError("an answer needs the human who gave it")
        except ValueError as e:
            logger.warning("weights: answer refused: %s", e)
            return False
        if _opted_out(about):
            return False
        self._append("answers", {
            "ts": ts or _utc_ts(), "subject_kind": kind, "subject_key": key, "scope": scope, "by_user": by_user,
            "verdict": verdict, "verbatim": 1 if verbatim else 0, "context": context, "pointer": pointer,
            "valid_from": valid_from, "valid_to": valid_to,
        })
        return True

    def park(self, subject, reason: str = "") -> bool:
        return self._set_parked(subject, "park", reason)

    def unpark(self, subject, reason: str = "") -> bool:
        return self._set_parked(subject, "unpark", reason)

    def _set_parked(self, subject, action: str, reason: str) -> bool:
        try:
            kind, key, scope = _subject(subject)
        except ValueError as e:
            logger.warning("weights: %s refused: %s", action, e)
            return False
        self._append("parked", {"ts": _utc_ts(), "subject_kind": kind, "subject_key": key, "scope": scope,
                                "action": action, "reason": reason})
        return True

    def is_parked(self, subject) -> bool:
        kind, key, scope = _subject(subject)
        with self._lock:
            self.load()
            rows = self._rows.get((kind, key, scope), {}).get("parked") or []
            return bool(rows) and rows[-1]["action"] == "park"

    def get(self, subject, at: datetime | None = None) -> model.Weight:
        """The weight of one subject, folded from its rows (RAM only). `at` = the weight as it stood at that time."""
        kind, key, scope = _subject(subject)
        with self._lock:
            self.load()
            b = self._rows.get((kind, key, scope)) or {"evidence": [], "answers": [], "parked": []}
            ev, an = list(b["evidence"]), list(b["answers"])
            parked = bool(b["parked"]) and b["parked"][-1]["action"] == "park"
            dom = (kind, scope)
            first, confirmations = self._first_ts.get(dom), self._domain_answers.get(dom, 0)
        now = at or datetime.now(UTC)
        if at is not None:
            ev = [r for r in ev if (model.parse_ts(r["ts"]) or now) <= at]
            an = [r for r in an if (model.parse_ts(r["ts"]) or now) <= at]
        mat = model.maturity_for(kind, first, confirmations, now)
        return model.fold(ev, an, kind=kind, now=now, maturity=mat, parked=parked)

    def rows(self, subject) -> dict:
        """J18b: a copy of one subject's raw rows {evidence, answers} for read-only history views."""
        kind, key, scope = _subject(subject)
        with self._lock:
            self.load()
            b = self._rows.get((kind, key, scope)) or {}
            return {"evidence": [dict(r) for r in b.get("evidence") or []],
                    "answers": [dict(r) for r in b.get("answers") or []]}

    def subjects(self, kind: str | None = None) -> list[tuple[str, str, str]]:
        with self._lock:
            self.load()
            return [s for s in self._rows if kind is None or s[0] == kind]

    def pending(self) -> int:
        return len(self._pending)


_store: WeightStore | None = None
_store_lock = threading.Lock()


def get_store() -> WeightStore:
    """The process's store for DB_PATH (swapped in tests by setting DB_PATH and calling `reset_for_tests()`)."""
    global _store
    with _store_lock:
        if _store is None or _store.path != Path(DB_PATH):
            _store = WeightStore(Path(DB_PATH))
        return _store


def reset_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
