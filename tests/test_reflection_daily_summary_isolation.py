"""The reflection meeting for one user must never see another user's daily STM summaries.

Before this fix, load_recent_daily_summaries() returned the newest rows from every user and
decrypted them with the system key, so user B's meeting was fed user A's day.
"""

import sqlite3

import pytest

import kaare_reflection
from kaare_core.memory import long_term


@pytest.fixture
def ltm(tmp_path, monkeypatch):
    monkeypatch.setattr(long_term, "DB_PATH", tmp_path / "interactions.db")
    monkeypatch.setattr(long_term, "_ltm", None)
    long_term.get_ltm()
    conn = sqlite3.connect(str(long_term.DB_PATH))
    rows = [
        ("2026-10-11", "alice", "alice built a greenhouse"),
        ("2026-10-11", "global", "household night report"),
        ("2026-10-10", "alice", "alice fixed the router"),
        ("2026-10-09", "bob", "bob played football"),
    ]
    for date, uid, text in rows:
        conn.execute(
            "INSERT INTO stm_daily_summary (date, user_id, summary, interaction_count, ts_created) "
            "VALUES (?, ?, ?, 1, '2026-10-11T00:00:00+00:00')",
            (date, uid, text),
        )
    conn.commit()
    conn.close()


def test_loader_returns_only_the_given_user(ltm):
    entries = long_term.load_recent_daily_summaries(days=3, user_id="bob")
    assert [e["summary"] for e in entries] == ["bob played football"]


def test_loader_user_without_rows_gets_nothing(ltm):
    assert long_term.load_recent_daily_summaries(days=3, user_id="carol") == []


def test_reflection_context_has_no_other_users_day(ltm):
    text = kaare_reflection._get_stm_daily_summaries("bob", days=3, lang="en")
    assert "bob played football" in text
    assert "alice" not in text
    assert "household night report" not in text
