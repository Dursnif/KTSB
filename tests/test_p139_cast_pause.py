"""P139 reopened #6 — pause / resume on a Cast screen without HA (D7: KTSB's own Cast first, HA as the fallback).

Before this the media tool had `cast_stop` only; pause/play existed only through HA (`ha_control media_pause`) or a TV's
own remote (`tv_key`). A Nest Hub that is not in HA could not be paused. Now `cast_pause` / `cast_play` go to the bridge
(`/cast/pause`, `/cast/play` → `cast_sender.pause/play`, confirmed by the device's own PAUSED / PLAYING, N26), HA's
media_pause / media_play as the fallback. No network: a fake pychromecast and a fake bridge client. Placeholders only."""

import asyncio
import types

import pytest
import yaml

from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em
from kaare_core.tools.definitions import get_tools
from kaare_core.voice import cast_sender as cs

NODES = {
    "example_tv": {"type": "android_tv", "host": "10.0.0.4", "entity_id": "media_player.tv", "place": "kjellerstue",
                   "description": "example google tv", "channels": {"cast": {"enabled": True}}},
}


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: (NODES.get(nid, {}).get("place", ""), ""))
    return p


class FakeMedia:
    def __init__(self, cast, state):
        self.cast, self.state = cast, state
        self.status = types.SimpleNamespace(player_state=state, title="Example", content_id="x")

    def _set(self, state):
        self.state = state
        self.status = types.SimpleNamespace(player_state=state, title="Example", content_id="x")

    def update_status(self, *a, **k):
        self.cast.calls.append(("update_status",))

    def pause(self):
        self.cast.calls.append(("pause",))
        self._set("PAUSED")

    def play(self):
        self.cast.calls.append(("play",))
        self._set("PLAYING")


class FakeCast:
    def __init__(self, state, app="Example App", app_id="ABCDEF12"):
        self.calls: list = []
        self.socket_client = types.SimpleNamespace(is_connected=True)
        self.status = types.SimpleNamespace(is_stand_by=False, is_active_input=True, display_name=app, app_id=app_id,
                                            volume_level=0.1, volume_muted=False)
        self.media_controller = FakeMedia(self, state)


@pytest.fixture
def fake(monkeypatch):
    def make(state, **kw):
        cast = FakeCast(state, **kw)
        monkeypatch.setattr(cs, "_casts", {"10.0.0.4": cast})
        monkeypatch.setattr(cs, "_POLL_S", 0.01)
        return cast

    return make


# ── cast_sender ──────────────────────────────────────────────────────────────

def test_pause_is_confirmed_by_the_device(fake):
    cast = fake("PLAYING")
    out = cs.pause("10.0.0.4")
    assert out["ok"] and ("pause",) in cast.calls and out["after"]["state"] == "PAUSED"


def test_play_resumes_and_is_confirmed(fake):
    cast = fake("PAUSED")
    out = cs.play("10.0.0.4")
    assert out["ok"] and ("play",) in cast.calls and out["after"]["state"] == "PLAYING"


def test_pause_with_nothing_playing_is_an_honest_failure(fake):
    cast = fake(None, app="Backdrop", app_id="E8C28D3C")
    out = cs.pause("10.0.0.4")
    assert not out["ok"] and ("pause",) not in cast.calls and out["error"]


# ── Kåre's media tool ────────────────────────────────────────────────────────

def test_cast_pause_uses_own_cast_first(nodes, monkeypatch):
    calls = []

    async def call(path, payload, timeout=60.0):
        calls.append((path, payload))
        return {"ok": True, "after": {"app": "Example App", "state": "PAUSED"}}

    monkeypatch.setattr(em.cast_client, "call", call)
    out = asyncio.run(em.dispatch("media", {"action": "cast_pause", "client": "example google tv", "_user_id": "u"}))
    assert calls == [("/cast/pause", {"host": "10.0.0.4"})]
    assert out.startswith("OK:")


def test_cast_play_falls_back_to_ha(nodes, monkeypatch):
    async def call(path, payload, timeout=60.0):
        return {"ok": False, "error": "unreachable: timed out"}

    ha = []

    async def service(entity, action):
        ha.append((entity, action))
        return "ok"

    monkeypatch.setattr(em.cast_client, "call", call)
    monkeypatch.setattr(em, "_ha_service_via_gateway", service)
    out = asyncio.run(em.dispatch("media", {"action": "cast_play", "client": "example google tv", "_user_id": "en"}))
    assert ha == [("media_player.tv", "media_play")]
    assert out.startswith("SENT:") and "unreachable" in out


def test_media_schema_offers_pause_and_play():
    media = next(t for t in get_tools("en") if t["function"]["name"] == "media")
    enum = media["function"]["parameters"]["properties"]["action"]["enum"]
    assert "cast_pause" in enum and "cast_play" in enum
