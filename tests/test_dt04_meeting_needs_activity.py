"""DT04 R0.9c — no nightly reflection meeting for a user with nothing new since that user's last meeting.

2026-11-15: all five users got a full meeting (31 min on the main model, web searches) although the portrait step
had logged «No interactions … skipping» for every one of them. Rule now, per user, in the night sequence:
- the user's own interactions (the portrait step's rows: outcome success / llm_fallback) newer than the user's newest
  meeting file → meeting; none → skipped with one INFO line (never an error);
- something a person asked the meeting to take up counts as activity: the user's comment on the last meeting, the
  user's topic, the admin's topic;
- what the cameras saw does not count (it is the household's, not the user's);
- a user who never had a meeting gets one when there is any interaction at all;
- the GUI's «Start reflection meeting» button is an explicit request: nobody is skipped;
- an unreadable interaction store never silences the meeting (it runs, as before).
Everything on tmp folders — never the house's databases.
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import kaare_night_sequence as ns
from kaare_core.memory import meeting_activity as ma

NOW = datetime(2026, 11, 15, 3, 30, tzinfo=UTC)


@pytest.fixture
def house(tmp_path, monkeypatch):
    db = tmp_path / "interactions.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE interactions (id INTEGER PRIMARY KEY, ts TEXT, user_id TEXT, prompt TEXT, "
                 "response TEXT, outcome TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(ma, "INTERACTIONS_DB", db)
    monkeypatch.setattr(ma, "REFLECTIONS_BASE", tmp_path / "reflections")
    monkeypatch.setattr(ma, "COMMENTS_BASE", tmp_path / "meeting_comments")
    monkeypatch.setattr(ma, "USER_TOPICS_BASE", tmp_path / "meeting_topics_user")
    monkeypatch.setattr(ma, "ADMIN_TOPICS_FILE", tmp_path / "meeting_topics.json")
    return tmp_path


def _talk(house, user, when, outcome="success"):
    conn = sqlite3.connect(house / "interactions.db")
    conn.execute("INSERT INTO interactions (ts, user_id, prompt, response, outcome) VALUES (?, ?, 'p', 'r', ?)",
                 (when.isoformat(timespec="seconds"), user, outcome))
    conn.commit()
    conn.close()


def _meeting(house, user, when):
    d = house / "reflections" / user
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{when:%Y-%m-%d}.md"
    f.write_text("# meeting\n", encoding="utf-8")
    os.utime(f, (when.timestamp(), when.timestamp()))
    (d / "latest.md").write_text("# meeting\n", encoding="utf-8")  # a copy, must not count as a newer meeting


# ── the rule ────────────────────────────────────────────────────────────────────

def test_nothing_since_the_last_meeting_means_no_meeting(house):
    _talk(house, "userone", NOW - timedelta(days=3))
    _meeting(house, "userone", NOW - timedelta(days=1))
    needed, why = ma.needs_meeting("userone")
    assert needed is False and "no new activity" in why


def test_a_new_interaction_after_the_last_meeting_means_a_meeting(house):
    _meeting(house, "userone", NOW - timedelta(days=1))
    _talk(house, "userone", NOW - timedelta(hours=5))
    assert ma.needs_meeting("userone")[0] is True


def test_another_users_talk_does_not_count(house):
    _meeting(house, "userone", NOW - timedelta(days=1))
    _talk(house, "userb", NOW - timedelta(hours=5))
    assert ma.needs_meeting("userone")[0] is False


def test_failed_turns_do_not_count_like_in_the_portrait_step(house):
    _meeting(house, "userone", NOW - timedelta(days=1))
    _talk(house, "userone", NOW - timedelta(hours=5), outcome="error")
    assert ma.needs_meeting("userone")[0] is False
    _talk(house, "userone", NOW - timedelta(hours=4), outcome="llm_fallback")
    assert ma.needs_meeting("userone")[0] is True


def test_a_user_who_never_had_a_meeting(house):
    assert ma.needs_meeting("userone")[0] is False  # never talked either
    _talk(house, "userone", NOW - timedelta(days=40))
    assert ma.needs_meeting("userone")[0] is True


@pytest.mark.parametrize("what", ["comment", "user_topic", "admin_topic"])
def test_something_a_person_asked_for_counts_as_activity(house, what):
    _meeting(house, "userone", NOW - timedelta(days=1))
    if what == "comment":
        d = house / "meeting_comments" / "reflection" / "userone"
        d.mkdir(parents=True)
        (d / "2026-11-14.txt").write_text("ta opp dette", encoding="utf-8")
    elif what == "user_topic":
        (house / "meeting_topics_user").mkdir()
        (house / "meeting_topics_user" / "userone.txt").write_text("et tema", encoding="utf-8")
    else:
        (house / "meeting_topics.json").write_text(json.dumps({"reflection": "admin-tema"}), encoding="utf-8")
    needed, why = ma.needs_meeting("userone")
    assert needed is True and why


def test_empty_request_files_do_not_count(house):
    _meeting(house, "userone", NOW - timedelta(days=1))
    (house / "meeting_topics_user").mkdir()
    (house / "meeting_topics_user" / "userone.txt").write_text("  \n", encoding="utf-8")
    (house / "meeting_topics.json").write_text(json.dumps({"reflection": ""}), encoding="utf-8")
    assert ma.needs_meeting("userone")[0] is False


def test_an_unreadable_store_never_silences_the_meeting(house, monkeypatch):
    _meeting(house, "userone", NOW - timedelta(days=1))
    monkeypatch.setattr(ma, "INTERACTIONS_DB", house / "missing" / "interactions.db")
    needed, why = ma.needs_meeting("userone")
    assert needed is True and "could not" in why


# ── the night sequence uses it; the button does not ─────────────────────────────

@pytest.fixture
def held(monkeypatch):
    calls = []

    async def fake_meeting(user_id=None):
        calls.append(user_id)

    monkeypatch.setattr(ns, "reflection_main", fake_meeting)
    monkeypatch.setattr(ns.meeting_activity, "needs_meeting",
                        lambda u: (u != "idle", "new interactions" if u != "idle" else "no new activity since X"))
    return calls


def test_the_night_skips_the_idle_user_with_one_info_line(held, caplog):
    with caplog.at_level(logging.INFO, logger=ns.log.name):
        asyncio.run(ns._run_reflection_all_users(["userone", "idle", "userb"]))
    assert held == ["userone", "userb"]
    skipped = [r for r in caplog.records if "idle" in r.getMessage() and "skipped" in r.getMessage()]
    assert len(skipped) == 1 and skipped[0].levelno == logging.INFO
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_button_holds_every_meeting(held):
    asyncio.run(ns._run_reflection_all_users(["userone", "idle"], skip_idle=False))
    assert held == ["userone", "idle"]
