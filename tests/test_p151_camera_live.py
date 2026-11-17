"""P151 phase 4: Kåre's `camera live` / `live_stop` — the reply says what the screen really did (N26)."""

import asyncio

import pytest

from kaare_core.tools import executor_camera as ec

CAMS = [{"api_name": "cam_east", "friendly_name": "utekamera øst"},
        {"api_name": "door", "friendly_name": "ringeklokke"},
        {"api_name": "cam_nw", "friendly_name": "utekamera nordvest"}]


@pytest.fixture
def bridge(monkeypatch):
    calls: list = []
    replies: dict = {}

    async def call(path, payload, timeout=60):
        calls.append((path, payload))
        return replies.get(path, {"ok": False, "error": "no reply set"})

    async def cams():
        return CAMS

    async def displays():
        return [{"id": "tv_a"}, {"id": "tv_b"}]

    monkeypatch.setattr(ec.cast_client, "call", call)
    monkeypatch.setattr(ec, "get_cameras", cams)
    monkeypatch.setattr(ec, "get_display_nodes", displays)
    monkeypatch.setattr(ec, "_audit", lambda *a, **k: None)
    return calls, replies


def run(**args):
    return asyncio.run(ec._kamera({"_user_id": "u", **args}, "en"))


def test_live_ok_only_when_the_screen_confirms(bridge):
    calls, replies = bridge
    replies["/screen/camera"] = {"ok": True, "showing": True, "minutes": 2.0,
                                 "session": {"mode": "mse", "size": "640x360"}}
    out = run(action="live", camera="øst", screen="node:tv_a")
    assert out.startswith("OK:") and "screen confirms" in out and "straight from Frigate" in out and "2 min" in out
    assert calls == [("/screen/camera", {"node": "tv_a", "camera": "cam_east"})]


def test_live_minutes_override_is_passed(bridge):
    calls, replies = bridge
    replies["/screen/camera"] = {"ok": True, "showing": True, "minutes": 10.0, "session": {"mode": "fmp4"}}
    out = run(action="live", camera="door", screen="node:tv_a", minutes=10)
    assert calls[0][1]["minutes"] == 10.0 and "re-encoded by KTSB" in out and "10 min" in out


def test_live_sent_but_not_confirmed_is_never_ok(bridge):
    _, replies = bridge
    replies["/screen/camera"] = {"ok": True, "showing": False, "error": "the page did not report a picture in time"}
    out = run(action="live", camera="door", screen="node:tv_a")
    assert out.startswith("SENT, not confirmed") and "OK:" not in out


def test_live_switched_off(bridge):
    _, replies = bridge
    replies["/screen/camera"] = {"ok": False, "off": True}
    assert "switched off" in run(action="live", camera="door", screen="node:tv_a")


def test_live_not_paired_gives_the_setup_steps_and_why(bridge):
    _, replies = bridge
    replies["/screen/camera"] = {"ok": False, "error": "not_paired", "setup_url": "http://10.0.0.2:8011/screen/tv_b"}
    out = run(action="live", camera="door", screen="node:tv_b")
    assert "http://10.0.0.2:8011/screen/tv_b" in out and "four-digit code" in out and "Why:" in out


def test_live_ambiguous_camera_asks(bridge):
    calls, _ = bridge
    out = run(action="live", camera="utekamera", screen="node:tv_a")
    assert out.startswith("Which camera?") and "utekamera øst" in out and calls == []


def test_live_needs_one_screen(bridge, monkeypatch):
    calls, _ = bridge
    monkeypatch.setattr(ec, "node_image_targets", lambda src, displays: [])
    out = run(action="live", camera="door")
    assert out.startswith("Which screen?") and "tv_a" in out and calls == []


def test_live_stop_restores_and_names_the_input_caveat(bridge):
    _, replies = bridge
    replies["/screen/stop"] = {"ok": True, "stopped": True, "restored": {"ok": True, "needs_standby": False}}
    out = run(action="live_stop", screen="node:tv_a")
    assert "stopped on tv_a" in out and "closed" in out and "remote" in out


def test_live_stop_without_screen_stops_the_running_one(bridge):
    calls, replies = bridge
    replies["/screen/active"] = {"sessions": [{"node": "tv_b", "kind": "camera"}, {"node": "tv_a", "kind": "card"}]}
    replies["/screen/stop"] = {"ok": True, "stopped": True, "restored": {"needs_standby": True, "standby": {"x": 1}}}
    out = run(action="live_stop")
    assert ("/screen/stop", {"node": "tv_b"}) in calls and ("/screen/stop", {"node": "tv_a"}) not in calls
    assert "standby" in out


def test_live_stop_nothing_running(bridge):
    _, replies = bridge
    replies["/screen/active"] = {"sessions": []}
    assert "No live camera" in run(action="live_stop")


def test_match_camera():
    assert ec._match_camera("ringeklokke", CAMS) == ("door", "ringeklokke")
    assert ec._match_camera("cam_nw", CAMS) == ("cam_nw", "utekamera nordvest")
    assert len(ec._match_camera("utekamera", CAMS)) == 2
    assert ec._match_camera("", CAMS) == CAMS
