"""N26 — HA actions say what really happened: HA answers 200 also for unknown entities and before slow devices
report, so the gateway checks the state before and after the call."""

import asyncio

import httpx
import pytest

import kaare_ha_gateway as gw
from kaare_core.ha import action_truth as truth
from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.tools import executor_ha as eh

# ── pure rules ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "entity", "pre", "expected"),
    [
        ("turn_on", "light.a", "off", {"on"}),
        ("turn_off", "switch.a", "on", {"off"}),
        ("toggle", "switch.a", "on", {"off"}),
        ("toggle", "switch.a", "unknown", None),
        ("open", "cover.a", "closed", {"open", "opening"}),
        ("set_level", "light.a", "off", {"on"}),
        ("set_temperature", "climate.a", "heat", None),
        ("turn_on", "scene.a", "x", None),
    ],
)
def test_expected_states(action, entity, pre, expected):
    got = truth.expected_states(action, entity, pre)
    assert (set(got) if got is not None else None) == expected


def test_judge():
    on = frozenset({"on"})
    assert truth.judge("turn_on", on, "off", "on") == truth.CONFIRMED
    assert truth.judge("turn_on", on, "on", None) == truth.ALREADY
    assert truth.judge("set_level", on, "on", None) == truth.ACCEPTED  # level itself is not checked
    assert truth.judge("turn_on", on, "off", None) is None  # keep watching
    assert truth.judge("set_temperature", None, "heat", None) == truth.ACCEPTED


def test_changed_state_of_both_reply_shapes():
    rows = [{"entity_id": "light.a", "state": "on"}]
    assert truth.changed_state_of("light.a", rows) == "on"
    assert truth.changed_state_of("light.a", {"changed_states": rows}) == "on"
    assert truth.changed_state_of("light.b", rows) is None


# ── gateway against a fake HA ─────────────────────────────────────────────────


class FakeHA:
    """`states`: entity → list of states returned by successive GETs (last one repeats). `changed`: service reply."""

    def __init__(self, states: dict[str, list[str]], changed=None):
        self.states = states
        self.changed = changed if changed is not None else []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/states/"):
            eid = path.rsplit("/", 1)[1]
            seq = self.states.get(eid)
            if seq is None:
                return httpx.Response(404, json={"message": "Entity not found."})
            state = seq.pop(0) if len(seq) > 1 else seq[0]
            return httpx.Response(200, json={"entity_id": eid, "state": state})
        if path.startswith("/api/services/"):
            self.calls.append(path)
            return httpx.Response(200, json=self.changed)
        return httpx.Response(404)


@pytest.fixture
def ha(monkeypatch):
    def make(states, changed=None, verify=0.6):
        fake = FakeHA(states, changed)
        monkeypatch.setattr(gw, "_http", httpx.Client(transport=httpx.MockTransport(fake.handler)))
        monkeypatch.setattr(gw, "HA_API_URL", "http://ha.test")
        monkeypatch.setattr(gw, "VERIFY_SECONDS", verify)
        monkeypatch.setattr(gw, "_VERIFY_POLL_S", 0.05)
        monkeypatch.setattr(gw, "ALIASES", {})
        return fake

    return make


def _run(action, entity):
    return gw.execute_ha_command({"action": action, "entity_id": entity, "params": {}})


def test_unknown_entity_is_not_found_and_nothing_is_sent(ha):
    fake = ha({})
    out = _run("turn_on", "light.taklys_verksted")
    assert out["status"] == out["outcome"] == truth.NOT_FOUND and fake.calls == []


def test_unavailable_entity_is_not_sent(ha):
    fake = ha({"switch.a": ["unavailable"]})
    assert _run("turn_on", "switch.a")["outcome"] == truth.UNAVAILABLE and fake.calls == []


def test_confirmed_from_hass_reply(ha):
    ha({"switch.a": ["off"]}, changed=[{"entity_id": "switch.a", "state": "on"}])
    out = _run("turn_on", "switch.a")
    assert out["status"] == "ok" and out["outcome"] == truth.CONFIRMED


def test_slow_device_confirmed_by_polling(ha):
    ha({"light.zigbee": ["off", "off", "off", "on"]})  # empty reply, reports a moment later
    assert _run("turn_on", "light.zigbee")["outcome"] == truth.CONFIRMED


def test_never_reported_is_not_confirmed_not_ok(ha):
    ha({"light.tuya": ["off"]}, verify=0.2)
    out = _run("turn_on", "light.tuya")
    assert out["outcome"] == truth.NOT_CONFIRMED and out["status"] == truth.NOT_CONFIRMED


def test_already_on(ha):
    ha({"switch.a": ["on"]})
    assert _run("turn_on", "switch.a")["outcome"] == truth.ALREADY


def test_toggle_goes_to_the_toggle_service(ha):
    fake = ha({"switch.a": ["on", "off"]})
    assert _run("toggle", "switch.a")["outcome"] == truth.CONFIRMED
    assert fake.calls == ["/api/services/switch/toggle"]


def test_zero_wait_only_trusts_the_reply(ha):
    ha({"light.a": ["off", "on"]}, verify=0)
    assert _run("turn_on", "light.a")["outcome"] == truth.NOT_CONFIRMED


# ── memory correction ─────────────────────────────────────────────────────────


def test_stm_correct_actions_and_render():
    stm = ShortTermMemory(user_id="u1")
    stm.record_action("turn_off", "light.gone", ok=True, user_id="u1")
    stm.set_state("light.gone", "turn_off", source="ha")
    stm.record_action("turn_on", "light.slow", ok=False, result="unconfirmed", user_id="u1")
    assert stm.ok_action_entities() == {"light.gone"}
    assert stm.correct_actions("light.gone", "not_found") == 2  # action + believed state
    assert stm.ok_action_entities() == set()
    ctx = stm.build_prompt_context(user_id="u1", lang="en")
    assert "FAILED: turn_off light.gone (does not exist in Home Assistant)" in ctx
    assert "SENT, NOT CONFIRMED: turn_on light.slow" in ctx


# ── what Kåre is told ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reply", "prefix", "needle", "note"),
    [
        ({"status": "ok", "outcome": "confirmed", "state": "on"}, "OK: ", "turned on", ""),
        ({"status": "ok", "outcome": "already", "state": "on"}, "OK: ", "was already on", ""),
        (
            {"status": "not_confirmed", "outcome": "not_confirmed", "state": "off"},
            "SENT: ",
            "not an error",
            "unconfirmed",
        ),
        (
            {"status": "not_found", "outcome": "not_found"},
            "",
            "does not exist in Home Assistant",
            "not_found",
        ),
        ({"status": "unavailable", "outcome": "unavailable"}, "", "is unavailable", ""),
    ],
)
def test_tool_text_and_memory_note(monkeypatch, reply, prefix, needle, note):
    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, json=reply))
        return real(*a, **k)

    monkeypatch.setattr(eh.httpx, "AsyncClient", client)
    out = asyncio.run(
        eh._control_entity("light.x", "turn_on", lang="en", location={"_rid": "rid-t"})
    )
    assert out.startswith(prefix) and needle in out
    assert eh.pop_entity_note("rid-t", "light.x") == note
