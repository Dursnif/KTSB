"""DT04-S05b (2026-11-16, observed): a teen asked «Hvem ble sett på kameraene i dag?». `camera` is not in the teen's tool
list, so Kåre never saw it — and invented the reason: «Home Assistant er ikke koblet til … når integrasjonen er satt opp
kan jeg sjekke det». Frigate was configured; the real reason was the role. `action_claim` (P142) fired in shadow.

Now Kåre is told, by name and without the tool's schema, which tools his user's role does not have, and that he says
so honestly instead of guessing another reason (thread 9). Only role limits are named — tools missing for another reason
(model size, agents off, cloud off) are not. Strangers / household identities get no names (P120 knowledge boundary):
only that most tools are not available for them. Placeholders only; the permission table is a fixture."""

import pytest

from kaare_core import config
from kaare_core.routers import router_generate as rg
from kaare_core.tools.i18n import t

PERMS = {
    "always_included": ["reason_freely"],
    "no_always_included": ["household", "stranger"],
    "roles": {
        "admin": ["all"],
        "teen": ["ha_control", "timer", "note", "library", "library_online"],
        "stranger": ["get_weather"],
    },
}


@pytest.fixture
def perms(monkeypatch):
    monkeypatch.setattr(config, "_TOOL_PERMISSIONS", PERMS)


def test_teen_is_told_camera_is_withheld(perms):
    withheld = config.get_tools_withheld_for_role("teen")
    assert "camera" in withheld
    assert "timer" not in withheld and "note" not in withheld
    assert "reason_freely" not in withheld  # always included
    assert "library" not in withheld  # allowed (online variant is a flag, not a tool)


def test_admin_has_nothing_withheld(perms):
    assert config.get_tools_withheld_for_role("admin") == []


def test_admin_only_tools_count_as_withheld_for_others(perms):
    assert set(config.get_tools_withheld_for_role("teen")) & config.ADMIN_ONLY_TOOLS


def test_withheld_list_does_not_change_what_the_role_gets(perms):
    offered = {tl["function"]["name"] for tl in config.get_tools_for_role("teen")}
    assert not offered & set(config.get_tools_withheld_for_role("teen"))


def test_note_names_the_withheld_tools_and_says_be_honest(perms):
    note = rg._role_withheld_note("teen", "", "nb")
    assert "camera" in note
    assert note.startswith(t("gen_role_withheld", "nb", tools="")[:20])


def test_stranger_gets_no_tool_names(perms):
    note = rg._role_withheld_note("stranger", "", "en")
    assert note == t("gen_role_withheld_identity", "en")
    assert "camera" not in note


def test_admin_gets_no_note(perms):
    assert rg._role_withheld_note("admin", "", "de") == ""


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_texts_in_three_languages(lang):
    assert "x_tool" in t("gen_role_withheld", lang, tools="x_tool")
    assert t("gen_role_withheld_identity", lang)
