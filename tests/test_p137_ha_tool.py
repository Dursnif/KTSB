"""P137 phase 3 — location-scoped ha_read / ha_control (kaare_core/tools/executor_ha.py)."""

import asyncio

import pytest

from kaare_core import rooms
from kaare_core.tools import executor_ha as ex

HA = {
    "floors": {"andre": {"name": "Andre etasje", "aliases": ["andre etg"]}},
    "areas": {"stue": {"name": "Stue", "floor_id": ""}, "soverom": {"name": "Soverom", "floor_id": "andre"},
              "loft": {"name": "Loft", "floor_id": "andre"}},
    "entities": {
        "light.taklys_stue": {"name": "Taklys stue", "area_id": "stue", "domain": "light"},
        "light.lampe_stue": {"name": "Lampe stue", "area_id": "stue", "domain": "light"},
        "light.ikke_valgt": {"name": "Ikke valgt", "area_id": "stue", "domain": "light"},
        "switch.kaffe": {"name": "Kaffe", "area_id": "stue", "domain": "switch"},
        "light.soverom": {"name": "Soverom tak", "area_id": "soverom", "domain": "light"},
        "light.loft": {"name": "Loft lys", "area_id": "loft", "domain": "light"},
    },
}
ROOMS = {
    "places": {"stue": {"synonyms": ["stua"]}},
    "things": {"light.taklys_stue": {"note": "over sofaen"}, "light.lampe_stue": {}, "switch.kaffe": {},
               "light.soverom": {}, "light.loft": {}},
}


@pytest.fixture
def setup(monkeypatch):
    reg = rooms.build(ROOMS, HA)
    calls = []

    async def fake_apply(entity_id, action, params):
        calls.append((entity_id, action, params))
        return (entity_id != "light.lampe_stue" or action != "set_color", "ok", {})

    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: reg)
    monkeypatch.setattr(rooms, "node_place", lambda node, **k: ("stue", "") if node == "stue_node" else ("", ""))
    monkeypatch.setattr(ex, "_apply_full", fake_apply)
    monkeypatch.setattr(ex, "_load_aliases", lambda: {"aliases": {"taklys": "light.taklys_stue"}})
    return calls


def run(name, **args):
    return asyncio.run(ex.dispatch(name, {"_user_id": "test", **args}))


def test_room_command_hits_only_registered_things_of_the_domain(setup):
    out = run("ha_control", action="turn_off", room="stua", domain="light")
    assert sorted(c[0] for c in setup) == ["light.lampe_stue", "light.taklys_stue"]  # not «ikke_valgt», not switch
    assert out.startswith("OK:") and "2 × light" in out


def test_default_domain_is_light(setup):
    run("ha_control", action="turn_on", room="stue")
    assert {c[0] for c in setup} == {"light.lampe_stue", "light.taklys_stue"}


def test_node_place_is_the_default_location(setup):
    run("ha_control", action="turn_off", _source_node="stue_node")
    assert {c[0] for c in setup} == {"light.lampe_stue", "light.taklys_stue"}


def test_no_place_and_no_node_asks(setup):
    out = run("ha_control", action="turn_off")
    assert setup == [] and "hvilket rom" in out


def test_unknown_place_never_falls_back_to_the_node(setup):
    out = run("ha_control", action="turn_off", room="månen", _source_node="stue_node")
    assert setup == [] and "månen" in out


def test_floor_command_covers_every_place_on_it(setup):
    run("ha_control", action="turn_off", floor="andre etg")
    assert {c[0] for c in setup} == {"light.soverom", "light.loft"}


def test_light_only_actions_force_light(setup):
    out = run("ha_control", action="set_color", room="stue", domain="switch", rgb_color=[255, 0, 0])
    assert {c[0] for c in setup} == {"light.lampe_stue", "light.taklys_stue"}
    assert "Feilet for: Lampe stue" in out


def test_explicit_entity_id_still_works_for_unregistered(monkeypatch, setup):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen.update(json)
            return Resp()

    monkeypatch.setattr(ex.httpx, "AsyncClient", Client)
    out = run("ha_control", action="turn_on", entity_id="light.ikke_valgt")
    assert seen["entity_id"] == "light.ikke_valgt" and out.startswith("OK:")


def test_room_devices_lists_registered_with_aliases_note_and_count(setup):
    out = run("ha_read", action="room_devices", room="stua")
    assert "Taklys stue — light.taklys_stue" in out
    assert "«taklys»" in out and "over sofaen" in out
    assert "Ikke valgt" not in out
    assert "1 ting her som ikke er lagt inn" in out


def test_room_devices_from_node(setup):
    out = run("ha_read", action="room_devices", _source_node="stue_node")
    assert out.startswith("Stue:") and "noden" in out


def test_room_list_shows_places_and_floors(setup):
    out = run("ha_read", action="room_list")
    assert "- Stue — 3" in out and "Andre etasje: Soverom, Loft" in out


def test_block_ha_write_still_blocks_location_commands(setup):
    out = run("ha_control", action="turn_off", room="stue", _block_ha_write=True)
    assert setup == [] and not out.startswith("OK")


def test_location_results_are_handed_to_the_router(setup):
    run("ha_control", action="turn_off", room="stue", _rid="r1")
    assert sorted(ex.pop_location_results("r1")) == [("light.lampe_stue", True, ""), ("light.taklys_stue", True, "")]
    assert ex.pop_location_results("r1") == []
