"""
Tests for P105 Phase 3 Step 3 — adapters/llm_adapter.py's personality_self/
world_ctx prompt blocks, rerouted from raw[:2000]/a module-level file cache
to live knowledge_store queries.

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_llm_adapter_knowledge_context.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import pytest

import adapters.llm_adapter as llm_adapter
import kaare_core.memory.knowledge_store as ks


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "test_knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    yield


def test_personality_self_block_empty_when_no_facts():
    assert llm_adapter._load_personality_self_block() == ""


def test_personality_self_block_renders_as_bullets():
    ks.add_fact(domain="personality", content="[2026-05-13] Jeg er kort og direkte.")
    ks.add_fact(domain="personality", content="[2026-06-01] Jeg spør før jeg antar.")
    block = llm_adapter._load_personality_self_block()
    assert "- [2026-05-13] Jeg er kort og direkte." in block
    assert "- [2026-06-01] Jeg spør før jeg antar." in block


def test_personality_self_block_respects_limit(monkeypatch):
    monkeypatch.setattr(llm_adapter, "_PERSONALITY_SELF_LIMIT", 2)
    for i in range(5):
        ks.add_fact(domain="personality", content=f"observasjon {i}", importance=0.5)
    block = llm_adapter._load_personality_self_block()
    assert len(block.splitlines()) == 2


def test_world_ctx_empty_when_no_facts():
    assert llm_adapter._render_world_ctx() == ""


def test_world_ctx_groups_by_category_and_renders_fields():
    ks.add_fact(domain="world", section="GPU", content="Blackwell er GPU0.")
    ks.add_fact(domain="world", section="GPU::vram", content="24GB")
    ctx = llm_adapter._render_world_ctx()
    assert "## GPU" in ctx
    assert "Blackwell er GPU0." in ctx
    assert "- vram: 24GB" in ctx


def test_world_ctx_excludes_variables():
    ks.add_fact(domain="world", section="GPU", content="Blackwell er GPU0.")
    ks.add_fact(domain="world", section="var::test_key", content='{"value": "42"}')
    ctx = llm_adapter._render_world_ctx()
    assert "Blackwell" in ctx
    assert "test_key" not in ctx
    assert "42" not in ctx


def test_world_ctx_respects_limit(monkeypatch):
    monkeypatch.setattr(llm_adapter, "_WORLD_CTX_LIMIT", 2)
    for i in range(5):
        ks.add_fact(domain="world", section="Notes", content=f"fakta {i}", importance=0.5)
    ctx = llm_adapter._render_world_ctx()
    # 2 facts shown under one "## Notes" header
    lines = [ln for ln in ctx.splitlines() if ln.strip() and not ln.startswith("##")]
    assert len(lines) == 2
