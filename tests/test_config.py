"""
Config loading tests.

Verifies that all config files load correctly and return valid values.
No network, no GPU needed.
Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_config.py -v
"""
import sys
sys.path.insert(0, "/kaare")

from kaare_core.config import (
    get_model, get_llm_config, get_service, get_settings,
    filter_tools_by_model, is_embedding_enabled, is_stt_enabled,
)


# ── Modeller ──────────────────────────────────────────────────────────────────

def test_get_model_kare_returns_string():
    m = get_model("kare")
    assert isinstance(m, str) and len(m) > 0

def test_get_model_miss_kare_returns_string():
    m = get_model("miss_kare")
    assert isinstance(m, str) and len(m) > 0

def test_get_model_unknown_role_raises():
    """Ukjent rolle skal kaste KeyError — ingen stille fallback."""
    import pytest
    with pytest.raises(KeyError):
        get_model("finnes_ikke_denne_rollen_xyzzy")


# ── LLM-konfig ────────────────────────────────────────────────────────────────

def test_get_llm_config_default_has_required_keys():
    cfg = get_llm_config("default")
    assert isinstance(cfg, dict)
    for key in ("base_url", "model_role"):
        assert key in cfg, f"Mangler nøkkel '{key}' i llm_config default"

def test_get_llm_config_base_url_is_string():
    cfg = get_llm_config("default")
    assert isinstance(cfg.get("base_url"), str)
    assert len(cfg["base_url"]) > 0


# ── Tjenester ─────────────────────────────────────────────────────────────────

def test_get_service_ollama_returns_something():
    result = get_service("ollama")
    assert result is not None

def test_get_service_ha_url_is_string():
    result = get_service("home_assistant", "url")
    assert isinstance(result, str) and len(result) > 0


# ── Settings ──────────────────────────────────────────────────────────────────

def test_get_settings_returns_dict():
    s = get_settings()
    assert isinstance(s, dict)

def test_settings_has_language():
    s = get_settings()
    lang = s.get("kare_language") or s.get("language")
    assert lang in ("nb", "en", "de"), f"Ugyldig language: {lang!r}"


# ── filter_tools_by_model ─────────────────────────────────────────────────────

def test_filter_tools_small_model_gets_no_tools():
    """Modeller under 9B skal ikke få tools."""
    dummy_tools = [{"name": "kamera"}, {"name": "library"}]
    result = filter_tools_by_model(dummy_tools, size_b=3.0)
    assert result == [], f"3B-modell fikk tools: {result}"

def test_filter_tools_large_model_gets_tools():
    """27B-modell skal få tools — bruker ekte tool-format fra definitions."""
    from kaare_core.tools.definitions import get_tools
    tools = get_tools("nb")
    result = filter_tools_by_model(tools, size_b=27.0)
    assert len(result) > 0


# ── Feature flags ─────────────────────────────────────────────────────────────

def test_is_embedding_enabled_returns_bool():
    assert isinstance(is_embedding_enabled(), bool)

def test_is_stt_enabled_returns_bool():
    assert isinstance(is_stt_enabled(), bool)
