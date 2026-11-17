"""P142 intake 2026-11-16 (from P139 reopened #7, BUG-log 2026-11-16 «Kåre did not find the TV in the living room»): a write tool must
not store a made-up object as a fact. Kåre stored «TV in the living room: media_player.tv_a_invented» in world memory (world:305)
before he had tried it — the entity never existed; the house's TV is a different HA entity.

Owner decision 2026-11-16: use the Model Enforcer for every invented file / path / object — no separate check in the world tool, a
tool-call detector in the enforcer: `ungrounded_object`. Before a write (the actions in `_WRITE_ACTIONS`) it looks for
object references in the arguments — HA entity ids, `node:<id>`, file paths — and checks them against the house's own
registers (HA snapshot, nodes.yaml) and against what tools and the user showed in this request. A register that does
not exist here (no HA) is never judged (rule 24). Ships in shadow (P142 D4). No live files: registers are fakes."""

import pytest

from kaare_core import model_enforcement as me
from kaare_core.tools.i18n import t

KNOWN_ENTITIES = {"media_player.tv_a_real", "light.example_lamp"}
KNOWN_NODES = {"tv_a", "example_tv"}


@pytest.fixture
def house(monkeypatch):
    monkeypatch.setattr(me, "_known_objects", lambda: (set(KNOWN_ENTITIES), set(KNOWN_NODES)))
    logged = []
    monkeypatch.setattr(me, "_log_enforcement", lambda rid, det, outcome, phrase="": logged.append((det, outcome, phrase)))
    return logged


def _det():
    return me.UngroundedObjectDetector()


WORLD_305 = {"action": "add", "category": "Rom", "text": "TV i stua: media_player.tv_a_invented"}


def test_the_world_305_case_fires(house):
    d = _det()
    assert d.matches(tool_name="world", args=WORLD_305)
    assert "media_player.tv_a_invented" in d.matched_phrase


def test_a_real_entity_passes(house):
    assert not _det().matches(tool_name="world", args={**WORLD_305, "text": "TV i stua: media_player.tv_a_real"})


def test_an_entity_a_tool_showed_this_request_passes(house):
    d = _det()
    d.update_facts({"grounding_text": "… media_player.tv_a_invented (new, not in the snapshot yet) …"})
    assert not d.matches(tool_name="world", args=WORLD_305)


def test_no_ha_register_never_judges_entities(monkeypatch):
    monkeypatch.setattr(me, "_known_objects", lambda: (None, set(KNOWN_NODES)))
    assert not _det().matches(tool_name="world", args=WORLD_305)


def test_unknown_node_fires_and_known_node_passes(house):
    assert _det().matches(tool_name="note", args={"action": "write", "text": "Stue-TV = node:tv_b"})
    assert not _det().matches(tool_name="note", args={"action": "write", "text": "Stue-TV = node:tv_a"})


def test_read_actions_and_non_write_tools_are_not_judged(house):
    assert not _det().matches(tool_name="world", args={"action": "read", "text": "media_player.tv_a_invented"})
    assert not _det().matches(tool_name="ha_control", args={"entity_id": "media_player.tv_a_invented"})


def test_an_ungrounded_path_in_a_write_fires(house):
    d = _det()
    assert d.matches(tool_name="note", args={"action": "write", "text": "Feilen står i /kaare/logs/not_a_real_log_x.log"})
    assert "/kaare/logs/not_a_real_log_x.log" in d.matched_phrase


# Replay 2026-11-16 over every logged write (N-D5): these were false hits of the first version (N-D1).
@pytest.mark.parametrize("text", [
    "Lyset ble slått av med light.turn_off kl. 18:02",
    "Rutinen weather.get_forecasts 00:03 er husets egen",
    "Dev-møtet sjekket /api/meetings/status",
    "14.11 11:27: image.car-ha-events på a709d724",
])
def test_service_names_api_routes_and_shorthand_do_not_fire(house, text):
    assert not _det().matches(tool_name="note", args={"action": "write", "text": text})


def test_an_invented_light_from_the_replay_still_fires(house):
    d = _det()
    assert d.matches(tool_name="note", args={"action": "write", "text": "light.kjellergang slått av"})
    assert d.matched_phrase == "light.kjellergang"


def test_plain_words_never_fire(house):
    assert not _det().matches(tool_name="world", args={"action": "add", "text": "Samsung-TV-en står i stua, 65 tommer."})


def test_chat_profile_has_it_in_shadow(house):
    enf = me.ModelEnforcer.for_profile("chat", "nb", rid="rid-test", )
    enf._modes = {}
    assert me.profile_default_mode("chat", me.UngroundedObjectDetector()) == me.MODE_SHADOW
    assert enf.check_tool_call("world", WORLD_305) is None  # shadow: the call runs
    assert ("ungrounded_object", "shadow", "media_player.tv_a_invented") in house


def test_a_tool_result_recorded_earlier_grounds_the_write(house):
    enf = me.ModelEnforcer.for_profile("chat", "nb", rid="rid-test")
    enf._modes = {"chat:ungrounded_object": me.MODE_ON}
    enf.record_call("ha_read", {"action": "states"}, me.CALL_RAN, "media_player.tv_a_invented: off")
    assert enf.check_tool_call("world", WORLD_305) is None


def test_on_mode_returns_a_correction_naming_the_object(house):
    enf = me.ModelEnforcer.for_profile("chat", "en", rid="rid-test")
    enf._modes = {"chat:ungrounded_object": me.MODE_ON}
    out = enf.check_tool_call("world", WORLD_305)
    assert out and "media_player.tv_a_invented" in out


def test_correction_text_in_three_languages():
    for lang in ("nb", "en", "de"):
        assert "{items}" not in t("gen_correction_ungrounded_object", lang, items="x.y")
        assert "x.y" in t("gen_correction_ungrounded_object", lang, items="x.y")
