"""P140 phase 5: Kåre's plex_continue / plex_skipped / plex_mark — «KTSB knows» next to «Plex claims»."""

import asyncio

import pytest

from kaare_core.media import plex_listener as pl
from kaare_core.media import plex_watch_tool as wt
from kaare_core.media import watch_store as ws

SHOW = "18047"


def _ep(n: int, season: int = 1) -> dict:
    return {
        "rating_key": f"{season}{n:02d}",
        "type": "episode",
        "show_key": SHOW,
        "show_title": "Spesialavsnittet",
        "season": season,
        "episode": n,
        "title": f"Ep {n}",
        "duration_ms": 2_600_000,
        "credits_ms": 2_560_000,
    }


LEAVES = [
    {"ratingKey": f"1{n:02d}", "parentIndex": 1, "index": n, "title": f"Ep {n}"}
    for n in range(1, 4)
] + [{"ratingKey": "201", "parentIndex": 2, "index": 1, "title": "S2 opener"}]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "DB_PATH", tmp_path / "watch.db")
    users = {"userone": {"plex_account_id": 1}, "kid": {"plex_account_id": 2}, "guest": {}}
    monkeypatch.setattr(wt, "get_user", lambda u: users.get(u))
    deck = {"items": []}

    async def fake_fetch(path, params=None):
        if path.endswith("/allLeaves"):
            return {"Metadata": LEAVES}
        if path == "/library/onDeck":
            return {"Metadata": deck["items"]}
        raise AssertionError(path)

    monkeypatch.setattr(wt.plex, "fetch", fake_fetch)
    return deck


def _play(account, item, offsets, t0, session):
    for i, off in enumerate(offsets):
        ws.record_progress(
            account_id=account,
            session_key=session,
            item=item,
            state="playing",
            offset_ms=off,
            player="Verksted-TV 2",
            now=t0 + i * 10,
        )


def run(coro):
    return asyncio.run(coro)


def test_not_linked_points_to_the_real_gui_place(env):
    out = run(wt.continue_text("guest", "", "nb"))
    assert "Brukere → rediger → Plex-konto" in out


def test_finished_episode_gives_next_across_season(env):
    _play(1, _ep(3), [0, 2_561_000], 1000, "a")  # S01E03 finished → next is S02E01
    out = run(wt.continue_text("userone", "spesial", "en"))
    assert "KTSB knows: last S01E03" in out and "watched to the end" in out
    assert "next: S02E01" in out and "[id:201]" in out
    assert "Plex says: nothing" in out


def test_resume_and_disagreement_hint_for_owner(env):
    _play(1, _ep(2), [0, 600_000], 1000, "a")
    env["items"] = [
        {
            "ratingKey": "101",
            "grandparentRatingKey": SHOW,
            "type": "episode",
            "parentIndex": 1,
            "index": 1,
            "title": "Ep 1",
            "viewOffset": 120_000,
        }
    ]
    out = run(wt.continue_text("userone", "", "nb"))
    assert "KTSB vet: S01E02" in out and "plex_play rating_key=102 offset=600" in out
    assert "Plex sier: fortsett S01E01" in out
    assert "uenige" in out


def test_non_owner_gets_ktsb_only(env):
    _play(2, _ep(1), [0, 100_000], 1000, "k")
    out = run(wt.continue_text("kid", "", "de"))
    assert "KTSB weiß" in out and "nicht verfügbar" in out


def test_skipped_and_mark(env):
    _play(1, _ep(1), [0, 300_000], 1000, "a")
    _play(1, _ep(2), [0], 2000, "b")
    out = run(wt.skipped_text("userone", "", "nb"))
    assert "S01E01" in out and "[id:101]" in out
    assert "sett (brukerens eget ord)" in run(wt.mark_text("userone", "101", "watched", "nb"))
    assert "ingen hoppet-over" in run(wt.skipped_text("userone", "", "nb"))
    assert "Ukjent status" in run(wt.mark_text("userone", "101", "nope", "nb"))


def test_unknown_show_query(env):
    _play(1, _ep(1), [0], 1000, "a")
    assert "plex_search" in run(wt.continue_text("userone", "breaking", "en"))


def test_ktsb_started_play_is_marked(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "DB_PATH", tmp_path / "watch.db")
    pid, _ = ws.record_progress(
        account_id=1, session_key="s", item=_ep(1), state="playing", offset_ms=0, now=1
    )
    ws.mark_source(pid, "ktsb", "userone")
    with ws._lock, ws._conn() as conn:
        row = conn.execute("SELECT source, ktsb_user FROM plays WHERE id=?", (pid,)).fetchone()
    assert (row["source"], row["ktsb_user"]) == ("ktsb", "userone")
    pl.expect_ktsb_start("101", "userone")
    assert "101" in pl._expected
