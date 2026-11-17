"""P139 phase 1 — media players through HA: volume, mute, source, pause/play/stop in `ha_control`, checked against
the player's attributes (N26), and «the sound» with nothing named = the speaker the speaking node's replies play on."""

import asyncio
import json

import httpx
import pytest
import yaml

import kaare_ha_gateway as gw
from kaare_core.ha import action_truth as truth
from kaare_core.tools import executor_ha as eh
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import _T

CAST_FLAGS = 152461  # a Nest speaker in this house: PAUSE, VOLUME_SET, VOLUME_MUTE, PLAY, STOP … no SELECT_SOURCE


def row(state: str, **attrs) -> dict:
    return {"state": state, "attributes": attrs}


# ── pure rules ────────────────────────────────────────────────────────────────


def test_set_volume_judge():
    pre = row("playing", volume_level=0.3)
    assert truth.media_judge("set_volume", {"volume_level": 0.3}, pre, None) == truth.ALREADY
    assert truth.media_judge("set_volume", {"volume_level": 0.6}, pre, None) is None
    assert truth.media_judge("set_volume", {"volume_level": 0.6}, pre, row("playing", volume_level=0.6000001)) == (
        truth.CONFIRMED
    )
    assert truth.media_judge("set_volume", {"volume_level": 0.6}, pre, row("playing", volume_level=0.3)) is None


def test_cast_off_hides_volume_so_nothing_can_be_checked():
    assert truth.media_judge("set_volume", {"volume_level": 0.4}, row("off"), row("off")) == truth.ACCEPTED
    assert truth.media_judge("mute", {"is_volume_muted": True}, row("off"), row("off")) == truth.ACCEPTED


def test_volume_step_judge():
    pre = row("playing", volume_level=0.5)
    assert truth.media_judge("volume_up", {}, pre, row("playing", volume_level=0.6)) == truth.CONFIRMED
    assert truth.media_judge("volume_down", {}, pre, row("playing", volume_level=0.6)) is None
    assert truth.media_judge("volume_up", {}, row("playing", volume_level=1.0), None) == truth.ALREADY


def test_mute_source_and_transport_judge():
    assert truth.media_judge("mute", {}, row("playing", is_volume_muted=True), None) == truth.ALREADY
    assert truth.media_judge("unmute", {}, row("playing", is_volume_muted=True), None) is None
    assert truth.media_judge("unmute", {}, None, row("playing", is_volume_muted=False)) == truth.CONFIRMED
    assert truth.media_judge("select_source", {"source": "HDMI 1"}, None, row("on", source="HDMI 1")) == (
        truth.CONFIRMED
    )
    assert truth.media_judge("media_pause", {}, row("playing"), row("paused")) == truth.CONFIRMED
    assert truth.media_judge("media_play", {}, row("playing"), None) == truth.ALREADY
    assert truth.media_judge("media_stop", {}, row("playing"), row("idle")) == truth.CONFIRMED


def test_unsupported_by_domain_and_feature_flags():
    assert truth.media_unsupported("set_volume", "light.a", {})
    assert not truth.media_unsupported("set_volume", "media_player.nest", {"supported_features": CAST_FLAGS})
    assert truth.media_unsupported("select_source", "media_player.nest", {"supported_features": CAST_FLAGS})
    # volume_up is offered by HA for VOLUME_SET *or* VOLUME_STEP — a Cast has only VOLUME_SET
    assert not truth.media_unsupported("volume_up", "media_player.nest", {"supported_features": CAST_FLAGS})
    assert not truth.media_unsupported("select_source", "media_player.x", {})  # no flags reported → try


def test_match_source():
    attrs = {"source_list": ["HDMI 1", "Netflix", "Live TV"]}
    assert truth.match_source("hdmi1", attrs) == "HDMI 1"
    assert truth.match_source("live tv", attrs) == "Live TV"
    assert truth.match_source("YouTube", attrs) is None
    assert truth.match_source("YouTube", {}) == "YouTube"


def test_allowed_list_never_blocks_actions_the_user_was_never_shown():
    old_list = set(truth.LEGACY_OFFERED_ACTIONS)  # a list saved before P139: every old action ticked
    assert truth.allowed("set_volume", old_list, set())
    assert not truth.allowed("set_volume", {"turn_on"}, set(truth.GATEWAY_ACTIONS))  # shown and left out
    assert not truth.allowed("toggle", {"turn_on"}, set())  # an old action left out stays blocked
    assert truth.allowed("anything", set(), set())  # empty list = everything


# ── gateway against a fake HA ─────────────────────────────────────────────────


class FakeHA:
    """`rows`: entity → successive state rows (last repeats). Records service calls with their bodies."""

    def __init__(self, rows: dict[str, list[dict]], changed=None, fail: int = 0):
        self.rows = rows
        self.changed = changed if changed is not None else []
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/states/"):
            eid = path.rsplit("/", 1)[1]
            seq = self.rows.get(eid)
            if seq is None:
                return httpx.Response(404, json={"message": "Entity not found."})
            r = seq.pop(0) if len(seq) > 1 else seq[0]
            return httpx.Response(200, json={"entity_id": eid, **r})
        if path.startswith("/api/services/"):
            self.calls.append((path, json.loads(request.content or b"{}")))
            if self.fail:
                return httpx.Response(self.fail, json={"message": "no"})
            return httpx.Response(200, json=self.changed)
        return httpx.Response(404)


@pytest.fixture
def ha(monkeypatch):
    def make(rows, changed=None, verify=0.4, fail=0):
        fake = FakeHA(rows, changed, fail)
        monkeypatch.setattr(gw, "_http", httpx.Client(transport=httpx.MockTransport(fake.handler)))
        monkeypatch.setattr(gw, "HA_API_URL", "http://ha.test")
        monkeypatch.setattr(gw, "VERIFY_SECONDS", verify)
        monkeypatch.setattr(gw, "_VERIFY_POLL_S", 0.05)
        monkeypatch.setattr(gw, "ALIASES", {})
        return fake

    return make


def _run(action, entity, **params):
    return gw.execute_ha_command({"action": action, "entity_id": entity, "params": params})


def test_set_volume_confirmed_by_polling_and_sent_as_level(ha):
    nest = "media_player.nest"
    fake = ha({nest: [row("playing", volume_level=0.3, supported_features=CAST_FLAGS)] * 2
               + [row("playing", volume_level=0.6, supported_features=CAST_FLAGS)]})
    out = _run("set_volume", nest, volume_pct=60)
    assert out["outcome"] == truth.CONFIRMED and out["media"]["volume_level"] == 0.6
    assert fake.calls == [("/api/services/media_player/volume_set", {"entity_id": nest, "volume_level": 0.6})]


def test_off_cast_volume_is_accepted_not_failed(ha):
    ha({"media_player.nest": [row("off", supported_features=CAST_FLAGS)]})
    out = _run("set_volume", "media_player.nest", volume_pct=40)
    assert out["outcome"] == truth.ACCEPTED and out["status"] == "ok"


def test_unsupported_and_unknown_source_send_nothing(ha):
    fake = ha({"media_player.nest": [row("playing", supported_features=CAST_FLAGS)],
               "media_player.tv": [row("on", source="Live TV", source_list=["HDMI 1", "Live TV"])]})
    assert _run("select_source", "media_player.nest", source="HDMI 1")["outcome"] == truth.UNSUPPORTED
    out = _run("select_source", "media_player.tv", source="YouTube")
    assert out["outcome"] == truth.SOURCE_UNKNOWN and out["media"]["source_list"] == ["HDMI 1", "Live TV"]
    assert fake.calls == []


def test_already_muted_sends_nothing(ha):
    fake = ha({"media_player.tv": [row("on", is_volume_muted=True)]})
    assert _run("mute", "media_player.tv")["outcome"] == truth.ALREADY and fake.calls == []


def test_ha_refusal_is_an_error_not_a_crash(ha):
    ha({"media_player.tv": [row("on", volume_level=0.2)]}, fail=500)
    assert _run("set_volume", "media_player.tv", volume_pct=50)["outcome"] == truth.ERROR


def test_unknown_player_is_not_found(ha):
    fake = ha({})
    assert _run("media_pause", "media_player.gone")["outcome"] == truth.NOT_FOUND and fake.calls == []


# ── what Kåre is told ─────────────────────────────────────────────────────────


def _reply(monkeypatch, reply: dict) -> list[dict]:
    real = httpx.AsyncClient
    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=reply)

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(eh.httpx, "AsyncClient", client)
    return sent


@pytest.mark.parametrize(
    ("reply", "prefix", "needle"),
    [
        ({"status": "ok", "outcome": "confirmed", "state": "playing", "media": {"volume_level": 0.6}}, "OK: ",
         "volume 60%"),
        ({"status": "ok", "outcome": "accepted", "state": "off", "media": {}}, "OK: ", "cannot be checked"),
        ({"status": "not_confirmed", "outcome": "not_confirmed", "state": "playing", "media": {}}, "SENT: ",
         "does not show the change"),
        ({"status": "unsupported", "outcome": "unsupported", "state": "playing", "media": {}}, "",
         "does not support"),
    ],
)
def test_media_tool_text(monkeypatch, reply, prefix, needle):
    sent = _reply(monkeypatch, reply)
    out = asyncio.run(eh._control_entity("media_player.nest", "set_volume", lang="en", volume_pct="60%"))
    assert out.startswith(prefix) and needle in out
    assert sent[0]["params"] == {"volume_pct": 60}


def test_media_action_on_a_light_is_refused_without_a_call(monkeypatch):
    sent = _reply(monkeypatch, {})
    out = asyncio.run(eh._control_entity("light.a", "media_pause", lang="en"))
    assert "is not a media player" in out and sent == []


def test_set_volume_without_level_asks_for_it(monkeypatch):
    sent = _reply(monkeypatch, {})
    assert "volume_pct" in asyncio.run(eh._control_entity("media_player.a", "set_volume", lang="en"))
    assert sent == []


# ── «the sound» = the speaking node's speaker ─────────────────────────────────


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    path = tmp_path / "nodes.yaml"
    path.write_text(yaml.safe_dump({"nodes": {
        "atom": {"type": "wyoming", "entity_id": "", "audio_out": ["nest"], "description": "Mic bedroom"},
        "nest": {"type": "ha_media_player", "entity_id": "media_player.nest", "description": "Nest bedroom"},
        "sat": {"type": "wyoming", "entity_id": "", "has_audio": True, "description": "Satellite"},
    }}))
    monkeypatch.setattr(eh, "_NODES_PATH", path)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: ("", ""))
    return path


def test_node_audio_entities_follow_audio_out(nodes):
    assert eh.node_audio_entities("atom") == ([("media_player.nest", "Nest bedroom")], "Nest bedroom")
    assert eh.node_audio_entities("sat") == ([], "Satellite")  # the satellite's own speaker — not HA
    assert eh.node_audio_entities("") == ([], "")


def test_volume_up_with_nothing_named_hits_the_nodes_speaker_not_the_radio(nodes, monkeypatch):
    sent = _reply(monkeypatch, {"status": "ok", "outcome": "confirmed", "state": "playing",
                                "media": {"volume_level": 0.5}})
    out = asyncio.run(eh._control_entity("", "volume_up", lang="en",
                                         location={"_source_node": "atom", "_rid": "rid-v"}))
    assert [s["entity_id"] for s in sent] == ["media_player.nest"]
    assert out.startswith("OK:") and "Nest bedroom (volume 50%)" in out
    assert eh.pop_location_results("rid-v") == [("media_player.nest", True, "")]


def test_node_without_ha_speaker_says_so_and_sends_nothing(nodes, monkeypatch):
    sent = _reply(monkeypatch, {})
    out = asyncio.run(eh._control_entity("", "volume_up", lang="en", location={"_source_node": "sat"}))
    assert "Home Assistant does not control" in out and sent == []
    out = asyncio.run(eh._control_entity("", "volume_up", lang="en", location={}))
    assert "Ask the user which speaker" in out


# ── schema + texts ────────────────────────────────────────────────────────────


def test_tool_schema_and_texts_cover_every_media_action():
    for lang in ("nb", "en", "de"):
        tool = next(x for x in get_tools(lang) if x["function"]["name"] == "ha_control")
        props = tool["function"]["parameters"]["properties"]
        assert set(truth.MEDIA_ACTIONS) <= set(props["action"]["enum"])
        assert {"volume_pct", "source"} <= set(props)
    for action in truth.MEDIA_ACTIONS:
        assert set(_T[f"ha_act_{action}"]) == {"nb", "en", "de"}
        assert action in truth.MEDIA_SERVICES
