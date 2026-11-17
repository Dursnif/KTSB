"""P138 — GUI endpoints: voice pipeline settings (silence/ducking) and the per-node webhook token."""

import asyncio

import pytest
import yaml
from fastapi import HTTPException

import kaare_core.routers.router_settings as rs


@pytest.fixture
def offline(monkeypatch):
    """No call may reach the live voice bridge from a test."""
    monkeypatch.setattr(rs, "get_service", lambda *a, **k: "http://127.0.0.1:9")
    monkeypatch.setattr(rs, "media_host", lambda: "10.1.2.3")


def _voice(tmp_path, monkeypatch):
    p = tmp_path / "voice_settings.yaml"
    p.write_text(
        "stt:\n  max_record_seconds: 10  # cap\n  silence_threshold_seconds: 1.5\nduck:\n  mpd: true\n"
    )
    monkeypatch.setattr(rs, "_VOICE_SETTINGS_PATH", p)
    return p


def test_voice_pipeline_get_defaults_for_missing_keys(tmp_path, monkeypatch, offline):
    _voice(tmp_path, monkeypatch)
    view = rs.api_get_voice_pipeline(None)
    assert view["silence_seconds"] == 1.5 and view["max_record_seconds"] == 10
    assert (
        view["duck_on_listen"] is True and view["duck_volume"] == 0.2
    )  # code defaults (lightest tier)


def test_voice_pipeline_put_writes_and_keeps_comments(tmp_path, monkeypatch, offline):
    p = _voice(tmp_path, monkeypatch)
    out = asyncio.run(
        rs.api_put_voice_pipeline(
            {"silence_seconds": 0.8, "duck_volume": 0.1, "duck_on_listen": False}, None
        )
    )
    assert out["ok"] and out["silence_seconds"] == 0.8
    data = yaml.safe_load(p.read_text())
    assert (
        data["stt"]["silence_threshold_seconds"] == 0.8 and data["stt"]["max_record_seconds"] == 10
    )
    assert data["duck"] == {"mpd": True, "volume": 0.1, "on_listen": False}
    assert "# cap" in p.read_text()


@pytest.mark.parametrize(
    "payload",
    [
        {"silence_seconds": 99},
        {"duck_volume": -1},
        {"duck_on_listen": "yes"},
        {"nope": 1},
        {"max_record_seconds": "x"},
    ],
)
def test_voice_pipeline_put_rejects_bad_values(tmp_path, monkeypatch, offline, payload):
    _voice(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.api_put_voice_pipeline(payload, None))
    assert exc.value.status_code == 400


def test_node_webhook_new_and_remove(tmp_path, monkeypatch, offline):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": {"verksted": {"type": "esp32", "room": "Verksted"}}}))
    monkeypatch.setattr(rs, "_NODES_PATH", p)
    out = asyncio.run(rs.api_node_webhook("verksted", {"action": "new"}, None))
    assert out["url"] == "http://10.1.2.3:9/trigger/verksted" and len(out["token"]) >= 30
    assert yaml.safe_load(p.read_text())["nodes"]["verksted"]["webhook_token"] == out["token"]
    again = asyncio.run(rs.api_node_webhook("verksted", {"action": "new"}, None))
    assert again["token"] != out["token"]  # a new token replaces the old one
    gone = asyncio.run(rs.api_node_webhook("verksted", {"action": "remove"}, None))
    assert (
        gone["token"] == ""
        and "webhook_token" not in yaml.safe_load(p.read_text())["nodes"]["verksted"]
    )
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_node_webhook("ghost", {"action": "new"}, None))
