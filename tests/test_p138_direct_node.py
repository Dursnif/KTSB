"""P138 Phase 4b — KTSB Direct: the machine KTSB runs on is always a node; its speaker is LOCAL in routing."""

import asyncio

import pytest
import yaml
from fastapi import HTTPException

import kaare_core.routers.router_settings as rs
from kaare_core.voice import direct_node as dn
from kaare_core.voice import output_routing as r

NEST = {"type": "ha_media_player", "entity_id": "media_player.nest", "room": "Verksted"}


def test_with_direct_adds_one_and_keeps_input():
    nodes = {"nest": dict(NEST)}
    out = dn.with_direct(nodes)
    assert out["direct"]["type"] == "direct" and out["direct"]["enabled"] is True
    assert "direct" not in nodes  # input untouched
    assert dn.with_direct(out) == out  # idempotent


def test_with_direct_keeps_an_existing_direct_under_another_id():
    nodes = {"host": {"type": "direct", "room": "Verksted"}}
    assert list(dn.with_direct(nodes)) == ["host"]
    assert dn.direct_id(nodes) == "host"


def test_with_direct_adopts_a_user_node_called_direct():
    out = dn.with_direct({"direct": {"type": "esp32", "room": "Verksted"}})
    assert out["direct"]["type"] == "direct" and out["direct"]["room"] == "Verksted"


def test_keep_direct_restores_deleted_and_fixes_type():
    old = {"direct": {"type": "direct", "room": "Verksted", "default_user": "u1"}, "nest": NEST}
    assert dn.keep_direct(old, {"nest": NEST})["direct"]["room"] == "Verksted"
    changed = dn.keep_direct(old, {"direct": {"type": "esp32", "room": "Stue"}})
    assert changed["direct"] == {"type": "direct", "room": "Stue"}


def test_keep_direct_refuses_a_second_direct_node():
    old = {"direct": {"type": "direct"}, "nest": NEST}
    out = dn.keep_direct(
        old,
        {
            "direct": {"type": "direct"},
            "nest": {**NEST, "type": "direct"},
            "new": {"type": "direct"},
        },
    )
    assert out["nest"] == NEST and "new" not in out


def test_routing_direct_auto_is_local_even_in_esp32_mode():
    nodes = dn.with_direct({"nest": NEST})
    assert r.audio_targets("direct", nodes, r.INPUT_BUTTON, playback_mode="esp32") == [r.LOCAL]


def test_routing_explicit_direct_or_itself_is_local():
    nodes = dn.with_direct({"nest": NEST})
    nodes["direct"]["audio_out"] = ["direct", "nest"]
    assert r.audio_targets("direct", nodes, r.INPUT_BUTTON) == [r.LOCAL, "nest"]
    nodes["sat"] = {"type": "wyoming", "audio_out": ["direct"]}
    assert r.audio_targets("sat", nodes, r.INPUT_WYOMING) == [r.LOCAL]


def test_routing_speakerless_satellite_picks_direct_as_local():
    nodes = dn.with_direct({"sat": {"type": "wyoming", "has_audio": False, "room": "Verksted"}})
    nodes["direct"]["room"] = "Verksted"
    assert r.audio_targets("sat", nodes, r.INPUT_WYOMING) == [r.LOCAL]


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def offline(monkeypatch):
    """No call may reach the live voice bridge from a test."""
    monkeypatch.setattr(rs, "get_service", lambda *a, **k: "http://127.0.0.1:9")


def _nodes_file(tmp_path, monkeypatch, nodes):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": nodes}))
    monkeypatch.setattr(rs, "_NODES_PATH", p)
    return p


def test_api_get_nodes_shows_direct_before_it_is_saved(tmp_path, monkeypatch):
    _nodes_file(tmp_path, monkeypatch, {"nest": NEST})
    assert rs.api_get_nodes(None)["nodes"]["direct"]["type"] == "direct"


def test_api_put_nodes_cannot_delete_direct(tmp_path, monkeypatch, offline):
    p = _nodes_file(
        tmp_path, monkeypatch, {"nest": NEST, "direct": {"type": "direct", "room": "Verksted"}}
    )
    asyncio.run(rs.api_put_nodes({"nodes": {"nest": NEST}}, None))
    saved = yaml.safe_load(p.read_text())["nodes"]
    assert saved["direct"] == {"type": "direct", "room": "Verksted"}


def test_api_webhook_works_on_unsaved_direct(tmp_path, monkeypatch, offline):
    monkeypatch.setattr(rs, "media_host", lambda: "10.1.2.3")
    p = _nodes_file(tmp_path, monkeypatch, {"nest": NEST})
    out = asyncio.run(rs.api_node_webhook("direct", {"action": "new"}, None))
    assert yaml.safe_load(p.read_text())["nodes"]["direct"]["webhook_token"] == out["token"]


def _voice(tmp_path, monkeypatch):
    p = tmp_path / "voice_settings.yaml"
    p.write_text("audio:\n  mic_device: 8  # house\n  mic_sample_rate: 44100\n")
    monkeypatch.setattr(rs, "_VOICE_SETTINGS_PATH", p)
    return p


def test_direct_audio_get_without_bridge(tmp_path, monkeypatch, offline):
    _voice(tmp_path, monkeypatch)
    view = asyncio.run(rs.api_get_direct_audio(None))
    assert (
        view["mic_device"] == 8 and view["inputs"] == [] and view["error"] == "bridge_unreachable"
    )


def test_direct_audio_put_name_and_default(tmp_path, monkeypatch, offline):
    p = _voice(tmp_path, monkeypatch)
    asyncio.run(rs.api_put_direct_audio({"mic_device": "USB Audio Device"}, None))
    audio = yaml.safe_load(p.read_text())["audio"]
    assert audio == {"mic_device": "USB Audio Device", "mic_sample_rate": None}
    asyncio.run(rs.api_put_direct_audio({"mic_device": ""}, None))
    assert yaml.safe_load(p.read_text())["audio"]["mic_device"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"mic_device": True},
        {"mic_device": ["x"]},
        {"mic_device": "a\nb"},
        {"mic_device": "x" * 201},
        {"mic_device": -1},
        {"rate": 1},
    ],
)
def test_direct_audio_put_rejects_bad_values(tmp_path, monkeypatch, offline, payload):
    _voice(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.api_put_direct_audio(payload, None))
    assert exc.value.status_code == 400
