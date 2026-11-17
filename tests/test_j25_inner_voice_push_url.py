"""J25 — the inner voices' push URL is set in the GUI (rule 25): Innstillinger → LLM/Modeller → Indre stemmer (Jing & Jang).

- GET returns each voice's push URL; PUT stores it; an empty value = write the files directly (same machine / shared mount);
- only an http(s) URL is accepted (anything else 400, nothing written) — a typo must not silently stop the thoughts;
- the shipped template has the key empty, so migrate_configs gives existing installs the field.
No live state: services.yaml/settings.yaml point at tmp_path."""

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from kaare_core.routers import router_settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    svc, st = tmp_path / "services.yaml", tmp_path / "settings.yaml"
    svc.write_text("jing: {provider: openvino, push_url: ''}\njang: {provider: openvino, push_url: ''}\ninner_voices: {}\n")
    st.write_text("{}\n")
    monkeypatch.setattr(router_settings, "_SERVICES_PATH", svc)
    monkeypatch.setattr(router_settings, "_SETTINGS_PATH", st)
    return svc


def _put(payload):
    return asyncio.run(router_settings.api_put_inner_voices(payload, _u={"role": "admin"}))


def test_push_url_round_trip(cfg):
    _put({"jing_push_url": "http://192.0.2.10:8000/api/inner-voices/push", "jang_push_url": ""})
    out = asyncio.run(router_settings.api_get_inner_voices(_u={"role": "admin"}))
    assert out["jing"]["push_url"] == "http://192.0.2.10:8000/api/inner-voices/push" and out["jang"]["push_url"] == ""


@pytest.mark.parametrize("bad", ["ftp://x/push", "192.0.2.10:8000/api/inner-voices/push", "http://", "javascript:alert(1)"])
def test_only_http_urls(cfg, bad):
    before = cfg.read_text()
    with pytest.raises(HTTPException) as e:
        _put({"jing_push_url": bad})
    assert e.value.status_code == 400 and cfg.read_text() == before


def test_template_ships_the_empty_field():
    tpl = yaml.safe_load((ROOT / "configs_default" / "services.yaml").read_text())
    assert tpl["jing"]["push_url"] == "" and tpl["jang"]["push_url"] == ""
