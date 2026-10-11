"""Reply language and core personality follow the user's own language choice (pre-release fix 2026-10-10).

Before: the language instruction («Svar alltid på norsk.») and the core personality were built once from the
installation's language, so a user with their own language (profile `prompt_top.language`) was told to answer
in the household's language. BUG-log 2026-10-05.
No file I/O: the override lookup and the module-level blocks are patched.
"""

import pytest

import adapters.llm_adapter as la
from kaare_core.tools import i18n


@pytest.fixture
def install(monkeypatch):
    """A Norwegian installation in standard mode, with one known core text per language and mode."""
    monkeypatch.setattr(la, "_LANGUAGE_BLOKK", la._kare_lang_instruction("nb"))
    monkeypatch.setattr(la, "_PERSONALITY_MODE", "standard")
    monkeypatch.setattr(la, "_PERSONALITY_CORE", "CORE-standard-nb")
    monkeypatch.setattr(la, "_PERSONALITY_CORES", {f: f"CORE-standard-{f}" for f in ("nb", "en", "de")})
    monkeypatch.setattr(la, "_PERSONALITY_CORES_MINIMAL", {f: f"CORE-minimal-{f}" for f in ("nb", "en", "de")})
    monkeypatch.setattr(la, "_PERSONALITY_CORES_LETVEKT", {f: f"CORE-letvekt-{f}" for f in ("nb", "en", "de")})
    overrides: dict[str, str] = {}
    monkeypatch.setattr(la, "get_user_lang_override", lambda uid="global": overrides.get(uid))
    return overrides


def test_user_without_own_language_follows_installation(install):
    assert la._language_block_for("ola").startswith("Svar alltid på norsk.")
    assert la._personality_core_for("ola") == "CORE-standard-nb"


def test_user_with_own_language_gets_it(install):
    install["anna"] = "en"
    assert la._language_block_for("anna").startswith("Always reply in English.")
    assert la._personality_core_for("anna") == "CORE-standard-en"


def test_any_installation_language_is_kept_for_users_without_override(install, monkeypatch):
    monkeypatch.setattr(la, "_LANGUAGE_BLOKK", la._kare_lang_instruction("sv"))
    assert la._language_block_for("ola").startswith("Svara alltid på svenska.")


def test_override_keeps_the_installation_mode(install, monkeypatch):
    install["anna"] = "de"
    monkeypatch.setattr(la, "_PERSONALITY_MODE", "minimal")
    assert la._personality_core_for("anna") == "CORE-minimal-de"


def test_custom_core_is_used_as_is(install, monkeypatch):
    install["anna"] = "en"
    monkeypatch.setattr(la, "_PERSONALITY_MODE", "egendefinert")
    monkeypatch.setattr(la, "_PERSONALITY_CORE", "MY OWN CORE")
    assert la._personality_core_for("anna") == "MY OWN CORE"
    assert la._language_block_for("anna").startswith("Always reply in English.")


def test_fallback_prompt_uses_the_users_core(install, monkeypatch):
    install["anna"] = "en"
    monkeypatch.setattr(la, "get_lang", lambda uid="global": "en")
    prompt = la._build_system_fallback("anna", include_time=False)
    assert "CORE-standard-en" in prompt and "CORE-standard-nb" not in prompt


def test_override_lookup_without_a_user():
    assert i18n.get_user_lang_override("global") is None
    assert i18n.get_user_lang_override("") is None
    assert i18n.get_user_lang_override("no-such-user-p-test") is None
