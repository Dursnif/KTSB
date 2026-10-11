"""
Tests for P105 Phase 3 Step 6 — kaare_core/memory/knowledge_reflection.py.

Covers the two safety mechanisms built after a real production incident
2026-09-07 (BUG-log/FIX-log): a single LLM call over ~116 structurally
similar facts collapsed into repetitive output and corrupted real importance
scores before any guard existed. _looks_degenerate() and
_repair_truncated_json_array() are regression tests against that exact
failure shape, not speculative coverage.

Every test points knowledge_store.DB_PATH at a fresh tmp_path file, and
mocks adapters.llm_adapter.call_llm_chat/_build_system — no real LLM/GPU
call, no live production data touched.

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_knowledge_reflection.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

import adapters.llm_adapter as llm_adapter
import kaare_core.memory.knowledge_reflection as kr
import kaare_core.memory.knowledge_store as ks


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "test_knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    monkeypatch.setattr(kr, "_LAST_RUN_PATH", tmp_path / "last_run.json")
    monkeypatch.setattr(llm_adapter, "_build_system", lambda base, user_id="": "FAKE SYSTEM PROMPT")
    monkeypatch.setattr(
        kr, "_build_system", lambda base, user_id="": "FAKE SYSTEM PROMPT"
    )  # patched where used
    yield


# ── _looks_degenerate() — regression test against the actual 2026-09-07 incident ──


def test_looks_degenerate_catches_the_real_incident_pattern():
    garbage = [
        {
            "id": i,
            "action": "reimportance",
            "new_importance": 0.7,
            "reason": "Recurring instability needs prioritized fix.",
        }
        for i in range(1, 59)
    ]
    assert kr._looks_degenerate(garbage) is not None


def test_looks_degenerate_allows_varied_legitimate_output():
    varied = [
        {"id": 1, "action": "retire", "reason": "Superseded by 2026-09-04 correction."},
        {
            "id": 2,
            "action": "reimportance",
            "new_importance": 0.8,
            "reason": "Critical hardware fact.",
        },
        {"id": 3, "action": "retire", "reason": "One-off, never resurfaced."},
        {
            "id": 4,
            "action": "reimportance",
            "new_importance": 0.3,
            "reason": "Situational, low value.",
        },
        {"id": 5, "action": "retire", "reason": "Duplicate of a clearer entry."},
    ]
    assert kr._looks_degenerate(varied) is None


def test_looks_degenerate_does_not_false_positive_on_small_incidental_repeat():
    small = [
        {"id": 1, "action": "retire", "reason": "stale car detection log"},
        {"id": 2, "action": "retire", "reason": "stale car detection log"},
        {
            "id": 3,
            "action": "reimportance",
            "new_importance": 0.6,
            "reason": "genuine hardware fact",
        },
    ]
    assert kr._looks_degenerate(small) is None  # below the 5-change threshold


# ── _repair_truncated_json_array() ──────────────────────────────────────────────


def test_repair_recovers_complete_objects_from_truncated_output():
    truncated = (
        '[{"id": 1, "action": "retire", "reason": "ok"}, '
        '{"id": 2, "action": "reimportance", "new_importance": 0.7, "reason": "fine"}, '
        '{"id": 3, "action": "retire", "reason": "cut off here and never clo'
    )
    result = kr._parse_llm_response(truncated)
    assert len(result) == 2
    assert result[0]["id"] == 1 and result[1]["id"] == 2


def test_parse_llm_response_handles_clean_json_normally():
    clean = json.dumps([{"id": 1, "action": "retire", "reason": "x"}])
    assert kr._parse_llm_response(clean) == [{"id": 1, "action": "retire", "reason": "x"}]


def test_parse_llm_response_strips_markdown_fences():
    fenced = "```json\n" + json.dumps([{"id": 1, "action": "retire", "reason": "x"}]) + "\n```"
    assert kr._parse_llm_response(fenced) == [{"id": 1, "action": "retire", "reason": "x"}]


# ── _apply_changes() — including cluster fan-out ────────────────────────────────


def test_apply_changes_retire_and_reimportance():
    id1 = ks.add_fact(domain="world", content="a", importance=0.5)
    id2 = ks.add_fact(domain="world", content="b", importance=0.5)
    changes = [
        {"id": id1, "action": "retire", "reason": "x"},
        {"id": id2, "action": "reimportance", "new_importance": 0.8, "reason": "y"},
    ]
    result = kr._apply_changes(changes, valid_ids={id1, id2})
    assert result == {"retired": 1, "reimportanced": 1}
    assert ks.get_all_facts("world")[0]["importance"] == 0.8


def test_apply_changes_ignores_unknown_ids():
    id1 = ks.add_fact(domain="world", content="a")
    changes = [{"id": 9999, "action": "retire", "reason": "hallucinated id"}]
    result = kr._apply_changes(changes, valid_ids={id1})
    assert result == {"retired": 0, "reimportanced": 0}
    assert len(ks.get_all_facts("world")) == 1


def test_apply_changes_fans_out_to_cluster_members():
    """The knowledge_dedup.py integration: a decision on a cluster
    representative must apply to every member of that cluster, not just the
    representative's own row."""
    rep_id = ks.add_fact(domain="notes", content="Bil på nedside vest (88%)")
    dup_id = ks.add_fact(domain="notes", content="Bil på nedside vest (91%)")
    other_id = ks.add_fact(domain="notes", content="Sjekk dekktrykk")
    id_to_cluster_ids = {rep_id: [rep_id, dup_id]}  # other_id is its own singleton, not listed
    changes = [{"id": rep_id, "action": "retire", "reason": "repeated car detection noise"}]
    result = kr._apply_changes(
        changes, valid_ids={rep_id, dup_id, other_id}, id_to_cluster_ids=id_to_cluster_ids
    )
    assert result["retired"] == 2
    remaining = ks.get_all_facts("notes")
    assert len(remaining) == 1
    assert remaining[0]["id"] == other_id


# ── should_run() ─────────────────────────────────────────────────────────────


def test_should_run_true_when_no_last_run_recorded():
    assert kr.should_run() is True


def test_should_run_false_within_the_week():
    kr._set_last_run(datetime.now(UTC))
    assert kr.should_run() is False


def test_should_run_true_after_a_week():
    kr._set_last_run(datetime.now(UTC) - timedelta(days=8))
    assert kr.should_run() is True


def test_should_run_true_on_pending_important_tag_even_within_the_week():
    kr._set_last_run(datetime.now(UTC))
    fid = ks.add_fact(domain="notes", content="x")
    ks.set_important_tag(fid, True)
    assert kr.should_run() is True


# ── run() end-to-end with mocked LLM + mocked clustering (no real HTTP) ─────────


def test_run_end_to_end_with_mocked_llm(monkeypatch):
    id1 = ks.add_fact(domain="personality", content="[2026-05-13] test observation", importance=0.5)

    async def fake_cluster_facts(facts, threshold=None):
        return [[f] for f in facts]  # no grouping — one cluster per fact

    async def fake_call_llm_chat(role, messages, **kwargs):
        return {
            "ok": True,
            "text": json.dumps(
                [{"id": id1, "action": "reimportance", "new_importance": 0.2, "reason": "minor"}]
            ),
        }

    monkeypatch.setattr(kr.knowledge_dedup, "cluster_facts", fake_cluster_facts)
    monkeypatch.setattr(llm_adapter, "call_llm_chat", fake_call_llm_chat)
    monkeypatch.setattr(kr, "call_llm_chat", fake_call_llm_chat)  # patched where used

    summary = asyncio.run(kr.run())
    assert summary == {"reviewed": 1, "retired": 0, "reimportanced": 1, "errors": 0}
    assert ks.get_all_facts("personality")[0]["importance"] == 0.2
    assert ks.has_unprocessed_important_tag() is False


def test_run_clears_important_tags_and_sets_last_run_even_with_no_facts():
    assert kr._get_last_run() is None
    summary = asyncio.run(kr.run())
    assert summary == {"reviewed": 0, "retired": 0, "reimportanced": 0, "errors": 0}
    assert kr._get_last_run() is not None
