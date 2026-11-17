"""P139 phase 4 — Plex in one round: the code finds the item and the screen, plays over KTSB's own Cast (confirmed by the
Plex server), falls back to Home Assistant's Plex→Cast path."""

import asyncio

import httpx
import pytest
import yaml

from adapters import plex_adapter as pa
from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em

# ── finding the item ──────────────────────────────────────────────────────────

HUBS = {"MediaContainer": {"Hub": [
    {"type": "show", "Metadata": [{"title": "Futurama", "ratingKey": "16051", "year": 1999}]},
    {"type": "movie", "Metadata": [{"title": "Futurama: Into the Wild Green Yonder", "ratingKey": "15651", "year": 2009},
                                   {"title": "Futurama: Bender's Game", "ratingKey": "15650", "year": 2008}]},
]}}
LEAVES = {"MediaContainer": {"Metadata": [
    {"ratingKey": "16185", "parentIndex": 1, "index": 1, "title": "Space Pilot 3000"},
    {"ratingKey": "16186", "parentIndex": 1, "index": 2, "title": "The Series Has Landed"},
]}}


@pytest.fixture
def plex(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/hubs/search":
            return httpx.Response(200, json=HUBS)
        if req.url.path.endswith("/allLeaves"):
            return httpx.Response(200, json=LEAVES)
        return httpx.Response(404)

    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(pa.httpx, "AsyncClient", client)
    monkeypatch.setattr(pa, "_plex_cfg", lambda: {"url": "http://plex.test:32400"})
    monkeypatch.setattr(pa, "_token", lambda: "tok")


def test_show_season_episode_gives_the_episode(plex):
    hit = asyncio.run(pa.find_item("Futurama", 1, 1))
    assert hit["rating_key"] == "16185" and "S01E01" in hit["label"] and "Space Pilot 3000" in hit["label"]


def test_exact_show_title_wins_over_films_containing_it(plex):
    assert asyncio.run(pa.find_item("Futurama")) == {"show": "Futurama"}


def test_exact_film_title(plex):
    assert asyncio.run(pa.find_item("Futurama: Into the Wild Green Yonder"))["rating_key"] == "15651"


def test_missing_episode_is_empty(plex):
    assert asyncio.run(pa.find_item("Futurama", 9, 9)) == {}


# ── the tool: own Cast first, server-confirmed; HA fallback ───────────────────

NODES = {"example_tv": {"type": "android_tv", "host": "10.0.0.4", "entity_id": "media_player.tv",
                        "description": "thomson google tv", "channels": {"cast": {"enabled": True}}}}


@pytest.fixture
def tool(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: ("", ""))
    monkeypatch.setattr(em, "get_lang", lambda uid: "en")

    async def find(q, season=None, episode=None):
        return {"rating_key": "16185", "label": "Futurama S01E01", "type": "episode"} if season else {"show": "Futurama"}

    async def session(rk, offset=0, resume=False):
        return {"protocol": "http", "address": "p", "port": 32400, "token": "t", "machine_id": "m",
                "play_queue_id": 1, "key": f"/library/metadata/{rk}"}

    monkeypatch.setattr(em, "_plex_find_item", find)
    monkeypatch.setattr(em, "_plex_cast_session", session)
    monkeypatch.setattr(em.plex_listener, "expect_ktsb_start", lambda *a, **k: None)
    calls = {"bridge": [], "ha": []}

    async def play_ha(entity_id, ctype, cid):
        calls["ha"].append(entity_id)
        return "ok"

    async def meta(key):
        return {"type": "episode", "library_name": "TV", "show_name": "Futurama", "season_number": 1, "episode_number": 1}

    monkeypatch.setattr(em, "_play_media_via_ha", play_ha)
    monkeypatch.setattr(em, "_plex_get_metadata", meta)

    def bridge(result):
        async def call(path, payload, timeout=60.0):
            calls["bridge"].append((path, payload["host"]))
            return result

        monkeypatch.setattr(em.cast_client, "call", call)

    return calls, bridge


def _run(args):
    return asyncio.run(em.dispatch("media", {"action": "plex_play", "_user_id": "en", **args}))


def test_one_round_own_cast_confirmed_by_the_server(tool, monkeypatch):
    calls, bridge = tool
    bridge({"ok": True, "after": {"app": "Plex", "state": "PLAYING"}})

    async def seen(rk):
        return {"state": "playing", "offset_s": 3, "player": "Plex Cast"}

    monkeypatch.setattr(em, "_plex_session_for", seen)
    out = _run({"query": "Futurama", "season": 1, "episode": 1, "client": "thomson"})
    assert calls["bridge"] == [("/cast/plex", "10.0.0.4")] and calls["ha"] == []
    assert out.startswith("OK:") and "Plex server confirms" in out


def test_device_says_playing_but_server_sees_nothing_is_not_ok(tool, monkeypatch):
    _calls, bridge = tool
    bridge({"ok": True, "after": {"app": "Plex", "state": "PLAYING"}})

    async def seen(rk):
        return {}

    async def fast(rk, limit=20.0):
        return await seen(rk)

    monkeypatch.setattr(em, "_plex_confirm", fast)
    out = _run({"query": "Futurama", "season": 1, "episode": 1, "client": "thomson"})
    assert out.startswith("SENT:") and "has not reported playback" in out


def test_own_cast_failure_falls_back_to_ha(tool):
    calls, bridge = tool
    bridge({"ok": False, "error": "unreachable: timed out"})
    out = _run({"rating_key": "16185", "client": "thomson"})
    assert calls["ha"] == ["media_player.tv"] and out.startswith("SENT:") and "unreachable" in out


def test_show_without_episode_asks(tool):
    calls, bridge = tool
    bridge({"ok": True})
    out = _run({"query": "Futurama", "client": "thomson"})
    assert "season and episode" in out and calls["bridge"] == []
