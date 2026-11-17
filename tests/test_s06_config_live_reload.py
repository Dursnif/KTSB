"""A setting saved in the GUI takes effect without a restart (rule 25; DT04-S05d).

`PUT /api/settings/services/ha` wrote services.yaml but `get_service()` kept serving the copy loaded at start-up, so the
HA registry sync answered `not_configured` (or kept the old URL) until the API was restarted or Hot reload was pressed.
"""

import asyncio
import re
from pathlib import Path

import pytest

import kaare_core.config as config
import kaare_core.config_store as config_store
import kaare_core.routers.router_settings as rs
import kaare_core.routers.router_system as rsys


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    (tmp_path / "services.yaml").write_text("home_assistant:\n  url: ''\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text("lang: en\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_SERVICES", {"home_assistant": {"url": ""}})
    monkeypatch.setattr(config, "_SETTINGS", {"lang": "en"})
    monkeypatch.setattr(rs, "_SERVICES_PATH", tmp_path / "services.yaml")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", tmp_path / "settings.yaml")
    return tmp_path


def test_put_ha_url_is_in_use_without_restart(cfg_dir, monkeypatch):
    async def _no_gateway():
        return True

    monkeypatch.setattr(rs, "_reload_ha_gateway", _no_gateway)
    monkeypatch.setattr(rs.ha_registry_sync, "request_resync", lambda: None)
    asyncio.run(rs.api_put_services_ha({"url": "http://ha.example:8123"}, _u=None))
    assert config.get_service("home_assistant", "url") == "http://ha.example:8123"


def test_any_services_or_settings_write_refreshes_the_cache(cfg_dir):
    config_store.write_yaml(cfg_dir / "services.yaml", {"frigate": {"url": "http://f:5000"}}, "test")
    assert config.get_service("frigate", "url") == "http://f:5000"
    config_store.write_yaml(cfg_dir / "settings.yaml", {"lang": "de"}, "test")
    assert config.get_settings()["lang"] == "de"


def test_writes_outside_the_config_dir_do_not_touch_the_cache(cfg_dir, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "services.yaml"
    config_store.write_yaml(other, {"frigate": {"url": "http://wrong"}}, "test")
    assert config.get_service("frigate", "url") in (None, "", {})


# ── what a save needs before it is in use (`applies`, shown by the GUI's save feedback) ──────────────────────────


def _put(fn, payload):
    return asyncio.run(fn(payload, _u=None))


def test_applies_ha_url_in_use_but_log_bridge_needs_restart(cfg_dir, monkeypatch):
    async def _gateway_ok():
        return True

    monkeypatch.setattr(rs, "_reload_ha_gateway", _gateway_ok)
    monkeypatch.setattr(rs.ha_registry_sync, "request_resync", lambda: None)
    assert _put(rs.api_put_services_ha, {"url": "http://ha:8123"})["applies"] == "restart:ha-log-bridge"
    assert _put(rs.api_put_services_ha, {"timeout": 5})["applies"] == "now"


def test_applies_ha_gateway_unreachable_says_restart_gateway(cfg_dir, monkeypatch):
    async def _gateway_down():
        return False

    monkeypatch.setattr(rs, "_reload_ha_gateway", _gateway_down)
    monkeypatch.setattr(rs.ha_registry_sync, "request_resync", lambda: None)
    assert _put(rs.api_put_services_ha, {"verify_seconds": 2})["applies"] == "restart:gateway"


def test_applies_per_service(cfg_dir, monkeypatch):
    monkeypatch.setattr(rs, "_MQTT_ENV_PATH", cfg_dir / "mqtt.env")
    monkeypatch.setattr(rs.plex_listener, "wake", lambda: None)
    assert _put(rs.api_put_services_mqtt, {"host": "broker"})["applies"] == "now"
    assert _put(rs.api_put_services_frigate, {"url": "http://f:5000"})["applies"] == "now"
    assert _put(rs.api_put_services_plex, {"url": "http://p:32400"})["applies"] == "now"
    assert _put(rs.api_put_services_embedding, {"device": "CPU"})["applies"] == "restart:embedding"
    assert _put(rs.api_put_services_embedding, {"emb_enabled": True})["applies"] == "now"
    assert _put(rs.api_put_services_voice, {"stt_backend": "openvino"})["applies"] == "restart:voice"


def test_every_restart_key_is_a_real_restart_button():
    src = Path(rs.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'_applies_restart\("([^"]+)"\)', src))
    assert keys and keys <= set(rsys._RESTARTABLE_SERVICES), keys - set(rsys._RESTARTABLE_SERVICES)
