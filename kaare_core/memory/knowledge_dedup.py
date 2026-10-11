"""
Near-duplicate clustering for knowledge_reflection.py (P105 Fase 3).

Groups near-identical facts (e.g. dozens of near-identical Frigate/Jang
observation-log entries accumulated over months) so the reflection LLM
judges one representative per cluster instead of every member individually.

This exists because sending many structurally-similar facts to the
reflection LLM in one call was found (2026-09-07, BUG-log/FIX-log) to
reliably trigger a repetitive-output collapse — the model's judgment on one
item silently spread to the whole batch, including facts that didn't
actually belong there, corrupting real data before a safety guard existed.
Splitting near-duplicates out algorithmically means the LLM only ever sees
genuinely heterogeneous batches, which is both cheaper (one judgment covers
N facts) and structurally avoids the collapse instead of just detecting it
after the fact.

Embedding via BGE-M3 (port 11446) — same service/call shape Argus already
uses to index its own log messages into Qdrant (kaare_argus.py:573-598,
`qdrant_upsert_batch`). Clustering itself runs in-process (batches here are
at most a few hundred facts) since there is no existing generic
cosine-similarity/clustering utility in the codebase — Qdrant does
similarity scoring internally, but only for actual Qdrant collections, and
knowledge_store facts don't (and shouldn't) live in one of those.
"""

from __future__ import annotations

import logging
import math

import httpx

from kaare_core.config import get_service as _svc

logger = logging.getLogger(__name__)

EMBED_URL = _svc("ollama", "embed") + "/api/embed"
# Calibrated against real notes content 2026-09-07: genuine near-duplicates
# (e.g. two "Frigate: Bil på ..." log lines) scored 0.86-0.90 cosine;
# genuinely distinct facts scored 0.31-0.45 — a wide, clean gap, so 0.80
# sits comfortably in the middle with margin both ways.
DEFAULT_SIMILARITY_THRESHOLD = 0.80


async def _embed_batch(texts: list[str]) -> list[list[float]] | None:
    """One HTTP call embeds the whole list — same batched-input shape Argus
    already relies on, not one call per text."""
    if not texts:
        return []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(EMBED_URL, json={"model": "bge-m3", "input": texts})
            r.raise_for_status()
            return r.json()["embeddings"]
    except Exception as e:
        logger.warning("[knowledge_dedup] embed call failed: %s", e)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def cluster_facts(
    facts: list[dict], threshold: float = DEFAULT_SIMILARITY_THRESHOLD
) -> list[list[dict]]:
    """Greedy single-pass clustering: each fact joins the first existing
    cluster whose representative (that cluster's first member) it's similar
    enough to; otherwise it starts a new cluster. A fact dict's "content"
    field is what gets embedded/compared — same shape knowledge_reflection's
    _collect_active_facts() produces.

    Returns a list of clusters, each a list of fact dicts (order preserved
    within each cluster; first element is the representative). A cluster of
    size 1 just means "no near-duplicate found."

    Falls back to one-cluster-per-fact (no grouping at all) if the embed
    call fails, so callers degrade to today's un-clustered behavior instead
    of crashing.
    """
    if not facts:
        return []
    texts = [f["content"] for f in facts]
    embeddings = await _embed_batch(texts)
    if embeddings is None:
        return [[f] for f in facts]

    clusters: list[dict] = []  # {"rep_embedding": [...], "members": [fact, ...]}
    for fact, emb in zip(facts, embeddings, strict=False):
        placed = False
        for cluster in clusters:
            if _cosine(cluster["rep_embedding"], emb) >= threshold:
                cluster["members"].append(fact)
                placed = True
                break
        if not placed:
            clusters.append({"rep_embedding": emb, "members": [fact]})

    logger.info(
        "[knowledge_dedup] clustered %d facts into %d groups (threshold=%.2f)",
        len(facts),
        len(clusters),
        threshold,
    )
    return [c["members"] for c in clusters]
