"""
Kåre's learning store (P118).

Storage for the nightly dev slot: the agenda inbox Kåre writes to during the day
("raise this at the dev meeting"), the topics those rows are grouped into, and the
slot's own outputs (verdicts, findings, proposals, consolidations, resume state).

Database file: /kaare/state/memory/learning.db (own file, T2-D16).

Rules this module enforces (P118 invariants, T2-D8/T2-D9):
  - Nothing is deleted. Inbox rows are append-only: text, source, rid and pointer are
    never rewritten; only status/topic/consumed_at move forward.
  - Code groups, counts and links — it never decides or drops. A near-duplicate is a new
    row in an existing topic (count + 1), never a discarded write.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from kaare_core.memory.agenda_themes import TOOL_HINT_SOURCES, normalize_theme, resolve_theme
from kaare_core.tools.trace_reader import tools_by_rid

logger = logging.getLogger(__name__)

DB_PATH = Path("/kaare/state/memory/learning.db")
LEGACY_KARE_LIST = Path("/kaare/state/lister/kare_huske.json")

# Grouping: how many recent inbox rows a new row is compared against, and how similar the
# normalized texts must be to join the same topic. Same threshold as the kare list's own
# near-duplicate check (lister.py), so "the same note" means the same thing in both places.
_GROUP_WINDOW = 200
_GROUP_THRESHOLD = 0.75

# Inbox row status: new → cleaned → problem | event | known | not_real → consolidated;
# `echo` = a row that matched a resolved topic (stored and linked, never reopens it — P133).
OPEN_STATUSES = ("new",)
# Drawers in this store (P133 D1/D9): agenda = faults to investigate (the dev slot develops
# them); observation = «I saw X at T» (never developed, counted into patterns by S6).
KINDS = ("agenda", "observation")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen  TEXT    NOT NULL,
    last_seen   TEXT    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    label       TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'new',
    merged_into INTEGER REFERENCES topics(id),
    theme       TEXT    NOT NULL DEFAULT '',
    last_developed TEXT,
    kind        TEXT    NOT NULL DEFAULT 'agenda',
    resolved_at TEXT,
    resolved_rid TEXT,
    resolved_by TEXT,
    conclusion  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    user_id     TEXT    NOT NULL DEFAULT 'global',
    rid         TEXT    NOT NULL DEFAULT '',
    pointer     TEXT    NOT NULL DEFAULT '',
    text        TEXT    NOT NULL,
    context     TEXT    NOT NULL DEFAULT '',
    topic_id    INTEGER REFERENCES topics(id),
    status      TEXT    NOT NULL DEFAULT 'new',
    consumed_at TEXT,
    theme       TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT 'agenda',
    title       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_items(status);
CREATE INDEX IF NOT EXISTS idx_inbox_topic ON inbox_items(topic_id);
CREATE INDEX IF NOT EXISTS idx_inbox_ts ON inbox_items(ts);

CREATE TABLE IF NOT EXISTS verdicts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL,
    kind          TEXT    NOT NULL,
    target        TEXT    NOT NULL,
    verdict       TEXT    NOT NULL,
    evidence_refs TEXT    NOT NULL DEFAULT '[]',
    verified      INTEGER NOT NULL DEFAULT 0,
    model         TEXT    NOT NULL DEFAULT '',
    ts            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL,
    topic_id      INTEGER REFERENCES topics(id),
    text          TEXT    NOT NULL,
    evidence_refs TEXT    NOT NULL DEFAULT '[]',
    verified      INTEGER NOT NULL DEFAULT 0,
    superseded_by INTEGER REFERENCES findings(id),
    ts            TEXT    NOT NULL,
    verifier      TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id  INTEGER REFERENCES findings(id),
    text        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    decided_by  TEXT    NOT NULL DEFAULT '',
    ts          TEXT    NOT NULL,
    decided_at  TEXT,
    topic_id    INTEGER REFERENCES topics(id),
    kind        TEXT    NOT NULL DEFAULT 'change'
);

CREATE TABLE IF NOT EXISTS consolidations (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    level   TEXT    NOT NULL,
    period  TEXT    NOT NULL,
    summary TEXT    NOT NULL,
    covers  TEXT    NOT NULL DEFAULT '[]',
    counts  TEXT    NOT NULL DEFAULT '{}',
    ts      TEXT    NOT NULL,
    scope   TEXT    NOT NULL DEFAULT 'dev'
);

CREATE TABLE IF NOT EXISTS run_state (
    run_id   TEXT PRIMARY KEY,
    step     TEXT NOT NULL DEFAULT '',
    cursor   TEXT NOT NULL DEFAULT '',
    started  TEXT NOT NULL,
    finished TEXT
);

CREATE TABLE IF NOT EXISTS change_snapshots (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL,
    step    TEXT NOT NULL,
    ts      TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run ON run_artifacts(run_id, step);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_LEADING_TAG_RE = re.compile(r"^\s*\[[^\]]{0,60}\]\s*")
_LEADING_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\s*[—–-]?\s*")
_WS_RE = re.compile(r"\s+")


def _utc_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _get_conn() -> sqlite3.Connection:
    """Opens a connection in WAL mode — same pattern as long_term.py / knowledge_store.py."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to an existing database without losing data (long_term.py pattern)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    # P118 B3: a topic the 27B judged to be the same as another points to it (never merged by code).
    if cols and "merged_into" not in cols:
        conn.execute("ALTER TABLE topics ADD COLUMN merged_into INTEGER REFERENCES topics(id)")
        conn.commit()
    # P118 B8: theme per topic and per row (set at write time), and when S3 last took the topic.
    if cols and "theme" not in cols:
        conn.execute("ALTER TABLE topics ADD COLUMN theme TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if cols and "last_developed" not in cols:
        conn.execute("ALTER TABLE topics ADD COLUMN last_developed TEXT")
        conn.commit()
    icols = {row[1] for row in conn.execute("PRAGMA table_info(inbox_items)").fetchall()}
    if icols and "theme" not in icols:
        conn.execute("ALTER TABLE inbox_items ADD COLUMN theme TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # P133: drawers (agenda | observation) + titles; resolved lifecycle on topics; reopen
    # proposals point at a topic.
    for table, col, ddl in (
        ("inbox_items", "kind", "TEXT NOT NULL DEFAULT 'agenda'"),
        ("inbox_items", "title", "TEXT NOT NULL DEFAULT ''"),
        ("topics", "kind", "TEXT NOT NULL DEFAULT 'agenda'"),
        ("topics", "resolved_at", "TEXT"),
        ("topics", "resolved_rid", "TEXT"),
        ("topics", "resolved_by", "TEXT"),
        ("topics", "conclusion", "TEXT NOT NULL DEFAULT ''"),
        ("proposals", "topic_id", "INTEGER REFERENCES topics(id)"),
        ("proposals", "kind", "TEXT NOT NULL DEFAULT 'change'"),
    ):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if existing and col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            conn.commit()
    ccols = {row[1] for row in conn.execute("PRAGMA table_info(consolidations)").fetchall()}
    # P118 B6: which learning track a consolidation row belongs to ('dev' = this slot; P115 track 1
    # can use e.g. 'reflection:<user>' later with the same table — T2-P7).
    if ccols and "scope" not in ccols:
        conn.execute("ALTER TABLE consolidations ADD COLUMN scope TEXT NOT NULL DEFAULT 'dev'")
        conn.commit()
    fcols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
    # P118 B4: the 9B evidence verifier's answer per finding (json), next to the code check.
    if fcols and "verifier" not in fcols:
        conn.execute("ALTER TABLE findings ADD COLUMN verifier TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _init_db() -> None:
    conn = _get_conn()
    try:
        _migrate(conn)
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


_initialized_for: Path | None = None


def _ensure_db() -> None:
    """Create the schema once per process (and again if DB_PATH is swapped, e.g. in tests)."""
    global _initialized_for
    if _initialized_for != DB_PATH:
        _init_db()
        _initialized_for = DB_PATH


def _normalize(text: str) -> str:
    """Comparison form of a note: leading «[Jang 2026-10-03 12:18]» / «2026-09-01 17:21 —»
    stamps removed, whitespace collapsed, lowercased. Only used to group; the stored text
    is always the original."""
    s = text or ""
    for _ in range(2):
        s = _LEADING_TAG_RE.sub("", s)
        s = _LEADING_DATE_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip().lower()


def _find_topic(conn: sqlite3.Connection, text: str, kind: str = "agenda") -> int | None:
    """Topic of the most similar recent inbox row of the same kind, or None. Exact normalized
    match wins."""
    target = _normalize(text)
    if not target:
        return None
    rows = conn.execute(
        "SELECT text, topic_id FROM inbox_items WHERE topic_id IS NOT NULL AND kind = ? "
        "ORDER BY id DESC LIMIT ?",
        (kind, _GROUP_WINDOW),
    ).fetchall()
    best_ratio, best_topic = 0.0, None
    matcher = difflib.SequenceMatcher(None, target, "")
    for row_text, topic_id in rows:
        other = _normalize(row_text)
        if other == target:
            return topic_id
        matcher.set_seq2(other)
        if (
            matcher.real_quick_ratio() < _GROUP_THRESHOLD
            or matcher.quick_ratio() < _GROUP_THRESHOLD
        ):
            continue
        ratio = matcher.ratio()
        if ratio >= _GROUP_THRESHOLD and ratio > best_ratio:
            best_ratio, best_topic = ratio, topic_id
    return best_topic


_TITLE_CHARS = 80


def _root_topic(conn: sqlite3.Connection, topic_id: int) -> int:
    """Follow `merged_into` links to the topic a row really belongs to (max 10 hops)."""
    seen = 0
    current = topic_id
    while seen < 10:
        row = conn.execute("SELECT merged_into FROM topics WHERE id = ?", (current,)).fetchone()
        if not row or row[0] is None:
            return current
        current = row[0]
        seen += 1
    return current


def _is_resolved(conn: sqlite3.Connection, topic_id: int) -> bool:
    row = conn.execute("SELECT status FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return bool(row) and row[0] == "resolved"


def _resolved_info(conn: sqlite3.Connection, topic_id: int) -> dict:
    row = conn.execute(
        "SELECT id, label, conclusion, resolved_at, resolved_by FROM topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    return {
        "topic_id": row[0],
        "label": row[1],
        "conclusion": row[2],
        "resolved_at": row[3] or "",
        "resolved_by": row[4] or "",
    }


def add_inbox_item(
    text: str,
    *,
    source: str,
    user_id: str = "global",
    rid: str = "",
    pointer: str = "",
    context: str = "",
    ts: str | None = None,
    theme: str | None = None,
    kind: str = "agenda",
    title: str = "",
    echo_of: int | None = None,
) -> dict:
    """Append one agenda row. Never drops a write: a near-duplicate joins the existing topic.

    `theme` is the writer's choice (P118 B8); when it is missing or not a known theme, code
    rules set it (agenda_themes.resolve_theme). A new topic takes its first row's theme.

    P133: `kind` picks the drawer (agenda | observation); grouping stays within a kind.
    `title` is the writer's short title (a new topic's first label). A row that groups with a
    **resolved** topic — by text, or because the caller found it by meaning (`echo_of`) — is
    stored with status 'echo' and linked; the topic stays resolved. The return value then
    carries `resolved` with the conclusion so the writer is told at once.

    Returns {"item_id", "topic_id", "count", "known", "first_seen", "label"} — `known` is True
    when the row joined an existing topic (the caller tells Kåre «already on the agenda»).
    A row without a rid must carry a pointer (P118 B1: rid or explicit pointer, never neither);
    the caller passes one, and if it did not, the row points at its own source.
    """
    _ensure_db()
    text = (text or "").strip()
    if not text:
        raise ValueError("empty inbox text")
    now = ts or _utc_ts()
    if not rid and not pointer:
        pointer = f"source:{source}"
    if kind not in KINDS:
        kind = "agenda"
    title = (title or "").strip()[:_TITLE_CHARS]
    rid_tools: list[str] = []
    if rid and normalize_theme(theme) is None and source in TOOL_HINT_SOURCES:
        # Writer gave no theme: the tools of the same turn are the first code hint.
        try:
            rid_tools = tools_by_rid({rid}).get(rid, [])
        except Exception as e:
            logger.warning("learning_store: tool lookup for theme failed (%s)", e)
    row_theme = resolve_theme(
        theme, text=text, context=context, source=source, rid_tools=rid_tools
    )
    conn = _get_conn()
    try:
        topic_id = _find_topic(conn, text, kind)
        resolved = None
        root = _root_topic(conn, topic_id) if topic_id is not None else None
        if root is not None and _is_resolved(conn, root):
            topic_id = root
        elif echo_of is not None and _is_resolved(conn, echo_of):
            topic_id = echo_of
        if topic_id is not None and _is_resolved(conn, topic_id):
            resolved = _resolved_info(conn, topic_id)
        known = topic_id is not None
        if known:
            conn.execute(
                "UPDATE topics SET count = count + 1, first_seen = MIN(first_seen, ?), "
                "last_seen = MAX(last_seen, ?), theme = CASE WHEN theme = '' THEN ? ELSE theme END "
                "WHERE id = ?",
                (now, now, row_theme, topic_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO topics (first_seen, last_seen, count, label, theme, kind) "
                "VALUES (?, ?, 1, ?, ?, ?)",
                (now, now, title, row_theme, kind),
            )
            topic_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO inbox_items "
            "(ts, source, user_id, rid, pointer, text, context, topic_id, theme, kind, title, "
            "status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                source,
                user_id or "global",
                rid or "",
                pointer or "",
                text,
                context or "",
                topic_id,
                row_theme,
                kind,
                title,
                "echo" if resolved else "new",
            ),
        )
        item_id = cur.lastrowid
        count, first_seen, label, topic_theme = conn.execute(
            "SELECT count, first_seen, label, theme FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
        conn.commit()
        return {
            "item_id": item_id,
            "topic_id": topic_id,
            "count": count,
            "known": known,
            "first_seen": first_seen,
            "label": label,
            "theme": topic_theme,
            "kind": kind,
            "resolved": resolved,
        }
    finally:
        conn.close()


def count_open_items(kind: str = "agenda") -> int:
    """Inbox rows of one drawer not yet handled by a dev slot run."""
    _ensure_db()
    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        return conn.execute(
            f"SELECT COUNT(*) FROM inbox_items WHERE status IN ({placeholders}) AND kind = ?",
            (*OPEN_STATUSES, kind),
        ).fetchone()[0]
    finally:
        conn.close()


def count_open_topics(kind: str = "agenda") -> int:
    """Distinct topics of one drawer that still have unhandled inbox rows."""
    _ensure_db()
    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        return conn.execute(
            f"SELECT COUNT(DISTINCT topic_id) FROM inbox_items "
            f"WHERE status IN ({placeholders}) AND kind = ?",
            (*OPEN_STATUSES, kind),
        ).fetchone()[0]
    finally:
        conn.close()


def list_inbox(
    status: str | None = None, limit: int = 500, kind: str | None = None
) -> list[dict]:
    """Inbox rows, newest first (read-only view for the slot, tests and a future GUI view)."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        sql = "SELECT * FROM inbox_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_meta(key: str) -> str | None:
    _ensure_db()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_meta(key: str, value: str) -> None:
    _ensure_db()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


_LEGACY_IMPORT_KEY = "legacy_kare_list_imported"


def import_legacy_kare_list(path: Path | None = None) -> int:
    """One-time copy of Kåre's kare list (state/lister/kare_huske.json) into the inbox as
    source='legacy' (T2-D15). The JSON file is read only — never changed or removed (rule 1).
    Idempotent: a meta flag records that the import ran. Returns the number of rows imported
    (0 when it already ran or the file is missing/empty)."""
    _ensure_db()
    if get_meta(_LEGACY_IMPORT_KEY):
        return 0
    src = path or LEGACY_KARE_LIST
    try:
        items = json.loads(src.read_text(encoding="utf-8")) if src.exists() else []
    except Exception as e:
        logger.warning("learning_store: legacy kare list unreadable (%s) — import skipped", e)
        return 0
    if not isinstance(items, list):
        items = []
    imported = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("tekst") or "").strip()
        if not text:
            continue
        day = str(item.get("dato") or "").strip()
        ts = f"{day}T00:00:00+00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None
        add_inbox_item(
            text,
            source="legacy",
            pointer=f"kare_huske.json#{item.get('id', '')}",
            context=str(item.get("kontekst") or ""),
            ts=ts,
        )
        imported += 1
    set_meta(_LEGACY_IMPORT_KEY, json.dumps({"ts": _utc_ts(), "rows": imported, "file": str(src)}))
    logger.info("learning_store: imported %d legacy kare-list rows into the agenda inbox", imported)
    return imported


# ── Dev-slot runs (P118 B2+) ────────────────────────────────────────────────


def open_topics(item_limit: int = 10) -> list[dict]:
    """Topics with unhandled inbox rows, oldest first, each with its newest `item_limit` rows
    (pointers included) and the total count of open rows. Nothing is filtered or judged here."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        topics = conn.execute(
            f"SELECT t.*, COUNT(i.id) AS open_items FROM topics t "
            f"JOIN inbox_items i ON i.topic_id = t.id AND i.status IN ({placeholders}) "
            f"WHERE t.kind = 'agenda' GROUP BY t.id ORDER BY t.first_seen ASC",
            OPEN_STATUSES,
        ).fetchall()
        out = []
        for t_row in topics:
            items = conn.execute(
                f"SELECT id, ts, source, user_id, rid, pointer, text, context FROM inbox_items "
                f"WHERE topic_id = ? AND status IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (t_row["id"], *OPEN_STATUSES, item_limit),
            ).fetchall()
            d = dict(t_row)
            d["items"] = [dict(i) for i in items]
            out.append(d)
        return out
    finally:
        conn.close()


def start_run(run_id: str, step: str = "S0") -> None:
    _ensure_db()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO run_state (run_id, step, cursor, started) VALUES (?, ?, '', ?) "
            "ON CONFLICT(run_id) DO UPDATE SET step = excluded.step",
            (run_id, step, _utc_ts()),
        )
        conn.commit()
    finally:
        conn.close()


def set_run_step(run_id: str, step: str, cursor: str = "") -> None:
    _ensure_db()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE run_state SET step = ?, cursor = ? WHERE run_id = ?", (step, cursor, run_id)
        )
        conn.commit()
    finally:
        conn.close()


def finish_run(run_id: str) -> None:
    _ensure_db()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE run_state SET finished = ?, step = 'done' WHERE run_id = ?", (_utc_ts(), run_id)
        )
        conn.commit()
    finally:
        conn.close()


def last_finished_run_start() -> str | None:
    """Start time of the newest finished run — S0 collects what happened after it."""
    _ensure_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT started FROM run_state WHERE finished IS NOT NULL ORDER BY started DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def save_artifact(run_id: str, step: str, payload: dict) -> int:
    _ensure_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO run_artifacts (run_id, step, ts, payload) VALUES (?, ?, ?, ?)",
            (run_id, step, _utc_ts(), json.dumps(payload, ensure_ascii=False, default=str)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def load_artifact(run_id: str, step: str) -> dict | None:
    _ensure_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload FROM run_artifacts WHERE run_id = ? AND step = ? ORDER BY id DESC LIMIT 1",
            (run_id, step),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def add_finding(
    run_id: str,
    text: str,
    evidence_refs: list,
    verified: bool,
    topic_id: int | None = None,
    verifier: dict | None = None,
) -> int:
    """Store a finding with its S4 result (and the 9B verifier's answer, if any).
    Unverified findings are kept, never dropped."""
    _ensure_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO findings (run_id, topic_id, text, evidence_refs, verified, ts, verifier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                topic_id,
                text,
                json.dumps(evidence_refs, ensure_ascii=False),
                1 if verified else 0,
                _utc_ts(),
                json.dumps(verifier or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_inbox_item(item_id: int) -> dict | None:
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_verdict(
    run_id: str,
    kind: str,
    target: str,
    verdict: str,
    evidence_refs: list,
    verified: bool = False,
    model: str = "",
) -> int:
    """Record a 27B decision (kind: priority / clean / trace). Code only stores it."""
    _ensure_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO verdicts (run_id, kind, target, verdict, evidence_refs, verified, model, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                kind,
                target,
                verdict,
                json.dumps(evidence_refs, ensure_ascii=False),
                1 if verified else 0,
                model,
                _utc_ts(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def apply_topic_verdict(topic_id: int, status: str, label: str = "") -> int:
    """Status change that follows from a 27B verdict (P115 D5): the topic and its open rows move
    to `status`; rows get consumed_at. Text, source, rid and pointer are never touched.
    Returns the number of inbox rows moved."""
    _ensure_db()
    now = _utc_ts()
    conn = _get_conn()
    try:
        if label:
            conn.execute(
                "UPDATE topics SET status = ?, label = ? WHERE id = ?",
                (status, label[:200], topic_id),
            )
        else:
            conn.execute("UPDATE topics SET status = ? WHERE id = ?", (status, topic_id))
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        cur = conn.execute(
            f"UPDATE inbox_items SET status = ?, consumed_at = ? WHERE topic_id = ? AND status IN ({placeholders})",
            (status, now, topic_id, *OPEN_STATUSES),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def topic_history(topic_id: int, item_limit: int = 10, verdict_limit: int = 5) -> dict | None:
    """Everything stored about one topic: the topic row, its newest rows (any status), earlier
    verdicts and findings. Read-only — the dev slot's `dev_history` tool."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        topic = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        if not topic:
            return None
        items = conn.execute(
            "SELECT id, ts, source, rid, pointer, text, context, status FROM inbox_items "
            "WHERE topic_id = ? ORDER BY id DESC LIMIT ?",
            (topic_id, item_limit),
        ).fetchall()
        verdicts = conn.execute(
            "SELECT run_id, kind, verdict, evidence_refs, verified, ts FROM verdicts "
            "WHERE target = ? ORDER BY id DESC LIMIT ?",
            (f"topic:{topic_id}", verdict_limit),
        ).fetchall()
        findings = conn.execute(
            "SELECT run_id, text, evidence_refs, verified, ts FROM findings "
            "WHERE topic_id = ? ORDER BY id DESC LIMIT ?",
            (topic_id, verdict_limit),
        ).fetchall()
        return {
            "topic": dict(topic),
            "items": [dict(r) for r in items],
            "verdicts": [dict(r) for r in verdicts],
            "findings": [dict(r) for r in findings],
        }
    finally:
        conn.close()


def earlier_verdicts(topic_ids: list[int], per_topic: int = 2) -> dict[int, list[dict]]:
    """Newest earlier verdicts per topic — Janus's look back (T2-D2)."""
    if not topic_ids:
        return {}
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    out: dict[int, list[dict]] = {}
    try:
        for tid in topic_ids:
            rows = conn.execute(
                "SELECT run_id, kind, verdict, verified, ts FROM verdicts WHERE target = ? "
                "ORDER BY id DESC LIMIT ?",
                (f"topic:{tid}", per_topic),
            ).fetchall()
            if rows:
                out[tid] = [dict(r) for r in rows]
        return out
    finally:
        conn.close()


def link_topic(topic_id: int, into_id: int) -> int:
    """Follow a 27B «same as #into_id» verdict: the topic and its open rows get status 'merged',
    the topic points to `into_id`, and the target's count grows by the linked topic's count.
    Rows keep their own topic_id, text, rid and pointer — the link is reversible and nothing is
    deleted. Returns the number of rows moved to 'merged'."""
    if topic_id == into_id:
        return 0
    _ensure_db()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT count FROM topics WHERE id = ?", (topic_id,)).fetchone()
        target = conn.execute("SELECT id FROM topics WHERE id = ?", (into_id,)).fetchone()
        if not row or not target:
            return 0
        conn.execute(
            "UPDATE topics SET status = 'merged', merged_into = ? WHERE id = ?", (into_id, topic_id)
        )
        conn.execute("UPDATE topics SET count = count + ? WHERE id = ?", (row[0], into_id))
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        cur = conn.execute(
            f"UPDATE inbox_items SET status = 'merged', consumed_at = ? WHERE topic_id = ? AND status IN ({placeholders})",
            (_utc_ts(), topic_id, *OPEN_STATUSES),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def pending_problems(limit: int = 500) -> list[dict]:
    """Topics judged 'problem' (main topics, not linked away) that have no finding yet — the S3
    queue carried over from earlier nights. Without this, a problem S1 found but S3 had no time
    for would never be developed (live run 2026-10-03). Order (P118 B8): never developed first,
    then least recently developed, then oldest — so a topic S3 tried without a finding moves to
    the back instead of leading the queue every night."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            # A topic S1 left without a label is described by its first row (S3 needs a text).
            "SELECT t.id, CASE WHEN t.label = '' THEN substr((SELECT i.text FROM inbox_items i "
            "WHERE i.topic_id = t.id ORDER BY i.id LIMIT 1), 1, 300) ELSE t.label END AS label, "
            "t.first_seen, t.theme, t.last_developed FROM topics t "
            "WHERE t.status = 'problem' AND t.merged_into IS NULL AND t.kind = 'agenda' "
            "AND NOT EXISTS (SELECT 1 FROM findings f WHERE f.topic_id = t.id) "
            "ORDER BY t.last_developed IS NOT NULL, t.last_developed ASC, t.first_seen ASC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def topic_info(topic_ids: list[int]) -> dict[int, dict]:
    """id → {theme, last_developed, first_seen, label} for the given topics (S3 selection)."""
    if not topic_ids:
        return {}
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in topic_ids)
        rows = conn.execute(
            "SELECT id, theme, last_developed, first_seen, label FROM topics "
            f"WHERE id IN ({placeholders})",
            tuple(topic_ids),
        ).fetchall()
        return {r["id"]: dict(r) for r in rows}
    finally:
        conn.close()


def mark_developed(topic_id: int) -> None:
    """S3 took this topic tonight (finding or not) — it moves behind the untried ones."""
    _ensure_db()
    conn = _get_conn()
    try:
        conn.execute("UPDATE topics SET last_developed = ? WHERE id = ?", (_utc_ts(), topic_id))
        conn.commit()
    finally:
        conn.close()


# ── Resolved lifecycle (P133 C2, D4/D11) ────────────────────────────────────


def resolve_topic(topic_id: int, conclusion: str, *, rid: str, user_id: str) -> dict | None:
    """Mark a topic resolved with a short conclusion. Only the caller checks *who* may do it
    (executor: live user turn + role from settings, D11); this function records it and locks
    the topic: later rows that match it are stored as 'echo' and never reopen it. Open rows of
    the topic leave the queue (status 'resolved'). Returns the resolved info, or None when the
    topic does not exist or the conclusion is empty."""
    conclusion = (conclusion or "").strip()
    if not conclusion:
        return None
    _ensure_db()
    conn = _get_conn()
    try:
        root = _root_topic(conn, topic_id)
        if not conn.execute("SELECT 1 FROM topics WHERE id = ?", (root,)).fetchone():
            return None
        now = _utc_ts()
        conn.execute(
            "UPDATE topics SET status = 'resolved', conclusion = ?, resolved_at = ?, "
            "resolved_rid = ?, resolved_by = ? WHERE id = ?",
            (conclusion[:1000], now, rid or "", user_id or "", root),
        )
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        conn.execute(
            f"UPDATE inbox_items SET status = 'resolved', consumed_at = ? "
            f"WHERE topic_id = ? AND status IN ({placeholders})",
            (now, root, *OPEN_STATUSES),
        )
        conn.commit()
        return _resolved_info(conn, root)
    finally:
        conn.close()


def resolved_topics(limit: int = 100) -> list[dict]:
    """Resolved topics, newest first, with conclusion and how many echoes they got since."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT t.id, t.label, t.kind, t.theme, t.conclusion, t.resolved_at, t.resolved_by, "
            "(SELECT COUNT(*) FROM inbox_items i WHERE i.topic_id = t.id AND i.status = 'echo') "
            "AS echoes FROM topics t WHERE t.status = 'resolved' "
            "ORDER BY t.resolved_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def echo_counts(since_utc: str) -> dict[int, int]:
    """Echo rows per resolved topic since a time — «the solution holds: N echoes»."""
    _ensure_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT topic_id, COUNT(*) FROM inbox_items WHERE status = 'echo' AND ts >= ? "
            "GROUP BY topic_id",
            (since_utc,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


def echo_topic(topic_id: int, resolved_id: int) -> int:
    """Follow Janus's «same as resolved #X» (P133 C4): the topic points to the resolved one and
    its open rows become echoes of it — the resolved topic stays resolved. Nothing is deleted;
    returns the number of rows moved. 0 when the target is not resolved."""
    if topic_id == resolved_id:
        return 0
    _ensure_db()
    conn = _get_conn()
    try:
        if not _is_resolved(conn, resolved_id):
            return 0
        row = conn.execute("SELECT count FROM topics WHERE id = ?", (topic_id,)).fetchone()
        if not row:
            return 0
        conn.execute(
            "UPDATE topics SET status = 'merged', merged_into = ? WHERE id = ?",
            (resolved_id, topic_id),
        )
        conn.execute("UPDATE topics SET count = count + ? WHERE id = ?", (row[0], resolved_id))
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        cur = conn.execute(
            f"UPDATE inbox_items SET status = 'echo', consumed_at = ? "
            f"WHERE topic_id = ? AND status IN ({placeholders})",
            (_utc_ts(), topic_id, *OPEN_STATUSES),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def proposal_followups(since_utc: str) -> list[dict]:
    """Confirmed change proposals decided since `since_utc`, with how many new rows their topic
    got after the decision — code counts, Janus judges whether the symptom stopped (P133 C4)."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT p.id, p.text, p.decided_at, COALESCE(f.topic_id, p.topic_id) AS topic_id, "
            "t.label AS topic_label FROM proposals p "
            "LEFT JOIN findings f ON f.id = p.finding_id "
            "LEFT JOIN topics t ON t.id = COALESCE(f.topic_id, p.topic_id) "
            "WHERE p.status = 'confirmed' AND p.kind = 'change' AND p.decided_at >= ? "
            "ORDER BY p.decided_at",
            (since_utc,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["new_rows_since"] = (
                conn.execute(
                    "SELECT COUNT(*) FROM inbox_items i JOIN topics t ON t.id = i.topic_id "
                    "WHERE (t.id = ? OR t.merged_into = ?) AND i.ts > ?",
                    (d["topic_id"], d["topic_id"], d["decided_at"]),
                ).fetchone()[0]
                if d["topic_id"]
                else 0
            )
            out.append(d)
        return out
    finally:
        conn.close()


def add_reopen_proposal(topic_id: int, text: str) -> int | None:
    """A request to reopen a resolved topic — a human confirms it like any dev proposal
    (never reopened silently, P133 PR3). None when the topic is not resolved."""
    _ensure_db()
    conn = _get_conn()
    try:
        root = _root_topic(conn, topic_id)
        if not _is_resolved(conn, root):
            return None
        cur = conn.execute(
            "INSERT INTO proposals (finding_id, text, status, ts, topic_id, kind) "
            "VALUES (NULL, ?, 'pending', ?, ?, 'reopen')",
            ((text or "").strip()[:1000], _utc_ts(), root),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def backfill_themes() -> int:
    """One-off and idempotent: rows and topics written before B8 have no theme. Code rules only
    (no LLM) — same resolve_theme() a new write without a theme gets. A topic takes its first
    row's theme. Returns the number of topics given a theme."""
    _ensure_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, source, rid, text, context, topic_id FROM inbox_items WHERE theme = '' "
            "ORDER BY id"
        ).fetchall()
        if not rows:
            return 0
        try:
            tools = tools_by_rid({r[2] for r in rows if r[2]})
        except Exception as e:
            logger.warning("learning_store: tool lookup for theme backfill failed (%s)", e)
            tools = {}
        for item_id, source, rid, text, context, _tid in rows:
            theme = resolve_theme(
                None, text=text, context=context, source=source, rid_tools=tools.get(rid)
            )
            conn.execute("UPDATE inbox_items SET theme = ? WHERE id = ?", (theme, item_id))
        cur = conn.execute(
            "UPDATE topics SET theme = (SELECT i.theme FROM inbox_items i "
            "WHERE i.topic_id = topics.id ORDER BY i.id LIMIT 1) "
            "WHERE theme = '' AND EXISTS (SELECT 1 FROM inbox_items i WHERE i.topic_id = topics.id)"
        )
        conn.commit()
        logger.info(
            "learning_store: theme backfill — %d rows, %d topics", len(rows), cur.rowcount
        )
        return cur.rowcount
    finally:
        conn.close()


# ── Proposals (P118 B4, T2-D13: confirmed in chat, never applied by the slot) ──


def add_proposal(finding_id: int, text: str) -> int:
    _ensure_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO proposals (finding_id, text, status, ts) VALUES (?, ?, 'pending', ?)",
            (finding_id, text, _utc_ts()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_proposals(status: str | None = "pending", limit: int = 50) -> list[dict]:
    """Proposals with their finding (text, evidence refs, topic label), newest first."""
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT p.id, p.text, p.status, p.decided_by, p.ts, p.decided_at, p.kind, "
            "f.id AS finding_id, f.text AS finding, f.evidence_refs, "
            "COALESCE(f.topic_id, p.topic_id) AS topic_id, t.label AS topic_label "
            "FROM proposals p LEFT JOIN findings f ON f.id = p.finding_id "
            "LEFT JOIN topics t ON t.id = COALESCE(f.topic_id, p.topic_id) "
        )
        args: tuple = ()
        if status:
            sql += "WHERE p.status = ? "
            args = (status,)
        sql += "ORDER BY p.id DESC LIMIT ?"
        return [dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]
    finally:
        conn.close()


def decide_proposal(proposal_id: int, status: str, decided_by: str) -> dict | None:
    """Record a human decision ('confirmed' / 'rejected') on a pending proposal. Only records —
    applying a confirmed proposal is a human's job (VISION thread 1). Returns the row or None
    when it does not exist or was already decided."""
    if status not in ("confirmed", "rejected"):
        raise ValueError(status)
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "UPDATE proposals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, decided_by, _utc_ts(), proposal_id),
        )
        conn.commit()
        if not cur.rowcount:
            return None
        row = conn.execute(
            "SELECT kind, topic_id FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if status == "confirmed" and row and row["kind"] == "reopen" and row["topic_id"]:
            # A human confirmed the reopen (P133 PR3): the topic goes back in the queue. The
            # conclusion and resolved_* fields stay as history.
            conn.execute(
                "UPDATE topics SET status = 'problem', last_developed = NULL WHERE id = ?",
                (row["topic_id"],),
            )
            conn.commit()
        return dict(conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone())
    finally:
        conn.close()


def run_findings(run_id: str) -> list[dict]:
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT f.*, t.label AS topic_label, t.theme AS topic_theme FROM findings f "
            "LEFT JOIN topics t ON t.id = f.topic_id "
            "WHERE f.run_id = ? ORDER BY f.id",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Consolidations (P118 B6, T2-D4/T2-D8: week → month → half-year → year; nothing deleted) ──


def consolidation_exists(level: str, period: str, scope: str = "dev") -> bool:
    _ensure_db()
    conn = _get_conn()
    try:
        return (
            conn.execute(
                "SELECT 1 FROM consolidations WHERE level = ? AND period = ? AND scope = ?",
                (level, period, scope),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def add_consolidation(
    level: str, period: str, summary: str, covers: list, counts: dict, scope: str = "dev"
) -> int:
    _ensure_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO consolidations (level, period, summary, covers, counts, ts, scope) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                level,
                period,
                summary,
                json.dumps(covers),
                json.dumps(counts, ensure_ascii=False),
                _utc_ts(),
                scope,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_consolidations(level: str, periods: list[str], scope: str = "dev") -> list[dict]:
    if not periods:
        return []
    _ensure_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" for _ in periods)
        rows = conn.execute(
            f"SELECT * FROM consolidations WHERE level = ? AND scope = ? AND period IN ({marks}) ORDER BY period",
            (level, scope, *periods),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def earliest_activity() -> str | None:
    """Oldest timestamp in the inbox (the start of what can be consolidated)."""
    _ensure_db()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT MIN(ts) FROM inbox_items").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def period_counts(start_utc: str, end_utc: str) -> dict:
    """Mechanical counts for [start, end): inbox rows by source/status, topics touched, findings,
    verified, proposals by status, slot runs. Code only counts — it never judges."""
    _ensure_db()
    conn = _get_conn()
    try:
        rng = (start_utc, end_utc)
        by_source = dict(
            conn.execute(
                "SELECT source, COUNT(*) FROM inbox_items WHERE ts >= ? AND ts < ? GROUP BY source",
                rng,
            ).fetchall()
        )
        by_status = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM inbox_items WHERE ts >= ? AND ts < ? GROUP BY status",
                rng,
            ).fetchall()
        )
        topics = conn.execute(
            "SELECT COUNT(DISTINCT topic_id) FROM inbox_items WHERE ts >= ? AND ts < ?", rng
        ).fetchone()[0]
        findings, verified = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(verified), 0) FROM findings WHERE ts >= ? AND ts < ?",
            rng,
        ).fetchone()
        proposals = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM proposals WHERE ts >= ? AND ts < ? GROUP BY status",
                rng,
            ).fetchall()
        )
        runs = conn.execute(
            "SELECT COUNT(*) FROM run_state WHERE started >= ? AND started < ? AND finished IS NOT NULL",
            rng,
        ).fetchone()[0]
        return {
            "inbox_rows": sum(by_source.values()),
            "by_source": by_source,
            "by_status": by_status,
            "topics": topics,
            "findings": findings,
            "verified": verified,
            "proposals": proposals,
            "runs": runs,
        }
    finally:
        conn.close()


def period_material(start_utc: str, end_utc: str, limit: int = 20) -> dict:
    """What Janus reads for a week: the busiest topics (label + count) and verified findings."""
    _ensure_db()
    conn = _get_conn()
    try:
        rng = (start_utc, end_utc)
        topics = conn.execute(
            "SELECT t.id, t.label, t.status, COUNT(i.id) AS n FROM inbox_items i JOIN topics t ON t.id = i.topic_id "
            "WHERE i.ts >= ? AND i.ts < ? GROUP BY t.id ORDER BY n DESC LIMIT ?",
            (*rng, limit),
        ).fetchall()
        findings = conn.execute(
            "SELECT f.text, t.label FROM findings f LEFT JOIN topics t ON t.id = f.topic_id "
            "WHERE f.ts >= ? AND f.ts < ? AND f.verified = 1 ORDER BY f.id LIMIT ?",
            (*rng, limit),
        ).fetchall()
        item_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM inbox_items WHERE ts >= ? AND ts < ? ORDER BY id", rng
            ).fetchall()
        ]
        return {
            "topics": [
                {"topic_id": r[0], "label": r[1], "status": r[2], "rows": r[3]} for r in topics
            ],
            "verified_findings": [{"topic": r[1] or "", "text": r[0]} for r in findings],
            "item_ids": item_ids,
        }
    finally:
        conn.close()


def retire_aged_open_rows(start_utc: str, end_utc: str, older_than_utc: str) -> int:
    """PR3: open rows of a consolidated week that are older than 7 days leave the ACTIVE inbox
    (status 'consolidated'). They stay in the table, linked from the week's consolidation row."""
    _ensure_db()
    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        cur = conn.execute(
            f"UPDATE inbox_items SET status = 'consolidated', consumed_at = ? "
            f"WHERE ts >= ? AND ts < ? AND ts < ? AND status IN ({placeholders})",
            (_utc_ts(), start_utc, end_utc, older_than_utc, *OPEN_STATUSES),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
