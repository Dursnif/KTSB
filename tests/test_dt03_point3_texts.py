"""Tool texts and honest messages found in DT03 R2 (2026-10-06/07).

- «Skriv et notat» landed on the main agent's own list: the user's notepad was described as «design notes».
- A plain law question went to web_search: the library description said «four operations» and never named `law`.
- With the embedding service off, search_argus told the model «Argus unavailable: All connection attempts failed».
"""

import asyncio

import pytest

from kaare_core.tools import executor_agents
from kaare_core.tools.i18n import t


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_library_description_names_law_before_web_search(lang):
    desc = t("tool_library_desc", lang)
    assert "action='law'" in desc and "web_search" in desc
    assert "action='fetch_table'" in desc


@pytest.mark.parametrize(
    "lang,word", [("nb", "notisblokk"), ("en", "notepad"), ("de", "Notizblock")]
)
def test_note_lists_point_user_notes_to_architect(lang, word):
    desc = t("tool_notat_list_name_desc", lang)
    architect = desc.split("'shopping'")[0]
    assert word in architect  # the user's notepad is the default
    kare = desc.split("'kare'=")[1].split("'agenda'")[0]
    assert "architect" not in kare and ("EGNE" in kare or "OWN" in kare or "EIGENEN" in kare)


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_search_argus_is_honest_when_embedding_is_off(monkeypatch, lang):
    def fake_svc(section, key=None):
        if section == "embedding":
            return {"enabled": False}
        raise AssertionError("no network lookups when embedding is off")

    monkeypatch.setattr(executor_agents, "_svc", fake_svc)
    out = asyncio.run(executor_agents._search_argus("anything", lang=lang))
    assert out == t("agent_argus_embedding_off", lang)
    assert "BGE-M3" in out
