"""The Frigate user's fact store (P144-s04): `state/frigate/frigate.db`.

Everything the Frigate system user writes goes through this module — nothing else opens the
DB. It is the one place where these live (or will live):
  - the storage gate (consent applies to what is stored — P144 invariants):
      * a claim KTSB checked and found wrong (`rejected`), or never checked (`none`), never stores a name
        («KTSB does not believe Frigate»); a claim checked without contradiction but not confirmed
        (`unverified`) keeps the name, marked unverified — the butler must remember (2026-10-26,
        P144 decision «storage gate by claim status», s06a);
      * a person who said «Frigate may not track me» is stored as `untracked`, no name;
      * a consent lookup that fails strips the name (fail closed);
      * a stripped name is also scrubbed from the event's free text and check JSON;
  - encryption (N19 point 3, comes last) → `_seal()` / `_unseal()`, plaintext until then;
  - retention tiers + rollups into `hourly` / `daily` (s07);
  - episode frames on disk (s11): `<state>/frigate_snapshots/episodes/<episode_id>/<i>.jpg`, own caps + rotation.

All functions are sync; async callers use `asyncio.to_thread` (rule 10). Writers never raise —
they log and return False. Times are UTC epoch seconds.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from kaare_core import receipts
from kaare_core.config import get_local_tz
from kaare_core.frigate_user import faces, sealing, settings
from kaare_core.retention import engine
from kaare_core.storage import rawlog, sqlite_files
from kaare_core.users import frigate_consent
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

DB_PATH = Path("/kaare/state/frigate/frigate.db")
# Argus reads the Frigate subsystem's JSONL log (kaare_argus.LOG_SOURCES); a store that cannot write says so there once
# per failure streak (P144-s14). normalcy.py reads only `detection_end` lines from it, so these lines never count.

SCHEMA_VERSION = 11

# Episode = the whole situation across cameras: an event starting within this many seconds of
# the open episode's last activity joins it; the episode closes after this long of quiet.
# Visit = events of the same person closer than VISIT_GAP_S. Both are GUI settings
# (Integrations → Frigate, `configs/frigate_user.yaml`, s06a); these constants are the fallback.
EPISODE_GAP_S = 120
VISIT_GAP_S = 30 * 60
# P144-s26: activity the store has not seen yet (a track Frigate still has open, an end event being written) holds an
# episode open at most this long past the episode's own end — someone standing at the door for an hour must not keep a
# situation open; what lands later is joined by the merge step (merge_episodes).
HOLD_MAX_S = 600
PENDING_STALE_S = 3600  # an event pending its analysis longer than this no longer holds its episode's conclusion
MERGED = "merged"  # status of an episode absorbed by merge_episodes — kept (never deleted), hidden from the readers
EPISODE_MIN_MOVEMENT = 0.08  # share of the frame (P144-s06d; s09's 7-day measurement)
# Objects that can stand still for hours (parked): they keep an episode open only with a real moving burst.
VEHICLE_LABELS = ("car", "license_plate", "motorcycle", "bus", "truck", "boat", "bicycle")
# Frigate labels that claim an animal (P144-s14 label gate; hidden from the 9B's see call like a face name).
ANIMAL_LABELS = ("deer", "dog", "cat", "bird", "horse", "sheep", "cow", "bear", "fox", "raccoon", "squirrel",
                 "rabbit", "skunk", "moose", "elk", "goat", "badger", "hedgehog", "kangaroo", "zebra", "giraffe",
                 "elephant")
PATH_MAX_POINTS = 60  # stored path per event (frames + route need the shape, not every box)

PERSON_KINDS = ("household", "safe_guest", "not_welcome", "unclassified", "unknown", "untracked")
NAMED_KINDS = ("household", "safe_guest", "not_welcome", "unclassified")
CLAIM_STATUSES = ("confirmed", "rejected", "unverified", "none")
NAMED_CLAIMS = ("confirmed", "unverified")  # claim statuses that may keep Frigate's claimed name


def _episode_gap(gap_s: float | None) -> float:
    if gap_s is not None:
        return gap_s
    try:
        return settings.episode_gap_s()
    except Exception:
        return EPISODE_GAP_S


def _min_movement() -> float:
    try:
        return settings.episode_min_movement()
    except Exception:
        return EPISODE_MIN_MOVEMENT


def _visit_gap(gap_s: float | None) -> float:
    if gap_s is not None:
        return gap_s
    try:
        return settings.visit_gap_s()
    except Exception:
        return VISIT_GAP_S


@dataclass
class FrigateUserEvent:
    event_id: str
    camera: str
    label: str
    start_ts: float
    end_ts: float | None = None
    place: str | None = None  # P137 place id
    person_ref: str | None = None  # KTSB username (household), Frigate face name (guests)
    # Who classifies the face is s05's accessor (kaare_core.frigate_user.faces). Until it is
    # wired (s06), the caller passes person_kind and this module only enforces the gate.
    person_kind: str = "unknown"
    claim_status: str = "none"
    claim_score: float | None = None
    check: dict = field(default_factory=dict)  # which layers voted what, and why
    attributes: dict = field(default_factory=dict)  # L3 output
    description: str | None = None  # appearance text — kept 7 days only (s07)
    analysis: str | None = None  # scene text
    # P144 6e (3): the image model's raw L3 reply as it gave it (≤ RAW_CAP_BYTES) — the same detail tier as description
    l3_raw: str | None = None
    created_ts: float | None = None  # set by the store
    tier: str = "event"
    episode_id: int | None = None  # set by the store (attach_to_episode)
    # Episode rule (s06d). In: the facts the pipe has — not stored as such. None = unknown (old behaviour).
    path_data: list | None = None  # Frigate path_data [[[x, y], ts], …]
    camera_role: str | None = None  # frigate_cameras.yaml role of the camera
    # The camera card's «Kan lage episoder» (P144-s14): False = never drives; None = unknown / not applied (urgent).
    camera_episodes: bool | None = None
    stationary: bool | None = None  # Frigate's flag at the end of the event (stored)
    # Out: set by the store from the facts above.
    drives: bool = True  # may open / keep open an episode
    active_start: float | None = None  # the event's last moving burst (None = start_ts / end_ts)
    active_end: float | None = None
    path: list = field(default_factory=list)  # stored: [[x, y, ts], …] inside the active window, ≤ PATH_MAX_POINTS
    # J19: Frigate's box over the track (box_track.normalised): [[ts, x, y, w, h, ratio, motionless, stationary], …]
    boxes: list = field(default_factory=list)
    # (empty = it did not move: a parked/stationary object; with drives False + a path = moved on a camera with episodes off)
    # Camera settings (P144-s12), in: what the pipe decided from the ticks. "normal" = the rule above; "join_only" =
    # never drives, may join an open episode (a plate); "none" = a plain event, never in an episode (not ticked /
    # below min. confidence). The pipe keeps it in check["episode_rule"] so a re-recorded event keeps it.
    episode_rule: str = "normal"
    # Licence plate claim (P144-s12 Part B) — Frigate's reading, never a fact: Frigate's known-plate name, or the raw
    # plate (plate_known False; dropped after the unclassified-name days, expire_unknown_plates). None = no plate read.
    plate_claim: str | None = None
    plate_known: bool | None = None
    # P150 6b: the pseudonymous `vehicle-<hex>` of a verified, unknown plate (frigate_user.vehicles) — kept when the privacy
    # rule drops plate_claim, never replaced by an update without one. None = no verified plate.
    vehicle_id: str | None = None


@dataclass
class Episode:
    episode_id: int
    start_ts: float
    end_ts: float
    cameras: list[str]
    labels: list[str]
    person_refs: list[str]  # only names the storage gate allowed
    event_count: int
    status: str  # open | closed
    conclusion: dict | None  # filled by the 27B step (s06) via conclude_episode()
    concluded_ts: float | None
    scene: dict | None = None  # the 9B scene step's JSON (s06d) via set_episode_scene()
    route: str | None = None  # the code route text (s06d)


@dataclass
class Visit:
    id: int
    person_ref: str
    person_kind: str
    first_ts: float
    last_ts: float
    cameras: list[str]
    event_count: int
    closed: bool


# ── schema + migrations ─────────────────────────────────────────────────────────

# Versioned one-time migrations: _MIGRATIONS[n] takes a version n-1 DB to version n.
# Version 0 = a file with no `meta` table (new or empty).
_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT    NOT NULL UNIQUE,
    camera       TEXT    NOT NULL,
    place        TEXT,
    label        TEXT    NOT NULL,
    start_ts     REAL    NOT NULL,
    end_ts       REAL,
    person_ref   TEXT,
    person_kind  TEXT    NOT NULL,
    claim_status TEXT    NOT NULL,
    claim_score  REAL,
    "check"      TEXT    NOT NULL DEFAULT '{}',
    attributes   TEXT    NOT NULL DEFAULT '{}',
    description  TEXT,
    analysis     TEXT,
    created_ts   REAL    NOT NULL,
    updated_ts   REAL    NOT NULL,
    tier         TEXT    NOT NULL DEFAULT 'event',
    visit_id     INTEGER REFERENCES visits(id),
    folded       INTEGER NOT NULL DEFAULT 0,
    episode_id   INTEGER REFERENCES episodes(episode_id)
);
CREATE INDEX IF NOT EXISTS idx_events_episode ON events(episode_id);
CREATE TABLE IF NOT EXISTS episodes (
    episode_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts     REAL    NOT NULL,
    end_ts       REAL    NOT NULL,
    cameras      TEXT    NOT NULL DEFAULT '[]',
    labels       TEXT    NOT NULL DEFAULT '[]',
    person_refs  TEXT    NOT NULL DEFAULT '[]',
    event_count  INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'open',
    conclusion   TEXT,
    concluded_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_episodes_open ON episodes(status, end_ts);
CREATE INDEX IF NOT EXISTS idx_episodes_time ON episodes(start_ts);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_ts);
CREATE INDEX IF NOT EXISTS idx_events_person ON events(person_ref, start_ts);
CREATE INDEX IF NOT EXISTS idx_events_unfolded ON events(folded);
CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_ref  TEXT    NOT NULL,
    person_kind TEXT    NOT NULL,
    first_ts    REAL    NOT NULL,
    last_ts     REAL    NOT NULL,
    cameras     TEXT    NOT NULL DEFAULT '[]',
    event_count INTEGER NOT NULL DEFAULT 0,
    closed      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_visits_person ON visits(person_ref, last_ts);
-- Filled by s07 (rollups); created now so the schema is one contract.
CREATE TABLE IF NOT EXISTS hourly (
    hour_ts     INTEGER NOT NULL,
    place       TEXT    NOT NULL DEFAULT '',
    camera      TEXT    NOT NULL DEFAULT '',
    person_ref  TEXT    NOT NULL DEFAULT '',
    person_kind TEXT    NOT NULL,
    label       TEXT    NOT NULL DEFAULT '',
    event_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_ts, place, camera, person_ref, person_kind, label)
);
CREATE TABLE IF NOT EXISTS daily (
    day         TEXT    NOT NULL,
    person_ref  TEXT    NOT NULL DEFAULT '',
    person_kind TEXT    NOT NULL,
    places      TEXT    NOT NULL DEFAULT '[]',
    first_ts    REAL,
    last_ts     REAL,
    event_count INTEGER NOT NULL DEFAULT 0,
    visit_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, person_ref, person_kind)
);
"""

# v2 (P144-s06d): the episode rule's per-event facts + the per-episode scene step.
_V2 = """
ALTER TABLE events ADD COLUMN stationary INTEGER;
ALTER TABLE events ADD COLUMN drives INTEGER NOT NULL DEFAULT 1;
ALTER TABLE events ADD COLUMN active_start REAL;
ALTER TABLE events ADD COLUMN active_end REAL;
ALTER TABLE events ADD COLUMN path TEXT NOT NULL DEFAULT '[]';
ALTER TABLE episodes ADD COLUMN scene TEXT;
ALTER TABLE episodes ADD COLUMN route TEXT;
"""

# v3 (P144-s06e): the dev-meeting topic register — a topic the 27B raised stays open until resolved; while open it is
# only counted, never raised again (shadow day 1: 16 of 40 episodes raised the same Frigate-tuning suggestion).
_V3 = """
CREATE TABLE IF NOT EXISTS dev_topics (
    topic            TEXT    PRIMARY KEY,
    text             TEXT    NOT NULL,
    theme            TEXT    NOT NULL DEFAULT 'other',
    first_ts         REAL    NOT NULL,
    last_ts          REAL    NOT NULL,
    count            INTEGER NOT NULL DEFAULT 1,
    status           TEXT    NOT NULL DEFAULT 'open',
    first_episode_id INTEGER,
    last_episode_id  INTEGER,
    resolved_ts      REAL
);
CREATE INDEX IF NOT EXISTS idx_dev_topics_status ON dev_topics(status, last_ts);
"""

# v4 (P144-s12): licence plates as vehicle claims (Frigate's reading — known-plate name or raw plate).
_V4 = """
ALTER TABLE events ADD COLUMN plate_claim TEXT;
ALTER TABLE events ADD COLUMN plate_known INTEGER;
"""

# v5 (P144-s07): retention — an event's counts are in hourly/daily (`rolled`), a slimmed episode, one row per run.
_V5 = """
ALTER TABLE events ADD COLUMN rolled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE episodes ADD COLUMN slimmed_ts REAL;
CREATE INDEX IF NOT EXISTS idx_events_rolled ON events(rolled, start_ts);
CREATE TABLE IF NOT EXISTS retention_runs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    dry_run INTEGER NOT NULL,
    report  TEXT    NOT NULL
);
"""

# v6 (J19, from P144-s31): Frigate's per-update box over the track (box_track.py) — small JSON, cleared with `path`.
_V6 = """
ALTER TABLE events ADD COLUMN boxes TEXT NOT NULL DEFAULT '[]';
"""

# v7 (J18): admin's answers to «Hva er her?» — a spot (camera × grid cell) where persons appear/disappear. Append-only:
# a new answer is a new row (the newest counts), nothing is overwritten or deleted.
_V7 = """
CREATE TABLE IF NOT EXISTS spot_answers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    camera  TEXT NOT NULL,
    cell    TEXT NOT NULL,
    status  TEXT NOT NULL,
    name    TEXT,
    by_user TEXT,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spot_answers ON spot_answers(camera, cell, id);
"""

# v8 (J20): admin's answers to «Hva er dette?» about a still thing (key camera|label|cell). Append-only, newest counts.
_V8 = """
CREATE TABLE IF NOT EXISTS thing_answers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    key     TEXT NOT NULL,
    status  TEXT NOT NULL,
    name    TEXT,
    by_user TEXT,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_thing_answers ON thing_answers(key, id);
"""

# v9 (P144 6e item 3): the image model's raw L3 reply per event, sealed, cleared with the detail tier (events.slim).
_V9 = """
ALTER TABLE events ADD COLUMN l3_raw TEXT;
"""

# v10 (P150 6b): the pseudonymous id of a verified unknown plate — what stays when the plate text is dropped.
_V10 = """
ALTER TABLE events ADD COLUMN vehicle_id TEXT;
CREATE INDEX IF NOT EXISTS idx_events_vehicle ON events(vehicle_id) WHERE vehicle_id IS NOT NULL;
"""

# v11 (DT04 R0.6a, N19 point 3): seal what was written as plaintext before encryption existed — once, in one
# transaction. `True` = a reference (deterministic seal, looked up with `=`/`IN`), `False` = free text / JSON.
_SEALED_COLUMNS: dict[str, tuple[tuple[str, bool], ...]] = {
    "events": (("person_ref", True), ("plate_claim", True), ("check", False), ("attributes", False),
               ("description", False), ("analysis", False), ("l3_raw", False)),
    "episodes": (("person_refs", False), ("scene", False), ("route", False), ("conclusion", False)),
    "visits": (("person_ref", True),),
    "hourly": (("person_ref", True),),
    "daily": (("person_ref", True),),
    "dev_topics": (("text", False),),
    "spot_answers": (("name", False), ("by_user", False)),
    "thing_answers": (("name", False), ("by_user", False)),
}


def _v11_seal_existing(conn: sqlite3.Connection) -> None:
    sealed = 0
    for table, cols in _SEALED_COLUMNS.items():
        names = ", ".join(f'"{c}"' for c, _ref in cols)
        for row in conn.execute(f"SELECT rowid, {names} FROM {table}").fetchall():
            new = [(sealing.seal_ref if ref else sealing.seal)(row[i + 1]) for i, (_c, ref) in enumerate(cols)]
            if new != list(row[1:]):
                sets = ", ".join(f'"{c}"=?' for c, _ref in cols)
                conn.execute(f"UPDATE {table} SET {sets} WHERE rowid=?", (*new, row[0]))
                sealed += 1
    logger.info("[FRIGATE_STORE] sealed %d existing rows", sealed)


def seal_existing_pictures() -> int:
    """Encrypt event snapshots and episode frames that were written before encryption (atomic per file). Read
    either way, so stopping half-way is harmless; a second run skips what is sealed."""
    root = episode_frames_dir().parent  # <state>/frigate_snapshots — the event snapshots; episodes/ below it
    n = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*.jpg"):
        data = path.read_bytes()
        sealed = sealing.seal_bytes(data)
        if sealed is not data and sealed != data:
            tmp = path.with_name(f".{path.name}.seal.tmp")
            tmp.write_bytes(sealed)
            os.replace(tmp, path)
            n += 1
    return n


_MIGRATIONS: dict[int, object] = {1: _V1, 2: _V2, 3: _V3, 4: _V4, 5: _V5, 6: _V6, 7: _V7, 8: _V8, 9: _V9, 10: _V10,
                                  11: _v11_seal_existing}

RAW_CAP_BYTES = 4096  # P144 6e (3): one raw model reply kept per L3 / frame at most (UTF-8 bytes)


def cap_raw(text: str | None) -> str | None:
    """The raw reply cut to RAW_CAP_BYTES without splitting a character; None when empty."""
    if not text:
        return None
    raw = str(text).encode("utf-8")
    return raw[:RAW_CAP_BYTES].decode("utf-8", errors="ignore") if len(raw) > RAW_CAP_BYTES else str(text)

_init_lock = threading.Lock()
_initialized_for: Path | None = None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_files.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA secure_delete=ON")  # a scrubbed name or an old plaintext value is overwritten, not left (R0.6a)
    sqlite_files.share_with_group(DB_PATH)
    return conn


def _read_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


def _migrate(conn: sqlite3.Connection) -> None:
    """Under a file lock beside the DB: several KTSB processes (API, Argus, Jing, Jang, the night job) open frigate.db,
    and two starting together both read the old version and both migrated (house 2026-11-14, v11). The version is read
    again under the lock, so the second one finds nothing to do."""
    lock_path = DB_PATH.with_name(DB_PATH.name + ".migrate.lock")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError as e:  # no file locks on this filesystem (some Docker Desktop shares): migrate unguarded
            logger.warning("[FRIGATE_STORE] no migration lock (%s) — migrating without it", e)
            _migrate_locked(conn)
            return
        try:
            _migrate_locked(conn)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _migrate_locked(conn: sqlite3.Connection) -> None:
    version = _read_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"frigate.db schema {version} is newer than this code ({SCHEMA_VERSION})"
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        step = _MIGRATIONS[target]
        if callable(step):
            step(conn)  # a Python step (v11): runs inside the transaction the version row commits
        else:
            conn.executescript(step)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(target),),
        )
        conn.commit()
        logger.info("[FRIGATE_STORE] schema migrated to version %d", target)
        if target == 11:
            # The plaintext the rows held before sealing may still sit in free pages and the WAL — rewrite the file.
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as e:
                logger.warning("[FRIGATE_STORE] vacuum after sealing failed: %s", e)
            # Pictures after the commit — files need no DB write lock (sealing them took 1.6 s in the house).
            try:
                logger.info("[FRIGATE_STORE] sealed %d existing pictures", seal_existing_pictures())
            except Exception as e:  # plaintext pictures still read; the next night step is not needed for that
                logger.warning("[FRIGATE_STORE] sealing existing pictures stopped: %s", e)


def _get_conn() -> sqlite3.Connection:
    """Open the DB, creating/migrating it once per DB_PATH (tests repoint DB_PATH)."""
    global _initialized_for
    conn = _connect()
    if _initialized_for != DB_PATH:
        with _init_lock:
            if _initialized_for != DB_PATH:
                try:
                    _migrate(conn)
                except Exception:
                    conn.close()
                    raise
                _initialized_for = DB_PATH
    return conn


def reset_for_tests() -> None:
    global _initialized_for
    _initialized_for = None
    _close_events_cache()
    with _status_lock:
        _write_state.update(ok=True, op=None, cause=None, error=None, since=None, failures=0)


# ── write failures are loud (P144-s14) ─────────────────────────────────────────
# 2026-10-28 02:00–02:07 every write failed («attempt to write a readonly database»: -wal/-shm owned by another user)
# and only an ERROR line showed it. Now: a WARNING with the cause, a status the GUI shows (Integrations → Frigate),
# one line for Argus per failure streak, and one retry on a fresh connection.

_status_lock = threading.Lock()
_write_state: dict = {"ok": True, "op": None, "cause": None, "error": None, "since": None, "failures": 0}

_CAUSE_HINT = {
    "read_only": "read-only database — check owner/permissions of frigate.db and its -wal/-shm files",
    "permission": "permission denied — check owner/permissions of the state/frigate folder",
    "disk_full": "disk full",
    "locked": "database locked by another writer",
    "cannot_open": "cannot open the database file",
    "error": "unexpected error",
}


def _write_cause(exc: BaseException) -> str:
    msg = str(exc).lower()
    if "readonly" in msg or "read-only" in msg:
        return "read_only"
    if isinstance(exc, PermissionError) or "permission denied" in msg:
        return "permission"
    if "disk is full" in msg or "no space left" in msg:
        return "disk_full"
    if "database is locked" in msg:
        return "locked"
    if "unable to open database" in msg:
        return "cannot_open"
    return "error"


def _argus_line(stage: str, level: str, message: str, **extra) -> None:
    """One JSONL line in the Frigate subsystem's Argus source. Never raises."""
    line = {"ts": datetime.now(UTC).isoformat(), "source": "frigate-store",
            "subsystem": "frigate", "stage": stage, "level": level, "message": message, **extra}
    try:
        with open(rawlog.current_segment("frigate_mqtt"), "a", encoding="utf-8") as f:  # P150: today's raw segment
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[FRIGATE_STORE] could not tell Argus (raw frigate_mqtt segment): %s", e)


def _write_failed(op: str, exc: BaseException) -> None:
    cause = _write_cause(exc)
    with _status_lock:
        first = _write_state["ok"]
        _write_state.update(ok=False, op=op, cause=cause, error=str(exc)[:300], failures=_write_state["failures"] + 1,
                            since=_write_state["since"] if not first else time.time())
    logger.warning("[FRIGATE_STORE] cannot write frigate.db (%s, %s): %s — %s", op, cause, exc, _CAUSE_HINT[cause])
    if first:
        _argus_line("store_write_failed", "error", f"Kameraminnet kan ikke skrive ({op}): {_CAUSE_HINT[cause]}",
                    error=str(exc)[:300], cause=cause, op=op)


def _write_ok() -> None:
    with _status_lock:
        if _write_state["ok"]:
            return
        failures, since = _write_state["failures"], _write_state["since"]
        _write_state.update(ok=True, op=None, cause=None, error=None, since=None, failures=0)
    logger.warning("[FRIGATE_STORE] frigate.db writable again after %d failed writes", failures)
    _argus_line("store_write_recovered", "info", f"Kameraminnet skriver igjen ({failures} feilede skrivinger)",
                failures=failures, failed_since=since)


def write_status() -> dict:
    """{ok, op, cause, error, since, failures} — the last write problem, for the GUI and Argus. ok=True when the last
    write succeeded (or nothing has failed since start)."""
    with _status_lock:
        return dict(_write_state)


def schema_version() -> int:
    conn = _get_conn()
    try:
        return _read_version(conn)
    finally:
        conn.close()


# ── encryption (N19 point 3, DT04 R0.6a) — the mechanism lives in sealing.py ────
# Free text and JSON: random nonce. A person or plate reference (events/visits/hourly/daily.person_ref,
# events.plate_claim): deterministic, so `=` / `IN` / GROUP BY / the rollups' primary keys keep working.


def _seal(text: str | None) -> str | None:
    return sealing.seal(text)


def _seal_ref(text: str | None) -> str | None:
    return sealing.seal_ref(text)


def _unseal(text: str | None) -> str | None:
    return sealing.unseal(text)


def _seal_bytes(data: bytes) -> bytes:
    return sealing.seal_bytes(data)


def _unseal_bytes(data: bytes) -> bytes | None:
    return sealing.unseal_bytes(data)


# ── storage gate ────────────────────────────────────────────────────────────────


def _consent_names(person_ref: str) -> tuple[bool, set[str]]:
    """(may be named, names to scrub if not). Raises on lookup errors — caller fails closed."""
    names = {person_ref}
    user = user_store.get_user(person_ref)
    if user:
        names.add(user.get("display_name") or "")
        names.update(user.get("frigate_faces") or [])
        if not user.get("frigate_tracking", True):
            return False, names
    if frigate_consent.is_untracked(person_ref):
        owner = user_store.get_username_for_frigate_face(person_ref)
        if owner:
            names.add(owner)
        return False, names
    return True, names


def _scrub_text(text: str | None, names: set[str]) -> str | None:
    if not text:
        return text
    for name in sorted((n for n in names if n), key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "person", text, flags=re.IGNORECASE)
    return text


def _scrub_obj(obj, names: set[str]):
    if isinstance(obj, str):
        return _scrub_text(obj, names)
    if isinstance(obj, dict):
        return {_scrub_text(k, names): _scrub_obj(v, names) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_obj(v, names) for v in obj]
    return obj


def _apply_gate(ev: FrigateUserEvent) -> tuple[FrigateUserEvent, str]:
    """Return the event as it may be stored, and why a name was dropped ('' if not)."""
    ref = (ev.person_ref or "").strip() or None
    kind, reason = ev.person_kind, ""
    scrub: set[str] = set()
    if ref and ev.claim_status not in NAMED_CLAIMS:
        kind, reason, scrub = "unknown", f"claim_{ev.claim_status}", {ref}
    elif ref and kind not in NAMED_KINDS:
        reason, scrub = "kind_unnamed", {ref}
    elif ref:
        try:
            allowed, names = _consent_names(ref)
            why = "consent"
        except Exception as e:
            logger.warning("[FRIGATE_STORE] consent lookup failed, name dropped: %s", e)
            allowed, names, why = False, {ref}, "consent_error"
        if not allowed:
            kind, reason, scrub = "untracked", why, names
    elif kind in NAMED_KINDS:
        kind = "unknown"  # a named kind without a name is nobody we can name
    if not reason:
        return ev, ""
    gated = replace(ev)
    gated.person_ref = None
    gated.person_kind = kind
    gated.description = _scrub_text(ev.description, scrub)
    gated.analysis = _scrub_text(ev.analysis, scrub)
    gated.l3_raw = _scrub_text(ev.l3_raw, scrub)
    gated.attributes = _scrub_obj(ev.attributes, scrub)
    gated.check = {**_scrub_obj(ev.check, scrub), "storage_gate": reason}
    return gated, reason


def _validate(ev: FrigateUserEvent) -> str:
    if not ev.event_id or not ev.camera or not ev.label:
        return "event_id, camera and label are required"
    if ev.person_kind not in PERSON_KINDS:
        return f"unknown person_kind {ev.person_kind!r}"
    if ev.claim_status not in CLAIM_STATUSES:
        return f"unknown claim_status {ev.claim_status!r}"
    return ""


# ── API ─────────────────────────────────────────────────────────────────────────


# Called after every stored event with (the event as the storage gate kept it, its episode id or None) — the RAM
# counters for the 27B's count table (counts.py, s16) listen here, so the everyday path never queries the DB for them.
_stored_listeners: list = []


def add_stored_listener(fn) -> None:
    if fn not in _stored_listeners:
        _stored_listeners.append(fn)


def _tell_listeners(ev: FrigateUserEvent, episode_id: int | None) -> None:
    for fn in _stored_listeners[:]:  # a copy: a listener may (un)register while we iterate
        try:
            fn(ev, episode_id)
        except Exception as e:
            logger.debug("[FRIGATE_STORE] stored-listener failed: %s", e)


HUMAN_VERDICTS = ("yes", "no")


def _human_fields(ev: FrigateUserEvent, human: dict) -> FrigateUserEvent:
    """A person's own answer decides the claim (P144 #9, P149 D3): «ja» = confirmed as that user, «nei» = the name was
    wrong (stored without a name, like any rejected claim). The answer is kept in check.human with who and when."""
    check = {**(ev.check or {}), "human": human}
    if "pre_human" not in check:  # N39: what the sensors had said, so a withdrawn answer can restore it
        check["pre_human"] = {"person_ref": ev.person_ref, "person_kind": ev.person_kind,
                              "claim_status": ev.claim_status}
    if human.get("verdict") == "yes":  # J22: admin's «it was <member>» names the member (`about`), not admin
        return replace(ev, person_ref=human.get("about") or human["by"], person_kind="household",
                       claim_status="confirmed", check=check)
    return replace(ev, person_ref=None, person_kind="unknown", claim_status="rejected", check=check)


def _keep_human(ev: FrigateUserEvent, old) -> FrigateUserEvent:
    """The pipe may write an event again later; a human answer already stored on it wins over the sensors. A write that
    carries its own human answer (J22: the member correcting admin's answer) is that newer answer — kept as it is."""
    if old is None or (ev.check or {}).get("human"):
        return ev
    try:
        old_check = json.loads(_unseal(old["check"]) or "{}")
    except (TypeError, ValueError):
        return ev
    keep = {k: old_check[k] for k in ("human_history", "pre_human") if k in old_check and k not in (ev.check or {})}
    if keep:  # N39: a withdrawn or corrected answer's history survives a re-write by the pipe
        ev = replace(ev, check={**(ev.check or {}), **keep})
    human = old_check.get("human")
    withdrawn = [{k: h.get(k) for k in ("by", "verdict", "ts")} for h in (ev.check or {}).get("human_history", [])
                 if isinstance(h, dict) and h.get("withdrawn_by")]
    if isinstance(human, dict) and {k: human.get(k) for k in ("by", "verdict", "ts")} in withdrawn:
        return ev  # N39: this very answer was withdrawn by its person — it must not come back
    if isinstance(human, dict) and human.get("verdict") in HUMAN_VERDICTS and human.get("by"):
        return _human_fields(ev, human)
    return ev


def set_human_answer(event_id: str, by_user: str, verdict: str, channel: str, ts: float | None = None,
                     about: str | None = None) -> bool:
    """Store a person's «ja»/«nei» to «Var det deg?» on the event itself (who, when, which channel). «privat» is not
    stored here — the weight system keeps it, the event stays as the sensors left it. Never raises."""
    if verdict not in HUMAN_VERDICTS or not by_user:
        return False
    ev = event_by_id(event_id)
    if ev is None:
        return False
    human = {"by": by_user, "verdict": verdict, "channel": channel, "ts": time.time() if ts is None else ts}
    if about and about != by_user:
        human["about"] = about  # J22: admin answered for this household member
    old = (ev.check or {}).get("human")
    if isinstance(old, dict):  # N39: the replaced answer is kept, never dropped
        ev = replace(ev, check={**(ev.check or {}), "human_history": [*(ev.check or {}).get("human_history", []), old]})
    return record_event(_human_fields(ev, human))


def clear_human_answer(event_id: str, by_user: str, channel: str) -> bool:
    """N39: a person withdrew their answer («vet ikke» / «privat» after «ja»/«nei»): the answer moves to
    check.human_history and the event goes back to what the sensors had said (check.pre_human). Never raises."""
    try:
        ev = event_by_id(event_id)
        if ev is None:
            return False
        check = dict(ev.check or {})
        old = check.pop("human", None)
        if not isinstance(old, dict):
            return True  # nothing on the event to take back
        check["human_history"] = [*check.get("human_history", []),
                                  {**old, "withdrawn_by": by_user, "withdrawn_channel": channel,
                                   "withdrawn_ts": time.time()}]
        pre = check.get("pre_human") or {}
        return record_event(replace(ev, person_ref=pre.get("person_ref"), person_kind=pre.get("person_kind") or "unknown",
                                    claim_status=pre.get("claim_status") or "none", check=check))
    except Exception as e:
        logger.warning("[FRIGATE_STORE] clear human answer failed: %s", type(e).__name__)
        return False


def record_event(ev: FrigateUserEvent, _retry: bool = True) -> bool:
    """Store (or update, same event_id) one event through the storage gate. Never raises."""
    problem = _validate(ev)
    if problem:
        logger.warning("[FRIGATE_STORE] event %s refused: %s", ev.event_id, problem)
        return False
    gated, reason = _apply_gate(ev)
    gated = _with_activity(gated)
    if gated.episode_rule in ("join_only", "none"):
        gated = replace(gated, drives=False)  # the camera settings (s12): never opens or moves an episode
    if reason:
        logger.info("[FRIGATE_STORE] event %s stored without name (%s)", ev.event_id, reason)
    now = time.time()
    try:
        conn = _get_conn()
    except Exception as e:
        _write_failed("record_event", e)
        return record_event(ev, _retry=False) if _retry else False
    try:
        conn.execute("BEGIN IMMEDIATE")  # one writer decides episode membership at a time
        old = conn.execute(
            'SELECT visit_id, "check" FROM events WHERE event_id=?', (gated.event_id,)
        ).fetchone()
        gated = _keep_human(gated, old)
        conn.execute(
            """INSERT INTO events (event_id, camera, place, label, start_ts, end_ts, person_ref,
                   person_kind, claim_status, claim_score, "check", attributes, description,
                   analysis, created_ts, updated_ts, tier, visit_id, folded,
                   stationary, drives, active_start, active_end, path, plate_claim, plate_known, boxes, l3_raw,
                   vehicle_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET
                   camera=excluded.camera, place=excluded.place, label=excluded.label,
                   start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                   person_ref=excluded.person_ref, person_kind=excluded.person_kind,
                   claim_status=excluded.claim_status, claim_score=excluded.claim_score,
                   "check"=excluded."check", attributes=excluded.attributes,
                   description=excluded.description, analysis=excluded.analysis, l3_raw=excluded.l3_raw,
                   updated_ts=excluded.updated_ts, tier=excluded.tier,
                   visit_id=NULL, folded=0, stationary=excluded.stationary, drives=excluded.drives,
                   active_start=excluded.active_start, active_end=excluded.active_end, path=excluded.path,
                   plate_claim=excluded.plate_claim, plate_known=excluded.plate_known,
                   vehicle_id=COALESCE(excluded.vehicle_id, events.vehicle_id),
                   boxes=CASE WHEN excluded.boxes='[]' THEN events.boxes ELSE excluded.boxes END""",
            (
                gated.event_id,
                gated.camera,
                gated.place,
                gated.label,
                gated.start_ts,
                gated.end_ts,
                _seal_ref(gated.person_ref),
                gated.person_kind,
                gated.claim_status,
                gated.claim_score,
                _seal(json.dumps(gated.check, ensure_ascii=False)),
                _seal(json.dumps(gated.attributes, ensure_ascii=False)),
                _seal(gated.description),
                _seal(gated.analysis),
                gated.created_ts or now,
                now,
                gated.tier,
                None if gated.stationary is None else int(gated.stationary),
                int(gated.drives),
                gated.active_start,
                gated.active_end,
                json.dumps(gated.path),
                _seal_ref(gated.plate_claim),
                None if gated.plate_known is None else int(gated.plate_known),
                json.dumps(gated.boxes),
                _seal(cap_raw(gated.l3_raw)),
                gated.vehicle_id,
            ),
        )
        if old and old["visit_id"]:
            _refresh_visit(conn, old["visit_id"])  # an updated event is folded again
        episode_id = attach_to_episode(conn, gated.event_id) if gated.episode_rule != "none" else None
        conn.commit()
        _write_ok()
        receipts.log_saved("frigate_event", where="frigate.db",
                           refs=[gated.event_id] + ([f"ep{episode_id}"] if episode_id else []))
        if not gated.check.get("pending"):  # s26: the RAM counts hear the finished row only (no name yet in a skeleton)
            _tell_listeners(gated, episode_id)
        return True
    except Exception as e:
        _write_failed("record_event", e)
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return record_event(ev, _retry=False) if _retry else False
    finally:
        conn.close()


def _row_to_event(r: sqlite3.Row) -> FrigateUserEvent:
    check = json.loads(_unseal(r["check"]) or "{}")
    rule = check.get("episode_rule")
    return FrigateUserEvent(
        event_id=r["event_id"],
        camera=r["camera"],
        place=r["place"],
        label=r["label"],
        start_ts=r["start_ts"],
        end_ts=r["end_ts"],
        person_ref=_unseal(r["person_ref"]),
        person_kind=r["person_kind"],
        claim_status=r["claim_status"],
        claim_score=r["claim_score"],
        check=check,
        attributes=json.loads(_unseal(r["attributes"]) or "{}"),
        description=_unseal(r["description"]),
        analysis=_unseal(r["analysis"]),
        l3_raw=_unseal(r["l3_raw"]) if "l3_raw" in r.keys() else None,  # noqa: SIM118 (sqlite3.Row: `in` checks values)
        vehicle_id=r["vehicle_id"] if "vehicle_id" in r.keys() else None,  # noqa: SIM118 (sqlite3.Row)
        created_ts=r["created_ts"],
        tier=r["tier"],
        episode_id=r["episode_id"],
        stationary=None if r["stationary"] is None else bool(r["stationary"]),
        drives=bool(r["drives"]),
        active_start=r["active_start"],
        active_end=r["active_end"],
        path=json.loads(r["path"] or "[]"),
        boxes=json.loads(r["boxes"] or "[]"),
        episode_rule=rule if rule in ("normal", "join_only", "none") else "normal",
        plate_claim=_unseal(r["plate_claim"]),
        plate_known=None if r["plate_known"] is None else bool(r["plate_known"]),
    )


def _row_to_visit(r: sqlite3.Row) -> Visit:
    return Visit(
        id=r["id"],
        person_ref=_unseal(r["person_ref"]) or "",
        person_kind=r["person_kind"],
        first_ts=r["first_ts"],
        last_ts=r["last_ts"],
        cameras=json.loads(r["cameras"] or "[]"),
        event_count=r["event_count"],
        closed=bool(r["closed"]),
    )


# ── N40: one short-lived cache of decoded recent events (the question pages: six endpoints, one decode) ───────────
# R0.6a: the decoded rows are personal — RAM only, per process, EVENTS_CACHE_S seconds, dropped by a timer even when no
# one asks again, never written anywhere. Exact invalidation: a dedicated read connection's PRAGMA data_version
# changes on every commit by any other connection (this process or another), so a new event or a human answer shows
# at once. Filtering (tracking off, role, may_see_snapshot) stays with the callers, after this — decoding is pure.
EVENTS_CACHE_S = 10.0
EVENTS_CACHE_SPAN_S = 15 * 86400  # the windows the question pages ask for (7–14 days, + the place images)
_ev_lock = threading.Lock()
_ev_cache: dict = {}  # {"path", "conn", "version", "lo", "rows", "t"}


def _drop_events_cache(expired_only: bool = False) -> None:
    with _ev_lock:
        if expired_only and time.monotonic() - _ev_cache.get("t", 0.0) < EVENTS_CACHE_S:
            return
        _ev_cache.pop("rows", None)
        _ev_cache.pop("version", None)


def _events_cache_size() -> int:
    """Tests: how many decoded rows the cache holds now."""
    with _ev_lock:
        return len(_ev_cache.get("rows") or [])


def _close_events_cache() -> None:
    with _ev_lock:
        conn = _ev_cache.pop("conn", None)
        _ev_cache.clear()
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _cached_recent(lo_needed: float) -> list[FrigateUserEvent] | None:
    """Decoded events with start_ts ≥ (now − span), from the cache or one fresh load; None when not usable."""
    with _ev_lock:
        now = time.time()
        if lo_needed < now - EVENTS_CACHE_SPAN_S + 60:
            return None
        path = str(DB_PATH)
        if _ev_cache.get("path") != path:
            old = _ev_cache.pop("conn", None)
            if old is not None:
                old.close()
            _ev_cache.clear()
            _ev_cache["path"] = path
        if _ev_cache.get("conn") is None:
            _get_conn().close()  # created/migrated once, as everywhere else
            _ev_cache["conn"] = sqlite_files.connect(path, check_same_thread=False)
        conn = _ev_cache["conn"]
        version = conn.execute("PRAGMA data_version").fetchone()[0]
        fresh = time.monotonic() - _ev_cache.get("t", 0.0) < EVENTS_CACHE_S
        if _ev_cache.get("rows") is not None and _ev_cache.get("version") == version and fresh \
                and _ev_cache.get("lo", now) <= lo_needed:
            return _ev_cache["rows"]
        lo = now - EVENTS_CACHE_SPAN_S
        load = _get_conn()
        try:
            load.row_factory = sqlite3.Row
            rows = [_row_to_event(r) for r in load.execute("SELECT * FROM events WHERE start_ts >= ? ORDER BY start_ts",
                                                           (lo,))]
        finally:
            load.close()
        _ev_cache.update(version=version, lo=lo, rows=rows, t=time.monotonic())
        timer = threading.Timer(EVENTS_CACHE_S + 0.1, _drop_events_cache, kwargs={"expired_only": True})
        timer.daemon = True
        timer.start()
        return rows


def events_between(
    start: float, end: float, person_ref: str | None = None, camera: str | None = None
) -> list[FrigateUserEvent]:
    """Events with start_ts in [start, end], oldest first. Recent windows without a person filter come from the N40
    cache (one decode for a whole page); each caller gets its own copies."""
    if person_ref is None:
        try:
            cached = _cached_recent(start)
        except Exception as e:
            logger.debug("[FRIGATE_STORE] events cache skipped: %s", type(e).__name__)
            cached = None
        if cached is not None:
            return [replace(e) for e in cached if start <= e.start_ts <= end and (camera is None or e.camera == camera)]
    sql = "SELECT * FROM events WHERE start_ts BETWEEN ? AND ?"
    args: list = [start, end]
    if person_ref is not None:
        sql += " AND person_ref=?"
        args.append(_seal_ref(person_ref))
    if camera is not None:
        sql += " AND camera=?"
        args.append(camera)
    conn = _get_conn()
    try:
        return [_row_to_event(r) for r in conn.execute(sql + " ORDER BY start_ts", args)]
    finally:
        conn.close()


def event_by_id(event_id: str) -> FrigateUserEvent | None:
    """One stored event by its Frigate event id (the second face sensor's late vote, P144-s19), or None."""
    if not event_id or not exists():
        return None
    row = _with_conn(lambda c: c.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone())
    return _row_to_event(row) if row else None


def visits_between(start: float, end: float, person_ref: str | None = None) -> list[Visit]:
    """Visits overlapping [start, end], oldest first."""
    sql = "SELECT * FROM visits WHERE last_ts >= ? AND first_ts <= ?"
    args: list = [start, end]
    if person_ref is not None:
        sql += " AND person_ref=?"
        args.append(_seal_ref(person_ref))
    conn = _get_conn()
    try:
        return [_row_to_visit(r) for r in conn.execute(sql + " ORDER BY first_ts", args)]
    finally:
        conn.close()


def last_visit(person_ref: str) -> Visit | None:
    """The person's latest visit (as folded by close_visits)."""
    conn = _get_conn()
    try:
        r = conn.execute(
            "SELECT * FROM visits WHERE person_ref=? ORDER BY last_ts DESC LIMIT 1",
            (_seal_ref(person_ref),),
        ).fetchone()
        return _row_to_visit(r) if r else None
    finally:
        conn.close()


def _refresh_visit(conn: sqlite3.Connection, visit_id: int) -> None:
    """Recompute a visit from its events; a visit left without events is removed (derived data)."""
    rows = conn.execute(
        "SELECT start_ts, COALESCE(end_ts, start_ts) AS end_ts, camera, person_kind "
        "FROM events WHERE visit_id=? ORDER BY start_ts",
        (visit_id,),
    ).fetchall()
    if not rows:
        conn.execute("DELETE FROM visits WHERE id=?", (visit_id,))
        return
    cameras = sorted({r["camera"] for r in rows})
    conn.execute(
        "UPDATE visits SET first_ts=?, last_ts=?, cameras=?, event_count=?, person_kind=? "
        "WHERE id=?",
        (
            rows[0]["start_ts"],
            max(r["end_ts"] for r in rows),
            json.dumps(cameras, ensure_ascii=False),
            len(rows),
            rows[-1]["person_kind"],
            visit_id,
        ),
    )


_FROM_SETTINGS = object()


def expire_unknown_plates(days=_FROM_SETTINGS, now: float | None = None) -> int:
    """Drop the raw text of unknown plates older than `days` (P144-s12: neighbours, guests and passers-by). `days`
    defaults to the GUI setting «Keep unknown plates» (s07): 7 days, or always (None → nothing dropped). The event
    keeps plate_known False = «an unknown vehicle»; known plates stay. Returns the number of events changed. Never
    raises."""
    if days is _FROM_SETTINGS:
        try:
            days = settings.unknown_plate_days()
        except Exception:
            days = settings.PLATE_KEEP_DAYS
    if days is None:
        return 0
    cutoff = (time.time() if now is None else now) - max(0, days) * 86400
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error("[FRIGATE_STORE] cannot open frigate.db: %s", e)
        return 0
    try:
        n = conn.execute(
            "UPDATE events SET plate_claim=NULL WHERE plate_known=0 AND plate_claim IS NOT NULL AND start_ts < ?",
            (cutoff,),
        ).rowcount
        conn.commit()
        return n
    except Exception as e:
        logger.error("[FRIGATE_STORE] expire_unknown_plates failed: %s", e)
        return 0
    finally:
        conn.close()


def close_visits(now: float | None = None, gap_s: float | None = None) -> int:
    """Fold unfolded named events into visits (same person, gap ≤ gap_s — default the GUI setting)
    and mark visits whose last event is older than gap_s as closed. Idempotent. Returns events
    folded; never raises."""
    now = time.time() if now is None else now
    gap_s = _visit_gap(gap_s)
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error("[FRIGATE_STORE] cannot open frigate.db: %s", e)
        return 0
    folded = 0
    try:
        rows = conn.execute(
            "SELECT id, person_ref, start_ts, COALESCE(end_ts, start_ts) AS end_ts, person_kind "
            "FROM events WHERE folded=0 ORDER BY start_ts"
        ).fetchall()
        for r in rows:
            if r["person_ref"] is None:
                conn.execute("UPDATE events SET folded=1 WHERE id=?", (r["id"],))
                continue
            v = conn.execute(
                "SELECT id FROM visits WHERE person_ref=? AND first_ts - ? <= ? AND last_ts + ? >= ? "
                "ORDER BY last_ts DESC LIMIT 1",
                (r["person_ref"], gap_s, r["end_ts"], gap_s, r["start_ts"]),
            ).fetchone()
            if v:
                visit_id = v["id"]
            else:
                visit_id = conn.execute(
                    "INSERT INTO visits (person_ref, person_kind, first_ts, last_ts) "
                    "VALUES (?, ?, ?, ?)",
                    (r["person_ref"], r["person_kind"], r["start_ts"], r["end_ts"]),
                ).lastrowid
            conn.execute("UPDATE events SET visit_id=?, folded=1 WHERE id=?", (visit_id, r["id"]))
            _refresh_visit(conn, visit_id)
            folded += 1
        conn.execute(
            "UPDATE visits SET closed = CASE WHEN last_ts < ? THEN 1 ELSE 0 END",
            (now - gap_s,),
        )
        conn.commit()
        return folded
    except Exception as e:
        logger.error("[FRIGATE_STORE] close_visits failed: %s", e)
        conn.rollback()
        return 0
    finally:
        conn.close()


# ── episodes ────────────────────────────────────────────────────────────────────

# An event's last activity: its end, or — while Frigate still tracks it — the last time we heard
# about it (so an event that never ends cannot hold an episode open forever).


def _last_burst(path: list, gap_s: float, min_movement: float, end: float) -> tuple[float, float] | None:
    """(start, end) of the last stretch of the path in which the object really moved: points more than gap_s apart
    split the path; a stretch moves when its spread (x or y) is at least min_movement of the frame. None = it never
    moved (parked, box jitter). A «parked 42 h, then drove off» car gives only its drive-off."""
    stretches: list[list[tuple[float, float, float]]] = []
    for point in path:
        try:
            (x, y), ts = point
            x, y, ts = float(x), float(y), float(ts)
        except (TypeError, ValueError):
            continue
        if not stretches or ts - stretches[-1][-1][2] > gap_s:
            stretches.append([])
        stretches[-1].append((x, y, ts))
    for b in reversed(stretches):
        xs, ys = [p[0] for p in b], [p[1] for p in b]
        if max(max(xs) - min(xs), max(ys) - min(ys)) >= min_movement:
            return b[0][2], min(end, b[-1][2])
    return None


def _with_activity(ev: FrigateUserEvent) -> FrigateUserEvent:
    """Decide whether the event may open / keep open an episode, and over which window (P144-s06d):
    a camera whose card says «no episodes» (P144-s14) never drives (but keeps its window and path); a vehicle or a stationary object
    drives only with a moving burst (its last one); a person/animal drives over its last moving burst, or its span. Without facts (no path,
    no role, not flagged stationary) the stored/default values stay — an update of a stored event keeps its window."""
    if (ev.path_data is None and ev.camera_role is None and ev.camera_episodes is None
            and not (ev.stationary and ev.active_start is None)):
        return ev
    gap_s = _episode_gap(None)
    min_movement = _min_movement()
    start = ev.start_ts
    end = ev.end_ts if ev.end_ts is not None else start
    ignored = ev.camera_episodes is False
    burst = _last_burst(ev.path_data, gap_s, min_movement, end) if ev.path_data else None
    if ev.label in VEHICLE_LABELS or ev.stationary:
        if ev.path_data is None and not ev.stationary:
            return replace(ev, drives=not ignored, active_start=start, active_end=end)  # movement unknown
        a0, a1 = burst or (start, end)
        drives = burst is not None
    else:
        (a0, a1), drives = burst or (start, end), True
    # A camera with episodes off keeps its window and path (the route and the frames still show it) but never drives.
    return replace(ev, drives=drives and not ignored, active_start=a0, active_end=a1,
                   path=_trim_path(ev.path_data, a0, a1) if drives else [])


def activity_window(ev: FrigateUserEvent) -> tuple[float, float] | None:
    """(start, end) over which this event would open / keep open an episode — the same rule record_event applies
    (P144-s06d), for activity not stored yet (P144-s26: a track Frigate still has open). None = it would not drive."""
    try:
        act = _with_activity(ev)
    except Exception:
        return None
    if not act.drives:
        return None
    a0 = act.active_start if act.active_start is not None else act.start_ts
    a1 = act.active_end if act.active_end is not None else (act.end_ts if act.end_ts is not None else a0)
    return a0, a1


def _trim_path(path: list | None, a0: float, a1: float) -> list:
    """The path inside the active window as [[x, y, ts], …], evenly thinned to PATH_MAX_POINTS (first + last kept)."""
    pts = []
    for point in path or []:
        try:
            (x, y), ts = point
            x, y, ts = float(x), float(y), float(ts)
        except (TypeError, ValueError):
            continue
        if a0 - 1 <= ts <= a1 + 1:
            pts.append([round(x, 4), round(y, 4), ts])
    if len(pts) > PATH_MAX_POINTS:
        step = (len(pts) - 1) / (PATH_MAX_POINTS - 1)
        pts = [pts[round(i * step)] for i in range(PATH_MAX_POINTS)]
    return pts


def attach_to_episode(conn: sqlite3.Connection, event_id: str, gap_s: float | None = None) -> int | None:
    """Put one stored event into an episode (called inside record_event's transaction).

    Rule (v2, P144-s06d): the event joins the open episode whose activity lies within gap_s of the event's activity
    window (any camera, any label). Only an event that `drives` may open a new episode or move an episode's bounds;
    one that does not (a camera with episodes off, parked/stationary object) joins an episode it overlaps, or stays without
    one (None). Closed episodes never reopen. An event already in an episode stays there. gap_s defaults to the GUI
    setting."""
    gap_s = _episode_gap(gap_s)
    ev = conn.execute(
        "SELECT start_ts, end_ts, drives, active_start, active_end, episode_id FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    episode_id = ev["episode_id"]
    if episode_id is None:
        a0 = ev["active_start"] if ev["active_start"] is not None else ev["start_ts"]
        a1 = ev["active_end"] if ev["active_end"] is not None else (ev["end_ts"] or ev["start_ts"])
        row = conn.execute(
            "SELECT episode_id FROM episodes WHERE status='open' AND end_ts + ? >= ? "
            "AND start_ts - ? <= ? ORDER BY end_ts DESC LIMIT 1",
            (gap_s, a0, gap_s, a1),
        ).fetchone()
        if row:
            episode_id = row["episode_id"]
        elif ev["drives"]:
            episode_id = conn.execute(
                "INSERT INTO episodes (start_ts, end_ts) VALUES (?, ?)", (a0, a1)
            ).lastrowid
        else:
            return None  # stored, but it does not make a situation on its own
        conn.execute("UPDATE events SET episode_id=? WHERE event_id=?", (episode_id, event_id))
    _refresh_episode(conn, episode_id)
    return episode_id


def _refresh_episode(conn: sqlite3.Connection, episode_id: int) -> None:
    rows = conn.execute(
        "SELECT COALESCE(active_start, start_ts) AS a0, COALESCE(active_end, end_ts, updated_ts, start_ts) AS a1, "
        "drives, camera, label, person_ref FROM events WHERE episode_id=?",
        (episode_id,),
    ).fetchall()
    if not rows:
        return
    bounds = [r for r in rows if r["drives"]] or rows  # only real activity sets the episode's time
    names = sorted({_unseal(r["person_ref"]) for r in rows if r["person_ref"]})
    conn.execute(
        "UPDATE episodes SET start_ts=?, end_ts=?, cameras=?, labels=?, person_refs=?, "
        "event_count=? WHERE episode_id=?",
        (
            min(r["a0"] for r in bounds),
            max(r["a1"] for r in bounds),
            json.dumps(sorted({r["camera"] for r in rows}), ensure_ascii=False),
            json.dumps(sorted({r["label"] for r in rows}), ensure_ascii=False),
            _seal(json.dumps(names, ensure_ascii=False)),
            len(rows),
            episode_id,
        ),
    )


def _row_to_episode(r: sqlite3.Row) -> Episode:
    conclusion = _unseal(r["conclusion"])
    return Episode(
        episode_id=r["episode_id"],
        start_ts=r["start_ts"],
        end_ts=r["end_ts"],
        cameras=json.loads(r["cameras"] or "[]"),
        labels=json.loads(r["labels"] or "[]"),
        person_refs=json.loads(_unseal(r["person_refs"]) or "[]"),
        event_count=r["event_count"],
        status=r["status"],
        conclusion=json.loads(conclusion) if conclusion else None,
        concluded_ts=r["concluded_ts"],
        scene=json.loads(_unseal(r["scene"])) if r["scene"] else None,
        route=_unseal(r["route"]),
    )


def closing_due(now: float | None = None, gap_s: float | None = None) -> bool:
    """Is any open episode quiet for more than gap_s by the clock? (The conclude loop asks Frigate for its open tracks
    only then.) Never raises."""
    now = time.time() if now is None else now
    try:
        return bool(_with_conn(lambda c: c.execute(
            "SELECT 1 FROM episodes WHERE status='open' AND end_ts < ? LIMIT 1", (now - _episode_gap(gap_s),)
        ).fetchone()))
    except Exception:
        return False


def _held(start: float, end: float, hold, gap_s: float, now: float) -> bool:
    """An episode quiet by the clock still waits while activity not stored yet lies within gap_s of it — the attach
    rule's own test — but never longer than HOLD_MAX_S past its end."""
    if not hold or now - end > gap_s + HOLD_MAX_S:
        return False
    return any(end + gap_s >= a0 and start - gap_s <= a1 for a0, a1 in hold)


def close_episodes(now: float | None = None, gap_s: float | None = None,
                   hold: list[tuple[float, float]] | None = None) -> list[Episode]:
    """Close open episodes quiet for more than gap_s (default the GUI setting); return the newly
    closed ones (for s06b's 27B step). P144-s26: `hold` = activity windows not stored yet (pipe.unstored_activity —
    end events being written, tracks Frigate still has open); an episode they touch stays open (at most HOLD_MAX_S past
    its end), so a late event joins it instead of opening a new one. Never raises."""
    now = time.time() if now is None else now
    gap_s = _episode_gap(gap_s)
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error("[FRIGATE_STORE] cannot open frigate.db: %s", e)
        return []
    try:
        conn.execute("BEGIN IMMEDIATE")
        ids = []
        for r in conn.execute(
            "SELECT episode_id, start_ts, end_ts FROM episodes WHERE status='open' AND end_ts < ? ORDER BY start_ts",
            (now - gap_s,),
        ):
            if _held(r["start_ts"], r["end_ts"], hold, gap_s, now):
                logger.debug("[FRIGATE_STORE] episode %s held open: activity not stored yet", r["episode_id"])
                continue
            ids.append(r["episode_id"])
        for episode_id in ids:
            conn.execute("UPDATE episodes SET status='closed' WHERE episode_id=?", (episode_id,))
        conn.commit()
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM episodes WHERE episode_id IN ({marks}) ORDER BY start_ts", ids
        ).fetchall()
        return [_row_to_episode(r) for r in rows]
    except Exception as e:
        logger.error("[FRIGATE_STORE] close_episodes failed: %s", e)
        conn.rollback()
        return []
    finally:
        conn.close()


def close_episode(episode_id: int) -> bool:
    """N40: close ONE open episode now (a stuck one, from «Analyser på nytt»). False when it is not open. Never raises."""
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error("[FRIGATE_STORE] cannot open frigate.db: %s", e)
        return False
    try:
        cur = conn.execute("UPDATE episodes SET status='closed' WHERE episode_id=? AND status='open'", (episode_id,))
        conn.commit()
        return cur.rowcount == 1
    except Exception as e:
        logger.error("[FRIGATE_STORE] close_episode failed: %s", e)
        return False
    finally:
        conn.close()


def episode_events(episode_id: int) -> list[FrigateUserEvent]:
    """All events of one episode (by id, not by time — a driving event's start_ts can lie before its episode), in
    order of activity."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE episode_id=? ORDER BY COALESCE(active_start, start_ts)", (episode_id,)
        ).fetchall()
        return [_row_to_event(r) for r in rows]
    finally:
        conn.close()


def episode(episode_id: int) -> Episode | None:
    conn = _get_conn()
    try:
        r = conn.execute("SELECT * FROM episodes WHERE episode_id=?", (episode_id,)).fetchone()
        return _row_to_episode(r) if r else None
    finally:
        conn.close()


def episodes_between(start: float, end: float) -> list[Episode]:
    """Episodes overlapping [start, end], oldest first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM episodes WHERE end_ts >= ? AND start_ts <= ? AND status != ? ORDER BY start_ts",
            (start, end, MERGED),
        ).fetchall()
        return [_row_to_episode(r) for r in rows]
    finally:
        conn.close()


def episodes_page(limit: int = 50, before: float | None = None) -> list[tuple[Episode, list[FrigateUserEvent]]]:
    """The episode view's list (s11): up to `limit` episodes starting before `before` (None = now), newest first,
    each with its events in order of activity — two queries for the whole page."""
    conn = _get_conn()
    try:
        sql, args = "SELECT * FROM episodes WHERE status != ?", [MERGED]
        if before is not None:
            sql += " AND start_ts < ?"
            args.append(before)
        rows = conn.execute(sql + " ORDER BY start_ts DESC, episode_id DESC LIMIT ?", (*args, limit)).fetchall()
        eps = [_row_to_episode(r) for r in rows]
        if not eps:
            return []
        ids = [e.episode_id for e in eps]
        by_ep: dict[int, list[FrigateUserEvent]] = {i: [] for i in ids}
        for r in conn.execute(
            f"SELECT * FROM events WHERE episode_id IN ({','.join('?' * len(ids))}) "
            "ORDER BY COALESCE(active_start, start_ts)", ids
        ):
            by_ep[r["episode_id"]].append(_row_to_event(r))
        return [(e, by_ep[e.episode_id]) for e in eps]
    finally:
        conn.close()


# ── one situation = one episode (P144-s26) ──────────────────────────────────────


def pending_events(since: float) -> list[FrigateUserEvent]:
    """Events from `since` whose analysis has not finished (a skeleton, `check.pending`), oldest first."""
    rows = _with_conn(lambda c: c.execute("SELECT * FROM events WHERE start_ts >= ? ORDER BY start_ts",
                                          (since,)).fetchall())
    return [e for e in map(_row_to_event, rows) if e.check.get("pending")]


def episodes_with_pending(episode_ids, now: float | None = None) -> set[int]:
    """Which of these episodes still hold an event waiting for its analysis (stored less than PENDING_STALE_S ago — a
    row stuck longer no longer holds the conclusion back)."""
    ids = [int(i) for i in episode_ids]
    if not ids:
        return set()
    now = time.time() if now is None else now
    rows = _with_conn(lambda c: c.execute(
        f'SELECT episode_id, "check", created_ts FROM events WHERE episode_id IN ({_marks(len(ids))})', ids).fetchall())
    out: set[int] = set()
    for r in rows:
        try:
            pending = json.loads(_unseal(r["check"]) or "{}").get("pending")
        except (TypeError, ValueError):
            pending = None
        if pending and now - float(r["created_ts"] or 0) < PENDING_STALE_S:
            out.add(int(r["episode_id"]))
    return out


def split_groups(start: float, end: float, gap_s: float | None = None, now: float | None = None) -> list[list[int]]:
    """Chains of episodes in [start, end] that overlap or lie within gap_s of each other — by the attach rule they are
    one situation that was split (a late event after a clock close: our queue, or a track Frigate held open longer than
    the gap). Only chains whose episodes are all closed and hold no pending event; oldest first, ids ascending."""
    gap_s = _episode_gap(gap_s)
    eps = sorted(episodes_between(start, end), key=lambda e: (e.start_ts, e.episode_id))
    chains: list[list[Episode]] = []
    reach = None
    for ep in eps:
        if chains and reach is not None and ep.start_ts <= reach + gap_s:
            chains[-1].append(ep)
            reach = max(reach, ep.end_ts)
        else:
            chains.append([ep])
            reach = ep.end_ts
    chains = [c for c in chains if len(c) > 1 and all(e.status == "closed" for e in c)]
    busy = episodes_with_pending([e.episode_id for c in chains for e in c], now)
    return [sorted(e.episode_id for e in c) for c in chains if not busy & {e.episode_id for e in c}]


def merge_episodes(episode_ids, scene: dict | None = None, now: float | None = None) -> dict | None:
    """Merge closed episodes that were one situation into the lowest id (P144-s26 item 4), in one transaction: their
    events move, the bounds are refreshed, every part's conclusion + scene goes into the survivor's
    `conclusion.history` (the s24 format + `merged_from`, earlier history kept), urgent results are kept, and the
    survivor starts again with `scene` (the caller's) and no conclusion — the conclude loop analyses it once more. The
    absorbed rows are kept, status MERGED + `merged_into`, hidden from the episode readers — never deleted. Returns
    {survivor, absorbed, frames: {id: the part's frame list}} or None (fewer than two, not all closed, or failed)."""
    ids = sorted({int(i) for i in episode_ids})
    if len(ids) < 2:
        return None
    now = time.time() if now is None else now
    survivor, others = ids[0], ids[1:]

    def go(conn):
        rows = {r["episode_id"]: r for r in conn.execute(
            f"SELECT * FROM episodes WHERE episode_id IN ({_marks(len(ids))})", ids)}
        if set(rows) != set(ids) or any(rows[i]["status"] != "closed" for i in ids):
            return None
        eps = {i: _row_to_episode(rows[i]) for i in ids}
        history: list = []
        urgent: list = []
        frames: dict[int, list] = {}
        for i in ids:
            ep = eps[i]
            c = ep.conclusion or {}
            history.extend(h if i == survivor else {**h, "episode_id": i} for h in c.get("history") or [])
            history.append({
                "replaced_ts": now, "merged_from": i, "window": [ep.start_ts, ep.end_ts],
                "conclusion": {k: v for k, v in c.items() if k not in ("history", "urgent")},
                "scene": {k: v for k, v in (ep.scene or {}).items() if k not in ("frames", "claims")},
            })
            urgent.extend(c.get("urgent") or [])
            frames[i] = list((ep.scene or {}).get("frames") or [])
        conn.execute(f"UPDATE events SET episode_id=? WHERE episode_id IN ({_marks(len(others))})",
                     (survivor, *others))
        _refresh_episode(conn, survivor)
        fresh: dict = {"history": history, "attempts": 0, "merged_from": others}
        if urgent:
            fresh["urgent"] = urgent
        conn.execute("UPDATE episodes SET conclusion=?, concluded_ts=NULL, scene=?, route=NULL WHERE episode_id=?",
                     (_seal(json.dumps(fresh, ensure_ascii=False)),
                      _seal(json.dumps(scene, ensure_ascii=False)) if scene is not None else None, survivor))
        for i in others:
            kept = {**(eps[i].conclusion or {}), "merged_into": survivor}
            conn.execute("UPDATE episodes SET status=?, conclusion=? WHERE episode_id=?",
                         (MERGED, _seal(json.dumps(kept, ensure_ascii=False)), i))
        return {"survivor": survivor, "absorbed": others, "frames": frames}

    try:
        out = _tx(go)
    except Exception as e:
        _write_failed("merge_episodes", e)
        return None
    if out:
        _write_ok()
        logger.info("[FRIGATE_STORE] episodes %s merged into %s (one situation)", others, survivor)
    return out


def hidden_names(refs) -> set[str]:
    """Names to hide at read time (s11): every spelling of a stored person who has since said «Frigate may not track
    me». The gate already kept them out at storage; this covers a «no» given afterwards. Fails closed — a lookup
    error hides the name."""
    out: set[str] = set()
    for ref in {r for r in refs if r}:
        try:
            allowed, names = _consent_names(ref)
        except Exception as e:
            logger.warning("[FRIGATE_STORE] consent lookup failed, name hidden: %s", e)
            allowed, names = False, {ref}
        if not allowed:
            out |= {n for n in names if n}
    return out


def scrub(obj, names: set[str]):
    """`obj` with these names replaced by «person» in every string (the gate's own scrub)."""
    return _scrub_obj(obj, names) if names else obj


# ── Kåre's read view (s08) — read helpers only ─────────────────────────────────


def exists() -> bool:
    """True when frigate.db is there — readers check this first so a read never creates the DB."""
    return DB_PATH.exists()


def vehicle_library() -> list[dict]:
    """P150 6b: every pseudonymous vehicle id with how often and on how many household days it was seen, and its newest
    event (for the picture) — never a plate text. Oldest first seen first."""
    tz = _tz()

    def go(conn):
        rows = conn.execute("SELECT vehicle_id, event_id, start_ts FROM events WHERE vehicle_id IS NOT NULL "
                            "ORDER BY start_ts").fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            v = out.setdefault(r["vehicle_id"], {"vehicle_id": r["vehicle_id"], "reads": 0, "days": set(),
                                                 "first_seen": r["start_ts"], "last_seen": r["start_ts"],
                                                 "newest_event_id": r["event_id"]})
            v["reads"] += 1
            v["days"].add(engine.local_day(r["start_ts"], tz))
            v["last_seen"], v["newest_event_id"] = r["start_ts"], r["event_id"]
        return [{**{k: x for k, x in v.items() if k != "days"}, "days_seen": len(v["days"])} for v in out.values()]

    return _with_conn(go)


def known_vehicles() -> list[dict]:
    """Vehicles Frigate already knows by name (its known plates, e.g. «Tilhenger») with their read counts."""
    def go(conn):
        rows = conn.execute("SELECT plate_claim, COUNT(*) AS n FROM events WHERE plate_known=1 AND plate_claim IS NOT "
                            "NULL GROUP BY plate_claim").fetchall()
        return sorted(({"name": _unseal(r["plate_claim"]), "reads": r["n"]} for r in rows),
                      key=lambda x: x["name"].casefold())

    return _with_conn(go)


def known_refs() -> set[str]:
    """Every stored person name (events, visits, day rollups) — for case-insensitive lookup in code, since a sealed
    name cannot be matched in SQL."""
    def go(conn):
        out: set[str] = set()
        for sql in ("SELECT DISTINCT person_ref FROM events WHERE person_ref IS NOT NULL",
                    "SELECT DISTINCT person_ref FROM visits WHERE person_ref != ''",
                    "SELECT DISTINCT person_ref FROM daily WHERE person_ref != ''"):
            out.update(n for (sealed,) in conn.execute(sql) if (n := _unseal(sealed)))
        return out
    return _with_conn(go)


def first_ts() -> float | None:
    """When the memory begins: the oldest event, visit or day rollup (a day counts from its local midnight)."""
    def go(conn):
        found = [v for (v,) in (conn.execute("SELECT MIN(start_ts) FROM events").fetchone(),
                                conn.execute("SELECT MIN(first_ts) FROM visits").fetchone()) if v is not None]
        day = conn.execute("SELECT MIN(day) FROM daily").fetchone()[0]
        if day:
            found.append(engine.day_bounds(day, _tz())[0])
        return min(found) if found else None
    return _with_conn(go)


def known_plates() -> set[str]:
    """Known-plate names Frigate has claimed (plate_known)."""
    rows = _with_conn(lambda c: c.execute(
        "SELECT DISTINCT plate_claim FROM events WHERE plate_known=1 AND plate_claim IS NOT NULL").fetchall())
    return {n for (sealed,) in rows if (n := _unseal(sealed))}


def visit_events(visit_id: int) -> list[FrigateUserEvent]:
    rows = _with_conn(lambda c: c.execute(
        "SELECT * FROM events WHERE visit_id=? ORDER BY start_ts", (visit_id,)).fetchall())
    return [_row_to_event(r) for r in rows]


def named_events_after(refs, after: float) -> list[FrigateUserEvent]:
    """Events of these persons starting after `after`, oldest first (events not yet folded into a visit)."""
    sealed = [_seal_ref(r) for r in refs if r]
    if not sealed:
        return []
    rows = _with_conn(lambda c: c.execute(
        f"SELECT * FROM events WHERE person_ref IN ({_marks(len(sealed))}) AND start_ts > ? ORDER BY start_ts",
        (*sealed, after)).fetchall())
    return [_row_to_event(r) for r in rows]


def plate_events(plates, limit: int = 20) -> list[FrigateUserEvent]:
    """Latest events whose known plate is one of these names, newest first."""
    sealed = [_seal_ref(p) for p in plates if p]
    if not sealed:
        return []
    rows = _with_conn(lambda c: c.execute(
        f"SELECT * FROM events WHERE plate_known=1 AND plate_claim IN ({_marks(len(sealed))}) "
        "ORDER BY start_ts DESC LIMIT ?", (*sealed, limit)).fetchall())
    return [_row_to_event(r) for r in rows]


def last_daily(refs) -> dict | None:
    """The newest day-rollup row of these persons: {day, person_ref, person_kind, places, first_ts, last_ts,
    event_count, visit_count}."""
    sealed = [_seal_ref(r) for r in refs if r]
    if not sealed:
        return None
    r = _with_conn(lambda c: c.execute(
        f"SELECT * FROM daily WHERE person_ref IN ({_marks(len(sealed))}) ORDER BY day DESC LIMIT 1",
        sealed).fetchone())
    if r is None:
        return None
    return {**dict(r), "person_ref": _unseal(r["person_ref"]), "places": json.loads(r["places"] or "[]")}


def episodes_window(start: float, end: float) -> list[tuple[Episode, list[FrigateUserEvent]]]:
    """Episodes overlapping [start, end], newest first, each with its events — two queries."""
    def go(conn):
        eps = [_row_to_episode(r) for r in conn.execute(
            "SELECT * FROM episodes WHERE end_ts >= ? AND start_ts <= ? AND status != ? "
            "ORDER BY start_ts DESC, episode_id DESC", (start, end, MERGED))]
        if not eps:
            return []
        ids = [e.episode_id for e in eps]
        by_ep: dict[int, list[FrigateUserEvent]] = {i: [] for i in ids}
        for r in conn.execute(f"SELECT * FROM events WHERE episode_id IN ({_marks(len(ids))}) "
                              "ORDER BY COALESCE(active_start, start_ts)", ids):
            by_ep[r["episode_id"]].append(_row_to_event(r))
        return [(e, by_ep[e.episode_id]) for e in eps]
    return _with_conn(go)


# ── the nightly step into Kåre's long-term memory (P144-s17) ───────────────────

def meta_get(key: str) -> str | None:
    """One value of frigate.db's meta table (J21b: the pseudonym key), or None."""
    row = _with_conn(lambda c: c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone())
    return row["value"] if row else None


def meta_set_once(key: str, value: str) -> str:
    """Store `value` under `key` unless one is there already; returns what is stored (first writer wins)."""
    _tx(lambda c: c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (key, value)))
    return meta_get(key) or value


def meta_replace(key: str, old: str, new: str) -> None:
    """Swap `old` for `new` only if `old` is still what is stored (R0.6c: seal a plaintext value in place, safe when two
    processes do it at once). `secure_delete` overwrites the old value on disk."""
    _tx(lambda c: c.execute("UPDATE meta SET value=? WHERE key=? AND value=?", (new, key, old)))


LTM_MARKER_KEY = "ltm_marker"


def first_episode_ts() -> float | None:
    """The oldest episode's start — where the nightly LTM step begins on its very first run."""
    return _with_conn(lambda c: c.execute("SELECT MIN(start_ts) FROM episodes WHERE status != ?",
                                          (MERGED,)).fetchone()[0])


def ltm_marker() -> dict:
    """The LTM step's progress: {"settled_through": "YYYY-MM-DD" | None, "done": {day: [episode ids in LTM]}}.
    Raises on an unreadable marker — the caller stops instead of starting over (that would compress days twice)."""
    row = _with_conn(lambda c: c.execute("SELECT value FROM meta WHERE key=?", (LTM_MARKER_KEY,)).fetchone())
    data = json.loads(row["value"]) if row else {}
    if not isinstance(data, dict):
        raise ValueError("ltm_marker is not an object")
    done = data.get("done") or {}
    return {"settled_through": data.get("settled_through"),
            "done": {str(day): sorted({int(i) for i in ids}) for day, ids in done.items()}}


def set_ltm_marker(marker: dict) -> bool:
    """Store the LTM step's progress (written only after the LTM episode is saved). Never raises."""
    value = json.dumps({"settled_through": marker.get("settled_through"), "done": marker.get("done") or {}},
                       ensure_ascii=False, sort_keys=True)
    try:
        _tx(lambda c: c.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (LTM_MARKER_KEY, value)))
        return True
    except Exception as e:
        _write_failed("set_ltm_marker", e)
        return False


def _update_episode(op: str, sql: str, args: tuple) -> bool:
    """One UPDATE on an episode row; a failure is reported (loud) and retried once on a fresh connection."""
    for _attempt in (1, 2):
        try:
            conn = _get_conn()
        except Exception as e:
            _write_failed(op, e)
            continue
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            _write_ok()
            return cur.rowcount == 1
        except Exception as e:
            _write_failed(op, e)
        finally:
            conn.close()
    return False


def set_episode_scene(episode_id: int, scene: dict | None, route: str | None) -> bool:
    """Store the per-episode scene step (9B, s06d) and the code route text. Never raises."""
    return _update_episode(
        "set_episode_scene", "UPDATE episodes SET scene=?, route=? WHERE episode_id=?",
        (_seal(json.dumps(scene, ensure_ascii=False)) if scene is not None else None, _seal(route), episode_id))


def conclude_episode(episode_id: int, conclusion: dict) -> bool:
    """Store the 27B's conclusion for a closed episode (s06). Never raises."""
    return _update_episode(
        "conclude_episode", "UPDATE episodes SET conclusion=?, concluded_ts=? WHERE episode_id=?",
        (_seal(json.dumps(conclusion, ensure_ascii=False)), time.time(), episode_id))


# ── dev-meeting topic register (s06e) ────────────────────────────────────────────


def _row_to_topic(r: sqlite3.Row) -> dict:
    return {
        "topic": r["topic"], "text": _unseal(r["text"]), "theme": r["theme"], "first_ts": r["first_ts"],
        "last_ts": r["last_ts"], "count": r["count"], "status": r["status"],
        "first_episode_id": r["first_episode_id"], "last_episode_id": r["last_episode_id"],
        "resolved_ts": r["resolved_ts"],
    }


def note_dev_topic(topic: str, text: str, theme: str, ts: float, episode_id: int | None) -> dict | None:
    """Count one sighting of a dev-meeting topic. New or resolved → (re)opened and `raised` True; already open →
    only counted, `raised` False. Returns {topic, raised, count, status}, or None on error. Never raises."""
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error("[FRIGATE_STORE] cannot open frigate.db: %s", e)
        return None
    try:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT status, count FROM dev_topics WHERE topic=?", (topic,)).fetchone()
        if r is None:
            conn.execute(
                "INSERT INTO dev_topics (topic, text, theme, first_ts, last_ts, count, status, first_episode_id, "
                "last_episode_id) VALUES (?, ?, ?, ?, ?, 1, 'open', ?, ?)",
                (topic, _seal(text), theme, ts, ts, episode_id, episode_id),
            )
            raised, count = True, 1
        elif r["status"] == "open":
            conn.execute(
                "UPDATE dev_topics SET count=count+1, last_ts=MAX(last_ts, ?), last_episode_id=? WHERE topic=?",
                (ts, episode_id, topic),
            )
            raised, count = False, r["count"] + 1
        else:  # resolved, and seen again: the fix did not hold — raise it again with the new text
            conn.execute(
                "UPDATE dev_topics SET count=count+1, last_ts=MAX(last_ts, ?), last_episode_id=?, status='open', "
                "text=?, resolved_ts=NULL WHERE topic=?",
                (ts, episode_id, _seal(text), topic),
            )
            raised, count = True, r["count"] + 1
        conn.commit()
        return {"topic": topic, "raised": raised, "count": count, "status": "open"}
    except Exception as e:
        logger.error("[FRIGATE_STORE] note_dev_topic %s failed: %s", topic, e)
        conn.rollback()
        return None
    finally:
        conn.close()


def dev_topics(status: str | None = "open") -> list[dict]:
    """The topic register, newest sighting first (`status=None` = all)."""
    conn = _get_conn()
    try:
        if status is None:
            rows = conn.execute("SELECT * FROM dev_topics ORDER BY last_ts DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM dev_topics WHERE status=? ORDER BY last_ts DESC", (status,)).fetchall()
        return [_row_to_topic(r) for r in rows]
    finally:
        conn.close()


def resolve_dev_topic(topic: str) -> bool:
    """Mark a topic resolved (done at the dev meeting); a later sighting raises it again. Never raises."""
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error("[FRIGATE_STORE] cannot open frigate.db: %s", e)
        return False
    try:
        cur = conn.execute(
            "UPDATE dev_topics SET status='resolved', resolved_ts=? WHERE topic=? AND status='open'",
            (time.time(), topic),
        )
        conn.commit()
        return cur.rowcount == 1
    except Exception as e:
        logger.error("[FRIGATE_STORE] resolve_dev_topic %s failed: %s", topic, e)
        return False
    finally:
        conn.close()


# ── episode frames on disk (s11) ──────────────────────────────────────────────────


def episode_frames_dir() -> Path:
    """`<state>/frigate_snapshots/episodes` — beside the DB's state folder, so a test that repoints DB_PATH never
    writes the live folder. Not touched by the old snapshot rotation (it only takes `*.jpg` in its own folder)."""
    return DB_PATH.parent.parent / "frigate_snapshots" / "episodes"


def _frame_files(episode_id: int) -> dict[int, Path]:
    d = episode_frames_dir() / str(int(episode_id))
    out: dict[int, Path] = {}
    if d.is_dir():
        for p in d.glob("*.jpg"):
            if p.stem.isdigit():
                out[int(p.stem)] = p
    return out


def stored_frame_indexes(episode_id: int) -> list[int]:
    try:
        return sorted(_frame_files(episode_id))
    except Exception:
        return []


def read_episode_frame(episode_id: int, index: int) -> bytes | None:
    """A stored frame, or None (never stored / trimmed / rotated away)."""
    try:
        path = _frame_files(episode_id).get(int(index))
        return _unseal_bytes(path.read_bytes()) if path else None
    except Exception as e:
        logger.warning("[FRIGATE_STORE] read frame %s/%s failed: %s", episode_id, index, e)
        return None


def _caps() -> tuple[float, float]:
    try:
        max_mb, keep_mb = settings.episode_frames_caps()
    except Exception:
        max_mb, keep_mb = settings.DEFAULTS["episode_frames_max_mb"], settings.DEFAULTS["episode_frames_keep_mb"]
    return max_mb * 1_048_576, keep_mb * 1_048_576


def _episode_dirs() -> list[tuple[int, dict[int, Path]]]:
    """Every episode folder, oldest (lowest id) first, with its frames."""
    root = episode_frames_dir()
    if not root.is_dir():
        return []
    ids = sorted(int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit())
    return [(i, _frame_files(i)) for i in ids]


def _split(files: dict[int, Path]) -> tuple[int | None, int, int]:
    """(representative index, bytes of the representative, bytes of the rest). Representative = the middle frame;
    once an episode is trimmed to it, it stays the middle of what is left."""
    if not files:
        return None, 0, 0
    idx = sorted(files)
    rep = idx[len(idx) // 2]
    sizes = {i: files[i].stat().st_size for i in idx}
    return rep, sizes[rep], sum(v for i, v in sizes.items() if i != rep)


def rotate_episode_frames() -> None:
    """Whole frame sets over `episode_frames_max_mb`: trim the oldest to their representative frame. Representatives
    over `episode_frames_keep_mb`: delete the oldest episodes' folders. Automatic by design (like the snapshot
    rotation). Never raises."""
    try:
        max_b, keep_b = _caps()
        dirs = [(i, f, *_split(f)) for i, f in _episode_dirs()]
        full = sum(rest for *_x, rest in dirs)
        for i, files, rep, _rep_b, rest in dirs:
            if full <= max_b:
                break
            if rest:
                for idx, path in files.items():
                    if idx != rep:
                        path.unlink(missing_ok=True)
                full -= rest
                logger.debug("[FRIGATE_STORE] episode %s frames trimmed to frame %s", i, rep)
        kept = sum(rep_b for *_x, rep_b, _rest in dirs)
        for i, _files, _rep, rep_b, _rest in dirs:
            if kept <= keep_b:
                break
            shutil.rmtree(episode_frames_dir() / str(i), ignore_errors=True)
            kept -= rep_b
            logger.debug("[FRIGATE_STORE] episode %s frames removed (keep cap)", i)
    except Exception as e:
        logger.warning("[FRIGATE_STORE] episode frame rotation failed: %s", e)


def save_episode_frames(episode_id: int, frames: list[bytes], files: list[int] | None = None) -> int:
    """Store the frames the scene step showed the model (index = position in the scene's `frames` list, or the given
    `files` numbers — P144-s24 backfill), then rotate. Returns how many were written (0 when the cap is 0). Never
    raises."""
    max_b, _keep_b = _caps()
    if max_b <= 0 or not frames:
        return 0
    written = 0
    try:
        d = episode_frames_dir() / str(int(episode_id))
        d.mkdir(parents=True, exist_ok=True)
        for i, data in zip(files if files is not None else range(len(frames)), frames, strict=True):
            if not data:
                continue
            tmp = d / f".{i}.jpg.tmp"
            tmp.write_bytes(_seal_bytes(data))
            os.replace(tmp, d / f"{i}.jpg")
            written += 1
    except Exception as e:
        logger.warning("[FRIGATE_STORE] save frames for episode %s failed: %s", episode_id, e)
    rotate_episode_frames()
    return written


def episode_frames_usage() -> dict:
    """For the storage-limits bars: MB of whole frame sets, MB of the kept frames, episode folders, and the caps."""
    full = keep = 0
    dirs = []
    try:
        dirs = _episode_dirs()
        for _i, files in dirs:
            _rep, rep_b, rest = _split(files)
            keep += rep_b
            full += rest
    except Exception as e:
        logger.warning("[FRIGATE_STORE] frame usage failed: %s", e)
    max_b, keep_b = _caps()
    return {"full_mb": round(full / 1_048_576, 1), "keep_mb": round(keep / 1_048_576, 1), "episodes": len(dirs),
            "max_mb": round(max_b / 1_048_576), "keep_cap_mb": round(keep_b / 1_048_576)}


# ── retention tiers + nightly rollup (s07) ────────────────────────────────────────
#
# By size (P150): frigate.db has a limit in MB (`frigate_db_max_mb`, Integrations → Frigate → Lagringsgrenser; 0 = off).
# Over it, the oldest rolled-up detail goes first — an episode's events and the episode itself are slimmed (who / when /
# where / conclusion stay), events outside an episode are deleted; once no detail is left, the oldest hourly counts.
# Daily counts, episodes and visits are the long memory and are kept. The generic runner is
# kaare_core.retention.engine; this adapter is the only code that touches frigate.db for it.
#
# Rollup per event: a settled event (older than RETENTION_SETTLE_S — its claim, episode and visit have settled) adds +1
# to its hourly and daily row and is marked `rolled` in the same transaction — idempotent, resumable after a crash,
# late events counted too. Trimming only ever touches rolled events (counts never forgotten). Grouping is done in
# Python on unsealed names, so the rollups keep working once _seal() encrypts (N19 point 3).

RETENTION_SETTLE_S = 86400
SLIM_CHECK_KEYS = ("decision", "storage_gate", "episode_rule", "doubt")
# Of the layer votes only what query.certainty() reads survives slimming (P144-s14, s08 finding 1): the word stays.
SLIM_VOTE_KEYS = {"l0": ("verdict",), "l3": ("verdict", "reason"), "sensors": ("relation", "lean")}  # + s19
SLIM_SCENE_KEYS = ("status", "frames")  # the frame list keeps the kept s11 frame reachable in the GUI


def slim_check(check: dict) -> dict:
    """The check a slimmed event keeps: decision/gate/rule/doubt + the L0/L3 verdicts the probability word needs."""
    check = check if isinstance(check, dict) else {}
    slim = {k: check[k] for k in SLIM_CHECK_KEYS if k in check}
    for layer, keys in SLIM_VOTE_KEYS.items():
        vote = check.get(layer)
        if isinstance(vote, dict):
            slim[layer] = {k: vote[k] for k in keys if k in vote}
    return slim


def _tz():
    try:
        return get_local_tz()
    except Exception:
        return None  # UTC


def _add_daily(conn: sqlite3.Connection, day: str, ref: str, kind: str, places: list[str], first: float | None,
               last: float | None, count: int, visits: int = 0) -> None:
    key = (day, _seal_ref(ref) if ref else "", kind)
    row = conn.execute(
        "SELECT places, first_ts, last_ts FROM daily WHERE day=? AND person_ref=? AND person_kind=?", key
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO daily (day, person_ref, person_kind, places, first_ts, last_ts, event_count, visit_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, json.dumps(sorted({p for p in places if p}), ensure_ascii=False), first, last, count, visits),
        )
        return
    merged = sorted({*json.loads(row["places"] or "[]"), *(p for p in places if p)})
    firsts = [v for v in (row["first_ts"], first) if v is not None]
    lasts = [v for v in (row["last_ts"], last) if v is not None]
    conn.execute(
        "UPDATE daily SET places=?, first_ts=?, last_ts=?, event_count=event_count+?, visit_count=visit_count+? "
        "WHERE day=? AND person_ref=? AND person_kind=?",
        (json.dumps(merged, ensure_ascii=False), min(firsts) if firsts else None, max(lasts) if lasts else None,
         count, visits, *key),
    )


def _recount_visits(conn: sqlite3.Connection, day: str, ref: str, kind: str) -> None:
    """visit_count = the person's visits that began that household day (visits are kept, so a recount is exact)."""
    if not ref:
        return
    start, end = engine.day_bounds(day, _tz())
    n = conn.execute(
        "SELECT COUNT(*) FROM visits WHERE person_ref=? AND first_ts >= ? AND first_ts < ?", (_seal_ref(ref), start, end)
    ).fetchone()[0]
    conn.execute("UPDATE daily SET visit_count=? WHERE day=? AND person_ref=? AND person_kind=?",
                 (n, day, _seal_ref(ref), kind))


def _roll_chunk(conn: sqlite3.Connection, limit: int, now: float) -> int:
    """Roll up to `limit` settled, unrolled events into hourly + daily and mark them rolled — one transaction."""
    tz = _tz()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT id, start_ts, end_ts, place, camera, person_ref, person_kind, label FROM events "
            "WHERE rolled=0 AND start_ts < ? ORDER BY start_ts LIMIT ?",
            (now - RETENTION_SETTLE_S, limit),
        ).fetchall()
        days: set[tuple[str, str, str]] = set()
        for r in rows:
            ref, place = _unseal(r["person_ref"]) or "", r["place"] or ""
            conn.execute(
                "INSERT INTO hourly (hour_ts, place, camera, person_ref, person_kind, label, event_count) "
                "VALUES (?, ?, ?, ?, ?, ?, 1) ON CONFLICT(hour_ts, place, camera, person_ref, person_kind, label) "
                "DO UPDATE SET event_count=event_count+1",
                (engine.hour_floor(r["start_ts"]), place, r["camera"], _seal_ref(ref) if ref else "", r["person_kind"],
                 r["label"]),
            )
            day = engine.local_day(r["start_ts"], tz)
            end = r["end_ts"] if r["end_ts"] is not None else r["start_ts"]
            _add_daily(conn, day, ref, r["person_kind"], [place or r["camera"]], r["start_ts"], end, 1)
            days.add((day, ref, r["person_kind"]))
        for day, ref, kind in days:
            _recount_visits(conn, day, ref, kind)
        if rows:
            ids = [r["id"] for r in rows]
            conn.execute(f"UPDATE events SET rolled=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise


def _with_conn(fn):
    conn = _get_conn()
    try:
        return fn(conn)
    finally:
        conn.close()


def _tx(fn):
    """Run fn(conn) in one short write transaction; returns its result."""

    def go(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            out = fn(conn)
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise

    return _with_conn(go)


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _expiring_names(conn: sqlite3.Connection, sql: str, args: tuple) -> list[str]:
    """Unclassified face names from `sql` (sealed person_ref in column 0) that are still unclassified — a face
    classified since keeps its name («name kept N days until classified», N19 point 6). Returned sealed."""
    out = []
    for (sealed,) in conn.execute(sql, args).fetchall():
        name = _unseal(sealed)
        if name and faces.classify(name) == faces.UNCLASSIFIED:
            out.append(sealed)
    return out


def _scrub_episode(conn: sqlite3.Connection, episode_id: int, names: set[str]) -> None:
    r = conn.execute("SELECT person_refs, scene, route, conclusion FROM episodes WHERE episode_id=?",
                     (episode_id,)).fetchone()
    if r is None:
        return
    refs = [n for n in json.loads(_unseal(r["person_refs"]) or "[]") if n not in names]
    scene = _scrub_obj(json.loads(_unseal(r["scene"])), names) if r["scene"] else None
    conclusion = _scrub_obj(json.loads(_unseal(r["conclusion"])), names) if r["conclusion"] else None
    conn.execute(
        "UPDATE episodes SET person_refs=?, scene=?, route=?, conclusion=? WHERE episode_id=?",
        (_seal(json.dumps(refs, ensure_ascii=False)),
         _seal(json.dumps(scene, ensure_ascii=False)) if scene is not None else None,
         _seal(_scrub_text(_unseal(r["route"]), names)),
         _seal(json.dumps(conclusion, ensure_ascii=False)) if conclusion is not None else None, episode_id),
    )


class FrigateRetention:
    """The Frigate memory's adapter for kaare_core.retention.engine (s07). Every step is one short transaction."""

    name = "frigate"

    def __init__(self, now: float | None = None, limit_mb: float | None = None):
        self.now = time.time() if now is None else now
        self.limit_mb = settings.retention()["db_max_mb"] if limit_mb is None else limit_mb

    # rollup
    def rollup(self, limit: int) -> int:
        return _with_conn(lambda conn: _roll_chunk(conn, limit, self.now))

    def record(self, report: engine.Report) -> None:
        _tx(lambda conn: conn.execute(
            "INSERT INTO retention_runs (ts, dry_run, report) VALUES (?, ?, ?)",
            (report.started_ts, int(report.dry_run), json.dumps(report.as_dict(), ensure_ascii=False))))

    # trim steps, in order
    def trim_steps(self, now: float) -> list[engine.TrimStep]:
        detail_cut, hourly_cut = self._size_cuts()
        try:
            name_cut = now - faces.unclassified_name_days() * 86400
        except Exception:
            name_cut = now - faces.DEFAULT_NAME_DAYS * 86400
        name_day = engine.local_day(name_cut, _tz())
        steps = [
            self._names_events(name_cut),
            self._names_visits(name_cut),
            self._names_hourly(name_cut),
            self._names_daily(name_day),
            self._slim_events(detail_cut),
            self._delete_events(detail_cut),
            self._slim_episodes(detail_cut, now),
            self._delete_rows("hourly.delete", "hourly", "hour_ts + 3600 <= ?", hourly_cut),
        ]
        return steps

    # P150: the cut is chosen by size, oldest first. Daily counts are never deleted.
    _DETAIL_BYTES = ("COALESCE(length(description),0)+COALESCE(length(analysis),0)+COALESCE(length(l3_raw),0)"
                     "+COALESCE(length(attributes),0)+COALESCE(length(path),0)+COALESCE(length(boxes),0)"
                     "+COALESCE(length(\"check\"),0)")

    def _size_cuts(self) -> tuple[float, float]:
        """(detail cut, hourly cut) as start-time cutoffs; -inf = nothing. Under the limit (or 0 = off): nothing.
        Over it: the oldest rolled-up events whose freed bytes cover the excess (estimated from their column lengths +
        a row overhead). Hourly rows only when no trimmable detail is left — a second stage, so counts outlive detail."""
        none = (float("-inf"), float("-inf"))
        if not self.limit_mb:
            return none
        excess = db_logical_bytes() - self.limit_mb * 1_048_576
        if excess <= 0:
            return none

        def plan(conn):
            rows = conn.execute(
                f"SELECT start_ts, {self._DETAIL_BYTES} + CASE WHEN episode_id IS NULL THEN {_ROW_OVERHEAD} ELSE 0 END "
                "AS b FROM events WHERE rolled=1 AND (tier='event' OR episode_id IS NULL) ORDER BY start_ts").fetchall()
            if rows:
                freed = 0
                for r in rows:
                    freed += r["b"]
                    if freed >= excess:
                        return r["start_ts"] + 1e-6, float("-inf")
                return rows[-1]["start_ts"] + 1e-6, float("-inf")
            oldest_full = conn.execute("SELECT MIN(start_ts) FROM events WHERE tier='event'").fetchone()[0]
            bound = oldest_full if oldest_full is not None else self.now
            hours = conn.execute("SELECT hour_ts FROM hourly WHERE hour_ts + 3600 <= ? ORDER BY hour_ts",
                                 (bound,)).fetchall()
            freed = 0
            for (hour_ts,) in hours:
                freed += _HOURLY_ROW_BYTES
                if freed >= excess:
                    return float("-inf"), hour_ts + 3600 + 1e-6
            return float("-inf"), (hours[-1][0] + 3600 + 1e-6) if hours else float("-inf")

        return _with_conn(plan)

    # names (N19 point 6: an unclassified face's name is kept `unclassified_name_days`)
    _EV_NAMES = "FROM events WHERE person_kind='unclassified' AND person_ref IS NOT NULL AND start_ts < ?"

    def _names_events(self, cutoff: float) -> engine.TrimStep:
        def names(conn):
            return _expiring_names(conn, f"SELECT DISTINCT person_ref {self._EV_NAMES}", (cutoff,))

        def count():
            def go(conn):
                ns = names(conn)
                return conn.execute(f"SELECT COUNT(*) {self._EV_NAMES} AND person_ref IN ({_marks(len(ns))})",
                                    (cutoff, *ns)).fetchone()[0] if ns else 0
            return _with_conn(go)

        def apply(limit):
            def go(conn):
                ns = names(conn)
                if not ns:
                    return 0
                rows = conn.execute(
                    f'SELECT id, person_ref, episode_id, description, analysis, l3_raw, attributes, "check" {self._EV_NAMES} '
                    f"AND person_ref IN ({_marks(len(ns))}) LIMIT ?", (cutoff, *ns, limit)).fetchall()
                episodes: dict[int, set[str]] = {}
                for r in rows:
                    gone = {_unseal(r["person_ref"])}
                    conn.execute(
                        'UPDATE events SET person_ref=NULL, description=?, analysis=?, l3_raw=?, attributes=?, "check"=? '
                        "WHERE id=?",
                        (_seal(_scrub_text(_unseal(r["description"]), gone)),
                         _seal(_scrub_text(_unseal(r["analysis"]), gone)),
                         _seal(_scrub_text(_unseal(r["l3_raw"]), gone)),
                         _seal(json.dumps(_scrub_obj(json.loads(_unseal(r["attributes"]) or "{}"), gone),
                                          ensure_ascii=False)),
                         _seal(json.dumps(_scrub_obj(json.loads(_unseal(r["check"]) or "{}"), gone),
                                          ensure_ascii=False)),
                         r["id"]))
                    if r["episode_id"] is not None:
                        episodes.setdefault(r["episode_id"], set()).update(gone)
                for episode_id, gone in episodes.items():
                    _scrub_episode(conn, episode_id, gone)
                return len(rows)
            return _tx(go)

        return engine.TrimStep("names.events", count, apply)

    def _names_visits(self, cutoff: float) -> engine.TrimStep:
        where = "FROM visits WHERE person_kind='unclassified' AND person_ref != '' AND last_ts < ?"

        def ids(conn, limit=-1):
            ns = _expiring_names(conn, f"SELECT DISTINCT person_ref {where}", (cutoff,))
            if not ns:
                return []
            return [r[0] for r in conn.execute(
                f"SELECT id {where} AND person_ref IN ({_marks(len(ns))}) LIMIT ?", (cutoff, *ns, limit))]

        def apply(limit):
            def go(conn):
                found = ids(conn, limit)
                if found:
                    conn.execute(f"UPDATE visits SET person_ref='' WHERE id IN ({_marks(len(found))})", found)
                return len(found)
            return _tx(go)

        return engine.TrimStep("names.visits", lambda: len(_with_conn(ids)), apply)

    def _merge_rollup_names(self, name: str, table: str, where: str, cutoff, merge) -> engine.TrimStep:
        sel = f"FROM {table} WHERE person_kind='unclassified' AND person_ref != '' AND {where}"

        def rows(conn, limit=-1):
            ns = _expiring_names(conn, f"SELECT DISTINCT person_ref {sel}", (cutoff,))
            if not ns:
                return []
            return conn.execute(f"SELECT rowid AS rid, * {sel} AND person_ref IN ({_marks(len(ns))}) LIMIT ?",
                                (cutoff, *ns, limit)).fetchall()

        def apply(limit):
            def go(conn):
                found = rows(conn, limit)
                for r in found:
                    conn.execute(f"DELETE FROM {table} WHERE rowid=?", (r["rid"],))
                    merge(conn, r)
                return len(found)
            return _tx(go)

        return engine.TrimStep(name, lambda: len(_with_conn(rows)), apply)

    def _names_hourly(self, cutoff: float) -> engine.TrimStep:
        def merge(conn, r):
            conn.execute(
                "INSERT INTO hourly (hour_ts, place, camera, person_ref, person_kind, label, event_count) "
                "VALUES (?, ?, ?, '', ?, ?, ?) ON CONFLICT(hour_ts, place, camera, person_ref, person_kind, label) "
                "DO UPDATE SET event_count=event_count+excluded.event_count",
                (r["hour_ts"], r["place"], r["camera"], r["person_kind"], r["label"], r["event_count"]))

        return self._merge_rollup_names("names.hourly", "hourly", "hour_ts + 3600 <= ?", cutoff, merge)

    def _names_daily(self, cutoff_day: str) -> engine.TrimStep:
        def merge(conn, r):
            _add_daily(conn, r["day"], "", r["person_kind"], json.loads(r["places"] or "[]"), r["first_ts"],
                       r["last_ts"], r["event_count"], r["visit_count"])

        return self._merge_rollup_names("names.daily", "daily", "day < ?", cutoff_day, merge)

    # tiers
    _SLIM_EVENTS = "FROM events WHERE rolled=1 AND episode_id IS NOT NULL AND tier='event' AND start_ts < ?"
    _OLD_EVENTS = "FROM events WHERE rolled=1 AND episode_id IS NULL AND start_ts < ?"

    def _slim_events(self, cutoff: float) -> engine.TrimStep:
        def apply(limit):
            def go(conn):
                rows = conn.execute(f'SELECT id, "check" {self._SLIM_EVENTS} LIMIT ?', (cutoff, limit)).fetchall()
                for r in rows:
                    check = json.loads(_unseal(r["check"]) or "{}")
                    slim = slim_check(check)
                    conn.execute(
                        "UPDATE events SET description=NULL, analysis=NULL, l3_raw=NULL, attributes='{}', path='[]', boxes='[]', "
                        "\"check\"=?, tier='slim' WHERE id=?",
                        (_seal(json.dumps(slim, ensure_ascii=False)), r["id"]))
                return len(rows)
            return _tx(go)

        return engine.TrimStep(
            "events.slim", lambda: _with_conn(lambda c: c.execute(f"SELECT COUNT(*) {self._SLIM_EVENTS}",
                                                                  (cutoff,)).fetchone()[0]), apply)

    def _delete_events(self, cutoff: float) -> engine.TrimStep:
        def apply(limit):
            return _tx(lambda c: c.execute(
                f"DELETE FROM events WHERE id IN (SELECT id {self._OLD_EVENTS} LIMIT ?)", (cutoff, limit)).rowcount)

        return engine.TrimStep(
            "events.delete", lambda: _with_conn(lambda c: c.execute(f"SELECT COUNT(*) {self._OLD_EVENTS}",
                                                                    (cutoff,)).fetchone()[0]), apply)

    def _slim_episodes(self, cutoff: float, now: float) -> engine.TrimStep:
        where = "FROM episodes WHERE status='closed' AND slimmed_ts IS NULL AND end_ts < ?"

        def apply(limit):
            def go(conn):
                rows = conn.execute(f"SELECT episode_id, scene {where} LIMIT ?", (cutoff, limit)).fetchall()
                for r in rows:
                    scene = json.loads(_unseal(r["scene"])) if r["scene"] else None
                    slim = {k: scene[k] for k in SLIM_SCENE_KEYS if k in scene} if isinstance(scene, dict) else None
                    conn.execute(
                        "UPDATE episodes SET scene=?, route=NULL, slimmed_ts=? WHERE episode_id=?",
                        (_seal(json.dumps(slim, ensure_ascii=False)) if slim else None, now, r["episode_id"]))
                return len(rows)
            return _tx(go)

        return engine.TrimStep(
            "episodes.slim",
            lambda: _with_conn(lambda c: c.execute(f"SELECT COUNT(*) {where}", (cutoff,)).fetchone()[0]), apply)

    def _delete_rows(self, name: str, table: str, where: str, cutoff) -> engine.TrimStep:
        def apply(limit):
            return _tx(lambda c: c.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT ?)",
                (cutoff, limit)).rowcount)

        return engine.TrimStep(
            name, lambda: _with_conn(lambda c: c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}",
                                                         (cutoff,)).fetchone()[0]), apply)


def run_retention(now: float | None = None, dry_run: bool | None = None, chunk: int = 500) -> dict:
    """The nightly retention job (s07, P150): roll up, then trim by the GUI's size limit — or, in a dry run (GUI setting
    `retention_dry_run`), only count what it would trim. Returns the report as a dict. Never raises."""
    try:
        r = settings.retention()
        dry = r["dry_run"] if dry_run is None else dry_run
        adapter = FrigateRetention(now, r["db_max_mb"])
    except Exception as e:
        logger.error("[FRIGATE_STORE] retention not started: %s", e)
        return {"adapter": "frigate", "errors": {"start": str(e)}, "steps": {}, "rolled": 0, "dry_run": True}
    return engine.run(adapter, dry_run=dry, now=adapter.now, chunk=chunk).as_dict()


_ROW_OVERHEAD = 200  # bytes a deleted event row frees beyond its text columns (ids, numbers, index entries) — estimate
_HOURLY_ROW_BYTES = 64  # one hourly row with its index entries — estimate


def db_logical_bytes() -> int:
    """The used part of frigate.db (pages minus free pages) — what a size limit measures. A delete moves pages to the
    free list, so this falls at once while the file keeps its size for reuse. 0 when there is no store. Never raises."""
    if not Path(DB_PATH).exists():
        return 0
    try:
        def go(conn):
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            size = conn.execute("PRAGMA page_size").fetchone()[0]
            return max(0, (pages - free) * size)
        return int(_with_conn(go))
    except Exception as e:
        logger.warning("[FRIGATE_STORE] cannot measure frigate.db: %s", e)
        return 0


def detail_since() -> float | None:
    """Start of the oldest event still in full detail — what Kåre may say about older events. None when none."""
    try:
        return _with_conn(lambda c: c.execute("SELECT MIN(start_ts) FROM events WHERE tier='event'").fetchone()[0])
    except Exception:
        return None


def retention_runs(limit: int = 10) -> list[dict]:
    """The latest retention reports, newest first."""
    rows = _with_conn(lambda c: c.execute(
        "SELECT ts, dry_run, report FROM retention_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall())
    return [{"ts": r["ts"], "dry_run": bool(r["dry_run"]), **json.loads(r["report"])} for r in rows]


# ── J18: learned spots («Hva er her?») ─────────────────────────────────────────────────────────────────────────────

SPOT_STATUSES = ("named", "not_a_place", "skip")
SPOT_NAME_MAX = 60


def add_spot_answer(camera: str, cell: str, status: str, name: str | None, by_user: str | None) -> bool:
    """One answer row (append-only). `name` only with status «named». False on bad input or a failed write."""
    if status not in SPOT_STATUSES or not camera or not re.fullmatch(r"\d,\d", cell or ""):
        return False
    name = (name or "").strip()[:SPOT_NAME_MAX] or None
    if status == "named" and not name:
        return False
    try:
        _tx(lambda c: c.execute("INSERT INTO spot_answers (camera, cell, status, name, by_user, ts) VALUES (?, ?, ?, ?, ?, ?)",
                                (camera, cell, status, _seal(name if status == "named" else None), _seal(by_user),
                                 time.time())))
        return True
    except Exception as e:
        _write_failed("add_spot_answer", e)
        return False


def spot_answers() -> dict[tuple[str, str], dict]:
    """(camera, cell) → the newest answer {status, name, by_user, ts}."""
    rows = _with_conn(lambda c: c.execute(
        "SELECT camera, cell, status, name, by_user, ts FROM spot_answers ORDER BY id").fetchall())
    return {(r["camera"], r["cell"]): {"status": r["status"], "name": _unseal(r["name"]), "by_user": _unseal(r["by_user"]),
                                       "ts": r["ts"]} for r in rows}


# ── J20: «Hva er dette?» answers ─────────────────────────────────────────────────────────────────────────────────

THING_STATUSES = ("named", "not_label", "wrong", "skip")


def add_thing_answer(key: str, status: str, name: str | None, by_user: str | None) -> bool:
    """One answer row (append-only). False on bad input or a failed write."""
    if status not in THING_STATUSES or not key or key.count("|") != 2:
        return False
    try:
        _tx(lambda c: c.execute("INSERT INTO thing_answers (key, status, name, by_user, ts) VALUES (?, ?, ?, ?, ?)",
                                (key, status, _seal(name), _seal(by_user), time.time())))
        return True
    except Exception as e:
        _write_failed("add_thing_answer", e)
        return False


def thing_answers() -> dict[str, dict]:
    """key → the newest answer {status, name, by_user, ts} (in answer order)."""
    rows = _with_conn(lambda c: c.execute("SELECT key, status, name, by_user, ts FROM thing_answers ORDER BY id").fetchall())
    out: dict[str, dict] = {}
    for r in rows:
        out.pop(r["key"], None)  # the newest answer last in the order
        out[r["key"]] = {"status": r["status"], "name": _unseal(r["name"]), "by_user": _unseal(r["by_user"]), "ts": r["ts"]}
    return out


# ── J21 5d: «Siste deteksjoner» from frigate.db (module 1's still-image results) ──────────────────────────────────────

READS_SCAN = 400  # episodes / events looked at per page at most


def recent_model_reads(before: float | None = None, limit: int = 40) -> tuple[list[dict], bool]:
    """P144 6e «Siste deteksjoner»: every single-image read the image model already made, newest first — no new model call.
    Two kinds: «l3» = the per-event look (its own words: `description` + `analysis`, kept as the event's detail tier), «frame» =
    one frame of module 4's frame-by-frame run (`scene.frames_state.rows`: the model's own fields per person — place,
    posture, carried, expression, gesture; not `changes`, which mixes in code's facts). Each row: kind, ts, camera, episode_id, event_id
    (l3) or frame + index (frame; index = the frame endpoint's), words. Read-only."""
    limit = max(1, min(int(limit), 200))
    cut = before if before is not None else 1e18
    out: list[dict] = []

    def go(conn):
        ev = conn.execute("SELECT * FROM events WHERE start_ts < ? AND (description IS NOT NULL OR analysis IS NOT NULL "
                          "OR l3_raw IS NOT NULL) "
                          "ORDER BY start_ts DESC LIMIT ?", (cut, READS_SCAN)).fetchall()
        eps = conn.execute("SELECT * FROM episodes WHERE start_ts < ? AND scene IS NOT NULL ORDER BY start_ts DESC LIMIT ?",
                           (cut, READS_SCAN)).fetchall()
        return ev, eps

    ev_rows, ep_rows = _with_conn(go)
    for r in ev_rows:
        e = _row_to_event(r)
        words = {k: v for k, v in (("description", e.description), ("analysis", e.analysis)) if v}
        if words or e.l3_raw:
            out.append({"kind": "l3", "ts": e.start_ts, "camera": e.camera, "label": e.label, "event_id": e.event_id,
                        "episode_id": e.episode_id, "words": words, "raw": e.l3_raw or ""})
    for r in ep_rows:
        try:
            scene = json.loads(_unseal(r["scene"]) or "{}")
        except Exception:
            continue
        rows = ((scene.get("frames_state") or {}).get("rows") or []) if isinstance(scene, dict) else []
        for fr in rows:
            if not isinstance(fr, dict) or not isinstance(fr.get("ts"), (int, float)) or fr["ts"] >= cut:
                continue
            words = {k: fr[k] for k in ("persons", "looks") if fr.get(k)}  # the model's own fields; `changes` mixes in code facts
            n = int(fr.get("frame") or 0)
            out.append({"kind": "frame", "ts": float(fr["ts"]), "camera": fr.get("camera") or "", "label": "",
                        "episode_id": r["episode_id"], "frame": n, "index": n - 1, "words": words,
                        "raw": str(fr.get("raw") or "")})
    out.sort(key=lambda x: -x["ts"])
    return out[:limit], len(out) > limit
