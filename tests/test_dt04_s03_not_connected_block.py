"""DT04 2.5: the prompt names what is NOT connected, so Kåre does not claim to know rooms and sensors."""

from pathlib import Path

import pytest
import yaml

from adapters import llm_adapter as la


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_no_ha_url_is_named(monkeypatch, lang):
    monkeypatch.setattr(la, "get_service", lambda s, k=None: "" if s == "home_assistant" else "http://x")
    out = la._build_not_connected_block(lang)
    assert "Home Assistant" in out
    assert "Frigate" not in out


def test_frigate_and_ha_both_named(monkeypatch):
    monkeypatch.setattr(la, "get_service", lambda s, k=None: "")
    out = la._build_not_connected_block("en")
    assert "Home Assistant" in out and "Frigate" in out


def test_everything_connected_is_empty(monkeypatch):
    monkeypatch.setattr(la, "get_service", lambda s, k=None: "http://x")
    assert la._build_not_connected_block("nb") == ""


def test_template_reason_freely_follows_the_default_role():
    """DT04 retest: a copied model_role/provider on reason_freely drifted from the default (27B name on an Ollama URL)."""
    llm = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs_default" / "llm.yaml").read_text())
    assert llm["reason_freely"].get("share_with") == "default"
    assert "model_role" not in llm["reason_freely"] and "base_url" not in llm["reason_freely"]
