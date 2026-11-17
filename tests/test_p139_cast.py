"""P139 phase 3 — KTSB's own Cast (voice bridge) first, HA as the fallback: pictures, web pages, NRK, speech."""

import asyncio
import base64
import sys
import types

import pytest
import yaml

from adapters.display import bridge_picture as bp
from kaare_core.tools import executor as ex
from kaare_core.tools import executor_camera as ec
from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em
from kaare_core.voice import cast_sender as cs
from kaare_core.voice.base import BaseVoiceProvider
from kaare_core.voice.providers.chromecast import CastFirstProvider

# ── cast_sender against a fake pychromecast ───────────────────────────────────


class FakeCast:
    def __init__(self, script):
        self.script = script  # list of snapshots the device reports, last repeats
        self.calls: list = []
        self.socket_client = types.SimpleNamespace(is_connected=True)
        self.media_controller = types.SimpleNamespace(
            play_media=lambda url, mime, title=None: self.calls.append(("play_media", url, mime)),
            status=None,
        )
        self._advance()

    def _advance(self):
        snap = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        self.status = types.SimpleNamespace(
            is_stand_by=snap.get("standby"), is_active_input=snap.get("active"), display_name=snap.get("app"),
            app_id=snap.get("app_id"), volume_level=0.13, volume_muted=False,
        )
        self.media_controller.status = types.SimpleNamespace(
            player_state=snap.get("state"), title=snap.get("title"), content_id=snap.get("content"),
        )

    def wait(self, timeout=None):
        pass

    def quit_app(self):
        self.calls.append(("quit",))

    def register_handler(self, h):
        pass

    def unregister_handler(self, h):
        pass


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        cast = FakeCast(script)
        monkeypatch.setattr(cs, "_casts", {"10.0.0.4": cast})
        monkeypatch.setattr(cs, "_POLL_S", 0.01)
        orig = cs._snapshot

        def snapshot(c):
            out = orig(c)
            c._advance()
            return out

        monkeypatch.setattr(cs, "_snapshot", snapshot)
        return cast

    return make


def test_picture_confirmed_when_the_receiver_reports_the_url(fake):
    url = "http://bridge/media/x.png"
    cast = fake([{"standby": True}, {"standby": False, "content": None}, {"standby": False, "content": url}])
    out = cs.show_picture("10.0.0.4", url)
    assert out["ok"] and out["before"]["standby"] is True and out["after"]["content"] == url
    assert cast.calls == [("play_media", url, "image/png")]


def test_nrk_waits_for_playing(fake, monkeypatch):
    fake([{"app": None}, {"app": "NRK TV", "state": "BUFFERING"}, {"app": "NRK TV", "state": "PLAYING", "title": "NRK1"}])
    played = []
    qp = types.ModuleType("pychromecast.quick_play")
    qp.quick_play = lambda cast, app, data, timeout: played.append((app, data))
    monkeypatch.setitem(sys.modules, "pychromecast.quick_play", qp)
    out = cs.play_nrk("10.0.0.4", "tv", "nrk1")
    assert out["ok"] and out["after"]["title"] == "NRK1" and played == [("nrktv", {"media_id": "nrk1"})]
    assert not cs.play_nrk("10.0.0.4", "podcast", "x")["ok"]


def test_unreachable_device_is_a_result_not_an_exception(monkeypatch):
    monkeypatch.setattr(cs, "_casts", {})

    def boom(host):
        raise OSError("No route to host")

    monkeypatch.setattr(cs, "_uuid_of", boom)
    out = cs.status("10.0.0.250")
    assert not out["ok"] and "unreachable" in out["error"]


def test_run_turns_a_hang_into_a_failed_result():
    out = asyncio.run(cs.run(lambda: __import__("time").sleep(1), timeout=0.05))
    assert not out["ok"] and "timed out" in out["error"]


# ── API side: picture through the bridge ──────────────────────────────────────


def test_text_card_is_a_png():
    png = bp.text_card("Kåre", "Hei! " * 40)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_picture_provider_sends_bytes_and_reads_the_answer(monkeypatch):
    seen = {}
    real = bp.httpx.AsyncClient

    def handler(req):
        seen.update(__import__("json").loads(req.content))
        seen["path"] = req.url.path
        return bp.httpx.Response(200, json={"ok": True, "after": {"content": "u"}})

    def client(*a, **k):
        k["transport"] = bp.httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(bp.httpx, "AsyncClient", client)
    monkeypatch.setattr(bp, "get_service", lambda *a: "http://bridge")
    out = asyncio.run(bp.BridgePictureProvider.__new__(type("P", (bp.BridgePictureProvider,), {"channel": "cast"}))
                      .send({"host": "10.0.0.4"}, text="hei", title="T"))
    assert out == {"ok": True, "method": "cast", "after": {"content": "u"}}
    assert seen["path"] == "/display/show" and seen["channel"] == "cast" and seen["host"] == "10.0.0.4"
    assert base64.b64decode(seen["image_b64"])[:4] == b"\x89PNG"  # no picture → text card


# ── Kåre's tools ──────────────────────────────────────────────────────────────

NODES = {
    "example_tv": {"type": "android_tv", "host": "10.0.0.4", "entity_id": "media_player.tv", "place": "kjellerstue",
                   "description": "thomson google tv", "channels": {"cast": {"enabled": True}}},
    "samsung": {"type": "samsung_tv", "host": "10.0.0.5", "channels": {"dlna": {"enabled": True}}},
}


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: (NODES.get(nid, {}).get("place", ""), ""))

    async def resolve_tv(name, season=None, episode=None):  # NRK's API stays out of the tests
        if name.lower() == "nrk1":
            return {"id": "nrk1", "kind": "live", "title": "NRK1", "subtitle": ""}
        if name.lower() == "dagsrevyen":
            return {"id": "NNFA19101926", "kind": "episode", "title": "Dagsrevyen", "subtitle": "I dag"}
        if name.lower() == "side om side":
            if season and episode:
                return {"id": "MUHH50000313", "kind": "episode", "title": "Side om side", "subtitle": "3."}
            return {"id": "", "kind": "needs_episode", "seasons": 10}
        return None

    monkeypatch.setattr(em.nrk_adapter, "resolve_tv", resolve_tv)
    return p


def test_cast_nodes_need_an_address_and_a_cast_channel(nodes):
    assert list(eh.cast_nodes()) == ["example_tv"]
    assert eh.resolve_cast_node("thomson")[0] == "example_tv"
    assert eh.resolve_cast_node("google tv")[0] == "example_tv"
    assert eh.resolve_cast_node("samsung") is None


def test_nrk_play_uses_own_cast_first(nodes, monkeypatch):
    calls = []

    async def call(path, payload, timeout=60.0):
        calls.append((path, payload))
        return {"ok": True, "after": {"app": "NRK TV", "title": "NRK1", "state": "PLAYING"}}

    monkeypatch.setattr(em.cast_client, "call", call)
    out = asyncio.run(em.dispatch("media", {"action": "nrk_play", "nrk_kind": "tv", "nrk_id": "NRK1",
                                            "client": "thomson", "_user_id": "u"}))
    assert calls == [("/cast/nrk", {"host": "10.0.0.4", "kind": "tv", "media_id": "nrk1"})]
    assert out.startswith("OK:") and "NRK1" in out


def test_nrk_play_falls_back_to_ha_and_says_so(nodes, monkeypatch):
    async def call(path, payload, timeout=60.0):
        return {"ok": False, "error": "unreachable: timed out"}

    ha = []

    async def play(entity_id, ctype, cid):
        ha.append((entity_id, ctype, cid))
        return "ok"

    monkeypatch.setattr(em.cast_client, "call", call)
    monkeypatch.setattr(em, "_play_media_via_ha", play)
    out = asyncio.run(em.dispatch("media", {"action": "nrk_play", "nrk_kind": "radio", "nrk_id": "p1",
                                            "client": "thomson", "_user_id": "en"}))
    assert ha == [("media_player.tv", "cast", '{"app_name": "nrkradio", "media_id": "p1"}')]
    assert out.startswith("SENT:") and "unreachable" in out


def test_nrk_play_rejects_bad_ids(nodes):
    out = asyncio.run(em.dispatch("media", {"action": "nrk_play", "nrk_kind": "tv", "nrk_id": "nrk1; rm -rf",
                                            "client": "thomson", "_user_id": "u"}))
    assert "nrk_kind" in out


def test_show_url(nodes, monkeypatch):
    calls = []

    async def call(path, payload, timeout=60.0):
        calls.append((path, payload))
        return {"ok": True, "after": {"app": "DashCast"}}

    monkeypatch.setattr(ex.cast_client, "call", call)
    run = lambda args: asyncio.run(ex._dispatch("announce", {"action": "show_url", "_user_id": "u", **args}))  # noqa: E731
    assert "http" in run({"url": "file:///etc/passwd", "target": "thomson"}) and calls == []
    assert "Cast" in run({"url": "https://example.com", "target": "samsung"})
    out = run({"url": "https://example.com", "target": "thomson"})
    assert out.startswith("OK:") and calls == [("/cast/url", {"host": "10.0.0.4", "url": "https://example.com"})]


# ── 3f: camera event on the screen ────────────────────────────────────────────


def test_camera_event_goes_to_the_users_screen_or_the_named_one(monkeypatch):
    sent = []

    async def displays():
        return [{"id": "example_tv"}, {"id": "tv_b"}]

    async def send(nid, **kw):
        sent.append((nid, kw["job"]))
        return {"ok": nid == "example_tv", "channel": "cast", "error": "dlna: no device"}

    monkeypatch.setattr(ec, "get_display_nodes", displays)
    monkeypatch.setattr(ec, "send_display", send)
    monkeypatch.setattr(ec, "node_image_targets", lambda node, ids: ["example_tv"] if node == "atom" else [])
    run = lambda args: asyncio.run(ec._show_on_screens(args, "aW1n", "a cat", "en"))  # noqa: E731
    assert run({"_source_node": "atom"}) == "The picture is shown on example_tv (via cast)." and sent == [("example_tv", "picture")]
    assert run({}) == ""  # chat with no node → chat only
    assert run({"screen": "none", "_source_node": "atom"}) == ""
    assert "Could not show" in run({"screen": "tv_b"})


# ── D7: speech to a Nest — own Cast first, HA fallback ────────────────────────


def test_cast_first_speech_falls_back_to_the_type_provider(tmp_path):
    class P(BaseVoiceProvider):
        def __init__(self, ok):
            self.ok, self.calls = ok, 0

        async def speak(self, wav_path, node_config):
            self.calls += 1
            return self.ok

        async def is_available(self, node_config):
            return self.ok

    wav = tmp_path / "a.wav"
    cast, ha = P(True), P(True)
    assert asyncio.run(CastFirstProvider(cast, ha).speak(wav, {})) and (cast.calls, ha.calls) == (1, 0)
    cast, ha = P(False), P(True)
    assert asyncio.run(CastFirstProvider(cast, ha).speak(wav, {"host": "h"})) and (cast.calls, ha.calls) == (1, 1)
    assert asyncio.run(CastFirstProvider(P(False), P(True)).is_available({}))


def test_cast_node_found_by_its_ha_player_name(nodes, monkeypatch):
    monkeypatch.setattr(eh.rooms, "get_register", lambda *a, **k: types.SimpleNamespace(
        resolve=lambda t: None, find_in_text=lambda t: None, name_of=lambda e: "Verksted-TV 2"))
    assert eh.resolve_cast_node("verksted")[0] == "example_tv"
    assert eh.resolve_cast_node("media_player.tv")[0] == "example_tv"
    assert eh.resolve_cast_node("garasje") is None


def test_youtube_on_a_cast_screen_waits_for_playing(fake, monkeypatch):
    fake([{"app": None}, {"app": "YouTube", "state": "BUFFERING"}, {"app": "YouTube", "state": "PLAYING"}])
    played = []
    qp = types.ModuleType("pychromecast.quick_play")
    qp.quick_play = lambda cast, app, data, timeout: played.append((app, data))
    monkeypatch.setitem(sys.modules, "pychromecast.quick_play", qp)
    out = cs.play_youtube("10.0.0.4", "l482T0yNkeo")
    assert out["ok"] and played == [("youtube", {"media_id": "l482T0yNkeo"})]


def test_stop_counts_the_nest_photo_frame_as_idle(fake):
    cast = fake([{"app": "YouTube", "app_id": "233637DE"}, {"app": "Backdrop", "app_id": "E8C28D3C"}])
    out = cs.stop("10.0.0.4")
    assert out["ok"] and cast.calls == [("quit",)] and out["after"]["app"] == "Backdrop"


def test_nrk_series_name_becomes_the_newest_episode(nodes, monkeypatch):
    calls = []

    async def call(path, payload, timeout=60.0):
        calls.append(payload["media_id"])
        return {"ok": True, "after": {"app": "NRK TV", "state": "PLAYING"}}

    monkeypatch.setattr(em.cast_client, "call", call)
    monkeypatch.setattr(em, "get_lang", lambda uid: "en")
    run = lambda **a: asyncio.run(em.dispatch("media", {"action": "nrk_play", "nrk_kind": "tv", "client": "thomson",  # noqa: E731
                                                         "_user_id": "en", **a}))
    out = run(nrk_id="Dagsrevyen")
    assert calls == ["NNFA19101926"] and "Picked at NRK: Dagsrevyen — I dag" in out
    assert "season and episode" in run(nrk_id="Side om side") and calls == ["NNFA19101926"]
    run(nrk_id="Side om side", season=1, episode=3)
    assert calls[-1] == "MUHH50000313"
    assert "nothing playable" in run(nrk_id="tull og tøys")


def test_clear_picture_closes_only_our_picture(fake):
    url = "http://bridge/media/a.png"
    cast = fake([{"app": "Default Media Receiver", "app_id": "CC1AD845", "content": url}, {"app": None}])
    assert cs.clear_picture("10.0.0.4", url)["ok"] and cast.calls == [("quit",)]
    cast = fake([{"app": "YouTube", "app_id": "233637DE", "content": "yt"}])
    out = cs.clear_picture("10.0.0.4", url)
    assert out["skipped"] and cast.calls == []  # something else started since — left alone


def test_snapshot_with_screen_shows_the_fresh_picture(monkeypatch):
    shown = []

    async def snap(cam):
        return "aW1n"

    async def vlm(prompt, images):
        return {"ok": True, "text": "the porch at night"}

    async def show(arguments, img_b64, caption, lang):
        shown.append((arguments["screen"], img_b64, ec._duration(arguments)))
        return "The picture is shown on example_tv (via cast)."

    monkeypatch.setattr(ec, "fetch_snapshot_b64", snap)
    monkeypatch.setattr(ec, "ask_llm", vlm)
    monkeypatch.setattr(ec, "_show_on_screens", show)
    monkeypatch.setattr(ec, "_audit", lambda *a, **k: None)  # keep the live audit log clean
    out = asyncio.run(ec.dispatch("camera", {"action": "snapshot", "camera": "ringeklokke", "screen": "thomson",
                                             "_user_id": "u"}))
    assert shown == [("thomson", "aW1n", 30)] and "shown on example_tv" in out
    shown.clear()
    asyncio.run(ec.dispatch("camera", {"action": "snapshot", "camera": "ringeklokke", "_user_id": "u"}))
    assert shown == []  # no screen named → chat only
