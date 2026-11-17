"""P137 — the place register (kaare_core/rooms.py) and its migration step (config_migrations step 7)."""

import json
from pathlib import Path

import yaml

from kaare_core import config_migrations as cm
from kaare_core import rooms

HA = {
    "floors": {"forste": {"name": "Første etasje", "level": 1, "aliases": []},
               "andre": {"name": "Andre etasje", "level": 2, "aliases": ["andre etg", "oppe"]}},
    "areas": {
        "stue": {"name": "Stue", "floor_id": "forste", "aliases": []},
        "kjokken": {"name": "Kjøkken", "floor_id": "forste", "aliases": []},
        "spisestue": {"name": "Spisestue", "floor_id": "forste", "aliases": []},
        "soverom": {"name": "Soverom", "floor_id": "andre", "aliases": [], "labels": ["ktsb"]},
    },
    "entities": {
        "light.taklys_stue": {"name": "Taklys stue", "area_id": "stue", "domain": "light", "exposed": True},
        "light.stalampe": {"name": "Stålampe", "area_id": "stue", "domain": "light", "exposed": False},
        "vacuum.q5_pro": {"name": "Q5 Pro", "area_id": "spisestue", "domain": "vacuum", "exposed": True},
        "sensor.diag": {"name": "Diag", "area_id": "stue", "domain": "sensor", "category": True, "exposed": True},
        "light.soverom": {"name": "Soverom tak", "area_id": "soverom", "domain": "light", "labels": [],
                          "area_labels": ["ktsb"]},
        "light.uten_rom": {"name": "Uten rom", "area_id": "", "domain": "light", "exposed": True},
    },
}


def test_ha_places_and_floors_with_ktsb_synonyms():
    reg = rooms.build({"places": {"stue": {"synonyms": ["stua", "dagligstue"]}}}, HA)
    assert reg.places["stue"].source == "ha"
    assert reg.places["stue"].name == "Stue"  # HA name, never overridden
    assert reg.resolve("stua") == ("place", "stue")
    assert reg.resolve("Kjøkken") == ("place", "kjokken")
    assert reg.resolve("andre etg") == ("floor", "andre")
    assert reg.find_in_text("skru av lysene i andre etg") == ("floor", "andre")


def test_manual_place_and_manual_thing_without_ha():
    reg = rooms.build({"places": {"bod": {"name": "Bod", "synonyms": ["boden"]}},
                       "things": {"mqtt:zigbee2mqtt/bod_dor": {"place": "bod", "note": "ved døra"}}}, {})
    assert reg.places["bod"].source == "manual"
    t = reg.things["mqtt:zigbee2mqtt/bod_dor"]
    assert (t.source, t.place, t.note) == ("mqtt", "bod", "ved døra")
    assert [x.ref for x in reg.things_at(place="bod")] == ["mqtt:zigbee2mqtt/bod_dor"]


def test_ha_wins_placement_of_ha_things():
    # KTSB said «stue» for the vacuum; HA says Spisestue → Spisestue (decision 18). The note stays KTSB's.
    reg = rooms.build({"things": {"vacuum.q5_pro": {"place": "stue", "note": "under bordet"}}}, HA)
    assert reg.things["vacuum.q5_pro"].place == "spisestue"
    assert reg.things["vacuum.q5_pro"].note == "under bordet"
    assert reg.things["vacuum.q5_pro"].name == "Q5 Pro"


def test_ambiguous_synonym_resolves_to_nothing():
    reg = rooms.build({"places": {"stue": {"synonyms": ["stua"]}, "tv_stue": {"name": "TV-stue", "synonyms": ["stua"]}}}, HA)
    assert reg.resolve("stua") is None
    assert "stua" in rooms.collisions(reg)
    assert reg.find_in_text("lyset i stua") is None


def test_hidden_place_claims_no_words():
    reg = rooms.build({"places": {"living_room": {"synonyms": ["stue", "stua"], "hidden": True},
                                  "stue": {"synonyms": ["stua"]}}}, HA)
    assert reg.resolve("stua") == ("place", "stue")


def test_exact_id_is_the_ha_place_similar_name_is_only_offered():
    reg = rooms.build({"places": {"stue": {"name": "Stue", "synonyms": ["stua"]},
                                  "kjøkken": {"name": "Kjøkken", "synonyms": ["kjøkkenet"]}}}, HA)
    assert reg.places["stue"].source == "ha"  # same id → that HA place, synonyms kept
    assert reg.places["kjøkken"].source == "manual"  # similar only
    assert ("kjøkken", "kjokken") in rooms.similar_places(reg)


def test_import_modes():
    def refs(mode, **extra):
        return set(rooms.build({"settings": {"import_mode": mode, **extra}}, HA).things)

    assert refs("rooms_only") == set()
    assert refs("pick") == set()
    assert refs("assist") == {"light.taklys_stue", "vacuum.q5_pro"}  # never diagnostics, never unplaced
    assert refs("label", import_label="ktsb") == {"light.soverom"}  # area label counts
    assert refs("all", import_domains=["light"]) == {"light.taklys_stue", "light.stalampe", "light.soverom"}


def test_things_at_floor_and_unregistered_count():
    reg = rooms.build({"things": {"light.taklys_stue": {}}}, HA)
    assert [t.ref for t in reg.things_at(floor="forste", domain="light")] == ["light.taklys_stue"]
    assert reg.unregistered_at(place="stue", domain="light") == 1  # stålampe; diag sensor not user-facing


def test_node_place_from_field_or_legacy_room(tmp_path):
    nodes = tmp_path / "nodes.yaml"
    nodes.write_text(yaml.safe_dump({"nodes": {
        "kjokken_node": {"room": "kjøkken", "location_note": "på benken"},
        "tv": {"place": "stue"},
        "ukjent": {"room": "workshop_tv"},
    }}, allow_unicode=True), encoding="utf-8")
    reg = rooms.build({}, HA)
    assert rooms.node_place("kjokken_node", nodes, reg) == ("kjokken", "på benken")
    assert rooms.node_place("tv", nodes, reg) == ("stue", "")
    assert rooms.node_place("ukjent", nodes, reg) == ("", "")
    assert rooms.node_place("finnes_ikke", nodes, reg) == ("", "")


def test_source_of_refs():
    assert rooms.source_of("light.taklys") == "ha"
    assert rooms.source_of("mqtt:zigbee2mqtt/x") == "mqtt"
    assert rooms.source_of("node:kjokken") == "node"
    assert rooms.source_of("Min lampe") == "manual"


def test_get_register_cache_follows_file_change(tmp_path):
    rp, hp = tmp_path / "rooms.yaml", tmp_path / "ha.json"
    rp.write_text("places:\n  bod: {name: Bod}\n", encoding="utf-8")
    hp.write_text(json.dumps(HA), encoding="utf-8")
    assert "bod" in rooms.get_register(rp, hp).places
    rp.write_text("places:\n  loft: {name: Loft}\n", encoding="utf-8")
    reg = rooms.get_register(rp, hp)
    assert "loft" in reg.places and "bod" not in reg.places


# ── migration step 7 ──

OLD_ALIASES = {
    "rooms": {"stue": ["stue", "stua"], "living_room": ["living room", "stue", "stua"], "bod": ["boden"]},
    "room_entities": {"stue": ["light.taklys_stue"], "living_room": ["light.living_room_ceiling"],
                      "Personer": ["sensor.phone"]},
    "aliases": {"lys stue": "light.taklys_stue"},
}


def _run_step7(tmp_path: Path, rooms_data: dict) -> tuple[dict, list[str]]:
    (tmp_path / "aliases.yaml").write_text(yaml.safe_dump(OLD_ALIASES, allow_unicode=True), encoding="utf-8")
    cm._RUN_CONFIG_DIR = tmp_path
    try:
        notes = cm._s7_rooms_from_aliases(rooms_data, {})
    finally:
        cm._RUN_CONFIG_DIR = cm.cs.CONFIG_DIR
    return rooms_data, notes


def test_step7_copies_rooms_and_hides_template_examples(tmp_path):
    data, notes = _run_step7(tmp_path, {"settings": {}, "places": {}, "things": {}})
    assert data["places"]["stue"]["synonyms"] == ["stue", "stua"]
    assert data["places"]["living_room"]["hidden"] is True
    assert data["places"]["living_room"]["template_example"] is True
    assert "hidden" not in data["places"]["bod"]
    assert data["places"]["Personer"] == {"name": "Personer", "synonyms": []}
    assert data["things"]["light.taklys_stue"] == {"place": "stue"}
    assert any("hidden" in n for n in notes)
    assert rooms.build(data, {}).resolve("stua") == ("place", "stue")


def test_step7_never_overwrites_existing_register(tmp_path):
    data, notes = _run_step7(tmp_path, {"places": {"loft": {"name": "Loft"}}, "things": {}})
    assert data["places"] == {"loft": {"name": "Loft"}} and notes == []


def test_step7_full_run_on_a_copy(tmp_path):
    cfg = tmp_path / "configs"
    cfg.mkdir()
    (cfg / "aliases.yaml").write_text(yaml.safe_dump(OLD_ALIASES, allow_unicode=True), encoding="utf-8")
    cm.run(cfg, Path("/kaare/configs_default"))
    data = yaml.safe_load((cfg / "rooms.yaml").read_text(encoding="utf-8"))
    assert data["settings"]["import_mode"] == "rooms_only"
    assert "stue" in data["places"] and data["places"]["living_room"]["hidden"] is True
    assert yaml.safe_load((cfg / "aliases.yaml").read_text(encoding="utf-8"))["rooms"]  # untouched


def test_place_and_floor_may_share_a_word_place_wins():
    ha = {"floors": {"loft": {"name": "Loft"}}, "areas": {"loft": {"name": "Loft", "floor_id": "loft"}}}
    reg = rooms.build({}, ha)
    assert reg.ambiguous == {}
    assert reg.resolve("loft") == ("place", "loft")


def test_link_place_moves_synonyms_and_things():
    data = {"places": {"kjøkken": {"name": "Kjøkken", "synonyms": ["kjøkken", "kjøkkenet", "kitchen"]}},
            "things": {"mqtt:x": {"place": "kjøkken"}}}
    out = rooms.link_place(data, "kjøkken", "kjokken")
    assert "kjøkken" not in out["places"] and "kjøkken" in data["places"]  # input untouched
    assert out["places"]["kjokken"]["synonyms"] == ["kjøkken", "kjøkkenet", "kitchen"]
    assert out["things"]["mqtt:x"]["place"] == "kjokken"
    reg = rooms.build(out, HA)
    assert reg.resolve("kjøkkenet") == ("place", "kjokken") and reg.resolve("kjøkken") == ("place", "kjokken")
