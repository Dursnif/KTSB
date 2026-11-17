"""P144-s19 item 6: Frigate's MQTT topic prefix moves from the broker card (Integrations → MQTT) to the Frigate tab.
Stored as `frigate.mqtt_topic_prefix`; an install from before keeps its value (config migration step 10 copies
`mqtt.topic_prefix`, the old key stays); an older GUI still sending `topic_prefix` to the MQTT route writes the Frigate
key. tmp files only."""

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from kaare_core import config_migrations as cm
from kaare_core.routers import router_settings as rs


def test_step_10_copies_the_old_value():
    data = {"mqtt": {"host": "h", "topic_prefix": "cams"}, "frigate": {"url": "u"}}
    assert cm._s10_frigate_topic_prefix(data, {})
    assert data["frigate"]["mqtt_topic_prefix"] == "cams"
    assert data["mqtt"]["topic_prefix"] == "cams"  # old key kept


def test_step_10_never_overwrites_and_skips_fresh_installs():
    data = {"mqtt": {"topic_prefix": "cams"}, "frigate": {"mqtt_topic_prefix": "mine"}}
    assert cm._s10_frigate_topic_prefix(data, {}) == []
    assert data["frigate"]["mqtt_topic_prefix"] == "mine"
    fresh = {"mqtt": {"host": ""}, "frigate": {}}
    assert cm._s10_frigate_topic_prefix(fresh, {}) == []
    assert "mqtt_topic_prefix" not in fresh["frigate"]


def test_step_10_is_registered():
    assert any(s.number == 10 and s.filename == "services.yaml" for s in cm.STEPS)


def test_template_has_the_key_on_frigate_not_on_mqtt():
    t = yaml.safe_load(Path("/kaare/configs_default/services.yaml").read_text(encoding="utf-8"))
    assert t["frigate"]["mqtt_topic_prefix"] == "frigate"
    assert "topic_prefix" not in t["mqtt"]


@pytest.fixture
def services(monkeypatch, tmp_path):
    p = tmp_path / "services.yaml"
    p.write_text("mqtt:\n  host: h\n  topic_prefix: old\nfrigate:\n  url: http://f\n")
    monkeypatch.setattr(rs, "_SERVICES_PATH", p)
    monkeypatch.setattr(rs, "_set_domain_enabled", lambda *a, **k: None)
    return p


def test_frigate_route_saves_and_validates_the_prefix(services):
    asyncio.run(rs.api_put_services_frigate({"mqtt_topic_prefix": "cams"}, _u=None))
    assert yaml.safe_load(services.read_text())["frigate"]["mqtt_topic_prefix"] == "cams"
    for bad in ("", "a/#", "a/+/b", "a b"):
        with pytest.raises(HTTPException):
            asyncio.run(rs.api_put_services_frigate({"mqtt_topic_prefix": bad}, _u=None))


def test_old_gui_sending_it_to_mqtt_writes_the_frigate_key(services):
    asyncio.run(rs.api_put_services_mqtt({"topic_prefix": "cams"}, _u=None))
    data = yaml.safe_load(services.read_text())
    assert data["frigate"]["mqtt_topic_prefix"] == "cams"
