"""FIX 2026-11-16 — ha_read room_devices lists KTSB's own nodes at the place, not only HA things.

Live: «skru på Samsung-TV-en i stua» — room_devices showed only HA lights/sensors, the node `samsung_stue`
(place stue, remote channel samsung_ws) was missing, so Kåre never tried `media tv_power` and invented an entity.
"""

import asyncio

import pytest
import yaml

from kaare_core import rooms
from kaare_core.tools import executor_ha as ex

HA = {
    "floors": {"forste": {"name": "Første etasje", "aliases": []}},
    "areas": {"stue": {"name": "Stue", "floor_id": "forste"}, "kjokken": {"name": "Kjøkken", "floor_id": "forste"}},
    "entities": {"light.taklys_stue": {"name": "Taklys stue", "area_id": "stue", "domain": "light"}},
}
ROOMS = {"places": {"stue": {"synonyms": ["stua"]}}, "things": {"light.taklys_stue": {}}}
NODES = {
    "nodes": {
        "tv_stue": {
            "description": "Samsung TV — stue", "type": "samsung_tv", "host": "10.0.0.5", "place": "stue",
            "has_display": True, "enabled": True,
            "channels": {"dlna": {"enabled": True}, "samsung_ws": {"enabled": True}},
        },
        "old_stue": {"description": "Gammel skjerm", "type": "tvoverlay", "host": "10.0.0.6", "place": "stue",
                     "has_display": True, "enabled": False},
        "speaker_kjokken": {"description": "Høyttaler kjøkken", "type": "esp32", "host": "10.0.0.7",
                            "place": "kjokken", "has_audio": True, "enabled": True},
    }
}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    reg = rooms.build(ROOMS, HA)
    path = tmp_path / "nodes.yaml"
    path.write_text(yaml.safe_dump(NODES, allow_unicode=True))
    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: reg)
    monkeypatch.setattr(ex, "_NODES_PATH", path)
    monkeypatch.setattr(ex, "_load_aliases", lambda: {"aliases": {}})


def run(**args):
    return asyncio.run(ex.dispatch("ha_read", {"_user_id": "test", "action": "room_devices", **args}))


def test_room_lists_the_ktsb_tv_node_with_its_remote_tool(setup):
    out = run(room="stua")
    assert "Taklys stue — light.taklys_stue" in out
    assert "Samsung TV — stue" in out and "node:tv_stue" in out
    assert "tv_power" in out  # the remote channel tells Kåre which tool turns it on
    assert "Gammel skjerm" not in out  # disabled node
    assert "Høyttaler kjøkken" not in out  # other place
    assert out.startswith("Stue (Første etasje): 2 ting")


def test_floor_lists_nodes_of_every_place_on_it(setup):
    out = run(floor="forste")
    assert "node:tv_stue" in out and "node:speaker_kjokken" in out
    assert "tv_power" in out.split("node:tv_stue")[1].split("\n")[0]
    assert "tv_power" not in out.split("node:speaker_kjokken")[1].split("\n")[0]


def test_no_nodes_file_keeps_ha_only_output(setup, monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "_NODES_PATH", tmp_path / "missing.yaml")
    out = run(room="stua")
    assert "node:" not in out and out.startswith("Stue (Første etasje): 1 ting")
