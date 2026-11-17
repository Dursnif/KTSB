"""P138 phase 4 — a Home Assistant button press starts a voice node (event or entity trigger, learn)."""

import asyncio

from kaare_core import ha_node_triggers as ht

EVENT_TRIG = {
    "kind": ht.KIND_EVENT,
    "event_type": "zha_event",
    "match": {"device_ieee": "a4:c1:38:74:81:60:df:e5", "command": "toggle"},
}
ENTITY_TRIG = {"kind": ht.KIND_ENTITY, "entity_id": "event.hall_button", "to": "single"}


def test_node_triggers_only_enabled_and_complete():
    nodes = {
        "a": {"trigger": EVENT_TRIG},
        "b": {"trigger": ENTITY_TRIG, "enabled": False},
        "c": {"trigger": {"kind": ht.KIND_EVENT}},  # no event_type
        "d": {"trigger": {"kind": "other", "entity_id": "x"}},
        "e": {},
        "f": "not a dict",
    }
    assert list(ht.node_triggers(nodes)) == ["a"]


def test_event_match_needs_every_key():
    data = {
        "device_ieee": "a4:c1:38:74:81:60:df:e5",
        "command": "toggle",
        "args": [],
        "endpoint_id": 1,
    }
    assert ht.event_matches(EVENT_TRIG, "zha_event", data)
    assert not ht.event_matches(EVENT_TRIG, "zha_event", {**data, "command": "on"})  # double press
    assert not ht.event_matches(EVENT_TRIG, "deconz_event", data)
    assert not ht.event_matches(ENTITY_TRIG, "zha_event", data)


def test_entity_match_state_or_event_type():
    assert ht.entity_matches(
        ENTITY_TRIG, "event.hall_button", "2026-10-17T20:00:00", {"event_type": "single"}
    )
    assert not ht.entity_matches(
        ENTITY_TRIG, "event.hall_button", "2026-10-17T20:00:00", {"event_type": "double"}
    )
    assert ht.entity_matches(
        {**ENTITY_TRIG, "to": ""}, "event.hall_button", "2026-10-17T20:00:01", {}
    )
    assert not ht.entity_matches(
        ENTITY_TRIG, "event.hall_button", "unavailable", {"event_type": "single"}
    )
    assert not ht.entity_matches(ENTITY_TRIG, "event.other", "x", {"event_type": "single"})
    legacy = {"kind": ht.KIND_ENTITY, "entity_id": "sensor.btn_action", "to": "single"}
    assert ht.entity_matches(legacy, "sensor.btn_action", "single", {})
    assert not ht.entity_matches(legacy, "sensor.btn_action", "", {})


def test_debounce_per_node():
    ht._last_fired.clear()
    assert not ht._debounced("n", now=100.0)
    assert ht._debounced("n", now=101.0)
    assert not ht._debounced("other", now=101.0)
    assert not ht._debounced("n", now=103.0)


def test_handle_message_fires_matching_nodes(monkeypatch):
    fired = []

    async def fake_fire(nid):
        fired.append(nid)
        return True

    monkeypatch.setattr(ht, "fire", fake_fire)
    triggers = {"verksted": EVENT_TRIG, "hall": ENTITY_TRIG}
    subs = {5: "zha_event", 6: "entities"}
    zha = {
        "id": 5,
        "type": "event",
        "event": {
            "event_type": "zha_event",
            "data": {"device_ieee": "a4:c1:38:74:81:60:df:e5", "command": "toggle"},
        },
    }
    assert asyncio.run(ht.handle_message(zha, subs, triggers)) == ["verksted"]
    ent = {
        "id": 6,
        "type": "event",
        "event": {
            "c": {
                "event.hall_button": {
                    "+": {"s": "2026-10-17T20:00:00", "a": {"event_type": "single"}}
                }
            }
        },
    }
    assert asyncio.run(ht.handle_message(ent, subs, triggers)) == ["hall"]
    initial = {
        "id": 6,
        "type": "event",
        "event": {"a": {"event.hall_button": {"s": "x", "a": {"event_type": "single"}}}},
    }
    assert (
        asyncio.run(ht.handle_message(initial, subs, triggers)) == []
    )  # current state is not a press
    assert fired == ["verksted", "hall"]


def test_fire_calls_voice_bridge_button_with_service_token(monkeypatch):
    seen = {}

    class Resp:
        status_code = 202

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None):
            seen["url"], seen["headers"] = url, headers
            return Resp()

    monkeypatch.setattr(ht.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ht, "get_service", lambda *a: "http://bridge:8011")
    monkeypatch.setattr(ht, "service_headers", lambda: {"X-Service": "t"})
    ht._last_fired.clear()
    assert asyncio.run(ht.fire("verksted"))
    assert seen["url"] == "http://bridge:8011/button/verksted" and seen["headers"] == {
        "X-Service": "t"
    }
    assert not asyncio.run(ht.fire("verksted"))  # debounced


def test_learn_candidates():
    snap = {
        "entities": {
            "event.hall_button": {
                "name": "Hall button",
                "domain": "event",
                "device_id": "d1",
                "device_name": "Hall switch",
            }
        }
    }
    ev = ht.candidate_from_event(
        "zha_event",
        {"device_ieee": "a4:c1", "device_id": "d1", "command": "toggle", "args": [], "params": {}},
        snap,
    )
    assert ev["kind"] == ht.KIND_EVENT and ev["match"] == {
        "device_ieee": "a4:c1",
        "device_id": "d1",
        "command": "toggle",
    }
    assert ev["label"] == "Hall switch (zha_event)"
    en = ht.candidate_from_entity(
        "event.hall_button", "2026-10-17T20:00:00", {"event_type": "single"}, snap
    )
    assert en == {
        "kind": ht.KIND_ENTITY,
        "entity_id": "event.hall_button",
        "to": "single",
        "label": "Hall button",
    }
    btn = ht.candidate_from_entity("button.x", "2026-10-17T20:00:00", {}, {})
    assert btn["to"] == ""


def test_learn_entity_list_skips_orphans():
    snap = {
        "entities": {
            "event.a": {"domain": "event"},
            "sensor.b_action": {"domain": "sensor"},
            "sensor.temp": {"domain": "sensor"},
            "event.dead": {"domain": "event", "orphan": True},
        }
    }
    assert ht._learn_entity_ids(snap) == ["event.a", "sensor.b_action"]


class FakeConn:
    """Stands in for ha_registry_sync._Conn: refuses zha_event like HA does for a non-admin token."""

    def __init__(self, admin: bool):
        self.admin = admin
        self.next_id = 1
        self.events: asyncio.Queue = asyncio.Queue()
        self.calls = []

    async def call(self, msg_type, **fields):
        self.calls.append((msg_type, fields))
        self.next_id += 1
        if msg_type == "subscribe_events" and not self.admin:
            raise RuntimeError("subscribe_events: Unauthorized")
        return None


def test_subscribe_reports_admin_required_per_node():
    triggers = {"verksted": EVENT_TRIG, "hall": ENTITY_TRIG}
    conn = FakeConn(admin=False)
    subs, refused = asyncio.run(ht._subscribe(conn, triggers))
    assert refused == {"verksted": "admin_required"}
    assert list(subs.values()) == ["entities"]
    conn = FakeConn(admin=True)
    subs, refused = asyncio.run(ht._subscribe(conn, triggers))
    assert refused == {} and sorted(subs.values()) == ["entities", "zha_event"]
