"""P144-s14 item 3 — the Frigate store warns loudly when it cannot write (incident 2026-10-28 02:00–02:07: a userone
process left `-wal/-shm` owned by userone, every write failed with only an ERROR in the journal, ≥ 3 events lost).

- a read-only DB → WARNING with the cause, `write_status()` says so (GUI: Integrations → Frigate), one JSONL line
  for Argus (frigate subsystem) on the change ok → failing, not one per write;
- every writer retries once on a fresh connection;
- the next successful write clears the status and tells Argus once.
Never touches live state (tmp DB, tmp Argus log).
"""

import json
import logging
import os

import pytest

from kaare_core.frigate_user import store as fstore
from kaare_core.storage import rawlog

T0 = 1_793_143_388.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "frigate" / "frigate.db"
    monkeypatch.setattr(fstore, "DB_PATH", path)
    fstore.reset_for_tests()
    assert fstore.schema_version() == fstore.SCHEMA_VERSION  # creates the DB
    yield path
    _writable(path)
    fstore.reset_for_tests()


def _writable(path):
    """What an admin does after the incident: the DB and its -wal/-shm (SQLite creates them with the DB's mode)."""
    for f in path.parent.glob(path.name + "*"):
        os.chmod(f, 0o644)


def _ev(eid, label="person"):
    return fstore.FrigateUserEvent(event_id=eid, camera="door", label=label, start_ts=T0, end_ts=T0 + 5)


def _argus(tmp):
    # P150: the store tells Argus through today's raw frigate_mqtt segment (conftest points the raw dir at tmp_path)
    return [json.loads(line) for _n, p in rawlog.segments("frigate_mqtt") for line in p.read_text().splitlines()]


def test_read_only_db_warns_with_cause_and_sets_the_status(db, caplog, tmp_path):
    os.chmod(db, 0o444)
    with caplog.at_level(logging.WARNING, logger=fstore.logger.name):
        assert fstore.record_event(_ev("e1")) is False
    warn = [r for r in caplog.records if r.levelno == logging.WARNING and "cannot write" in r.getMessage()]
    assert warn and "read-only" in warn[0].getMessage()
    st = fstore.write_status()
    assert st["ok"] is False and st["cause"] == "read_only" and st["op"] == "record_event"
    assert "readonly" in st["error"].lower()


def test_each_writer_retries_once(db, monkeypatch):
    os.chmod(db, 0o444)
    calls = []
    real = fstore._get_conn
    monkeypatch.setattr(fstore, "_get_conn", lambda: calls.append(1) or real())
    fstore.record_event(_ev("e1"))
    assert len(calls) == 2


def test_argus_hears_it_once_per_failure_streak_and_once_on_recovery(db, tmp_path):
    os.chmod(db, 0o444)
    fstore.record_event(_ev("e1"))
    fstore.record_event(_ev("e2"))
    lines = _argus(tmp_path)
    assert [ln["stage"] for ln in lines] == ["store_write_failed"]
    assert lines[0]["level"] == "error" and lines[0]["subsystem"] == "frigate" and lines[0]["error"]
    _writable(db)
    assert fstore.record_event(_ev("e3")) is True
    assert fstore.write_status()["ok"] is True
    assert [ln["stage"] for ln in _argus(tmp_path)] == ["store_write_failed", "store_write_recovered"]


def test_scene_and_conclusion_writers_report_too(db):
    assert fstore.record_event(_ev("e1"))
    (ep,) = fstore.episodes_between(0, T0 * 2)
    os.chmod(db, 0o444)
    assert fstore.conclude_episode(ep.episode_id, {"status": "done"}) is False
    assert fstore.write_status()["op"] == "conclude_episode"
    assert fstore.set_episode_scene(ep.episode_id, {"status": "done"}, "r") is False
    assert fstore.write_status()["op"] == "set_episode_scene"


def test_a_plain_bug_is_not_called_read_only(db, monkeypatch):
    assert fstore._write_cause(ValueError("boom")) == "error"
    assert fstore._write_cause(PermissionError("x")) == "permission"
    assert fstore._write_cause(Exception("database or disk is full")) == "disk_full"
