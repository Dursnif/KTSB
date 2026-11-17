"""P139 — fixes from the user's live test 2026-10-19: a TV is found through the place register (not only nodes with an
entity_id), media-player on/off is checked against HA's state, and Kåre is told which app is on screen."""

import asyncio

import httpx
import pytest
import yaml

import kaare_ha_gateway as gw
from kaare_core import rooms
from kaare_core.ha import action_truth as truth
from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em

HA = {
    "floors": {"kjeller": {"name": "Kjeller"}, "forste": {"name": "Første etasje"}},
    "areas": {
        "kjellerstue": {"name": "Kjellerstue", "floor_id": "kjeller"},
        "verksted": {"name": "Verksted", "floor_id": "kjeller"},
        "stue": {"name": "Stue", "floor_id": "forste"},
        "kjokken": {"name": "Kjøkken", "floor_id": "forste"},
    },
    "entities": {
        # restored + unavailable at snapshot time = «orphan», although the TV only had its power off
        "media_player.workshop_tv_2": {"name": "Verksted-TV 2", "area_id": "kjellerstue", "domain": "media_player",
                                       "orphan": True},
        "media_player.kjokken": {"name": "Kjøkken", "area_id": "kjokken", "domain": "media_player"},
    },
}
ROOMS = {
    "places": {"kjellerstue": {}, "verksted": {}, "stue": {"synonyms": ["stua"]}, "kjokken": {}},
    "things": {
        "media_player.workshop_tv_2": {"place": "kjellerstue"},
        "media_player.chromecast2592": {"place": "kjellerstue"},  # HA does not have it any more
        "media_player.kjokken": {"place": "kjokken"},
    },
}
NODES = {
    "example_tv": {"type": "android_tv", "entity_id": "", "host": "10.0.0.4", "place": "kjellerstue",
                   "description": "thomson google tv"},
}


@pytest.fixture
def reg(monkeypatch, tmp_path):
    r = rooms.build(ROOMS, HA)
    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: r)
    monkeypatch.setattr(rooms, "node_place", lambda nid, **k: ("kjellerstue", "") if nid == "example_tv" else ("", ""))
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh, "_load_aliases", lambda: {"aliases": {
        "tv verksted sittegruppe": "media_player.workshop_tv_2",
        "tv kjellerstue": "media_player.chromecast2592",  # left pointing at a player HA no longer has
    }})
    return r


# ── finding the screen ────────────────────────────────────────────────────────


def test_place_finds_the_tv_and_skips_what_ha_no_longer_has(reg):
    assert eh.resolve_screens("kjellerstue") == ([("media_player.workshop_tv_2", "Verksted-TV 2")], "")


def test_node_without_entity_resolves_through_its_place(reg):
    assert eh.resolve_screens("thomson")[0] == [("media_player.workshop_tv_2", "Verksted-TV 2")]


def test_alias_to_a_removed_player_falls_through_to_the_room(reg):
    assert eh.resolve_screens("tv kjellerstue")[0] == [("media_player.workshop_tv_2", "Verksted-TV 2")]


def test_alias_and_entity_id(reg):
    assert eh.resolve_screens("TV verksted sittegruppe")[0][0][0] == "media_player.workshop_tv_2"
    assert eh.resolve_screens("media_player.kjokken")[0][0][0] == "media_player.kjokken"


def test_room_without_a_player_offers_the_floor_but_marks_it(reg):
    found, other = eh.resolve_screens("stua")
    assert found == [("media_player.kjokken", "Kjøkken")] and other.startswith("Stue")


def test_plex_play_asks_before_using_a_screen_in_another_room(reg, monkeypatch):
    async def meta(key):
        raise AssertionError("must not get this far")

    monkeypatch.setattr(em, "_plex_get_metadata", meta)
    out = asyncio.run(em.dispatch("media", {"action": "plex_play", "rating_key": "1", "client": "stua",
                                            "_user_id": "u"}))
    assert "Kjøkken (media_player.kjokken)" in out


def test_plex_play_reaches_the_tv_by_room(reg, monkeypatch):
    played = []

    async def meta(key):
        return {"type": "episode", "library_name": "TV", "show_name": "Futurama", "season_number": 1,
                "episode_number": 1}

    async def play(entity_id, ha_type, content_id):
        played.append(entity_id)
        return "ok"

    monkeypatch.setattr(em, "_plex_get_metadata", meta)
    monkeypatch.setattr(em, "_play_media_via_ha", play)
    monkeypatch.setattr(em.plex_listener, "expect_ktsb_start", lambda *a, **k: None)
    asyncio.run(em.dispatch("media", {"action": "plex_play", "rating_key": "16185", "client": "kjellerstue",
                                      "_user_id": "u"}))
    assert played == ["media_player.workshop_tv_2"]


# ── on/off truth + what is on screen ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "pre", "expected"),
    [
        ("turn_on", "off", {"on", "idle", "playing", "paused", "buffering"}),
        ("turn_off", "paused", {"off", "standby"}),
        ("toggle", "playing", {"off", "standby"}),
        ("toggle", "unknown", None),
    ],
)
def test_player_on_off_expected_states(action, pre, expected):
    got = truth.expected_states(action, "media_player.tv", pre)
    assert (set(got) if got is not None else None) == expected


def test_gateway_waits_for_the_player_and_reports_the_app(monkeypatch):
    seq = [
        {"state": "off", "attributes": {}},
        {"state": "off", "attributes": {}},
        {"state": "paused", "attributes": {"app_name": "Default Media Receiver", "volume_level": 0.13}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/states/"):
            row = seq.pop(0) if len(seq) > 1 else seq[0]
            return httpx.Response(200, json={"entity_id": "media_player.tv", **row})
        return httpx.Response(200, json=[])

    monkeypatch.setattr(gw, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(gw, "HA_API_URL", "http://ha.test")
    monkeypatch.setattr(gw, "VERIFY_SECONDS", 0.5)
    monkeypatch.setattr(gw, "_VERIFY_POLL_S", 0.05)
    monkeypatch.setattr(gw, "ALIASES", {})
    out = gw.execute_ha_command({"action": "turn_on", "entity_id": "media_player.tv", "params": {}})
    assert out["outcome"] == truth.CONFIRMED and out["state"] == "paused"
    assert out["media"]["app_name"] == "Default Media Receiver"


def test_tool_text_names_the_app_and_says_nothing_is_playing(monkeypatch):
    reply = {"status": "ok", "outcome": "confirmed", "state": "paused",
             "media": {"app_name": "Default Media Receiver", "volume_level": 0.13}}
    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, json=reply))
        return real(*a, **k)

    monkeypatch.setattr(eh.httpx, "AsyncClient", client)
    out = asyncio.run(eh._control_entity("media_player.tv", "turn_on", lang="en"))
    assert out.startswith("OK:") and "Default Media Receiver" in out and "no film/show is playing" in out


def test_slow_ha_service_is_verified_by_state_not_a_500(monkeypatch):
    """HA's Cast turn_on outlasts our request timeout; HA still does it — read the state instead of failing."""
    seq = [{"state": "off", "attributes": {}}, {"state": "off", "attributes": {}},
           {"state": "paused", "attributes": {"app_name": "Default Media Receiver"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/services/"):
            raise httpx.ReadTimeout("timed out", request=request)
        row = seq.pop(0) if len(seq) > 1 else seq[0]
        return httpx.Response(200, json={"entity_id": "media_player.tv", **row})

    monkeypatch.setattr(gw, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(gw, "HA_API_URL", "http://ha.test")
    monkeypatch.setattr(gw, "VERIFY_SECONDS", 0.0)
    monkeypatch.setattr(gw, "SLOW_SERVICE_WAIT_S", 0.5)
    monkeypatch.setattr(gw, "_VERIFY_POLL_S", 0.05)
    monkeypatch.setattr(gw, "ALIASES", {})
    out = gw.execute_ha_command({"action": "turn_on", "entity_id": "media_player.tv", "params": {}})
    assert out["outcome"] == truth.CONFIRMED and out["state"] == "paused"
