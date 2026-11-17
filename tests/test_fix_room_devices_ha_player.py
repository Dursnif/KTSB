"""FIX 2026-11-16 (s04, approved by kaare-d7) — room_devices shows a node's own HA player, so Kåre can reach it.

Live twice the same day: «sett volumet på Nest Hub-en på hovedsoverommet til 10 %» (rid-1794858534528) — room_devices
listed `node:hovedsoverom [KTSB node: screen, speaker]` with no way to reach it; Kåre spent 2 minutes on Mechanic greps
and tried an invented entity before he found the right one. On Windows (DT04-S05h-b) the same ask took 315 s.
Now the node line names the HA player **set in the node's own config** — never a guess — and only when HA knows it.
Placeholders only; the register is built in the test."""

import asyncio

import pytest
import yaml

from kaare_core import rooms
from kaare_core.tools import executor_ha as ex
from kaare_core.tools.i18n import t

HA = {
    "floors": {"andre": {"name": "Andre etasje", "aliases": []}},
    "areas": {"rom_a": {"name": "Rom A", "floor_id": "andre"}},
    "entities": {"media_player.rom_a_google_nest_hub": {"name": "Rom A Nest Hub", "area_id": "rom_a",
                                                        "domain": "media_player"}},
}
ROOMS = {"places": {"rom_a": {}}, "things": {}}
NODES = {"nodes": {
    "rom_a": {"description": "Chromecast — rom a", "type": "ha_media_player", "host": "10.0.0.8", "place": "rom_a",
              "entity_id": "media_player.rom_a_google_nest_hub", "has_display": True, "has_audio": True,
              "enabled": True, "channels": {"cast": {"enabled": True}, "ha": {"enabled": True}}},
    "gone_a": {"description": "Gammel høyttaler", "type": "ha_media_player", "place": "rom_a",
               "entity_id": "media_player.removed_from_ha", "has_audio": True, "enabled": True},
    "bare_a": {"description": "ESP-høyttaler", "type": "esp32", "host": "10.0.0.9", "place": "rom_a",
               "has_audio": True, "enabled": True},
}}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    reg = rooms.build(ROOMS, HA)
    path = tmp_path / "nodes.yaml"
    path.write_text(yaml.safe_dump(NODES, allow_unicode=True))
    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: reg)
    monkeypatch.setattr(ex, "_NODES_PATH", path)
    monkeypatch.setattr(ex, "_load_aliases", lambda: {"aliases": {}})


def _lines(out: str) -> dict[str, str]:
    return {nid: next(line for line in out.splitlines() if f"node:{nid}" in line) for nid in ("rom_a", "gone_a", "bare_a")}


def test_the_nest_node_line_names_its_own_ha_player(setup):
    out = asyncio.run(ex.dispatch("ha_read", {"_user_id": "test", "action": "room_devices", "room": "rom a"}))
    line = _lines(out)["rom_a"]
    assert t("ha_node_ha_player", "nb", entity="media_player.rom_a_google_nest_hub") in line


def test_a_player_ha_does_not_have_is_not_offered(setup):
    out = asyncio.run(ex.dispatch("ha_read", {"_user_id": "test", "action": "room_devices", "room": "rom a"}))
    assert "media_player.removed_from_ha" not in _lines(out)["gone_a"]


def test_a_node_without_entity_gets_nothing_invented(setup):
    out = asyncio.run(ex.dispatch("ha_read", {"_user_id": "test", "action": "room_devices", "room": "rom a"}))
    assert "media_player." not in _lines(out)["bare_a"]


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_text_in_three_languages(lang):
    assert "media_player.x" in t("ha_node_ha_player", lang, entity="media_player.x")
