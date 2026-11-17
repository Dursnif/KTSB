"""P140 bug A (2026-10-26): position per screen — «fortsett der jeg var sist på Thomson-TV-en».

The user asked for where they were last *on one screen*; plex_continue only knew the person's newest play anywhere (a night
re-watch on the bedroom TV) and Kåre answered as if it were the Thomson. A play now carries the KTSB node it ran on
(from Plex's Player.address against nodes.yaml, or from plex_play), plus Plex's machineIdentifier so a screen is
still known after its IP changes.
"""

import asyncio
import sqlite3

import pytest

import adapters.plex_adapter as plex
from kaare_core.media import plex_listener as pl
from kaare_core.media import plex_watch_tool as wt
from kaare_core.media import watch_store as ws
from kaare_core.tools import executor_ha

SHOW = "500"
LEAVES = [
    {"ratingKey": f"1{n:02d}", "parentIndex": 1, "index": n, "title": f"Ep {n}"} for n in range(1, 13)
]


def _ep(n: int) -> dict:
    return {
        "rating_key": f"1{n:02d}",
        "type": "episode",
        "show_key": SHOW,
        "show_title": "Futurama",
        "season": 1,
        "episode": n,
        "title": f"Ep {n}",
        "duration_ms": 1_400_000,
        "credits_ms": 1_350_000,
    }


def _play(n, t0, session, *, screen="", player="Chromecast", player_id="", finished=True, path=None):
    offsets = [0, 1_351_000] if finished else [0, 600_000]
    for i, off in enumerate(offsets):
        ws.record_progress(
            account_id=1,
            session_key=session,
            item=_ep(n),
            state="playing",
            offset_ms=off,
            player=player,
            screen=screen,
            player_id=player_id,
            now=t0 + i * 10,
            path=path,
        )


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DB_PATH", tmp_path / "watch.db")
    ws.close_all()
    yield tmp_path / "watch.db"
    ws.close_all()


# --- store ---------------------------------------------------------------------------------------------------


def test_continue_point_per_screen(db):
    _play(9, 1000, "a", screen="example_tv")  # living room: E09 to the end
    _play(6, 5000, "b", screen="tv_bedroom", player="Tv-Bedroom")  # night re-watch: E06
    assert ws.continue_point(1, SHOW)["play"]["episode"] == 6  # the person, any screen — unchanged
    cp = ws.continue_point(1, SHOW, screen="example_tv")
    assert cp["kind"] == "next" and cp["play"]["episode"] == 9
    assert ws.continue_point(1, SHOW, screen="workshop_tv") is None


def test_rows_without_screen_match_by_player_name(db):
    # plays recorded before this fix have no screen; Plex's own app name «Tv-Bedroom» = node tv_bedroom
    _play(6, 5000, "b", player="Tv-Bedroom")
    assert ws.continue_point(1, SHOW, screen="tv_bedroom")["play"]["episode"] == 6


def test_player_id_keeps_the_screen_after_an_ip_change(db):
    _play(3, 1000, "a", screen="example_tv", player_id="m-1")
    _play(4, 2000, "b", player_id="m-1")  # same Plex client, its address no longer matched a node
    assert ws.screen_for_player_id("m-1") == "example_tv"
    assert ws.continue_point(1, SHOW, screen="example_tv")["play"]["episode"] == 4


def test_screen_is_filled_once_and_by_mark_source(db):
    pid, _ = ws.record_progress(account_id=1, session_key="s", item=_ep(1), state="playing", offset_ms=0, now=1)
    ws.mark_source(pid, "ktsb", "userone", screen="example_tv")
    ws.mark_source(pid, "ktsb", "userone", screen="other")  # never overwrites a known screen
    with ws._lock, ws._conn() as conn:
        assert conn.execute("SELECT screen FROM plays WHERE id=?", (pid,)).fetchone()["screen"] == "example_tv"


def test_latest_per_screen(db):
    _play(9, 1000, "a", screen="example_tv", finished=False)
    _play(6, 5000, "b", screen="tv_bedroom")
    out = {s: (p["episode"], status) for s, p, status in ws.latest_per_screen(1, SHOW)}
    assert out == {"example_tv": (9, "paused"), "tv_bedroom": (6, "watched")}


def test_old_database_gets_the_new_columns(db):
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE plays (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL, rating_key TEXT NOT"
        " NULL, item_type TEXT NOT NULL DEFAULT '', show_key TEXT NOT NULL DEFAULT '', show_title TEXT NOT NULL"
        " DEFAULT '', season INTEGER, episode INTEGER, title TEXT NOT NULL DEFAULT '', duration_ms INTEGER,"
        " credits_ms INTEGER, started_at REAL NOT NULL, last_seen_at REAL NOT NULL, last_offset_ms INTEGER NOT NULL"
        " DEFAULT 0, max_offset_ms INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT '', finished INTEGER NOT"
        " NULL DEFAULT 0, player TEXT NOT NULL DEFAULT '', ktsb_user TEXT NOT NULL DEFAULT '', room TEXT NOT NULL"
        " DEFAULT '', source TEXT NOT NULL DEFAULT 'live', session_key TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    conn.close()
    _play(2, 1000, "a", screen="example_tv", player_id="m-9")
    assert ws.continue_point(1, SHOW, screen="example_tv")["play"]["episode"] == 2


# --- node lookup ---------------------------------------------------------------------------------------------


def test_node_for_address(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(
        "nodes:\n  example_tv:\n    host: 192.0.2.189\n  off_tv:\n    host: 192.168.0.5\n    enabled: false\n"
        "  speaker:\n    type: snapcast\n"
    )
    monkeypatch.setattr(executor_ha, "_NODES_PATH", p)
    assert executor_ha.node_for_address("192.0.2.189") == "example_tv"
    assert executor_ha.node_for_address(" 192.0.2.189 ") == "example_tv"
    assert executor_ha.node_for_address("192.168.0.5") == ""  # disabled node
    assert executor_ha.node_for_address("") == ""


# --- listener ------------------------------------------------------------------------------------------------


def test_listener_records_screen_from_address_and_player_id(db, monkeypatch):
    monkeypatch.setattr(pl, "CACHE_PATH", db.parent / "plex_cache.json")
    events: list[dict] = []
    monkeypatch.setattr(pl.log_buffer, "write", lambda path, rec: events.append(rec))
    monkeypatch.setattr(pl.executor_ha, "node_for_address", lambda a: "example_tv" if a == "192.0.2.189" else "")
    pl._sessions.clear()
    pl._markers.clear()
    pl.set_user_lookup(lambda acc: "userone")
    session = {
        "sessionKey": "7", "ratingKey": "107", "type": "episode", "grandparentRatingKey": SHOW,
        "grandparentTitle": "Futurama", "parentIndex": 1, "index": 7, "title": "Ep 7", "duration": 1_400_000,
        "User": {"id": "1", "title": "owner"},
        "Player": {"title": "Chromecast", "address": "192.0.2.189", "machineIdentifier": "m-7", "state": "playing"},
    }

    def handler(request):
        if request.url.path == "/status/sessions":
            return pl.httpx.Response(200, json={"MediaContainer": {"Metadata": [session]}})
        return pl.httpx.Response(200, json={"MediaContainer": {"Metadata": [{}]}})

    async def go():
        async with pl.httpx.AsyncClient(transport=pl.httpx.MockTransport(handler)) as client:
            return await pl.handle_tick(client, "http://plex", {"sessionKey": "7", "ratingKey": "107",
                                                                "state": "playing", "viewOffset": 0})

    assert asyncio.run(go()) == "started"
    with ws._lock, ws._conn() as conn:
        row = conn.execute("SELECT screen, player_id FROM plays").fetchone()
    assert (row["screen"], row["player_id"]) == ("example_tv", "m-7")
    assert events[0]["screen"] == "example_tv"


def test_ktsb_start_carries_the_screen(db):
    pl.expect_ktsb_start("101", "userone", "example_tv")
    assert pl._expected["101"][2] == "example_tv"


# --- Kåre's tool ---------------------------------------------------------------------------------------------


@pytest.fixture
def tool(db, monkeypatch):
    monkeypatch.setattr(wt, "get_user", lambda u: {"plex_account_id": 2})  # non-owner: no On Deck call

    async def fake_fetch(path, params=None):
        assert path.endswith("/allLeaves"), path
        return {"Metadata": LEAVES}

    monkeypatch.setattr(wt.plex, "fetch", fake_fetch)
    screens = {"thomsen": [("example_tv", {})], "stua": [("example_tv", {}), ("nest_stue", {})], "verksted": [
        ("workshop_tv", {})]}
    monkeypatch.setattr(wt.executor_ha, "resolve_screen_nodes", lambda text: screens.get(text, []))
    monkeypatch.setattr(wt.executor_ha, "screen_names", lambda: {"example_tv": "example_tv"})  # never live nodes.yaml
    _play(9, 1000, "a", screen="example_tv", player="Chromecast")
    for n, t0 in ((5, 4000), (6, 5000)):
        _play(n, t0, f"b{n}", player="Tv-Bedroom")  # old rows: no screen, only Plex's app name


def _acc2(fn):
    # the fixture's plays are on account 1; the tool user is linked to account 2 → move them over
    with ws._lock, ws._conn() as conn:
        conn.execute("UPDATE plays SET account_id=2")
    return asyncio.run(fn)


def test_tool_continues_on_the_named_screen(tool):
    out = _acc2(wt.continue_text("userone", "futurama", "nb", client="thomsen"))
    assert "på example_tv" in out
    assert "sist S01E09" in out and "neste: S01E10" in out
    assert "Andre skjermer" in out and "Tv-Bedroom" in out and "S01E06" in out


def test_tool_says_plainly_when_the_screen_has_nothing(tool):
    out = _acc2(wt.continue_text("userone", "futurama", "nb", client="verksted"))
    assert "workshop_tv" in out and "ANNEN skjerm" in out
    assert "sist S01E06" in out  # the person's newest, labelled as another screen


def test_tool_asks_when_the_words_fit_several_screens(tool):
    out = _acc2(wt.continue_text("userone", "futurama", "en", client="stua"))
    assert "client=node:example_tv" in out and "client=node:nest_stue" in out


def test_tool_without_client_names_the_other_screens(tool):
    out = _acc2(wt.continue_text("userone", "futurama", "en"))
    assert "last S01E06" in out  # unchanged: the person's newest anywhere
    assert "Other screens" in out and "example_tv" in out and "S01E09" in out


def test_unknown_screen_words_fall_back_and_say_so(tool):
    out = _acc2(wt.continue_text("userone", "futurama", "de", client="garage"))
    assert "garage" in out and "S01E06" in out


# --- A2 (2026-10-26): Kåre must be able to see which screen ---------------------------------------------------
# Live test 13:58: Kåre used plex_sessions + plex_history, saw only «Chromecast» and answered with the bedroom
# re-watch again. Sessions now name the KTSB screen; history says it has none and points to plex_continue; old rows
# named like the node's HA player («Verksted-TV 2» = example_tv's media_player.workshop_tv_2) count for that node.

_REAL_CLIENT = pl.httpx.AsyncClient


def _mock_plex(monkeypatch, routes):
    def handler(request):
        return pl.httpx.Response(200, json={"MediaContainer": routes[request.url.path]})

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _REAL_CLIENT(*args, transport=pl.httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(plex.httpx, "AsyncClient", factory)
    monkeypatch.setattr(plex, "_plex_cfg", lambda: {"url": "http://pms", "timeout": 5})
    monkeypatch.setattr(plex, "_headers", dict)


def test_sessions_name_the_ktsb_screen(monkeypatch):
    _mock_plex(monkeypatch, {"/status/sessions": {"Metadata": [{
        "type": "episode", "grandparentTitle": "Futurama", "title": "My Three Suns", "parentIndex": 1, "index": 7,
        "viewOffset": 900_000, "duration": 1_350_000, "User": {"title": "owner"},
        "Player": {"title": "Chromecast", "address": "192.0.2.189", "state": "playing"}}]}})
    out = asyncio.run(plex.get_sessions("nb", screen_of=lambda a: "example_tv" if a == "192.0.2.189" else ""))
    assert "example_tv (Chromecast)" in out


def test_history_says_it_has_no_screen_and_points_to_plex_continue(monkeypatch):
    _mock_plex(monkeypatch, {
        "/accounts": {"Account": [{"id": 1, "name": "owner"}]},
        "/status/sessions/history/all": {"Metadata": [{"accountID": 1, "viewedAt": 1_793_000_000, "type": "episode",
                                                       "grandparentTitle": "Futurama", "title": "E6",
                                                       "parentIndex": 1, "index": 6}]},
    })
    out = asyncio.run(plex.get_history(None, 5, "nb"))
    assert "plex_continue" in out and "client" in out


def test_old_rows_named_like_the_nodes_ha_player_count_for_that_node(db):
    _play(8, 1000, "a", player="Verksted-TV 2")  # the Thomson's own Plex app, before screens were recorded
    names = {"example_tv": "example_tv", "workshop_tv_2": "example_tv", "Verksted-TV 2": "example_tv"}
    assert ws.continue_point(1, SHOW, screen="example_tv") is None  # without the node's names: unknown
    assert ws.continue_point(1, SHOW, screen="example_tv", names=names)["play"]["episode"] == 8
    assert [k for k, _p, _s in ws.latest_per_screen(1, SHOW, names=names)] == ["example_tv"]


def test_screen_names_from_nodes(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text("nodes:\n  example_tv:\n    host: 192.0.2.189\n    entity_id: media_player.workshop_tv_2\n")
    monkeypatch.setattr(executor_ha, "_NODES_PATH", p)
    monkeypatch.setattr(executor_ha.rooms, "get_register", lambda: type("R", (), {"name_of": lambda s, e: "Verksted-TV 2"})())
    names = executor_ha.screen_names()
    assert names["example_tv"] == names["workshop_tv_2"] == names["Verksted-TV 2"] == "example_tv"


def test_screen_names_work_without_ha(tmp_path, monkeypatch):
    # 2026-10-26: KTSB does not depend on HA — HA only adds data points
    p = tmp_path / "nodes.yaml"
    p.write_text("nodes:\n  example_tv:\n    host: 192.0.2.189\n    entity_id: media_player.workshop_tv_2\n")
    monkeypatch.setattr(executor_ha, "_NODES_PATH", p)

    def no_ha():
        raise RuntimeError("no HA")

    monkeypatch.setattr(executor_ha.rooms, "get_register", no_ha)
    assert executor_ha.screen_names() == {"example_tv": "example_tv", "workshop_tv_2": "example_tv"}
