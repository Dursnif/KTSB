"""P139 — which screen the words mean: a room with two screens (Nest Hub + TV) is told apart by the other words or
asked about, never guessed; a near spelling («Thomsen») finds the one clear node."""

import asyncio
import types

import pytest
import yaml

from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em

CAST = {"cast": {"enabled": True}}
NODES = {
    "hovedsoverom": {"type": "ha_media_player", "host": "10.0.0.8", "description": "Chromecast — hovedsoverom",
                     "entity_id": "media_player.bedroom_a_nest_hub", "channels": CAST},
    "tv_bedroom": {"type": "android_tv", "host": "10.0.0.9", "description": "Google TV Streamer – TV på hovedsoverom",
                        "entity_id": "media_player.tv_bedroom",
                        "channels": {**CAST, "atv_remote": {"enabled": True}}},
    "example_tv": {"type": "android_tv", "host": "10.0.0.4", "description": "thomson google tv",
                   "entity_id": "media_player.workshop_tv_2", "channels": CAST},
}
PLACES = {"hovedsoverom": "hovedsoverom", "tv_bedroom": "hovedsoverom", "example_tv": "kjellerstue"}
NAMES = {"media_player.bedroom_a_nest_hub": "bedroom a google nest hub",
         "media_player.tv_bedroom": "Tv-Hovedsoverom", "media_player.workshop_tv_2": "Verksted-TV 2"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)

    def resolve(text):
        return ("place", text.lower()) if text.lower() in ("hovedsoverom", "kjellerstue") else None

    reg = types.SimpleNamespace(resolve=resolve, find_in_text=lambda t: None, name_of=lambda e: NAMES.get(e, ""))
    monkeypatch.setattr(eh.rooms, "get_register", lambda *a, **k: reg)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: (PLACES.get(nid, ""), ""))


def node(text):
    hit = eh.resolve_cast_node(text)
    return hit[0] if hit else None


def test_room_with_two_screens_is_asked_not_guessed():
    assert node("hovedsoverom") is None and [n for n, _ in eh.cast_choices("hovedsoverom")] == ["hovedsoverom",
                                                                                               "tv_bedroom"]
    assert node("node:hovedsoverom") == "hovedsoverom" and node("node:nope") is None  # the handle «which one?» gives
    assert node("hovedsoverommet") is None and len(eh.cast_choices("hovedsoverommet")) == 2


def test_the_other_words_pick_the_screen():
    assert node("tv-en på hovedsoverommet") == "tv_bedroom"
    assert node("nest-en på hovedsoverommet") == "hovedsoverom"
    assert node("tv hovedsoverom") == "tv_bedroom"


def test_near_spelling_finds_the_one_clear_node():
    assert node("Thomsen TV") == "example_tv" and node("thomsen") == "example_tv"
    assert node("Thomas") is None and eh.cast_choices("Thomas") == []


def test_tools_ask_which_screen(monkeypatch):
    monkeypatch.setattr(em, "get_lang", lambda uid: "en")

    async def never(*a, **k):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(em.cast_client, "call", never)
    monkeypatch.setattr(em.youtube_adapter, "search", never)
    for args in ({"action": "youtube_play", "query": "x"}, {"action": "nrk_play", "nrk_kind": "radio", "nrk_id": "p1"}):
        out = asyncio.run(em.dispatch("media", {**args, "client": "hovedsoverom", "_user_id": "en"}))
        assert "client=node:hovedsoverom (Chromecast — hovedsoverom); client=node:tv_bedroom (Google TV" in out


def test_stop_takes_the_one_screen_that_plays(monkeypatch):
    monkeypatch.setattr(em, "get_lang", lambda uid: "en")
    calls = []

    async def call(path, payload, timeout=60.0):
        calls.append((path, payload["host"]))
        if path == "/cast/status":
            busy = payload["host"] == "10.0.0.8"
            return {"ok": True, "after": {"app": "YouTube" if busy else "Backdrop", "app_id": "233637DE" if busy else
                                          "E8C28D3C"}}
        return {"ok": True, "after": {"app": "Backdrop"}}

    monkeypatch.setattr(em.cast_client, "call", call)
    out = asyncio.run(em.dispatch("media", {"action": "cast_stop", "client": "hovedsoverom", "_user_id": "en"}))
    assert out.startswith("OK") and calls[-1] == ("/cast/stop", "10.0.0.8")
