"""
Tests for P105 Phase 3 — kaare_core/memory/knowledge_store.py, the shared
state_facts store that replaced world.md/personality_self.md/notater.json.

Every test points DB_PATH at a fresh tmp_path file (never the real
/kaare/state/memory/knowledge.db) — see the near-miss in BUG-log/FIX-log
2026-09-07 for exactly why that isolation matters.

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_knowledge_store.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import pytest

import kaare_core.memory.knowledge_store as ks


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the store at a throwaway file for every test in this module,
    and force re-init (the module caches an _initialized flag keyed to
    whatever DB_PATH was current the first time _ensure_init() ran)."""
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "test_knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    yield


def test_add_and_get_top_facts():
    ks.add_fact(
        domain="world", section="Hardware", content="GPU0 er en RTX PRO 4000.", importance=0.7
    )
    ks.add_fact(domain="world", section="Notes", content="Drivhus pågår.", importance=0.3)
    rows = ks.get_top_facts("world", limit=10)
    assert [r["content"] for r in rows] == ["GPU0 er en RTX PRO 4000.", "Drivhus pågår."]


def test_get_top_facts_orders_by_importance_then_recency():
    id_old = ks.add_fact(domain="world", content="eldre, lik viktighet", importance=0.5)
    id_new = ks.add_fact(domain="world", content="nyere, lik viktighet", importance=0.5)
    id_important = ks.add_fact(domain="world", content="viktigst", importance=0.9)
    rows = ks.get_top_facts("world", limit=10)
    ids_in_order = [r["id"] for r in rows]
    assert ids_in_order[0] == id_important  # importance wins first
    assert ids_in_order[1:] == [id_new, id_old]  # tie broken by recency (newest first)


def test_get_top_facts_excludes_section_prefix():
    ks.add_fact(domain="world", section="GPU", content="prosa-fakta")
    ks.add_fact(domain="world", section="var::vram", content='{"value": "24GB"}')
    rows = ks.get_top_facts("world", limit=10, exclude_section_prefixes=["var::"])
    assert len(rows) == 1
    assert rows[0]["content"] == "prosa-fakta"


def test_retire_fact_is_soft_delete():
    fid = ks.add_fact(domain="notes", content="skal pensjoneres")
    assert ks.retire_fact(fid) is True
    assert ks.get_all_facts("notes") == []
    # row still exists in the DB, just not returned as active
    stats = ks.get_stats()
    assert stats["total"] == 1
    assert stats["by_domain"] == {}


def test_update_fact_changes_fields_and_bumps_last_validated():
    fid = ks.add_fact(domain="world", content="original", importance=0.5)
    before = ks.get_all_facts("world")[0]["last_validated"]
    assert ks.update_fact(fid, content="oppdatert", importance=0.8) is True
    row = ks.get_all_facts("world")[0]
    assert row["content"] == "oppdatert"
    assert row["importance"] == 0.8
    assert row["last_validated"] >= before  # ISO timestamps; >= stays stable within one second


def test_get_fact_by_section_supports_upsert_addressing():
    """The pattern executor_world.py relies on for world_vars/update_field:
    section acts as a compound key so repeated writes update in place
    instead of piling up duplicate rows."""
    section = "var::test_key"
    assert ks.get_fact_by_section("world", section) is None
    ks.add_fact(domain="world", section=section, content="first")
    row1 = ks.get_fact_by_section("world", section)
    assert row1["content"] == "first"
    ks.update_fact(row1["id"], content="second")
    row2 = ks.get_fact_by_section("world", section)
    assert row2["id"] == row1["id"]
    assert row2["content"] == "second"
    assert len(ks.find_by_section_prefix("world", "var::")) == 1  # no duplicate row


def test_find_by_section_prefix():
    ks.add_fact(domain="world", section="var::a", content="1")
    ks.add_fact(domain="world", section="var::b", content="2")
    ks.add_fact(domain="world", section="GPU", content="not a var")
    rows = ks.find_by_section_prefix("world", "var::")
    assert {r["section"] for r in rows} == {"var::a", "var::b"}


def test_retire_all_scoped_to_section():
    ks.add_fact(domain="notes", section="handle", content="a")
    ks.add_fact(domain="notes", section="handle", content="b")
    ks.add_fact(domain="notes", section="diverse", content="c")
    count = ks.retire_all("notes", section="handle")
    assert count == 2
    remaining = ks.get_all_facts("notes")
    assert len(remaining) == 1
    assert remaining[0]["section"] == "diverse"


def test_retire_all_whole_domain():
    ks.add_fact(domain="notes", content="a")
    ks.add_fact(domain="notes", content="b")
    assert ks.retire_all("notes") == 2
    assert ks.get_all_facts("notes") == []


def test_important_tag_lifecycle():
    fid = ks.add_fact(domain="notes", content="viktig sak")
    assert ks.has_unprocessed_important_tag() is False
    ks.set_important_tag(fid, True)
    assert ks.has_unprocessed_important_tag() is True
    assert ks.clear_all_important_tags() == 1
    assert ks.has_unprocessed_important_tag() is False


def test_touch_all_validated_bumps_every_active_row():
    ks.add_fact(domain="world", content="a")
    ks.add_fact(domain="notes", content="b")
    before1 = ks.get_all_facts("world")[0]["last_validated"]
    count = ks.touch_all_validated()
    assert count == 2
    # values are ISO timestamps at second resolution; just confirm no error
    # and the row is still there and active — precise time-advance isn't
    # asserted to avoid a flaky same-second test
    assert len(ks.get_all_facts("world")) == 1
    assert len(ks.get_all_facts("notes")) == 1
    assert ks.get_all_facts("world")[0]["last_validated"] >= before1


def test_invalid_domain_rejected():
    assert ks.add_fact(domain="not_a_real_domain", content="x") is None
