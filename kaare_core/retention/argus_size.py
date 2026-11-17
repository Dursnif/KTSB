"""Argus kept by size, not by age (P150 interim, 2026-11-13).

The `argus_events` collection has one size limit in MB, set in the GUI (Settings → Logs/Storage; settings.yaml
`storage_limits.argus_events_mb`, 0 = off). Nothing is deleted because it is old. When the collection is over the
limit, the oldest household day goes first, one whole day at a time, and only after that day's rollup is written and
verified:

  measure → over the limit? → oldest day: read its points → write the rollup (one transaction) → verify the rollup
  counts every point read and Qdrant still holds exactly those → delete those ids → mark the day deleted → receipt.

The rollup lives in `state/argus/rollup.db` and is never deleted (D2):
  - `argus_daily`   one row per day × subsystem × source × entity × event type × level: n, first and last ts;
  - `argus_notable` warnings, errors and critical lines verbatim (message capped);
  - `argus_days`    one row per rolled day: points, when it was rolled and deleted, the job's rid;
  - `argus_runs`    the report of every run (for the GUI).
Re-rolling a day (a crash between rollup and delete) replaces that day's rows — counts are never doubled.

Size comes from Qdrant's telemetry (vector + payload bytes of live points — it does not swell while deleted points wait
for the optimizer, so a run never deletes more because of its own last deletes); fallback: points × an estimate.
Today is never deleted. At most `max_days` days per run keeps the night's IO bounded (rule 10).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import DatetimeRange, FieldCondition, Filter, OrderBy, PointIdsList

from kaare_core import config, config_store, receipts
from kaare_core.retention.engine import day_bounds, local_day
from kaare_core.storage import sqlite_files

log = logging.getLogger(__name__)

COLLECTION = "argus_events"
ARGUS_DEFAULT_MB = 2048  # the light default for a stranger (rule 24); the house sets its own in the GUI
ARGUS_MAX_MB = 10_000_000  # 10 TB — a sanity bound on what the GUI accepts, not a recommendation
ROLLUP_DB = Path("/kaare/state/argus/rollup.db")  # read per call (tests redirect it)
NOTABLE_LEVELS = ("warning", "error", "critical")
NOTABLE_MAX_CHARS = 2000
FALLBACK_POINT_BYTES = 4096 + 512  # 1024-dim float32 vector + a typical payload
SCROLL_PAGE = 1000
DELETE_BATCH = 1000
MAX_DAYS_PER_RUN = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS argus_daily (
    day TEXT NOT NULL, subsystem TEXT NOT NULL, source TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL, level TEXT NOT NULL, n INTEGER NOT NULL, first_ts TEXT, last_ts TEXT,
    PRIMARY KEY (day, subsystem, source, entity_id, event_type, level)
);
CREATE TABLE IF NOT EXISTS argus_notable (
    point_id TEXT PRIMARY KEY, day TEXT NOT NULL, ts TEXT, subsystem TEXT, source TEXT, entity_id TEXT,
    level TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS argus_days (
    day TEXT PRIMARY KEY, points INTEGER NOT NULL, rolled_at TEXT NOT NULL, deleted_at TEXT, rid TEXT
);
CREATE TABLE IF NOT EXISTS argus_runs (ts TEXT NOT NULL, report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS argus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_KEYS = ("subsystem", "source", "entity_id", "event_type", "level")


# ── the GUI-written limit ─────────────────────────────────────────────────────

def get_limit_mb() -> int:
    """settings.yaml storage_limits.argus_events_mb (GUI: Settings → Logs/Storage). 0 = off; missing or invalid →
    ARGUS_DEFAULT_MB. Read per call — a GUI change is picked up by the next night run. Never raises."""
    try:
        data = yaml.safe_load((config.CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")) or {}
        raw = (data.get("storage_limits") or {}).get("argus_events_mb", ARGUS_DEFAULT_MB)
        if isinstance(raw, bool):
            return ARGUS_DEFAULT_MB
        val = int(raw)
        return val if 0 <= val <= ARGUS_MAX_MB else ARGUS_DEFAULT_MB
    except Exception:
        return ARGUS_DEFAULT_MB


def limit_confirmed() -> bool:
    """True once the user saved the Argus limit in the GUI (settings.yaml storage_limits.argus_events_confirmed)."""
    try:
        data = yaml.safe_load((config.CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")) or {}
        return (data.get("storage_limits") or {}).get("argus_events_confirmed") is True
    except Exception:
        return False


def save_limit_mb(mb: int) -> None:
    """Write the limit as the user's own choice (GUI): it then applies as written (no upgrade floor)."""
    if isinstance(mb, bool) or not 0 <= int(mb) <= ARGUS_MAX_MB:
        raise ValueError(f"argus_events_mb must be 0 (off) to {ARGUS_MAX_MB}")

    def _apply(data):
        limits = data.get("storage_limits")
        data["storage_limits"] = {**(limits if isinstance(limits, dict) else {}), "argus_events_mb": int(mb),
                                  "argus_events_confirmed": True}

    config_store.update_yaml(config.CONFIG_DIR / "settings.yaml", _apply, "gui:argus_limit")


def effective_limit_mb(limit_mb: float, *, size_bytes: int, confirmed: bool, db_path: Path | None = None) -> float:
    """Upgrade guard: until the user saves a limit, a default never cuts below what Argus held the first time a limit met
    it (stored once in argus_meta.floor_bytes). Keeps an existing install's history on upgrade; it still stops growing
    past that floor. 0 (off) stays off."""
    if not limit_mb or confirmed:
        return limit_mb
    db_path = Path(db_path or ROLLUP_DB)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("INSERT OR IGNORE INTO argus_meta VALUES ('floor_bytes', ?)", (str(int(size_bytes)),))
        floor = int(conn.execute("SELECT value FROM argus_meta WHERE key='floor_bytes'").fetchone()[0])
    finally:
        conn.close()
    return max(float(limit_mb), floor / 1_048_576)


# ── measuring ─────────────────────────────────────────────────────────────────

def measure() -> dict:
    """{bytes, points, source} of the live collection — telemetry first, points × estimate as fallback. Never raises
    (bytes/points 0 and source «unavailable» when Qdrant cannot be reached)."""
    url = config.get_service("storage", "qdrant")
    key = config.get_qdrant_api_key(write=False)
    headers = {"api-key": key} if key else {}
    try:
        r = httpx.get(f"{url}/telemetry", params={"details_level": 3}, headers=headers, timeout=30)
        r.raise_for_status()
        cols = (((r.json().get("result") or {}).get("collections") or {}).get("collections")) or []
        for c in cols:
            if c.get("id") != COLLECTION:
                continue
            size = points = 0
            for shard in c.get("shards") or []:
                local = shard.get("local") or {}
                size += int(local.get("vectors_size_bytes") or 0) + int(local.get("payloads_size_bytes") or 0)
                points += int(local.get("num_points") or 0)
            if points:
                return {"bytes": size, "points": points, "source": "telemetry"}
    except Exception as e:
        log.info("[ARGUS_SIZE] telemetry unavailable (%s) — estimating from the point count", e)
    try:
        r = httpx.get(f"{url}/collections/{COLLECTION}", headers=headers, timeout=30)
        r.raise_for_status()
        points = int((r.json().get("result") or {}).get("points_count") or 0)
        return {"bytes": points * FALLBACK_POINT_BYTES, "points": points, "source": "estimate"}
    except Exception as e:
        log.warning("[ARGUS_SIZE] cannot measure %s: %s", COLLECTION, e)
        return {"bytes": 0, "points": 0, "source": "unavailable"}


# ── rollup ────────────────────────────────────────────────────────────────────

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_files.connect(db_path, timeout=30)
    conn.executescript(_SCHEMA)
    return conn


def _day_filter(day: str, tz) -> Filter:
    start, end = day_bounds(day, tz)
    return Filter(must=[FieldCondition(key="ts", range=DatetimeRange(
        gte=datetime.fromtimestamp(start, UTC), lt=datetime.fromtimestamp(end, UTC)))])


def _read_day(qc, day: str, tz) -> list:
    flt = _day_filter(day, tz)
    out, offset = [], None
    while True:
        recs, offset = qc.scroll(collection_name=COLLECTION, scroll_filter=flt, limit=SCROLL_PAGE, offset=offset,
                                 with_payload=["ts", "message", *_KEYS], with_vectors=False)
        out.extend(recs)
        if offset is None:
            return out


def rollup_day(qc, day: str, *, tz, db_path: Path | None = None, rid: str = "") -> list:
    """Write and verify the rollup of one household day; return the ids it covers (what may now be deleted).
    Raises on any failure — the caller then deletes nothing."""
    db_path = Path(db_path or ROLLUP_DB)
    recs = _read_day(qc, day, tz)
    agg: dict[tuple, list] = {}
    notable = []
    for r in recs:
        p = r.payload or {}
        key = tuple(str(p.get(k) or "") for k in _KEYS)
        ts = str(p.get("ts") or "")
        a = agg.setdefault(key, [0, ts, ts])
        a[0] += 1
        a[1] = min(a[1], ts) if a[1] else ts
        a[2] = max(a[2], ts)
        if str(p.get("level") or "").lower() in NOTABLE_LEVELS:
            notable.append((str(r.id), day, ts, *key[:3], str(p.get("level") or ""),
                            str(p.get("message") or "")[:NOTABLE_MAX_CHARS]))
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM argus_daily WHERE day=?", (day,))
            conn.executemany("INSERT INTO argus_daily VALUES (?,?,?,?,?,?,?,?,?)",
                             [(day, *k, a[0], a[1], a[2]) for k, a in agg.items()])
            conn.executemany("INSERT OR REPLACE INTO argus_notable VALUES (?,?,?,?,?,?,?,?)", notable)
            conn.execute("INSERT OR REPLACE INTO argus_days (day, points, rolled_at, deleted_at, rid) "
                         "VALUES (?,?,?,NULL,?)", (day, len(recs), datetime.now(UTC).isoformat(), rid))
        written = conn.execute("SELECT COALESCE(SUM(n),0) FROM argus_daily WHERE day=?", (day,)).fetchone()[0]
    finally:
        conn.close()
    still = qc.count(collection_name=COLLECTION, count_filter=_day_filter(day, tz), exact=True).count
    if written != len(recs) or still != len(recs):
        raise RuntimeError(f"rollup of {day} not verified: read {len(recs)}, rolled {written}, in Qdrant {still}")
    return [r.id for r in recs]


# ── the run ───────────────────────────────────────────────────────────────────

def _oldest_day(qc, tz, after: float | None) -> str | None:
    flt = None
    if after is not None:
        flt = Filter(must=[FieldCondition(key="ts", range=DatetimeRange(gte=datetime.fromtimestamp(after, UTC)))])
    recs, _ = qc.scroll(collection_name=COLLECTION, scroll_filter=flt, limit=1, with_payload=["ts"],
                        with_vectors=False, order_by=OrderBy(key="ts", direction="asc"))
    if not recs:
        return None
    ts = datetime.fromisoformat(str(recs[0].payload["ts"]).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return local_day(ts.timestamp(), tz)


def _record(db_path: Path, rep: dict) -> None:
    try:
        conn = _connect(db_path)
        try:
            with conn:
                conn.execute("INSERT INTO argus_runs VALUES (?,?)", (datetime.now(UTC).isoformat(), json.dumps(rep)))
        finally:
            conn.close()
    except Exception as e:
        log.warning("[ARGUS_SIZE] could not record the run: %s", e)


def run(qc, *, limit_mb: float, size: tuple[int, int], tz, now: float | None = None, dry_run: bool = False,
        db_path: Path | None = None, rid: str = "", max_days: int = MAX_DAYS_PER_RUN) -> dict:
    """Bring the collection under `limit_mb`, oldest day first, rollup before delete. Never raises — problems land in
    report["errors"] and stop the run (nothing more is deleted that night)."""
    now = time.time() if now is None else now
    db_path = Path(db_path or ROLLUP_DB)
    size_bytes, points = size
    rep = {"limit_mb": limit_mb, "size_bytes": size_bytes, "points": points, "dry_run": dry_run, "deleted": 0,
           "days": [], "planned": [], "errors": [], "status": "under_limit"}
    if not limit_mb:
        rep["status"] = "off"
        return rep
    if points <= 0 or size_bytes <= 0:
        rep["status"] = "empty_or_unmeasured"
        return rep
    per_point = size_bytes / points
    excess = points - int(limit_mb * 1_048_576 / per_point)
    if excess <= 0:
        return rep
    today = local_day(now, tz)
    cursor = None
    try:
        while excess > 0:
            if len(rep["days"]) + len(rep["planned"]) >= max_days:
                rep["status"] = "max_days_reached"
                break
            day = _oldest_day(qc, tz, cursor)
            if day is None or day >= today:
                rep["status"] = "only_today_left"
                break
            cursor = day_bounds(day, tz)[1]
            if dry_run:
                n = qc.count(collection_name=COLLECTION, count_filter=_day_filter(day, tz), exact=True).count
                rep["planned"].append([day, n])
                excess -= n
                rep["status"] = "dry_run"
                continue
            ids = rollup_day(qc, day, tz=tz, db_path=db_path, rid=rid)
            for i in range(0, len(ids), DELETE_BATCH):
                qc.delete(collection_name=COLLECTION, points_selector=PointIdsList(points=ids[i:i + DELETE_BATCH]))
            conn = _connect(db_path)
            try:
                with conn:
                    conn.execute("UPDATE argus_days SET deleted_at=? WHERE day=?", (datetime.now(UTC).isoformat(), day))
            finally:
                conn.close()
            receipts.log_saved("argus_rollup", where="state/argus/rollup.db", refs=[day], n=len(ids), rid=rid)
            rep["days"].append([day, len(ids)])
            rep["deleted"] += len(ids)
            excess -= len(ids)
            rep["status"] = "trimmed"
    except Exception as e:
        rep["errors"].append(str(e))
        rep["status"] = "error"
        log.error("[ARGUS_SIZE] stopped, nothing more deleted this run: %s", e)
    if not dry_run:
        _record(db_path, rep)
    return rep


def run_nightly(rid: str = "", dry_run: bool = False) -> dict:
    """The night job's entry point (sync — call it with asyncio.to_thread). Never raises."""
    try:
        limit = get_limit_mb()
        if not limit:
            log.info("[ARGUS_SIZE] limit off (0) — nothing deleted")
            return {"status": "off"}
        m = measure()
        if m["source"] == "unavailable":
            return {"status": "unmeasured"}
        qc = QdrantClient(url=config.get_service("storage", "qdrant"), api_key=config.get_qdrant_api_key(write=True))
        eff = effective_limit_mb(limit, size_bytes=m["bytes"], confirmed=limit_confirmed())
        rep = run(qc, limit_mb=eff, size=(m["bytes"], m["points"]), tz=config.get_local_tz(), dry_run=dry_run,
                  rid=rid)
        rep["configured_limit_mb"] = limit
        log.info("[ARGUS_SIZE] %s: %.0f of %.0f MB, %d points — deleted %d (days %s)%s", rep["status"],
                 m["bytes"] / 1_048_576, eff, m["points"], rep["deleted"], rep["days"] or rep["planned"],
                 f" errors: {rep['errors']}" if rep["errors"] else "")
        return rep
    except Exception as e:
        log.warning("[ARGUS_SIZE] run failed, nothing deleted: %s", e)
        return {"status": "error", "errors": [str(e)]}


def usage() -> dict:
    """For the GUI: limit, default, live size and the last run. Never raises."""
    m = measure()
    last = None
    try:
        if Path(ROLLUP_DB).exists():
            conn = sqlite_files.connect(f"file:{ROLLUP_DB}?mode=ro", uri=True, timeout=5)
            try:
                row = conn.execute("SELECT ts, report FROM argus_runs ORDER BY ts DESC LIMIT 1").fetchone()
                days = conn.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM argus_days "
                                    "WHERE deleted_at IS NOT NULL").fetchone()
            finally:
                conn.close()
            if row:
                r = json.loads(row[1])
                last = {"ts": row[0], "status": r.get("status"), "deleted": r.get("deleted", 0)}
            if days and days[0]:
                last = {**(last or {}), "rolled_days": days[0], "first_day": days[1], "last_day": days[2]}
    except Exception as e:
        log.info("[ARGUS_SIZE] no rollup info: %s", e)
    floor_mb = None
    try:
        if Path(ROLLUP_DB).exists():
            conn = sqlite_files.connect(f"file:{ROLLUP_DB}?mode=ro", uri=True, timeout=5)
            try:
                row = conn.execute("SELECT value FROM argus_meta WHERE key='floor_bytes'").fetchone()
            finally:
                conn.close()
            floor_mb = round(int(row[0]) / 1_048_576, 1) if row else None
    except Exception:
        floor_mb = None
    confirmed = limit_confirmed()
    return {"limit_mb": get_limit_mb(), "default_mb": ARGUS_DEFAULT_MB, "max_mb": ARGUS_MAX_MB,
            "confirmed": confirmed, "floor_mb": None if confirmed else floor_mb,
            "used_mb": round(m["bytes"] / 1_048_576, 1), "points": m["points"], "measured_by": m["source"],
            "last_run": last}

