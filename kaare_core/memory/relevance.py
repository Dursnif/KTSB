"""
Relevance engine for Kåre's memory drawers (P133 C2/C3, D6/D12).

The prompt carries an index (titles) of Kåre's notes and world facts; this module picks the
2–3 entries that matter for the current message and returns their full text («Relevant nå»).
It also tells the agenda/observation writer when a new row means the same as a resolved
topic's conclusion (echo, C2).

Vectors live in their own small store (state/memory/relevance.db), keyed by source:id and
the text's hash, so writers do not have to know about embeddings: a stale or missing vector
is re-embedded in the background (never in the reply path). The corpus is small (< 1 000
rows), so search is an in-memory dot product.

Provider order (rule 24 — works from the defaults): BGE-M3 (embedding service) → the semantic
memory embedder → off. Off means index only; nothing breaks.
Measured 2026-10-09: BGE-M3 on NPU 176 ms per query; mpnet on CPU 8 ms but misses the
trailer conclusion (P133 Research).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from pathlib import Path

import httpx
import numpy as np

from kaare_core.config import (
    get_embedding_config,
    get_memory_embed_config,
    get_memory_prompt_config,
)
from kaare_core.config import get_service as _svc

logger = logging.getLogger(__name__)

DB_PATH = Path("/kaare/state/memory/relevance.db")
_QUERY_TIMEOUT_S = 3.0
_BATCH = 16
_TEXT_CHARS = 1500

_cache: dict[str, tuple[str, str, np.ndarray]] = {}  # key → (hash, provider, unit vector)
_cache_loaded_for: Path | None = None
_sync_lock = asyncio.Lock()


# ── Provider ────────────────────────────────────────────────────────────────


def provider() -> dict | None:
    """The embedder to use now, or None (relevance off → index only)."""
    try:
        if not get_memory_prompt_config().get("relevance_enabled", True):
            return None
        if get_embedding_config().get("enabled"):
            return {"name": "bge-m3", "url": _svc("ollama", "embed") + "/api/embed"}
        if get_memory_embed_config().get("enabled"):
            return {"name": "semantic-embed", "url": _svc("internal", "semantic_embed") + "/embed"}
    except Exception as e:
        logger.warning("relevance: provider lookup failed: %s", e)
    return None


def _payload(prov: dict, texts: list[str]) -> dict:
    if prov["name"] == "bge-m3":
        return {"model": "bge-m3", "input": texts}
    return {"texts": texts}


def _unit(rows) -> np.ndarray:
    m = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


async def embed_async(texts: list[str], prov: dict, timeout: float = _QUERY_TIMEOUT_S):
    if not texts:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(prov["url"], json=_payload(prov, texts))
            r.raise_for_status()
            vecs = r.json().get("embeddings") or []
        return _unit(vecs) if len(vecs) == len(texts) else None
    except Exception as e:
        logger.warning("relevance: embed failed (%s): %s", prov["name"], e)
        return None


def embed_sync(texts: list[str], prov: dict, timeout: float = _QUERY_TIMEOUT_S):
    """For callers that already run in a worker thread (agenda/observation writes)."""
    if not texts:
        return None
    try:
        r = httpx.post(prov["url"], json=_payload(prov, texts), timeout=timeout)
        r.raise_for_status()
        vecs = r.json().get("embeddings") or []
        return _unit(vecs) if len(vecs) == len(texts) else None
    except Exception as e:
        logger.warning("relevance: embed failed (%s): %s", prov["name"], e)
        return None


# ── Vector store ────────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, hash TEXT NOT NULL, "
        "provider TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL)"
    )
    return conn


def _load_cache() -> None:
    global _cache_loaded_for
    if _cache_loaded_for == DB_PATH:
        return
    _cache.clear()
    try:
        conn = _conn()
        try:
            for key, h, prov, dim, blob in conn.execute(
                "SELECT key, hash, provider, dim, vec FROM vectors"
            ):
                _cache[key] = (h, prov, np.frombuffer(blob, dtype=np.float32).reshape(dim))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("relevance: vector store unreadable: %s", e)
    _cache_loaded_for = DB_PATH


def _store(rows: list[tuple[str, str, str, np.ndarray]]) -> None:
    conn = _conn()
    try:
        conn.executemany(
            "INSERT INTO vectors (key, hash, provider, dim, vec) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET hash = excluded.hash, provider = excluded.provider, "
            "dim = excluded.dim, vec = excluded.vec",
            [(k, h, p, int(v.shape[0]), v.astype(np.float32).tobytes()) for k, h, p, v in rows],
        )
        conn.commit()
    finally:
        conn.close()
    for k, h, p, v in rows:
        _cache[k] = (h, p, v)


def _hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _fresh(key: str, text: str, prov_name: str) -> np.ndarray | None:
    hit = _cache.get(key)
    if hit and hit[0] == _hash(text) and hit[1] == prov_name:
        return hit[2]
    return None


async def sync_vectors(docs: list[dict]) -> int:
    """Embed docs whose vector is missing or stale (text or provider changed). Background job —
    callers spawn it; one run at a time. Returns how many were embedded."""
    prov = provider()
    if prov is None or not docs:
        return 0
    async with _sync_lock:
        _load_cache()
        todo = [d for d in docs if _fresh(d["key"], d["text"], prov["name"]) is None]
        done = 0
        for i in range(0, len(todo), _BATCH):
            chunk = todo[i : i + _BATCH]
            vecs = await embed_async([d["text"][:_TEXT_CHARS] for d in chunk], prov, timeout=60.0)
            if vecs is None:
                break
            await asyncio.to_thread(
                _store,
                [
                    (d["key"], _hash(d["text"]), prov["name"], v)
                    for d, v in zip(chunk, vecs, strict=False)
                ],
            )
            done += len(chunk)
        if done:
            logger.info(
                "relevance: embedded %d of %d stale docs (%s)", done, len(todo), prov["name"]
            )
        return done


def has_stale(docs: list[dict]) -> bool:
    prov = provider()
    if prov is None:
        return False
    _load_cache()
    return any(_fresh(d["key"], d["text"], prov["name"]) is None for d in docs)


# ── Search ──────────────────────────────────────────────────────────────────


def _rank(qvec: np.ndarray, docs: list[dict], prov_name: str) -> list[tuple[float, dict]]:
    pairs = [(d, _fresh(d["key"], d["text"], prov_name)) for d in docs]
    pairs = [(d, v) for d, v in pairs if v is not None and v.shape == qvec.shape]
    if not pairs:
        return []
    m = np.stack([v for _, v in pairs])
    scores = m @ qvec
    order = np.argsort(-scores)
    return [(float(scores[i]), pairs[i][0]) for i in order]


async def relevant(query: str, docs: list[dict], *, k: int | None = None) -> list[dict]:
    """Top-k docs for the query at or above min_score, each with `score`. [] when relevance is
    off, the query is empty, or the embedder does not answer in time."""
    cfg = get_memory_prompt_config()
    prov = provider()
    query = (query or "").strip()
    if prov is None or not query or not docs:
        return []
    _load_cache()
    qv = await embed_async([query[:_TEXT_CHARS]], prov)
    if qv is None:
        return []
    out = []
    for score, d in _rank(qv[0], docs, prov["name"])[: k or cfg["top_k"]]:
        if score >= cfg["min_score"]:
            out.append({**d, "score": round(score, 3)})
    return out


def match_resolved_sync(text: str, resolved: list[dict]) -> tuple[int, float] | None:
    """Echo check at write time (runs in the writer's worker thread): the resolved topic whose
    conclusion means the same as `text` (≥ echo_min_score), or None. Missing conclusion vectors
    are embedded here — resolved topics are few."""
    prov = provider()
    text = (text or "").strip()
    if prov is None or not text or not resolved:
        return None
    _load_cache()
    docs = [
        {"key": f"resolved:{r['id']}", "text": f"{r.get('label') or ''}: {r['conclusion']}"}
        for r in resolved
        if r.get("conclusion")
    ]
    stale = [d for d in docs if _fresh(d["key"], d["text"], prov["name"]) is None]
    if stale:
        vecs = embed_sync([d["text"][:_TEXT_CHARS] for d in stale], prov, timeout=30.0)
        if vecs is not None:
            _store(
                [
                    (d["key"], _hash(d["text"]), prov["name"], v)
                    for d, v in zip(stale, vecs, strict=False)
                ]
            )
    qv = embed_sync([text[:_TEXT_CHARS]], prov)
    if qv is None:
        return None
    ranked = _rank(qv[0], docs, prov["name"])
    if not ranked:
        return None
    score, best = ranked[0]
    if score < get_memory_prompt_config()["echo_min_score"]:
        return None
    return int(best["key"].split(":", 1)[1]), round(score, 3)
