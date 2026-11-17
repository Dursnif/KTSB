"""P150 Argus interim (2026-11-13, two yes): Argus is kept by SIZE, not by age.

- No point is deleted because it is old; only when the collection is over the GUI limit, and then the oldest day first.
- Before a day is deleted, its rollup (counts per day × subsystem × source × entity × event type × level, warnings and
  errors verbatim) is written and verified — nothing is deleted without it (plan invariant).
- 0 = off. Dry run changes nothing. Today is never deleted.
Every test runs against an in-memory fake Qdrant and a tmp rollup.db — never the house.
"""

import asyncio
import inspect
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from fastapi import HTTPException

import kaare_nightjob
from kaare_core import config
from kaare_core.retention import argus_size
from kaare_core.routers import router_settings as rs

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
POINT_BYTES = 4608  # what the fake reports per point


class FakeQdrant:
    """Just enough of QdrantClient for argus_size: scroll (filter + order_by ts), count, delete by ids."""

    def __init__(self, points):
        self.points = {p["id"]: p for p in points}
        self.deleted: list = []

    @staticmethod
    def _ts(p):
        return datetime.fromisoformat(p["payload"]["ts"])

    def _match(self, p, flt):
        if flt is None:
            return True
        for cond in flt.must or []:
            r = cond.range
            ts = self._ts(p)
            if r.gte is not None and ts < r.gte:
                return False
            if r.lt is not None and ts >= r.lt:
                return False
        return True

    def _selected(self, flt):
        return [p for p in self.points.values() if self._match(p, flt)]

    def count(self, collection_name, count_filter=None, exact=True):
        assert collection_name == argus_size.COLLECTION
        return type("C", (), {"count": len(self._selected(count_filter))})()

    def scroll(self, collection_name, scroll_filter=None, limit=10, offset=None, with_payload=True,
               with_vectors=False, order_by=None):
        assert collection_name == argus_size.COLLECTION
        assert not with_vectors
        rows = sorted(self._selected(scroll_filter), key=lambda p: (self._ts(p), str(p["id"])))
        start = int(offset or 0)
        page = rows[start:start + limit]
        nxt = None if order_by is not None or start + limit >= len(rows) else start + limit
        recs = [type("R", (), {"id": p["id"], "payload": dict(p["payload"])})() for p in page]
        return recs, nxt

    def delete(self, collection_name, points_selector):
        assert collection_name == argus_size.COLLECTION
        for pid in points_selector.points:
            self.points.pop(pid, None)
            self.deleted.append(pid)

    def size(self):
        return len(self.points) * POINT_BYTES, len(self.points)


def _points(days_ago_counts: dict[int, int], level_every: int = 0):
    out, n = [], 0
    for days_ago, count in days_ago_counts.items():
        base = NOW - timedelta(days=days_ago)
        for i in range(count):
            n += 1
            level = "warning" if level_every and i % level_every == 0 else "info"
            out.append({"id": n, "payload": {
                "ts": (base + timedelta(seconds=i)).isoformat(), "subsystem": "home", "source": "ha-events",
                "entity_id": f"sensor.s{i % 3}", "event_type": "state_changed", "level": level,
                "message": f"msg {days_ago}/{i}"}})
    return out


def _run(fake, limit_mb, tmp_path, **kw):
    return argus_size.run(fake, limit_mb=limit_mb, size=fake.size(), tz=UTC, now=NOW.timestamp(),
                          db_path=tmp_path / "rollup.db", rid="test-rid", **kw)


def _mb(points):
    return points * POINT_BYTES / 1_048_576


# ── the bug Userone decided away: age alone deleted points ──────────────────────

def test_old_points_under_the_limit_are_kept(tmp_path):
    fake = FakeQdrant(_points({120: 50, 95: 50, 10: 50}))  # older than the old 90-day cut, but tiny
    rep = _run(fake, 2048, tmp_path)
    assert fake.deleted == []
    assert rep["deleted"] == 0
    assert not (tmp_path / "rollup.db").exists() or _rows(tmp_path, "argus_daily") == []


def test_nightjob_has_no_age_based_argus_delete():
    src = inspect.getsource(kaare_nightjob)
    assert "timedelta(days=90)" not in src, "the hardcoded 90-day Argus delete must be gone"
    assert "argus_size.run_nightly" in src, "the night job calls the size-based Argus limit"


# ── size limit, oldest day first, rollup before delete ────────────────────────

def _rows(tmp_path, table):
    with sqlite3.connect(tmp_path / "rollup.db") as c:
        return c.execute(f"SELECT * FROM {table}").fetchall()


def test_over_the_limit_deletes_oldest_days_first_after_rollup(tmp_path):
    fake = FakeQdrant(_points({30: 300, 20: 300, 10: 300, 1: 300}, level_every=100))
    limit_mb = _mb(650)  # 1200 points → must lose ≥ 550 → the two oldest days (600)
    rep = _run(fake, limit_mb, tmp_path)
    assert rep["deleted"] == 600
    assert len(fake.points) == 600
    left_days = {datetime.fromisoformat(p["payload"]["ts"]).date() for p in fake.points.values()}
    assert left_days == {(NOW - timedelta(days=10)).date(), (NOW - timedelta(days=1)).date()}
    with sqlite3.connect(tmp_path / "rollup.db") as c:
        per_day = dict(c.execute("SELECT day, SUM(n) FROM argus_daily GROUP BY day").fetchall())
        notable = c.execute("SELECT level, message FROM argus_notable ORDER BY ts").fetchall()
        days = c.execute("SELECT day, points, deleted_at IS NOT NULL FROM argus_days ORDER BY day").fetchall()
    assert per_day == {(NOW - timedelta(days=30)).date().isoformat(): 300,
                       (NOW - timedelta(days=20)).date().isoformat(): 300}
    assert notable == [("warning", "msg 30/0"), ("warning", "msg 30/100"), ("warning", "msg 30/200"),
                       ("warning", "msg 20/0"), ("warning", "msg 20/100"), ("warning", "msg 20/200")]
    assert [d[1:] for d in days] == [(300, 1), (300, 1)]


def test_rollup_counts_per_entity(tmp_path):
    fake = FakeQdrant(_points({30: 9, 1: 9}))
    _run(fake, _mb(10), tmp_path)
    with sqlite3.connect(tmp_path / "rollup.db") as c:
        rows = c.execute("SELECT entity_id, n FROM argus_daily ORDER BY entity_id").fetchall()
    assert rows == [("sensor.s0", 3), ("sensor.s1", 3), ("sensor.s2", 3)]


def test_limit_zero_means_off(tmp_path):
    fake = FakeQdrant(_points({30: 300, 1: 300}))
    rep = _run(fake, 0, tmp_path)
    assert fake.deleted == [] and rep["status"] == "off"


def test_dry_run_changes_nothing_and_lists_the_days(tmp_path):
    fake = FakeQdrant(_points({30: 300, 20: 300, 1: 300}))
    rep = _run(fake, _mb(400), tmp_path, dry_run=True)
    assert fake.deleted == []
    assert not (tmp_path / "rollup.db").exists()
    assert rep["planned"] == [[(NOW - timedelta(days=30)).date().isoformat(), 300],
                              [(NOW - timedelta(days=20)).date().isoformat(), 300]]


def test_today_is_never_deleted(tmp_path):
    fake = FakeQdrant(_points({0: 500}))
    rep = _run(fake, _mb(10), tmp_path)
    assert fake.deleted == []
    assert rep["status"] == "only_today_left"


def test_nothing_is_deleted_when_the_rollup_cannot_be_written(tmp_path):
    fake = FakeQdrant(_points({30: 300, 1: 300}))
    blocker = tmp_path / "rollup.db"
    blocker.mkdir()  # a folder where the db file should be → sqlite cannot open it
    rep = argus_size.run(fake, limit_mb=_mb(300), size=fake.size(), tz=UTC, now=NOW.timestamp(),
                         db_path=blocker, rid="r")
    assert fake.deleted == []
    assert rep["errors"]


def test_rollup_is_not_doubled_when_a_day_is_rolled_twice(tmp_path):
    """A crash after the rollup but before the delete: the next night rolls the same day again."""
    pts = _points({30: 300, 1: 300})
    fake = FakeQdrant(pts)
    db = tmp_path / "rollup.db"
    day = (NOW - timedelta(days=30)).date().isoformat()
    for _ in range(2):
        argus_size.rollup_day(fake, day, tz=UTC, db_path=db, rid="r")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT SUM(n) FROM argus_daily WHERE day=?", (day,)).fetchone()[0] == 300


def test_max_days_per_run_caps_the_work(tmp_path):
    fake = FakeQdrant(_points({40: 10, 30: 10, 20: 10, 1: 10}))
    rep = _run(fake, _mb(5), tmp_path, max_days=2)
    assert rep["deleted"] == 20 and rep["status"] == "max_days_reached"


# ── the GUI-written limit ─────────────────────────────────────────────────────

@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "settings.yaml"
    p.write_text("kare_language: nb\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(argus_size, "ROLLUP_DB", tmp_path / "rollup_default.db")
    monkeypatch.setattr(argus_size, "measure", lambda: {"bytes": 3 * 1_048_576, "points": 700, "source": "fake"})
    return p


@pytest.mark.parametrize(("content", "expected"), [
    ("", argus_size.ARGUS_DEFAULT_MB),
    ("storage_limits:\n  argus_events_mb: 20480\n", 20480),
    ("storage_limits:\n  argus_events_mb: 0\n", 0),
    ("storage_limits:\n  argus_events_mb: -5\n", argus_size.ARGUS_DEFAULT_MB),
    ("storage_limits:\n  argus_events_mb: lots\n", argus_size.ARGUS_DEFAULT_MB),
])
def test_limit_accessor(settings_file, content, expected):
    settings_file.write_text(content, encoding="utf-8")
    assert argus_size.get_limit_mb() == expected


def test_default_is_the_light_one():
    assert argus_size.ARGUS_DEFAULT_MB == 2048


def test_disk_usage_api_round_trip(settings_file):
    got = asyncio.run(rs.api_get_disk_usage(_u=None))
    assert got["argus"]["limit_mb"] == 2048 and got["argus"]["default_mb"] == 2048
    assert got["argus"]["used_mb"] == 3.0 and got["argus"]["points"] == 700
    got = asyncio.run(rs.api_put_disk_usage({"argus_events_mb": 20480}, _u=None))
    assert got["argus"]["limit_mb"] == 20480
    data = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert data["storage_limits"]["argus_events_mb"] == 20480
    assert data["kare_language"] == "nb", "other settings untouched"


@pytest.mark.parametrize("bad", [-1, "x", None, 10**9])
def test_disk_usage_api_rejects_bad_values(settings_file, bad):
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_disk_usage({"argus_events_mb": bad}, _u=None))
