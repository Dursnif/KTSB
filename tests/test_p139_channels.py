"""P139 phase 2 — channels per node: one node = one device, found by probing, picked per job, migrated from type."""

import asyncio

import pytest
import yaml

from adapters.display import registry as reg_mod
from kaare_core import config_migrations as cm
from kaare_core.routers import router_settings as rs
from kaare_core.voice import channels as ch
from kaare_core.voice import node_status as ns

THOMSON = {"type": "android_tv", "host": "10.0.0.4", "tvoverlay_port": 5001, "has_display": True}


# ── pure rules ────────────────────────────────────────────────────────────────


def test_legacy_channels_follow_the_old_type_map():
    assert ch.legacy_channels(THOMSON) == {"tvoverlay": {"enabled": True, "port": 5001}}
    assert ch.legacy_channels({"type": "samsung_tv", "entity_id": "media_player.s"}) == {
        "dlna": {"enabled": True},
        "ha": {"enabled": True},
    }
    assert ch.legacy_channels({"type": "fire_tv"}) == {}


def test_ha_channel_needs_an_entity():
    node = {"type": "android_tv", "channels": {"ha": {"enabled": True}, "cast": {"enabled": True}}}
    assert set(ch.node_channels(node)) == {"cast"}
    assert set(ch.node_channels({**node, "entity_id": "media_player.tv"})) == {"cast", "ha"}


def test_order_per_job_skips_disabled_and_driverless():
    node = {
        "type": "android_tv",
        "entity_id": "media_player.tv",
        "channels": {
            "tvoverlay": {"enabled": True},
            "cast": {"enabled": True},
            "atv_remote": {"enabled": True},  # a control channel — never used for notices/pictures
            "ha": {"enabled": False},
        },
    }
    assert ch.order_for("notice", node) == ["tvoverlay", "cast"]
    assert ch.order_for("picture", node) == ["cast", "tvoverlay"]


def test_merge_keeps_standby_channels_and_the_users_switch():
    node = {**THOMSON, "channels": {"tvoverlay": {"enabled": False, "port": 5001}}}
    out = ch.merge_found(node, {"cast": 8009, "atv_remote": 6466}, at="T1")
    assert out["tvoverlay"] == {"enabled": False, "port": 5001}  # did not answer (standby) → kept, still off
    assert out["cast"] == {"enabled": True, "found_at": "T1", "last_seen": "T1"}
    out2 = ch.merge_found({**node, "channels": out}, {"cast": 8009, "tvoverlay": 5001}, at="T2")
    assert out2["cast"]["found_at"] == "T1" and out2["cast"]["last_seen"] == "T2"
    assert out2["tvoverlay"]["enabled"] is False and out2["tvoverlay"]["last_seen"] == "T2"


def test_wants_channels():
    assert ch.wants_channels(THOMSON)
    assert ch.wants_channels({"type": "chromecast", "host": "h", "has_display": False})  # Cast speaker
    assert not ch.wants_channels({"type": "android_tv"})  # no address
    assert not ch.wants_channels({"type": "wyoming", "host": "h", "has_display": False})
    assert ch.wants_channels({"type": "ha_media_player", "host": "h", "has_display": False})  # Google Home with an IP
    assert not ch.wants_channels({"type": "ha_media_player", "entity_id": "media_player.x"})  # no address


def test_probe_reports_the_first_port_that_answers(monkeypatch):
    open_ports = {8009, 6466, 8002}

    async def fake_open(host, port):
        return port in open_ports

    monkeypatch.setattr(ch, "_open", fake_open)
    assert asyncio.run(ch.probe("h")) == {"cast": 8009, "atv_remote": 6466, "samsung_ws": 8002}
    assert asyncio.run(ch.probe("")) == {}


# ── display registry ──────────────────────────────────────────────────────────


class Provider:
    def __init__(self, name, ok, log):
        self.name, self.ok, self.log = name, ok, log

    async def send(self, cfg, **kw):
        self.log.append((self.name, cfg.get("tvoverlay_port")))
        return {"ok": True, "method": self.name} if self.ok else {"ok": False, "error": f"{self.name} down"}


def _registry(oks: dict[str, bool]):
    r = reg_mod.DisplayProviderRegistry.__new__(reg_mod.DisplayProviderRegistry)
    log: list = []
    r._by_channel = {n: Provider(n, oks.get(n, True), log) for n in ("tvoverlay", "cast", "dlna", "ha")}
    return r, log


def test_registry_falls_back_and_names_what_it_tried():
    r, log = _registry({"tvoverlay": False})
    node = {**THOMSON, "channels": {"tvoverlay": {"port": 7980}, "cast": {}}}
    out = asyncio.run(r.send("tv", node, text="hi"))
    assert out["ok"] and out["channel"] == "cast"
    assert out["tried"] == [{"channel": "tvoverlay", "error": "tvoverlay down"}]
    assert log[0] == ("tvoverlay", 7980)  # the channel's own port reaches the provider


def test_registry_picture_job_starts_with_cast():
    r, _log = _registry({})
    node = {**THOMSON, "channels": {"tvoverlay": {}, "cast": {}}}
    assert asyncio.run(r.send("tv", node, job="picture", text=""))["channel"] == "cast"


def test_registry_without_channels_says_so_and_lists_every_error():
    r, _log = _registry({"tvoverlay": False, "ha": False})
    assert asyncio.run(r.send("x", {"type": "wyoming"}, text=""))["error"] == "no display channel"
    out = asyncio.run(r.send("tv", {**THOMSON, "entity_id": "media_player.tv"}, text=""))
    assert not out["ok"] and out["error"] == "tvoverlay: tvoverlay down; ha: ha down"


# ── N29: online if any channel answers ────────────────────────────────────────


def test_status_lists_each_channel(monkeypatch):
    async def fake_probe(host, plan=None):
        return {"cast": 8009, "atv_remote": 6466}  # Google TV in standby: no TvOverlay

    monkeypatch.setattr(ns.channels, "probe", fake_probe)
    status, detail, per = asyncio.run(ns._channels({**THOMSON, "channels": {"tvoverlay": {}, "cast": {}}}, "h"))
    assert status == ns.ONLINE
    assert per == {"tvoverlay": False, "cast": True, "atv_remote": True}
    assert "tvoverlay ✗" in detail


# ── migration step 8 ──────────────────────────────────────────────────────────


def test_migration_step_writes_legacy_channels_once():
    data = {"nodes": {"tv": dict(THOMSON), "nest": {"type": "ha_media_player", "entity_id": "media_player.n"}}}
    notes = cm._s8_nodes_channels(data, {})
    assert data["nodes"]["tv"]["channels"] == {"tvoverlay": {"enabled": True, "port": 5001}}
    assert "channels" not in data["nodes"]["nest"]
    assert notes and "tv (tvoverlay)" in notes[0]
    assert cm._s8_nodes_channels(data, {}) == []  # idempotent
    step8 = next(s for s in cm.STEPS if s.number == 8)  # P144-s14 added step 9 after it
    assert step8.filename == "nodes.yaml"


def test_migration_run_on_a_copy(tmp_path):
    cfg = tmp_path / "configs"
    cfg.mkdir()
    (cfg / "nodes.yaml").write_text(yaml.safe_dump({"nodes": {"tv": dict(THOMSON)}}))
    (cfg / ".config_version").write_text(yaml.safe_dump({"steps": 7, "templates": "x"}))
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "nodes.yaml").write_text(yaml.safe_dump({"nodes": {}}))
    cm.run(cfg, defaults)
    out = yaml.safe_load((cfg / "nodes.yaml").read_text())
    assert out["nodes"]["tv"]["channels"] == {"tvoverlay": {"enabled": True, "port": 5001}}


@pytest.mark.parametrize("name", list(ch.CATALOGUE))
def test_every_channel_has_a_known_job(name):
    assert ch.CATALOGUE[name][1] <= {"notice", "picture", "control"}


# ── API: probe from the dialog, probe on save ─────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "get_service", lambda *a, **k: "http://127.0.0.1:9")
    calls: list[str] = []

    async def fake_probe(host, plan=None):
        calls.append(host)
        return {"cast": 8009}

    monkeypatch.setattr(rs.channels, "probe", fake_probe)

    def nodes_file(nodes):
        p = tmp_path / "nodes.yaml"
        p.write_text(yaml.safe_dump({"nodes": nodes}))
        monkeypatch.setattr(rs, "_NODES_PATH", p)
        return p

    return rs, calls, nodes_file


def test_probe_endpoint_writes_nothing(api):
    rs, calls, nodes_file = api
    p = nodes_file({"tv": dict(THOMSON)})
    before = p.read_text()
    out = asyncio.run(rs.api_probe_node_channels({"node": dict(THOMSON)}, None))
    assert out["found"] == {"cast": 8009} and set(out["channels"]) == {"tvoverlay", "cast"}
    assert p.read_text() == before and calls == ["10.0.0.4"]


def test_save_probes_only_new_or_moved_screens(api):
    rs, calls, nodes_file = api
    known = {**THOMSON, "channels": {"tvoverlay": {"enabled": True}}}
    p = nodes_file({"tv": known})
    asyncio.run(rs.api_put_nodes({"nodes": {"tv": known}}, None))
    assert calls == []  # same host, has channels → no probe on save
    asyncio.run(rs.api_put_nodes({"nodes": {"tv": {**known, "host": "10.0.0.99"}}}, None))
    assert calls == ["10.0.0.99"]
    assert set(yaml.safe_load(p.read_text())["nodes"]["tv"]["channels"]) == {"tvoverlay", "cast"}
