"""P151 phase 6: Settings → Screens API (the GUI switch D11, live minutes, Frigate RTSP port) and the node dialog's
screen-page proxy. Uses temp files — the live configs are never touched."""

import asyncio

import pytest
import yaml
from fastapi import HTTPException

from kaare_core.routers import router_settings as rs
from kaare_core.voice import screen as sc


@pytest.fixture
def files(tmp_path, monkeypatch):
    s, v = tmp_path / "settings.yaml", tmp_path / "services.yaml"
    s.write_text("location: x\n", encoding="utf-8")
    v.write_text("frigate:\n  url: http://f:5000\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", s)
    monkeypatch.setattr(rs, "_SERVICES_PATH", v)
    return s, v


def test_get_defaults_when_nothing_is_set(files):
    out = asyncio.run(rs.api_get_screens(_u={}))
    assert out == {"enabled": True, "live_minutes": 2.0, "rtsp_port": 8554}


def test_put_writes_and_the_core_reads_it(files):
    s, v = files
    asyncio.run(rs.api_put_screens({"enabled": False, "live_minutes": 5, "rtsp_port": 18554}, _u={}))
    got = yaml.safe_load(s.read_text())["screens"]
    assert got == {"enabled": False, "live_minutes": 5.0} and yaml.safe_load(s.read_text())["location"] == "x"
    assert yaml.safe_load(v.read_text())["frigate"] == {"url": "http://f:5000", "rtsp_port": 18554}
    assert sc.enabled(got) is False and sc.live_minutes(None, got) == 5.0
    assert asyncio.run(rs.api_get_screens(_u={}))["enabled"] is False


@pytest.mark.parametrize("bad", [{"live_minutes": 0}, {"live_minutes": 999}, {"rtsp_port": 0}, {"live_minutes": "x"}])
def test_put_rejects_bad_values(files, bad):
    with pytest.raises(HTTPException) as e:
        asyncio.run(rs.api_put_screens({"enabled": True, "live_minutes": 2, "rtsp_port": 8554, **bad}, _u={}))
    assert e.value.status_code == 400


def test_node_screen_proxy_only_known_steps(monkeypatch):
    sent = []

    async def bridge(path, payload):
        sent.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(rs, "_bridge_tv", bridge)
    asyncio.run(rs.api_node_screen("samsung", "pair", {"code": "12345678999"}, _u={}))
    assert sent == [("/screen/pair", {"node": "samsung", "code": "12345678"})]
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_node_screen("samsung", "camera", {}, _u={}))
