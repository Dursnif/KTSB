"""P144-s07: retention tiers for the Frigate memory (event 7 d → hourly 90 d → daily kept), the nightly rollup, the
dry run, unclassified names and unknown plates — plus the generic engine (kaare_core/retention) Argus will reuse.

- the rollup builds the right hourly/daily rows, once (idempotent), only for settled events, and survives a crash
  mid-chunk without double counts;
- an event older than the detail days is slimmed (in an episode) or deleted (no episode) only after it is rolled up;
- episodes and visits are kept; episode heavy fields are slimmed, the frame list stays;
- an unclassified face name is gone everywhere after its days (events, visits, episodes, hourly, daily) — counts kept;
- unknown plates: «7 days» vs «always»; known plates always kept;
- dry run writes rollups, changes nothing else, and reports what it would do;
- schema v5 from v4; settings + template; the night sequence step.
Never touches live state: frigate.db, users.db and the configs point at tmp_path.
"""

import asyncio
import inspect
import json
import logging
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

import kaare_night_sequence as ns
from kaare_core import config_migrations
from kaare_core.frigate_user import faces, pipe, sealing
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.retention import engine
from tests.test_p144_s04_frigate_store import T0, _ev
from tests.test_p144_s04_frigate_store import db as _s04_db  # noqa: F401 — fixture

DAY = 86400.0
TEMPLATE = Path("/kaare/configs_default/frigate_user.yaml")


@pytest.fixture
def db(_s04_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    monkeypatch.setattr(fstore, "get_local_tz", lambda: ZoneInfo("UTC"))
    return tmp_path


def _set(**changes):
    fsettings.update(changes)


def _open(v):
    """DT04 R0.6a: values are sealed at rest — read them as the store does, so asserts see the plaintext."""
    return sealing.unseal(v) if isinstance(v, str) else v


def _raw(sql, args=()):
    conn = sqlite3.connect(str(fstore.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return [{k: _open(r[k]) for k in r.keys()} for r in conn.execute(sql, args)]  # noqa: SIM118
    finally:
        conn.close()


def _dump() -> str:
    """Every text value in every table, unsealed — to prove a name is gone everywhere (sealed text would hide it)."""
    conn = sqlite3.connect(str(fstore.DB_PATH))
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return json.dumps({t: [[_open(v) for v in r] for r in conn.execute(f"SELECT * FROM {t}")] for t in tables},
                          ensure_ascii=False)
    finally:
        conn.close()


def _car(event_id, start, camera="veg", **kw):
    """A road car: a plain event, never in an episode (s12 camera settings)."""
    return _ev(event_id, person_ref=None, kind="unknown", claim="none", start=start, camera=camera, label="car",
               episode_rule="none", **kw)


# ── the generic engine ──────────────────────────────────────────────────────────


class FakeAdapter:
    name = "fake"

    def __init__(self, rows=5, fail_step=None):
        self.unrolled, self.rows, self.fail_step, self.reports = 3, rows, fail_step, []

    def rollup(self, limit):
        n = min(limit, self.unrolled)
        self.unrolled -= n
        return n

    def trim_steps(self, now):
        def apply_rows(limit):
            n = min(limit, self.rows)
            self.rows -= n
            return n

        def boom(_limit):
            raise RuntimeError("disk on fire")

        return [
            engine.TrimStep("a.broken", count=lambda: 1, apply=boom),
            engine.TrimStep("b.delete", count=lambda: self.rows, apply=apply_rows),
        ]

    def record(self, report):
        self.reports.append(report)


def test_engine_dry_run_counts_and_changes_nothing():
    a = FakeAdapter()
    rep = engine.run(a, dry_run=True, chunk=2, pause_s=0)
    assert rep.rolled == 3 and a.unrolled == 0  # rollups are written in a dry run too
    assert rep.steps == {"a.broken": 1, "b.delete": 5} and a.rows == 5
    assert rep.dry_run is True and a.reports == [rep]


def test_engine_live_applies_in_chunks_and_one_failing_step_does_not_stop_the_rest():
    a = FakeAdapter()
    rep = engine.run(a, dry_run=False, chunk=2, pause_s=0)
    assert a.rows == 0 and rep.steps["b.delete"] == 5
    assert "a.broken" in rep.errors and "disk on fire" in rep.errors["a.broken"]


def test_tiers_cutoffs():
    t = engine.Tiers(detail_days=7, hourly_days=90, daily_days=0)
    c = t.cutoffs(T0)
    assert c.detail == T0 - 7 * DAY and c.hourly == T0 - 90 * DAY and c.daily is None
    # a coarser tier never ends before a finer one
    c2 = engine.Tiers(detail_days=30, hourly_days=10, daily_days=5).cutoffs(T0)
    assert c2.hourly == T0 - 30 * DAY and c2.daily == T0 - 30 * DAY
    assert engine.hour_floor(7200 + 59) == 7200


# ── schema ──────────────────────────────────────────────────────────────────────


def test_migration_v5_from_v4(tmp_path, monkeypatch):
    path = tmp_path / "frigate" / "frigate.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(path))
    for v in (1, 2, 3, 4):
        conn.executescript(fstore._MIGRATIONS[v])
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '4')")
    conn.execute(
        "INSERT INTO events (event_id, camera, label, start_ts, person_kind, claim_status, created_ts, updated_ts) "
        "VALUES ('old', 'inngang', 'person', ?, 'unknown', 'none', ?, ?)", (T0, T0, T0))
    conn.commit()
    conn.close()
    monkeypatch.setattr(fstore, "DB_PATH", path)
    fstore.reset_for_tests()
    assert fstore.schema_version() == fstore.SCHEMA_VERSION  # v4 → v5 and on (J19 added v6)
    ev = _raw("SELECT rolled FROM events WHERE event_id='old'")[0]
    assert ev["rolled"] == 0  # existing events are rolled up by the first run
    assert "slimmed_ts" in {r["name"] for r in _raw("PRAGMA table_info(episodes)")}
    assert _raw("SELECT * FROM retention_runs") == []
    fstore.reset_for_tests()


# ── rollup ──────────────────────────────────────────────────────────────────────


def test_rollup_builds_hourly_and_daily(db):
    fstore.record_event(_ev("a", start=T0 + 60, place="inngang_ute"))
    fstore.record_event(_ev("b", start=T0 + 120, place="inngang_ute"))
    fstore.record_event(_ev("c", start=T0 + 3700, camera="hage", place="hagen"))
    fstore.record_event(_car("d", T0 + 200))
    fstore.close_visits(now=T0 + 3 * DAY)
    rep = fstore.run_retention(now=T0 + 3 * DAY)
    assert rep["rolled"] == 4
    hour = T0 - T0 % 3600
    hourly = {(r["hour_ts"], r["camera"], r["person_ref"], r["label"]): r for r in _raw("SELECT * FROM hourly")}
    assert hourly[(hour, "inngang", "userone", "person")]["event_count"] == 2
    assert hourly[(hour, "inngang", "userone", "person")]["place"] == "inngang_ute"
    assert hourly[(hour + 3600, "hage", "userone", "person")]["event_count"] == 1
    assert hourly[(hour, "veg", "", "car")]["person_kind"] == "unknown"
    daily = {r["person_ref"]: r for r in _raw("SELECT * FROM daily")}
    s = daily["userone"]
    assert s["day"] == "2027-01-15" and s["event_count"] == 3
    assert s["first_ts"] == T0 + 60 and s["last_ts"] == T0 + 3710
    assert json.loads(s["places"]) == ["hagen", "inngang_ute"]
    assert s["visit_count"] == 2  # 60 → 3700 s apart is more than the 30-min visit gap
    assert daily[""]["event_count"] == 1 and daily[""]["visit_count"] == 0


def test_rollup_is_idempotent_and_waits_for_settled_events(db):
    fstore.record_event(_ev("a", start=T0))
    fstore.record_event(_ev("fresh", start=T0 + 2 * DAY + 3600))
    fstore.run_retention(now=T0 + 2 * DAY + 7200)
    fstore.run_retention(now=T0 + 2 * DAY + 7200)
    assert sum(r["event_count"] for r in _raw("SELECT * FROM hourly")) == 1  # «fresh» is younger than a day
    assert _raw("SELECT rolled FROM events WHERE event_id='fresh'")[0]["rolled"] == 0
    assert _raw("SELECT event_count FROM daily")[0]["event_count"] == 1


def test_daily_uses_the_household_day(db, monkeypatch):
    monkeypatch.setattr(fstore, "get_local_tz", lambda: ZoneInfo("Europe/Oslo"))
    late = T0 - T0 % 86400 + 23.5 * 3600  # 23:30 UTC = 00:30 the next day in Oslo (winter)
    fstore.record_event(_ev("a", start=late))
    fstore.run_retention(now=late + 2 * DAY)
    assert _raw("SELECT day FROM daily")[0]["day"] == "2027-01-16"


def test_rollup_resumes_after_a_crash_without_double_counts(db, monkeypatch):
    for i in range(7):
        fstore.record_event(_car(f"c{i}", T0 + i * 10))
    real = fstore._roll_chunk
    calls = {"n": 0}

    def crash_second(conn, limit, now):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("power cut")
        return real(conn, limit, now)

    monkeypatch.setattr(fstore, "_roll_chunk", crash_second)
    rep = fstore.run_retention(now=T0 + 3 * DAY, chunk=3)
    assert rep["errors"]
    monkeypatch.setattr(fstore, "_roll_chunk", real)
    fstore.run_retention(now=T0 + 3 * DAY, chunk=3)
    assert sum(r["event_count"] for r in _raw("SELECT * FROM hourly")) == 7
    assert _raw("SELECT event_count FROM daily")[0]["event_count"] == 7


# ── trimming ────────────────────────────────────────────────────────────────────


def _episode_with_scene():
    fstore.record_event(_ev("p", start=T0, description="red jacket, tall", analysis="walks to door",
                            attributes={"beard": "full"}, path_data=[[[0.1, 0.5], T0], [[0.8, 0.5], T0 + 5]]))
    ep_id = _raw("SELECT episode_id FROM events WHERE event_id='p'")[0]["episode_id"]
    scene = {"status": "done", "persons": [{"clothing": "red jacket"}], "scene": "a man at the door",
             "frames": [{"camera": "inngang", "ts": T0 + 2}], "claims": ["userone"]}
    fstore.set_episode_scene(ep_id, scene, "inngang → hage")
    fstore.close_episodes(now=T0 + DAY)
    fstore.conclude_episode(ep_id, {"status": "done", "summary": "Userone came home"})
    return ep_id


def test_old_events_are_slimmed_or_deleted_episodes_and_visits_kept(db, monkeypatch):
    # P150: the cut is the store's size limit, not 7 days — under the limit nothing goes, over it the oldest.
    _set(retention_dry_run=False, frigate_db_max_mb=1)
    ep_id = _episode_with_scene()
    fstore.record_event(_car("road", T0 + 30))  # road camera: no episode
    fstore.close_visits(now=T0 + DAY)
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 1024)
    fstore.run_retention(now=T0 + 8 * DAY)
    assert {r["event_id"] for r in _raw("SELECT event_id FROM events")} == {"p", "road"}  # under the limit: nothing
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 50 * 1_048_576)
    rep = fstore.run_retention(now=T0 + 8 * DAY)
    assert rep["steps"]["events.delete"] == 1 and rep["steps"]["events.slim"] == 1
    p = _raw("SELECT * FROM events")
    assert [r["event_id"] for r in p] == ["p"]
    p = p[0]
    assert p["tier"] == "slim" and p["description"] is None and p["analysis"] is None
    assert json.loads(p["attributes"]) == {} and json.loads(p["path"]) == []
    assert p["person_ref"] == "userone" and p["claim_status"] == "confirmed" and p["episode_id"] == ep_id
    assert set(json.loads(p["check"])) <= {"decision", "storage_gate", "episode_rule"}
    ep = fstore.episode(ep_id)
    assert ep.conclusion["summary"] == "Userone came home" and ep.person_refs == ["userone"]
    assert ep.scene == {"status": "done", "frames": [{"camera": "inngang", "ts": T0 + 2}]}  # the kept frame opens
    assert ep.route is None
    assert len(_raw("SELECT * FROM visits")) == 1
    assert sum(r["event_count"] for r in _raw("SELECT * FROM hourly")) == 2  # counts never forgotten
    again = fstore.run_retention(now=T0 + 8 * DAY)
    assert again["steps"]["events.slim"] == 0 and again["steps"]["episodes.slim"] == 0


def test_an_unrolled_event_is_never_trimmed(db, monkeypatch):
    _set(retention_dry_run=False, frigate_db_max_mb=1)
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 50 * 1_048_576)  # P150: over the size limit
    fstore.record_event(_car("road", T0))
    adapter = fstore.FrigateRetention()
    steps = {s.name: s for s in adapter.trim_steps(T0 + 30 * DAY)}
    assert steps["events.delete"].count() == 0 and steps["events.delete"].apply(100) == 0
    assert len(_raw("SELECT * FROM events")) == 1
    fstore.run_retention(now=T0 + 30 * DAY)  # rolls it up first, then trims it
    assert _raw("SELECT * FROM events") == []
    assert _raw("SELECT event_count FROM hourly")[0]["event_count"] == 1


def test_hourly_goes_after_the_detail_daily_is_kept(db, monkeypatch):
    """P150: was «hourly expires after 90 days, daily after daily_days». Now hourly goes only when no detail is left and
    the store is still over its size limit; daily counts are never deleted."""
    _set(retention_dry_run=False, frigate_db_max_mb=1)
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 50 * 1_048_576)
    fstore.record_event(_car("road", T0))
    fstore.run_retention(now=T0 + 2 * DAY)  # the event goes first
    fstore.run_retention(now=T0 + 2 * DAY)  # then its hour
    assert _raw("SELECT * FROM hourly") == []
    fstore.run_retention(now=T0 + 3 * DAY)
    assert _raw("SELECT event_count FROM daily")[0]["event_count"] == 1


def test_dry_run_changes_nothing_but_rollups_and_reports(db, caplog, monkeypatch):
    _set(frigate_db_max_mb=1)
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 50 * 1_048_576)  # P150: over the size limit
    ep_id = _episode_with_scene()
    fstore.record_event(_car("road", T0 + 30))
    before = _raw("SELECT event_id, description, path, tier FROM events ORDER BY event_id")
    with caplog.at_level(logging.INFO):
        rep = fstore.run_retention(now=T0 + 100 * DAY)
    assert rep["dry_run"] is True
    assert rep["steps"]["events.delete"] == 1 and rep["steps"]["events.slim"] == 1
    # P150: hourly counts are a second stage (only once no detail is left), so a dry run with detail present shows 0
    assert rep["steps"]["episodes.slim"] == 1 and rep["steps"]["hourly.delete"] == 0
    assert _raw("SELECT event_id, description, path, tier FROM events ORDER BY event_id") == before
    assert fstore.episode(ep_id).route == "inngang → hage"
    assert sum(r["event_count"] for r in _raw("SELECT * FROM hourly")) == 2
    assert "would" in caplog.text and "events.delete" in caplog.text
    runs = _raw("SELECT * FROM retention_runs")
    assert len(runs) == 1 and runs[0]["dry_run"] == 1 and json.loads(runs[0]["report"])["steps"]["events.delete"] == 1


# ── names and plates ────────────────────────────────────────────────────────────


def test_unclassified_name_dropped_everywhere_after_its_days(db):
    _set(retention_dry_run=False)
    fstore.record_event(_ev("u", person_ref="Ola", kind="unclassified", claim="unverified", start=T0,
                            description="Ola in a blue coat", check={"claim": "Ola"}))
    ep_id = _raw("SELECT episode_id FROM events")[0]["episode_id"]
    fstore.set_episode_scene(ep_id, {"status": "done", "scene": "Ola at the door", "frames": []}, "Ola walks in")
    fstore.close_episodes(now=T0 + DAY)
    fstore.conclude_episode(ep_id, {"status": "done", "summary": "Ola visited"})
    fstore.close_visits(now=T0 + DAY)
    fstore.run_retention(now=T0 + 2 * DAY)
    assert "Ola" in _dump()  # kept within its 7 days (and rolled up under the name)
    fstore.run_retention(now=T0 + 8 * DAY)
    assert "Ola" not in _dump()
    assert _raw("SELECT person_ref, person_kind, event_count FROM hourly") == [
        {"person_ref": "", "person_kind": "unclassified", "event_count": 1}]
    assert _raw("SELECT person_ref, event_count FROM daily") == [{"person_ref": "", "event_count": 1}]
    assert fstore.episode(ep_id).conclusion["summary"] == "person visited"
    assert len(_raw("SELECT * FROM visits")) == 1


def test_unclassified_names_merge_into_the_nameless_rollup_rows(db):
    _set(retention_dry_run=False)
    fstore.record_event(_ev("u1", person_ref="Ola", kind="unclassified", claim="unverified", start=T0))
    fstore.record_event(_ev("u2", person_ref="Kari", kind="unclassified", claim="unverified", start=T0 + 60))
    fstore.run_retention(now=T0 + 8 * DAY)
    rows = _raw("SELECT person_ref, event_count FROM hourly WHERE person_kind='unclassified'")
    assert rows == [{"person_ref": "", "event_count": 2}]
    assert _raw("SELECT person_ref, event_count FROM daily WHERE person_kind='unclassified'") == rows


def test_a_face_classified_since_keeps_its_name(db):
    _set(retention_dry_run=False)
    fstore.record_event(_ev("u", person_ref="Ola", kind="unclassified", claim="unverified", start=T0))
    faces.set_face("Ola", "safe_guest", {})
    fstore.run_retention(now=T0 + 8 * DAY)
    assert _raw("SELECT person_ref FROM events")[0]["person_ref"] == "Ola"


def test_unclassified_names_wait_in_a_dry_run(db):
    fstore.record_event(_ev("u", person_ref="Ola", kind="unclassified", claim="unverified", start=T0))
    rep = fstore.run_retention(now=T0 + 8 * DAY)
    assert rep["steps"]["names.events"] == 1
    assert _raw("SELECT person_ref FROM events")[0]["person_ref"] == "Ola"


def _plate(event_id, plate, known):
    fstore.record_event(_car(event_id, T0, camera="innkjorsel", plate_claim=plate, plate_known=known))


def test_unknown_plates_7_days(db):
    _plate("x", "AB12345", False)
    _plate("k", "Toyota Auris", True)
    assert fstore.expire_unknown_plates(now=T0 + 6 * DAY) == 0
    assert fstore.expire_unknown_plates(now=T0 + 8 * DAY) == 1
    plates = {r["event_id"]: r["plate_claim"] for r in _raw("SELECT event_id, plate_claim FROM events")}
    assert plates == {"x": None, "k": "Toyota Auris"}


def test_unknown_plates_always_kept(db):
    _set(keep_unknown_plates="always")
    _plate("x", "AB12345", False)
    assert fstore.expire_unknown_plates(now=T0 + 400 * DAY) == 0
    assert _raw("SELECT plate_claim FROM events")[0]["plate_claim"] == "AB12345"


def test_the_pipe_uses_the_plate_setting_not_the_face_days():
    src = inspect.getsource(pipe._visit_loop)
    assert "store.expire_unknown_plates)" in src and "unclassified_name_days" not in src


# ── settings + night sequence ───────────────────────────────────────────────────


def test_settings_defaults_and_validation(db):
    s = fsettings.get_settings()
    assert s["retention_dry_run"] is True
    assert s["frigate_db_max_mb"] == 500  # P150: size limit replaces the day tiers
    assert s["keep_unknown_plates"] == "7_days"
    for bad in ({"keep_unknown_plates": "forever"}, {"retention_dry_run": "yes"}, {"frigate_db_max_mb": -1},
                {"event_detail_days": 7}):
        with pytest.raises(ValueError):
            fsettings.update(bad)
    assert fsettings.update({"frigate_db_max_mb": 0})["frigate_db_max_mb"] == 0  # 0 = off
    assert fsettings.update({"keep_unknown_plates": "always"})["keep_unknown_plates"] == "always"


def test_template_ships_dry_run_and_7_day_plates():
    data = yaml.safe_load(TEMPLATE.read_text())
    assert data["retention_dry_run"] is True and data["keep_unknown_plates"] == "7_days"
    assert data["frigate_db_max_mb"] == 500  # P150


def test_night_step_skips_without_a_db(db, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", db / "nowhere" / "frigate.db")
    fstore.reset_for_tests()
    assert asyncio.run(ns._run_frigate_retention()) is None
    assert not (db / "nowhere").exists()  # a fresh install never gets an empty DB from the night job


def test_night_sequence_runs_the_frigate_step():
    assert '"frigate_retention"' in inspect.getsource(ns.run_sequence)


def test_config_migration_adds_the_new_keys_to_an_old_file():
    old = {"enabled": True, "episode_gap_s": 120, "conclude": {"idle_gate_s": 30}}
    added = config_migrations.fill_missing_keys(old, yaml.safe_load(TEMPLATE.read_text()))
    for key in ("frigate_db_max_mb", "retention_dry_run", "keep_unknown_plates"):
        assert key in added
    assert old["retention_dry_run"] is True and old["keep_unknown_plates"] == "7_days" and old["enabled"] is True
