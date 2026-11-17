"""P137 phase 2 — HA registry snapshot (kaare_core/ha_registry_sync.py)."""

import asyncio
import json

from kaare_core import ha_registry_sync as sync
from kaare_core import rooms

RAW = {
    "version": "2026.10.1",
    "floors": [{"floor_id": "andre", "name": "Andre etasje", "level": 2, "aliases": ["oppe"]}],
    "areas": [
        {"area_id": "stue", "name": "Stue", "floor_id": "andre", "aliases": [], "labels": ["ktsb"]},
        {"area_id": "verksted", "name": "Verksted", "floor_id": None, "aliases": [], "labels": []},
    ],
    "labels": [{"label_id": "ktsb", "name": "KTSB"}],
    "devices": [
        {"id": "d1", "area_id": "stue", "name": "Hue lampe", "name_by_user": None},
        {"id": "zbt", "area_id": "verksted", "name": "Connect ZBT-1", "name_by_user": None},
    ],
    "entities": [
        {"entity_id": "light.lampe", "device_id": "d1", "area_id": None, "options": {"conversation": {"should_expose": True}}},
        {"entity_id": "light.gruppe", "device_id": "zbt", "area_id": None, "options": {}},
        {"entity_id": "light.gruppe_rettet", "device_id": "zbt", "area_id": "stue", "options": {}},
        {"entity_id": "sensor.rssi", "device_id": "d1", "entity_category": "diagnostic", "options": {}},
        {"entity_id": "light.av", "device_id": "d1", "disabled_by": "user", "options": {}},
        {"entity_id": "switch.skjult", "device_id": "d1", "hidden_by": "user", "labels": ["ktsb"], "options": {}},
    ],
    "states": [{"entity_id": "light.lampe", "attributes": {"friendly_name": "Taklampe stue"}}],
}


def test_snapshot_shape_and_area_inheritance():
    snap = sync.build_snapshot(RAW)
    assert snap["ha_version"] == "2026.10.1"
    assert list(snap["areas"]) == ["stue", "verksted"]  # HA order kept
    assert snap["floors"]["andre"]["aliases"] == ["oppe"]
    e = snap["entities"]
    assert e["light.lampe"]["area_id"] == "stue"  # inherited from the device
    assert e["light.lampe"]["name"] == "Taklampe stue"  # HA friendly name
    assert e["light.lampe"]["exposed"] is True
    assert e["light.gruppe"]["area_id"] == "verksted"  # a group inherits the coordinator's area (HA's rule)
    assert e["light.gruppe_rettet"]["area_id"] == "stue"  # the entity's own area wins
    assert e["sensor.rssi"]["category"] is True
    assert e["switch.skjult"]["hidden"] is True
    assert "light.av" not in e  # disabled left out
    assert e["light.lampe"]["area_labels"] == ["ktsb"]


def test_snapshot_feeds_the_register(tmp_path):
    hp = tmp_path / "ha.json"
    sync.write_snapshot(sync.build_snapshot(RAW), hp)
    assert json.loads(hp.read_text(encoding="utf-8"))["areas"]["stue"]["name"] == "Stue"
    rp = tmp_path / "rooms.yaml"
    rp.write_text("settings: {import_mode: assist}\n", encoding="utf-8")
    reg = rooms.get_register(rp, hp)
    assert reg.resolve("oppe") == ("floor", "andre")
    assert set(reg.things) == {"light.lampe"}  # assist mode: exposed + placed + user-facing


def test_sync_now_without_ha_config(monkeypatch):
    monkeypatch.setattr(sync, "_ha_config", lambda: ("", ""))
    assert asyncio.run(sync.sync_now())["state"] == "not_configured"


def test_state_only_entities_keep_their_name():
    raw = dict(RAW, states=RAW["states"] + [{"entity_id": "media_player.xbox", "attributes": {"friendly_name": "Xbox"}}])
    e = sync.build_snapshot(raw)["entities"]
    assert e["media_player.xbox"]["name"] == "Xbox" and e["media_player.xbox"]["area_id"] == ""


def test_background_loop_fetches_on_event_and_on_resync(monkeypatch, tmp_path):
    fetches = []
    written = []

    class FakeWS:
        def __init__(self):
            self.q = asyncio.Queue()

        async def send(self, msg):
            pass

        async def recv(self):
            return await self.q.get()

        async def close(self):
            pass

    ws = FakeWS()

    async def fake_connect(url, token):
        return sync._Conn(ws)

    async def fake_call(self, msg_type, **fields):
        return None

    async def fake_fetch(conn):
        fetches.append(1)
        return {"fetched_at": str(len(fetches)), "areas": {f"a{len(fetches)}": {}}, "floors": {}, "labels": {},
                "entities": {}}

    monkeypatch.setattr(sync, "HA_CACHE_PATH", tmp_path / "ha.json")
    monkeypatch.setattr(sync, "_last_content", {})
    monkeypatch.setattr(sync, "_ha_config", lambda: ("http://ha", "tok"))
    monkeypatch.setattr(sync, "_connect", fake_connect)
    monkeypatch.setattr(sync._Conn, "call", fake_call)
    monkeypatch.setattr(sync, "_fetch", fake_fetch)
    monkeypatch.setattr(sync, "write_snapshot", lambda snap, path=None: written.append(snap["fetched_at"]))
    monkeypatch.setattr(sync, "_DEBOUNCE_S", 0.01)
    monkeypatch.setattr(sync, "_wake", asyncio.Event())  # an Event binds to the first loop that waits on it

    async def scenario():
        sync._wake.clear()
        task = asyncio.create_task(sync.start_ha_registry_sync())
        for _ in range(100):
            if fetches:
                break
            await asyncio.sleep(0.01)
        await ws.q.put(json.dumps({"type": "event", "event": {"event_type": "area_registry_updated"}}))
        for _ in range(100):
            if len(fetches) >= 2:
                break
            await asyncio.sleep(0.01)
        sync.request_resync()
        for _ in range(100):
            if len(fetches) >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert len(fetches) == 3 and written == ["1", "2", "3"]
    assert sync.status()["state"] == "connected"


def test_restored_unavailable_entities_are_orphans_and_not_user_facing():
    raw = dict(RAW, states=RAW["states"] + [{"entity_id": "light.gruppe", "state": "unavailable",
                                              "attributes": {"restored": True, "friendly_name": "Gammel"}}])
    snap = sync.build_snapshot(raw)
    assert snap["entities"]["light.gruppe"]["orphan"] is True and snap["entities"]["light.lampe"]["orphan"] is False
    reg = rooms.build({"settings": {"import_mode": "all"}, "things": {"light.gone": {"place": "stue"}}}, snap)
    assert "light.gruppe" not in reg.things  # «all» never imports leftovers
    assert rooms.missing_in_ha(reg, "light.gruppe") == "orphan"
    assert rooms.missing_in_ha(reg, "light.gone") == "gone"
    assert rooms.missing_in_ha(reg, "light.lampe") == ""
    assert rooms.missing_in_ha(reg, "mqtt:x") == ""


def _ev(event_type, action="update", **data):
    return {"event_type": event_type, "data": {"action": action, **data}}


def test_irrelevant_registry_updates_are_ignored():
    # Seen live 2026-10-19: MPD flips supported_features around every hourly TTS; tablet battery icons.
    assert not sync.is_relevant(_ev("entity_registry_updated", entity_id="media_player.mpd",
                                    changes={"supported_features": 458687}))
    assert not sync.is_relevant(_ev("entity_registry_updated", entity_id="sensor.tablet_battery",
                                    changes={"original_icon": "mdi:battery-plus"}))
    assert not sync.is_relevant(_ev("device_registry_updated", device_id="d1", changes={"sw_version": "1.2"}))


def test_relevant_registry_events_fetch():
    assert sync.is_relevant(_ev("entity_registry_updated", entity_id="light.a", changes={"name": None}))
    assert sync.is_relevant(_ev("entity_registry_updated", entity_id="light.a",
                                changes={"options": {}, "supported_features": 1}))
    assert sync.is_relevant(_ev("entity_registry_updated", entity_id="light.b", old_entity_id="light.a",
                                changes={"entity_id": "light.a"}))
    assert sync.is_relevant(_ev("entity_registry_updated", "create", entity_id="light.new"))
    assert sync.is_relevant(_ev("entity_registry_updated", "remove", entity_id="light.old"))
    assert sync.is_relevant(_ev("device_registry_updated", device_id="d1", changes={"area_id": "stue"}))
    assert sync.is_relevant(_ev("entity_registry_updated", entity_id="light.a"))  # no changes field → fetch
    assert sync.is_relevant({"event_type": "area_registry_updated", "data": {"action": "update", "area_id": "stue"}})


class _Recorder(dict):
    """A registry row that remembers which keys build_snapshot reads."""

    def __init__(self, row, seen):
        super().__init__(row)
        self._seen = seen

    def get(self, key, default=None):
        self._seen.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._seen.add(key)
        return super().__getitem__(key)


def test_filter_fields_cover_what_the_snapshot_reads():
    """Guard: if build_snapshot starts reading a new entity/device field, the event filter must know it — or a
    change to that field would never reach the snapshot."""
    ent_seen, dev_seen = set(), set()
    raw = dict(RAW, entities=[_Recorder(e, ent_seen) for e in RAW["entities"]],
               devices=[_Recorder(d, dev_seen) for d in RAW["devices"]])
    sync.build_snapshot(raw)
    assert ent_seen and dev_seen
    assert ent_seen <= sync.ENTITY_FIELDS, ent_seen - sync.ENTITY_FIELDS
    assert dev_seen <= sync.DEVICE_FIELDS, dev_seen - sync.DEVICE_FIELDS


def test_store_writes_only_when_content_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "_last_content", {})
    hp = tmp_path / "ha.json"
    snap = sync.build_snapshot(RAW)
    assert sync.store_snapshot(snap, hp) is True
    first = hp.read_text(encoding="utf-8")
    again = dict(snap, fetched_at="2099-01-01T00:00:00Z")
    assert sync.store_snapshot(again, hp) is False
    assert hp.read_text(encoding="utf-8") == first  # fetched_at alone never rewrites the file
    monkeypatch.setattr(sync, "_last_content", {})  # restart: compares with the file on disk
    assert sync.store_snapshot(again, hp) is False
    changed = dict(snap, areas={**snap["areas"], "bad": {"name": "Bad"}})
    assert sync.store_snapshot(changed, hp) is True
    assert "bad" in json.loads(hp.read_text(encoding="utf-8"))["areas"]


def test_background_loop_ignores_irrelevant_events(monkeypatch, tmp_path):
    fetches = []

    class FakeWS:
        def __init__(self):
            self.q = asyncio.Queue()

        async def send(self, msg):
            pass

        async def recv(self):
            return await self.q.get()

        async def close(self):
            pass

    ws = FakeWS()

    async def fake_connect(url, token):
        return sync._Conn(ws)

    async def fake_call(self, msg_type, **fields):
        return None

    async def fake_fetch(conn):
        fetches.append(1)
        return {"fetched_at": "x", "areas": {}, "floors": {}, "labels": {}, "entities": {}}

    monkeypatch.setattr(sync, "_ha_config", lambda: ("http://ha", "tok"))
    monkeypatch.setattr(sync, "_connect", fake_connect)
    monkeypatch.setattr(sync._Conn, "call", fake_call)
    monkeypatch.setattr(sync, "_fetch", fake_fetch)
    monkeypatch.setattr(sync, "store_snapshot", lambda snap, path=None: False)
    monkeypatch.setattr(sync, "_DEBOUNCE_S", 0.01)
    monkeypatch.setattr(sync, "_wake", asyncio.Event())  # an Event binds to the first loop that waits on it

    def event(ev):
        return json.dumps({"type": "event", "event": ev})

    async def scenario():
        sync._wake.clear()
        task = asyncio.create_task(sync.start_ha_registry_sync())
        for _ in range(100):
            if fetches:
                break
            await asyncio.sleep(0.01)
        for _ in range(5):
            await ws.q.put(event(_ev("entity_registry_updated", entity_id="media_player.mpd",
                                     changes={"supported_features": 1})))
        await asyncio.sleep(0.1)
        ignored = len(fetches)
        await ws.q.put(event(_ev("entity_registry_updated", entity_id="light.a", changes={"area_id": None})))
        for _ in range(100):
            if len(fetches) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ignored

    assert asyncio.run(scenario()) == 1  # five irrelevant events → no fetch beyond the start
    assert len(fetches) == 2  # the relevant one fetched
