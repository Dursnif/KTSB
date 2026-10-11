"""
Tests for P105 Phase 3 Step 2 — executor_world.py / executor_personality.py /
notisblokk.py rerouted from flat files (world.md/personality_self.md/
notater.json/world_vars.json) to knowledge_store.

Every test points knowledge_store.DB_PATH at a fresh tmp_path file.

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_executor_knowledge_reroute.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import asyncio

import pytest

import kaare_core.memory.knowledge_store as ks
import kaare_core.tools.executor_personality as executor_personality
import kaare_core.tools.executor_world as executor_world
import kaare_core.tools.notisblokk as notisblokk
from kaare_core.tools.definitions import get_tools


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "test_knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    yield


def _run(coro):
    return asyncio.run(coro)


# ── executor_world.py ────────────────────────────────────────────────────────


def test_world_add_and_read():
    _run(
        executor_world.dispatch(
            "world",
            {
                "action": "add",
                "category": "GPU",
                "text": "Blackwell er GPU0.",
                "_user_id": "global",
            },
        )
    )
    result = _run(executor_world.dispatch("world", {"action": "read", "_user_id": "global"}))
    assert "Blackwell er GPU0." in result
    assert "## GPU" in result


def test_world_update_field_upserts_not_duplicates():
    """Repeated update_field on the same category/field must update in place,
    not pile up duplicate rows — the improvement over the old line-rewrite."""
    args_base = {"action": "update_field", "category": "GPU", "field": "vram", "_user_id": "global"}
    _run(executor_world.dispatch("world", {**args_base, "value": "24GB"}))
    _run(executor_world.dispatch("world", {**args_base, "value": "22GB brukt"}))
    facts = ks.get_all_facts("world")
    assert len(facts) == 1
    assert facts[0]["content"] == "22GB brukt"


def test_world_variables_roundtrip_and_upsert():
    set_args = {
        "action": "set_var",
        "key": "test_key",
        "value": "42",
        "description": "testverdi",
        "_user_id": "global",
    }
    _run(executor_world.dispatch("world", set_args))
    result = _run(
        executor_world.dispatch(
            "world", {"action": "read_var", "key": "test_key", "_user_id": "global"}
        )
    )
    assert "42" in result
    # upsert: setting again must not create a second row
    _run(executor_world.dispatch("world", {**set_args, "value": "43"}))
    assert len(ks.find_by_section_prefix("world", "var::")) == 1
    result2 = _run(
        executor_world.dispatch(
            "world", {"action": "read_var", "key": "test_key", "_user_id": "global"}
        )
    )
    assert "43" in result2


def test_world_variables_excluded_from_prose_read():
    _run(
        executor_world.dispatch(
            "world", {"action": "set_var", "key": "k", "value": "v", "_user_id": "global"}
        )
    )
    result = _run(executor_world.dispatch("world", {"action": "read", "_user_id": "global"}))
    assert "k" not in result or "var" not in result.lower()
    assert "tom" in result.lower()  # world_file_empty message, since only a var exists


def test_world_delete_and_edit_affect_all_matches():
    _run(
        executor_world.dispatch(
            "world", {"action": "add", "category": "X", "text": "duplikat A", "_user_id": "global"}
        )
    )
    _run(
        executor_world.dispatch(
            "world",
            {
                "action": "add",
                "category": "X",
                "text": "duplikat B (duplikat)",
                "_user_id": "global",
            },
        )
    )
    _run(
        executor_world.dispatch(
            "world", {"action": "delete", "fragment": "duplikat", "_user_id": "global"}
        )
    )
    assert ks.get_all_facts("world") == []


# ── executor_personality.py ──────────────────────────────────────────────────


def test_selfimage_update_requires_allowed_user():
    """configs/settings.yaml ships with contributors: selected, allowed_users:
    [stian] in this live install — a non-listed real user is silently no-oped
    (matches pre-P105 behavior, not a regression)."""
    _run(
        executor_personality.dispatch(
            "selvbilde", {"action": "update", "observation": "test", "_user_id": "some_other_user"}
        )
    )
    assert ks.get_all_facts("personality") == []


def test_selfimage_autonomous_kare_bypasses_the_gate():
    """FIX-log 2026-09-07: Jang's autonomous reflection loop calls Kåre's own
    tools with user_id="kare" (reflection_loop.py:151) — that must always be
    allowed, regardless of the contributors/allowed_users setting, since it
    isn't a human user's influence at all."""
    result = _run(
        executor_personality.dispatch(
            "selvbilde",
            {"action": "update", "observation": "autonom observasjon", "_user_id": "kare"},
        )
    )
    assert "Notert" in result
    facts = ks.get_all_facts("personality")
    assert len(facts) == 1
    assert "autonom observasjon" in facts[0]["content"]


def test_selfimage_edit_and_delete_use_fragment_matching():
    _run(
        executor_personality.dispatch(
            "selvbilde", {"action": "update", "observation": "korte svar", "_user_id": "kare"}
        )
    )
    _run(
        executor_personality.dispatch(
            "selvbilde",
            {"action": "edit", "fragment": "korte", "new_text": "presise svar", "_user_id": "kare"},
        )
    )
    read = _run(executor_personality.dispatch("selvbilde", {"action": "read", "_user_id": "kare"}))
    assert "presise svar" in read
    _run(
        executor_personality.dispatch(
            "selvbilde", {"action": "delete", "fragment": "presise", "_user_id": "kare"}
        )
    )
    assert ks.get_all_facts("personality") == []


# ── notisblokk.py ─────────────────────────────────────────────────────────────


def test_note_write_read_delete():
    result = notisblokk.skriv_notat("Sjekk dekktrykk", kategori="hus")
    assert "Notert" in result
    facts = ks.get_all_facts("notes")
    assert len(facts) == 1
    note_id = str(facts[0]["id"])
    assert note_id in notisblokk.les_notater()
    assert "slettet" in notisblokk.slett_notat(note_id).lower()
    assert ks.get_all_facts("notes") == []


def test_note_mark_important_and_clear():
    notisblokk.skriv_notat("viktig sak", kategori="diverse")
    note_id = str(ks.get_all_facts("notes")[0]["id"])
    assert ks.has_unprocessed_important_tag() is False
    notisblokk.marker_notat_viktig(note_id, True)
    assert ks.has_unprocessed_important_tag() is True
    listing = notisblokk.les_notater()
    assert "⭐" in listing


def test_note_toem_notater_bulk_retire():
    notisblokk.skriv_notat("a", kategori="handle")
    notisblokk.skriv_notat("b", kategori="handle")
    notisblokk.skriv_notat("c", kategori="diverse")
    notisblokk.tøm_notater("handle")
    remaining = ks.get_all_facts("notes")
    assert len(remaining) == 1
    assert remaining[0]["section"] == "diverse"


def _world_action_enum() -> list[str]:
    for tool in get_tools("en"):
        fn = tool.get("function", tool)
        if fn["name"] == "world":
            return fn["parameters"]["properties"]["action"]["enum"]
    raise AssertionError("world tool not found in get_tools()")


def test_world_set_var_uses_exact_enum_value_from_definitions():
    # The model sends what definitions.py offers; «sett_var» in the tests hid a dispatch that
    # only accepted the old name (N25 / dev-slot finding 2026-10-11).
    set_var = next(a for a in _world_action_enum() if a.endswith("_var") and a.startswith("set"))
    result = _run(
        executor_world.dispatch(
            "world", {"action": set_var, "key": "enum_key", "value": "7", "_user_id": "global"}
        )
    )
    assert "Unknown action" not in result
    assert len(ks.find_by_section_prefix("world", "var::")) == 1


def test_world_every_enum_action_is_dispatched():
    for action in _world_action_enum():
        result = _run(
            executor_world.dispatch(
                "world",
                {"action": action, "key": "k", "value": "v", "fragment": "x", "_user_id": "global"},
            )
        )
        assert "Unknown action" not in result, action
