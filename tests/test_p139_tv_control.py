"""P139 phase 5 — TV remote control: one executor (D4), Android TV Remote + Samsung drivers, before/after truth,
origin logged, pairing secrets in the bridge only."""

import asyncio

import pytest
import yaml

from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em
from kaare_core.voice import tv_control as tc

LINKS = {"https://www.youtube.com": "com.google.android.youtube.tv", "vnd.example://x": "com.example"}
ATV = {"host": "10.0.0.4", "channels": {"atv_remote": {"enabled": True}}}


class FakeRemote:
    def __init__(self, on=True, volume=5, muted=False, app="launcher"):
        self.is_on, self.current_app = on, app
        self.volume_info = {"level": volume, "max": 100, "muted": muted}
        self.sent: list = []

    def send_key_command(self, code):
        self.sent.append(code)
        if code == "POWER":
            self.is_on = not self.is_on
        elif code == "VOLUME_UP":
            self.volume_info = {**self.volume_info, "level": self.volume_info["level"] + 1}
        elif code == "VOLUME_MUTE":
            self.volume_info = {**self.volume_info, "muted": not self.volume_info["muted"]}

    def send_launch_app_command(self, target):
        self.sent.append(("app", target))
        if target in LINKS:  # a link the TV can open; anything else changes nothing (like the Thomson)
            self.current_app = LINKS[target]

    def disconnect(self):
        pass


@pytest.fixture
def atv(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PAIR_DIR", tmp_path / "pair")
    monkeypatch.setattr(tc, "ACTION_LOG", tmp_path / "tv.log")
    d = tc.secrets_dir("10.0.0.4")
    for f in ("cert.pem", "key.pem", "paired"):
        (d / f).write_text("x")
    remote = FakeRemote()

    async def connected(host):
        return remote

    monkeypatch.setattr(tc, "_atv_connected", connected)
    logged = []
    monkeypatch.setattr(tc.log_buffer, "write", lambda path, rec: logged.append(rec))
    return remote, logged


def run(action, value=""):
    return asyncio.run(tc.act("tv", ATV, action, value))


def test_power_reads_state_first_and_never_toggles_blind(atv):
    remote, logged = atv
    assert run("power_on")["outcome"] == "already" and remote.sent == []  # it is on: nothing sent
    out = run("power_off")
    assert out["outcome"] == "confirmed" and remote.sent == ["POWER"] and out["after"]["on"] is False
    assert logged[-1]["origin"] == "requested" and logged[-1]["action"] == "power_off"


def test_volume_and_mute_are_confirmed_by_read_back(atv):
    _remote, _ = atv
    out = run("volume_up")
    assert out["outcome"] == "confirmed" and (out["before"]["volume"], out["after"]["volume"]) == (5, 6)
    assert run("mute")["after"]["muted"] is True


def test_app_by_name_is_confirmed_by_the_foreground_app(atv):
    remote, _ = atv
    out = run("app", "YouTube")
    assert out["outcome"] == "confirmed" and remote.sent == [("app", "https://www.youtube.com")]
    assert run("app", "vnd.example://x")["outcome"] == "confirmed"  # any link: confirmed by a new foreground app
    assert run("app", "https://www.youtube.com")["after"]["app"] == "com.google.android.youtube.tv"  # known link


def test_falling_back_to_the_launcher_is_not_an_opened_app(atv, monkeypatch):
    remote, _ = atv
    monkeypatch.setattr(tc, "_atv_wait", lambda r, done, limit: asyncio.sleep(0, tc._atv_state(r)))
    remote.current_app = "com.plexapp.android"
    LINKS["late-home://x"] = "com.google.android.apps.tv.launcherx"
    try:
        assert run("app", "late-home://x")["outcome"] == "not_confirmed"
    finally:
        del LINKS["late-home://x"]


def test_app_never_sends_market_links_or_odd_input(atv):
    remote, _ = atv
    for bad in ("com.google.android.youtube.tv", "market://launch?id=x", "com.evil;rm", "plex://a; rm -rf /", ""):
        assert run("app", bad)["outcome"] == "unsupported", bad
    assert remote.sent == []


def test_unconfirmed_launch_drops_the_connection(atv, monkeypatch):
    remote, _ = atv
    tc._atv["10.0.0.4"] = remote
    monkeypatch.setattr(tc, "_atv_wait", lambda r, done, limit: asyncio.sleep(0, tc._atv_state(r)))
    assert run("app", "other://nothing")["outcome"] == "not_confirmed" and "10.0.0.4" not in tc._atv


def test_unknown_key_and_unpaired(atv, tmp_path):
    assert run("key", "self_destruct")["outcome"] == "unsupported"
    (tc.secrets_dir("10.0.0.4") / "paired").unlink()
    assert run("status")["outcome"] == "not_paired"


def test_no_remote_channel(atv):
    out = asyncio.run(tc.act("tv", {"host": "10.0.0.9", "channels": {"cast": {}}}, "power_on"))
    assert out["outcome"] == "unsupported"


# ── Samsung ───────────────────────────────────────────────────────────────────


@pytest.fixture
def samsung(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PAIR_DIR", tmp_path / "pair")
    (tc.secrets_dir("10.0.0.5") / "samsung_token.txt").write_text("123")
    state = {"power": None}
    keys, wol = [], []

    class TV:
        def send_key(self, k):
            keys.append(k)
            if k == "KEY_POWER":
                state["power"] = "on" if state["power"] == "standby" else "standby"

    monkeypatch.setattr(tc, "_samsung", lambda host, timeout=8: TV())
    monkeypatch.setattr(tc, "_samsung_state", lambda host: {"on": state["power"] == "on", "power": state["power"],
                                                             "volume": 10, "muted": False})

    def wake(mac, host=""):
        wol.append(mac)
        state["power"] = "on"

    monkeypatch.setattr(tc, "wake_on_lan", wake)
    monkeypatch.setattr(tc, "_port_open", lambda host, port, timeout=1.0: state["power"] is not None)
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    return state, keys, wol


def test_samsung_on_from_off_network_uses_wol_then_closes_the_bar(samsung):
    _state, keys, wol = samsung
    out = tc.samsung_act("10.0.0.5", "power_on", mac="aa:bb:cc:dd:ee:ff")
    assert out["outcome"] == "confirmed" and wol and keys == ["KEY_RETURN"]


def test_samsung_on_from_network_standby_uses_the_power_key(samsung):
    state, keys, wol = samsung
    state["power"] = "standby"
    out = tc.samsung_act("10.0.0.5", "power_on", mac="aa:bb:cc:dd:ee:ff")
    assert out["outcome"] == "confirmed" and not wol and keys == ["KEY_POWER", "KEY_RETURN"]


def test_samsung_needs_a_mac_to_wake(samsung):
    assert tc.samsung_act("10.0.0.5", "power_on")["outcome"] == "unsupported"


# ── the tool ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tool(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": {"example_tv": {**ATV, "description": "thomson google tv"}}}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: ("", ""))
    monkeypatch.setattr(em, "get_lang", lambda uid: "en")
    calls = []

    def bridge(result):
        async def call(path, payload, timeout=60.0):
            calls.append((path, payload))
            return result

        monkeypatch.setattr(em.cast_client, "call", call)

    return calls, bridge


def _tool(args):
    return asyncio.run(em.dispatch("media", {"_user_id": "u", **args}))


def test_tv_power_tool(tool):
    calls, bridge = tool
    bridge({"outcome": "confirmed", "after": {"on": True, "app": "launcher"}})
    out = _tool({"action": "tv_power", "tv_value": "on", "client": "thomson"})
    assert calls == [("/tv/act", {"node": "example_tv", "action": "power_on", "value": "on"})]
    assert out.startswith("OK:") and "the TV confirms" in out


def test_tv_tool_reports_not_paired_and_bad_value(tool):
    _calls, bridge = tool
    bridge({"outcome": "not_paired"})
    assert "not paired" in _tool({"action": "tv_mute", "client": "thomson"})
    assert "tv_value" in _tool({"action": "tv_volume", "tv_value": "sideways", "client": "thomson"})
    assert "No TV with a remote" in _tool({"action": "tv_mute", "client": "kitchen radio"})


def test_ha_status_adds_what_the_tv_itself_reports(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": {"example_tv": {**ATV, "entity_id": "media_player.tv"}}}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)

    async def ha(entity_id, lang="nb"):
        return "TV: off"

    async def call(path, payload, timeout=60.0):
        assert (path, payload["node"], payload["action"]) == ("/tv/act", "example_tv", "status")
        return {"ok": True, "after": {"on": True, "app": "launcher", "volume": 0}}

    monkeypatch.setattr(eh, "_read_ha_status", ha)
    monkeypatch.setattr(eh, "get_lang", lambda uid: "en")
    monkeypatch.setattr(eh.cast_client, "call", call)
    out = asyncio.run(eh.dispatch("ha_read", {"action": "status", "entity_id": "media_player.tv", "_user_id": "en"}))
    assert out.startswith("TV: off\n") and "on True, app launcher" in out and "wins over Home Assistant" in out
    assert asyncio.run(eh.dispatch("ha_read", {"action": "status", "entity_id": "light.x", "_user_id": "en"})) == "TV: off"
