"""P150 (2026-11-13: «indeks har også oversikt over dem dagene det ikke har blitt noe sammendrag»): when the night job
skips a day's STM summary because nobody talked to Kåre, it writes that down (stm_daily_skipped), so every day has a row —
a summary or a skip. STM pruning then reads two small indexed tables instead of scanning all interactions; a day with
neither is a real gap (logged), never pruned. Never the house: tmp interactions.db.
"""

import asyncio
import sqlite3

import pytest

import kaare_nightjob as nj
from kaare_core.memory import long_term
from kaare_core.storage import limits, stm_history

KB = 1024


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "interactions.db"
    monkeypatch.setattr(long_term, "DB_PATH", path)
    long_term._init_db()
    return path


def test_a_skip_is_written_and_read_back(db):
    long_term.save_daily_skip("2026-11-09", covers="2026-11-08", reason="no_interactions")
    long_term.save_daily_skip("2026-11-09", covers="2026-11-08", reason="no_interactions")  # idempotent
    assert long_term.skipped_dates() == {"2026-11-09"}
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT covers, reason FROM stm_daily_skipped").fetchall() == [("2026-11-08", "no_interactions")]


def test_the_night_job_records_a_day_without_conversations(db, monkeypatch):
    monkeypatch.setattr(nj, "save_daily_skip", long_term.save_daily_skip)
    with sqlite3.connect(db) as conn:
        asyncio.run(nj.compress_daily_stm(conn))
    assert len(long_term.skipped_dates()) == 1


def test_prune_uses_skips_and_does_not_scan_interactions(tmp_path, db, monkeypatch):
    hist = tmp_path / "stm_history"
    for d in ("2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10"):
        (hist / d).mkdir(parents=True)
        (hist / d / "u.json").write_bytes(b"x" * 10 * KB)
    monkeypatch.setattr(limits, "get_bytes", lambda key: 15 * KB)
    long_term.save_daily_summary("2026-11-08", "s", 3, allow_system_key=True)  # covers 11-07
    long_term.save_daily_skip("2026-11-09", covers="2026-11-08", reason="no_interactions")
    long_term.save_daily_skip("2026-11-10", covers="2026-11-09", reason="no_interactions")
    monkeypatch.setattr(long_term, "interaction_dates", lambda: pytest.fail("no full scan when every day has a row"))
    assert stm_history.prune(hist) == ["2026-11-07", "2026-11-08", "2026-11-09"]


def test_a_real_gap_is_never_pruned(tmp_path, db, monkeypatch, caplog):
    hist = tmp_path / "stm_history"
    for d in ("2026-11-07", "2026-11-08"):
        (hist / d).mkdir(parents=True)
        (hist / d / "u.json").write_bytes(b"x" * 10 * KB)
    monkeypatch.setattr(limits, "get_bytes", lambda key: 1)
    monkeypatch.setattr(long_term, "interaction_dates", lambda: {"2026-11-07"})  # talked, but no summary, no skip
    assert stm_history.prune(hist) == []
    assert "no summary" in caplog.text
