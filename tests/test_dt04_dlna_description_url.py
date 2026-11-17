"""DT04 R0.1 — DLNA without SSDP: a description URL set on the node, or remembered after one successful discovery.

Docker Desktop (Windows/macOS) gets no SSDP reply inside a container, and a Samsung does not answer a unicast
MediaRenderer search at all (DT04-I1). TCP to the renderer's description URL works, so the provider tries, in order:
the URL the user set on the node (GUI) → the URL remembered from an earlier discovery → SSDP. A URL found by SSDP is
remembered per host, so later calls need no discovery.

async_upnp_client lives only in the voice venv; the tests run in the main venv, so the four modules dlna.py imports
are stubbed before the import (the tests patch `_play_at` and `async_search` anyway).
"""

import asyncio
import json
import sys
import types

import pytest


def _stub_upnp() -> None:
    if "async_upnp_client" in sys.modules:
        return
    names = {
        "async_upnp_client": {},
        "async_upnp_client.aiohttp": {"AiohttpRequester": object},
        "async_upnp_client.client_factory": {"UpnpFactory": object},
        "async_upnp_client.profiles": {},
        "async_upnp_client.profiles.dlna": {"DmrDevice": object},
        "async_upnp_client.search": {"async_search": None},
    }
    for name, attrs in names.items():
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


_stub_upnp()

from kaare_core.voice.providers import dlna  # noqa: E402

HOST = "192.0.2.10"  # RFC 5737 documentation address
DESC = f"http://{HOST}:9197/dmr"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake renderer: `ok_urls` play, everything else fails. SSDP answers with `ssdp` (list of (ST, LOCATION))."""
    state = {"played": [], "ok_urls": set(), "ssdp": [], "searches": 0}

    async def fake_play_at(description_url, host, url, title):
        state["played"].append(description_url)
        if description_url in state["ok_urls"]:
            return True, ""
        return False, "connection refused"

    async def fake_search(async_callback, timeout, target=None, search_target=None, **_kw):
        state["searches"] += 1
        for st, loc in state["ssdp"]:
            if search_target and st != search_target:
                continue
            await async_callback({"ST": st, "LOCATION": loc})

    monkeypatch.setattr(dlna, "_play_at", fake_play_at)
    monkeypatch.setattr(dlna, "async_search", fake_search)
    monkeypatch.setattr(dlna, "LOCATIONS_PATH", tmp_path / "dlna_locations.json")
    return state


def _run(coro):
    return asyncio.run(coro)


def test_configured_description_url_is_used_without_any_ssdp(env):
    env["ok_urls"].add(DESC)
    ok, err = _run(dlna.play_uri(HOST, "http://media/x.png", "t", 1, description_url=DESC))
    assert ok and err == ""
    assert env["played"] == [DESC]
    assert env["searches"] == 0


def test_a_url_found_by_ssdp_is_remembered_and_reused_without_ssdp(env):
    env["ssdp"] = [(dlna._RENDERER, DESC)]
    env["ok_urls"].add(DESC)
    ok, _ = _run(dlna.play_uri(HOST, "http://media/1.png", "t", 1))
    assert ok
    assert json.loads(dlna.LOCATIONS_PATH.read_text())[HOST] == DESC
    searches = env["searches"]
    env["ssdp"] = []  # inside Docker Desktop: SSDP gone
    ok, _ = _run(dlna.play_uri(HOST, "http://media/2.png", "t", 1))
    assert ok
    assert env["searches"] == searches  # no new discovery


def test_a_stale_remembered_url_falls_back_to_ssdp_and_is_replaced(env):
    dlna.LOCATIONS_PATH.write_text(json.dumps({HOST: f"http://{HOST}:1111/old"}))
    env["ssdp"] = [(dlna._RENDERER, DESC)]
    env["ok_urls"].add(DESC)
    ok, _ = _run(dlna.play_uri(HOST, "http://media/x.png", "t", 1))
    assert ok
    assert env["played"] == [f"http://{HOST}:1111/old", DESC]
    assert json.loads(dlna.LOCATIONS_PATH.read_text())[HOST] == DESC


def test_a_failing_configured_url_still_tries_discovery(env):
    env["ssdp"] = [(dlna._RENDERER, DESC)]
    env["ok_urls"].add(DESC)
    ok, _ = _run(dlna.play_uri(HOST, "http://media/x.png", "t", 1, description_url=f"http://{HOST}:2222/typo"))
    assert ok
    assert env["played"][-1] == DESC


def test_a_location_on_another_host_is_never_remembered(env):
    other = "http://198.51.100.7:9197/dmr"
    env["ssdp"] = [(dlna._RENDERER, other)]  # unicast answer pointing elsewhere
    env["ok_urls"].add(other)
    _run(dlna.play_uri(HOST, "http://media/x.png", "t", 1))
    assert not dlna.LOCATIONS_PATH.exists() or HOST not in json.loads(dlna.LOCATIONS_PATH.read_text())


def test_nothing_answers_gives_an_error_that_names_the_gui_field(env):
    ok, err = _run(dlna.play_uri(HOST, "http://media/x.png", "t", 1))
    assert not ok
    assert "description URL" in err


def test_description_url_of_reads_the_node_channel():
    node = {"host": HOST, "channels": {"dlna": {"enabled": True, "description_url": f"  {DESC} "}}}
    assert dlna.description_url_of(node) == DESC
    assert dlna.description_url_of({"host": HOST}) == ""
    assert dlna.description_url_of({"channels": {"dlna": "junk"}}) == ""
    assert dlna.description_url_of({"channels": {"dlna": {"description_url": "file:///etc/passwd"}}}) == ""


def test_an_unreadable_locations_file_is_ignored(env):
    dlna.LOCATIONS_PATH.write_text("{not json")
    env["ssdp"] = [(dlna._RENDERER, DESC)]
    env["ok_urls"].add(DESC)
    ok, _ = _run(dlna.play_uri(HOST, "http://media/x.png", "t", 1))
    assert ok
    assert json.loads(dlna.LOCATIONS_PATH.read_text())[HOST] == DESC
