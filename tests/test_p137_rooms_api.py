"""P137 phase 5 — /api/settings/rooms (kaare_core/routers/router_rooms.py), on temporary files."""

import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kaare_core import rooms
from kaare_core.routers import router_rooms as rr

HA = {
    "areas": {"stue": {"name": "Stue", "floor_id": "f1"}, "kjokken": {"name": "Kjøkken", "floor_id": "f1"}},
    "floors": {"f1": {"name": "Første etasje"}},
    "entities": {
        "light.tak": {"name": "Taklys", "area_id": "stue", "device_id": "d1", "device_name": "Hue", "domain": "light"},
        "sensor.rssi": {"name": "RSSI", "area_id": "stue", "device_id": "d1", "domain": "sensor", "category": True},
    },
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    rp, hp = tmp_path / "rooms.yaml", tmp_path / "ha.json"
    rp.write_text(yaml.safe_dump({"settings": {"import_mode": "rooms_only"},
                                  "places": {"kjøkken": {"name": "Kjøkken", "synonyms": ["kjøkkenet"]}},
                                  "things": {}}, allow_unicode=True), encoding="utf-8")
    hp.write_text(json.dumps(HA), encoding="utf-8")
    monkeypatch.setattr(rooms, "ROOMS_PATH", rp)
    monkeypatch.setattr(rooms, "HA_CACHE_PATH", hp)
    app = FastAPI()
    app.include_router(rr.router)
    app.dependency_overrides[rr._require_admin] = lambda: {"username": "admin"}
    return TestClient(app), rp


def test_get_shows_ha_and_manual_places_and_similar(client):
    c, _ = client
    v = c.get("/api/settings/rooms").json()
    by_id = {p["id"]: p for p in v["places"]}
    assert by_id["stue"]["source"] == "ha" and by_id["kjøkken"]["source"] == "manual"
    assert v["similar"] == [{"manual": "kjøkken", "ha": "kjokken"}]
    assert v["collisions"] == {"kjøkken": ["kjokken", "kjøkken"]}
    assert by_id["stue"]["unregistered"] == 1  # the diagnostic sensor does not count


def test_put_refuses_collisions_then_link_solves_them(client):
    c, rp = client
    body = yaml.safe_load(rp.read_text(encoding="utf-8"))
    r = c.put("/api/settings/rooms", json=body)
    assert r.status_code == 409 and "kjøkken" in r.json()["detail"]["collisions"]
    v = c.post("/api/settings/rooms/link", json={"manual": "kjøkken", "ha": "kjokken"}).json()
    assert v["collisions"] == {} and v["similar"] == []
    saved = yaml.safe_load(rp.read_text(encoding="utf-8"))
    assert "kjøkken" not in saved["places"] and "kjøkkenet" in saved["places"]["kjokken"]["synonyms"]


def test_put_saves_things_note_and_mode(client):
    c, rp = client
    c.post("/api/settings/rooms/link", json={"manual": "kjøkken", "ha": "kjokken"})
    body = yaml.safe_load(rp.read_text(encoding="utf-8"))
    body["things"] = {"light.tak": {"note": "over sofaen"}, "mqtt:z2m/bod": {"place": "stue"}}
    body["settings"] = {"import_mode": "assist", "import_label": "", "import_domains": []}
    v = c.put("/api/settings/rooms", json=body).json()
    things = {t["ref"]: t for t in v["things"]}
    assert things["light.tak"]["note"] == "over sofaen" and things["light.tak"]["place"] == "stue"
    assert things["mqtt:z2m/bod"]["source"] == "mqtt"
    assert v["settings"]["import_mode"] == "assist"


def test_put_rejects_bad_mode_and_unplaced_manual_thing(client):
    c, _ = client
    assert c.put("/api/settings/rooms", json={"settings": {"import_mode": "everything"}}).status_code == 400
    r = c.put("/api/settings/rooms", json={"things": {"mqtt:x": {"note": "?"}}})
    assert r.status_code == 400


def test_ha_tree_groups_by_area_and_device(client):
    c, _ = client
    tree = c.get("/api/settings/rooms/ha_tree").json()
    stue = next(a for a in tree["areas"] if a["id"] == "stue")
    ents = {e["ref"]: e for e in stue["devices"][0]["entities"]}
    assert stue["devices"][0]["name"] == "Hue"
    assert ents["light.tak"]["user_facing"] is True and ents["sensor.rssi"]["user_facing"] is False


def test_view_reports_node_places(client, tmp_path, monkeypatch):
    nodes = tmp_path / "nodes.yaml"
    nodes.write_text(yaml.safe_dump({"nodes": {"k": {"room": "Kjøkken"}, "x": {"room": "workshop_tv"},
                                               "s": {"place": "stue", "location_note": "på hylla"}}},
                                    allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(rooms, "NODES_PATH", nodes)
    c, _ = client
    c.post("/api/settings/rooms/link", json={"manual": "kjøkken", "ha": "kjokken"})
    v = c.get("/api/settings/rooms").json()
    assert v["nodes"]["k"]["place"] == "kjokken"
    assert v["nodes"]["x"]["place"] == ""
    assert v["nodes"]["s"] == {"place": "stue", "note": "på hylla", "room": ""}
