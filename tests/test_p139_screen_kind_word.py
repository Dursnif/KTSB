"""P139 ## Bugs 2026-11-16 «"nest" does not find the Nest Hub in hovedsoverom» (s04 live test 10f, rid-1794851150647).

Kåre sent `screen="nest"`. The lookup answered «which one?» with only two of the house's three Nest Hubs: the matcher
stopped at the first stage that hit (node id / description — two nodes are described «Nest Hub med skjerm») and never
reached the stage that reads the node's HA player id / name, where the third one says it is a Nest
(`media_player.<room>_google_nest_hub`). A word that names the kind of device must find every node of that kind,
whichever field says it. Placeholders only; the register is built in the test (no live state)."""

import pytest
import yaml

from kaare_core import rooms
from kaare_core.tools import executor_ha as eh

HA = {
    "floors": {"forste": {"name": "Første etasje"}},
    "areas": {"rom_a": {"name": "Rom A", "floor_id": "forste"}, "rom_b": {"name": "Rom B", "floor_id": "forste"},
              "rom_c": {"name": "Rom C", "floor_id": "forste"}, "stue": {"name": "Stue", "floor_id": "forste"}},
    "entities": {
        "media_player.rom_a_google_nest_hub": {"name": "Rom A Google Nest Hub", "area_id": "rom_a",
                                               "domain": "media_player"},
        "media_player.nest_rom_b": {"name": "Nest rom B", "area_id": "rom_b", "domain": "media_player"},
        "media_player.nest_rom_c": {"name": "Nest rom C", "area_id": "rom_c", "domain": "media_player"},
        "media_player.stue_tv": {"name": "Stue TV", "area_id": "stue", "domain": "media_player"},
    },
}
ROOMS = {"places": {"rom_a": {}, "rom_b": {}, "rom_c": {}, "stue": {}}, "things": {}}
NODES = {
    # described as a Chromecast — only its HA player says «nest hub» (the house's case)
    "rom_a": {"type": "ha_media_player", "entity_id": "media_player.rom_a_google_nest_hub", "host": "10.0.0.1",
              "place": "rom_a", "description": "Chromecast — rom a", "has_display": True},
    "rom_b": {"type": "ha_media_player", "entity_id": "media_player.nest_rom_b", "place": "rom_b",
              "description": "Nest Hub med skjerm — rom b", "has_display": True},
    "rom_c": {"type": "ha_media_player", "entity_id": "media_player.nest_rom_c", "place": "rom_c",
              "description": "Nest Hub med skjerm — rom c", "has_display": True},
    "stue_tv": {"type": "android_tv", "entity_id": "media_player.stue_tv", "host": "10.0.0.4", "place": "stue",
                "description": "tv i stua", "has_display": True},
}


@pytest.fixture
def reg(monkeypatch, tmp_path):
    r = rooms.build(ROOMS, HA)
    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: r)
    monkeypatch.setattr(rooms, "node_place", lambda nid, **k: (NODES.get(nid, {}).get("place", ""), ""))
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    return r


def test_the_kind_word_offers_every_nest_whichever_field_names_it(reg):
    targets, choices = eh.resolve_display_node("nest", list(NODES))
    assert targets == []
    assert {nid for nid, _cfg in choices} == {"rom_a", "rom_b", "rom_c"}


def test_naming_the_room_still_picks_exactly_one(reg):
    assert eh.resolve_display_node("nest hub rom a", list(NODES))[0] == ["rom_a"]


def test_a_tv_is_not_dragged_in_by_the_kind_word(reg):
    _t, choices = eh.resolve_display_node("nest", list(NODES))
    assert "stue_tv" not in {nid for nid, _cfg in choices}
