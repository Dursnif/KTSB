"""
Tests for P105 Phase 3 Step 6 enhancement — kaare_core/memory/knowledge_dedup.py.

Near-duplicate clustering, built after Stian diagnosed the root cause of the
2026-09-07 reflection-job collapse: a batch of many structurally-similar
facts makes the LLM pattern-match the whole batch as "one case." These tests
mock the BGE-M3 embed call (fixed, hand-crafted vectors) rather than hitting
the real embedding service — deterministic, no live dependency, and lets us
assert exact cluster membership instead of just "it ran."

The 0.80 similarity threshold itself was calibrated against real production
text (see knowledge_dedup.py's module comment and P105 plan Phase log) —
these tests only verify the clustering *mechanism*, not that specific
calibration.

Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_knowledge_dedup.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import asyncio

import kaare_core.memory.knowledge_dedup as dedup


def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert dedup._cosine(v, v) == 1.0


def test_cosine_orthogonal_vectors():
    assert dedup._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_zero_vector_without_crashing():
    assert dedup._cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cluster_facts_groups_near_duplicates(monkeypatch):
    facts = [
        {"id": 1, "content": "Bil på nedside vest (88%) – 10. juni."},
        {"id": 2, "content": "Bil på nedside vest (91%) – 11. juni."},
        {"id": 3, "content": "Sjekk dekktrykk før lang kjøretur."},
    ]
    # hand-crafted embeddings: 1&2 near-identical, 3 orthogonal
    fake_embeddings = [
        [1.0, 0.0, 0.0],
        [0.99, 0.01, 0.0],
        [0.0, 1.0, 0.0],
    ]

    async def fake_embed_batch(texts):
        assert texts == [f["content"] for f in facts]
        return fake_embeddings

    monkeypatch.setattr(dedup, "_embed_batch", fake_embed_batch)

    clusters = asyncio.run(dedup.cluster_facts(facts, threshold=0.8))
    assert len(clusters) == 2
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]
    car_cluster = next(c for c in clusters if len(c) == 2)
    assert {f["id"] for f in car_cluster} == {1, 2}
    tire_cluster = next(c for c in clusters if len(c) == 1)
    assert tire_cluster[0]["id"] == 3


def test_cluster_facts_all_distinct_stays_singletons(monkeypatch):
    facts = [{"id": i, "content": f"fact {i}"} for i in range(3)]

    async def fake_embed_batch(texts):
        return [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]  # all far apart

    monkeypatch.setattr(dedup, "_embed_batch", fake_embed_batch)
    clusters = asyncio.run(dedup.cluster_facts(facts, threshold=0.8))
    assert len(clusters) == 3
    assert all(len(c) == 1 for c in clusters)


def test_cluster_facts_falls_back_to_singletons_on_embed_failure(monkeypatch):
    """Graceful degradation: if the embedding service is unreachable, don't
    crash the reflection job — just skip clustering (equivalent to today's
    un-clustered behavior)."""
    facts = [{"id": 1, "content": "a"}, {"id": 2, "content": "a"}]

    async def failing_embed_batch(texts):
        return None

    monkeypatch.setattr(dedup, "_embed_batch", failing_embed_batch)
    clusters = asyncio.run(dedup.cluster_facts(facts))
    assert len(clusters) == 2
    assert all(len(c) == 1 for c in clusters)


def test_cluster_facts_empty_input():
    assert asyncio.run(dedup.cluster_facts([])) == []
