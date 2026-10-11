"""P116 B22 — one-time import of pre-knowledge.db state files (synthetic data only)."""

import json

import pytest

import kaare_core.memory.knowledge_import as ki
import kaare_core.memory.knowledge_store as ks


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    monkeypatch.setattr(ks, "DB_PATH", state / "memory" / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    monkeypatch.setattr(ki, "_STATE", state)
    monkeypatch.setattr(ki, "_MARKER", state / "memory" / "knowledge_import_done.json")
    (state / "world.md").write_text(
        "# My world\n\nIntro text that is not a fact.\n\n---\n\n"
        "## Hardware\n- GPU: Example card\n- Host: box-1\n  - RAM: 32 GB\n\n## Rooms\nKitchen has two lamps\n",
        encoding="utf-8",
    )
    (state / "world_vars.json").write_text(
        json.dumps(
            {
                "house.rooms": {
                    "verdi": "4",
                    "beskrivelse": "Room count",
                    "satt": "2026-08-24",
                    "av": "Kåre",
                }
            }
        ),
        encoding="utf-8",
    )
    (state / "personality_self.md").write_text(
        "# Self\n- [2026-05-13] [2026-05-13] I am curious\n- [2026-06-01] I ask before guessing\n",
        encoding="utf-8",
    )
    (state / "notater.json").write_text(
        json.dumps([{"id": "a", "dato": "2026-07-01", "kategori": "Ideer", "tekst": "Try X"}]),
        encoding="utf-8",
    )
    return state


def test_imports_all_domains_once(sandbox):
    before = (sandbox / "world.md").read_bytes()
    result = ki.import_legacy_state()
    assert result == {"world": 4, "personality": 2, "notes": 1}
    world = {r["content"] for r in ks.get_all_facts("world")}
    assert "GPU: Example card" in world
    assert "Host: box-1; RAM: 32 GB" in world  # indented sub-bullet joined to its parent
    assert "Kitchen has two lamps" in world
    assert not any("Intro text" in c for c in world)  # text before the first section skipped
    var = ks.get_fact_by_section("world", "var::house.rooms")
    assert json.loads(var["content"]) == {"value": "4", "description": "Room count"}
    assert ks.get_all_facts("personality")[0]["content"].startswith(
        "[2026-05-13] I am"
    )  # doubled date collapsed
    assert ks.get_all_facts("notes")[0]["section"] == "ideer"
    assert (sandbox / "world.md").read_bytes() == before  # legacy file untouched
    assert ki.import_legacy_state() == {}  # marker stops a second run


def test_skips_domains_that_already_have_rows(sandbox):
    ks.add_fact(domain="notes", content="already here")
    result = ki.import_legacy_state()
    assert "notes" not in result and result["world"] == 4
    assert len(ks.get_all_facts("notes")) == 1
