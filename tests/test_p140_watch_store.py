"""P140 phase 2: watch store — finished / paused / skipped rules (2026-10-18) and catch-up."""

import sqlite3

import pytest

from kaare_core.media import watch_store as ws

ACC = 1
SHOW = "100"


def _ep(
    n: int, season: int = 1, duration: int = 2_600_000, credits: int | None = 2_560_000
) -> dict:
    return {
        "rating_key": f"{season}{n:02d}",
        "type": "episode",
        "show_key": SHOW,
        "show_title": "Show",
        "season": season,
        "episode": n,
        "title": f"Ep {n}",
        "duration_ms": duration,
        "credits_ms": credits,
    }


@pytest.fixture
def db(tmp_path):
    return tmp_path / "watch.db"


def _play(db, item, offsets, t0, session="s1", step=10.0, state="playing"):
    """Feed progress ticks; returns the list of events."""
    events = []
    for i, off in enumerate(offsets):
        _pid, ev = ws.record_progress(
            account_id=ACC,
            session_key=session,
            item=item,
            state=state,
            offset_ms=off,
            now=t0 + i * step,
            path=db,
        )
        events.append(ev)
    return events


def _status(db, item):
    return ws.show_status(ACC, SHOW, path=db)[item["rating_key"]]["status"]


def test_credits_marker_counts_as_finished(db):
    ep = _ep(1)
    events = _play(db, ep, [0, 1_000_000, 2_561_000], t0=1000)
    assert events == ["started", "", "finished"]
    assert _status(db, ep) == "watched"


def test_ninety_percent_without_marker(db):
    ep = _ep(1, credits=None)
    _play(db, ep, [0, 2_350_000], t0=1000)
    assert _status(db, ep) == "watched"
    ep2 = _ep(2, credits=None)
    _play(db, ep2, [0, 2_000_000], t0=5000, session="s2")
    assert _status(db, ep2) == "paused"


def test_unfinished_then_later_episode_is_skipped(db):
    e1, e3 = _ep(1), _ep(3)
    _play(db, e1, [0, 300_000], t0=1000)
    assert _status(db, e1) == "paused"
    _play(db, e3, [0], t0=2000, session="s2")
    assert _status(db, e1) == "skipped"
    assert [p["rating_key"] for p in ws.skipped(ACC, path=db)] == [e1["rating_key"]]


def test_going_back_is_rewatch_not_skip(db):
    e5, e2 = _ep(5), _ep(2)
    _play(db, e5, [0, 300_000], t0=1000)
    _play(db, e2, [0], t0=2000, session="s2")  # back to an earlier episode
    assert _status(db, e5) == "paused"


def test_restarting_a_skipped_episode_makes_it_paused_again(db):
    e1, e2 = _ep(1), _ep(2)
    _play(db, e1, [0, 300_000], t0=1000)
    _play(db, e2, [0], t0=2000, session="s2")
    assert _status(db, e1) == "skipped"
    _play(db, e1, [300_000, 400_000], t0=9000, session="s3")  # newest play wins
    assert _status(db, e1) == "paused"


def test_user_override_beats_the_rules(db):
    e1, e2 = _ep(1), _ep(2)
    _play(db, e1, [0, 300_000], t0=1000)
    _play(db, e2, [0], t0=2000, session="s2")
    assert ws.set_override(ACC, e1["rating_key"], "watched", path=db)
    assert _status(db, e1) == "watched"
    assert ws.set_override(ACC, e1["rating_key"], "clear", path=db)
    assert _status(db, e1) == "skipped"
    assert not ws.set_override(ACC, e1["rating_key"], "bogus", path=db)


def test_continue_point_resume_and_next(db):
    e1, e2 = _ep(1), _ep(2)
    _play(db, e1, [0, 2_561_000], t0=1000)
    cp = ws.continue_point(ACC, SHOW, path=db)
    assert cp["kind"] == "next" and cp["play"]["episode"] == 1
    _play(db, e2, [0, 600_000], t0=5000, session="s2")
    cp = ws.continue_point(ACC, path=db)
    assert cp["kind"] == "resume" and cp["play"]["last_offset_ms"] == 600_000


def test_pause_resume_events_and_new_play_after_gap(db):
    ep = _ep(1)
    assert _play(db, ep, [0], t0=1000) == ["started"]
    _pid, ev = ws.record_progress(
        account_id=ACC, session_key="s1", item=ep, state="paused", offset_ms=5000, now=1010, path=db
    )
    assert ev == "paused"
    _pid, ev = ws.record_progress(
        account_id=ACC,
        session_key="s1",
        item=ep,
        state="playing",
        offset_ms=5000,
        now=1020,
        path=db,
    )
    assert ev == "resumed"
    _pid, ev = ws.record_progress(
        account_id=ACC,
        session_key="s1",
        item=ep,
        state="playing",
        offset_ms=6000,
        now=1020 + ws.SESSION_GAP_S + 5,
        path=db,
    )
    assert ev == "started"


def test_history_import_dedupes_against_live_play(db):
    e1, e2 = _ep(1), _ep(2)
    _play(db, e1, [0, 2_000_000], t0=1000)  # not finished by KTSB's rule …
    entry = {**e1, "viewed_at": 1500}  # … but Plex counted it
    added = ws.import_history(
        ACC, [entry, {**e2, "viewed_at": 3000}, {**e2, "viewed_at": 3000}], path=db
    )
    assert added == 1
    assert _status(db, e1) == "watched"
    assert _status(db, e2) == "watched"


def test_meta_roundtrip(db):
    assert ws.get_meta("x", "d", path=db) == "d"
    ws.set_meta("x", "42", path=db)
    assert ws.get_meta("x", path=db) == "42"


def test_readonly_connection_is_dropped_and_reopened(db, monkeypatch):
    ws.set_meta("k", "1", path=db)
    key = str(db)
    real = ws._conns[key]

    class ReadOnly:
        def __getattr__(self, name):
            return getattr(real, name)

        def __enter__(self):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    ws._conns[key] = ReadOnly()
    with pytest.raises(sqlite3.OperationalError):
        ws.set_meta("k", "2", path=db)
    assert key not in ws._conns  # dropped → next call reopens
    ws.set_meta("k", "3", path=db)
    assert ws.get_meta("k", path=db) == "3"


def test_marker_wins_over_ninety_percent(db):
    # Live 2026-10-19: «finished» fired at 90 % although the credits started at 98.5 % — the marker decides.
    ep = _ep(1, duration=2_641_210, credits=2_601_089)
    events = _play(db, ep, [0, 2_385_564, 2_590_000], t0=1000)
    assert events == ["started", "", ""]
    assert _status(db, ep) == "paused"
    assert _play(db, ep, [2_600_000], t0=1100) == [
        "finished"
    ]  # within the 2 s tolerance of the marker
