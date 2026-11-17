"""P146 option (a): an episode marked 'mixed' or 'superseded' never comes out of any reader.

Readers of LTM episodes (DBG02 code map): semantic search (RAG on every turn + tool `memory search`), the file
fallback for semantic search, the reflection meeting, and the semantic reindex (which must carry the mark into
Qdrant, or a rebuild would bring hidden episodes back). «Relevant nå», the dev slot and the GUI do not read episodes.
"""

import asyncio
import json
import sqlite3

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import kaare_reflection
from kaare_core.memory import long_term, semantic_memory, semantic_reindex
from kaare_core.memory import semantic_memory_file as smf

LIVE_DB = "/kaare/state/memory/interactions.db"
VEC = [1.0, 0.0, 0.0, 0.0]


@pytest.fixture
def ltm(tmp_path, monkeypatch):
    path = tmp_path / "interactions.db"
    monkeypatch.setattr(long_term, "DB_PATH", path)
    monkeypatch.setattr(long_term, "_ltm", None)
    long_term.get_ltm()
    conn = sqlite3.connect(str(path))
    for ep_id, owner, status in [
        (1, "alice", "mixed"),
        (2, "alice", "ok"),
        (3, "global", "mixed"),
        (4, "alice", "superseded"),
        (5, "global", "ok"),
    ]:
        conn.execute(
            "INSERT INTO episodes (id, ts_created, user_id, from_id, to_id, interaction_count, narrative, topics, "
            "status) VALUES (?, '2026-10-01T03:00:00+00:00', ?, ?, ?, 1, ?, '', ?)",
            (ep_id, owner, ep_id, ep_id, f"narrative {ep_id}", status),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def qdrant(monkeypatch):
    client = QdrantClient(":memory:")
    client.create_collection("mem_test", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    model = {"key": "test", "collection": "mem_test", "min_score": 0.0, "dim": 4}
    monkeypatch.setattr(semantic_memory, "_get_client", lambda write=False: client)
    monkeypatch.setattr(semantic_memory, "_emb_enabled", lambda: True)
    monkeypatch.setattr(semantic_memory, "active_model", lambda: model)

    async def fake_embed(text):
        return VEC

    monkeypatch.setattr(semantic_memory, "embed", fake_embed)
    return client, model


def _search(user_id):
    hits = asyncio.run(semantic_memory.search_memory("anything", limit=10, user_id=user_id))
    return sorted(h["episode_id"] for h in hits)


def test_semantic_search_skips_marked_points(qdrant):
    client, model = qdrant
    points = [
        PointStruct(id=1, vector=VEC, payload={"user_id": "alice", "narrative": "n1", "status": "mixed"}),
        PointStruct(id=2, vector=VEC, payload={"user_id": "alice", "narrative": "n2"}),  # pre-P146 point
        PointStruct(id=3, vector=VEC, payload={"user_id": "global", "narrative": "n3", "status": "mixed"}),
        PointStruct(id=4, vector=VEC, payload={"user_id": "alice", "narrative": "n4", "status": "superseded"}),
        PointStruct(id=5, vector=VEC, payload={"user_id": "global", "narrative": "n5", "status": "ok"}),
    ]
    client.upsert(model["collection"], points=points)
    assert _search("alice") == [2, 5]
    assert _search("bob") == [5]
    assert _search("global") == [5]


def test_reindex_carries_the_mark_into_qdrant(ltm, qdrant, monkeypatch):
    client, model = qdrant
    monkeypatch.setattr(semantic_reindex, "DB_PATH", ltm)
    monkeypatch.setattr(semantic_reindex, "_get_client", lambda write=False: client)
    monkeypatch.setattr(semantic_reindex, "ensure_collection", lambda m: None)

    async def fake_embed_texts(texts, model=None, timeout=None):
        return [VEC for _ in texts]

    monkeypatch.setattr(semantic_reindex, "embed_texts", fake_embed_texts)
    asyncio.run(semantic_reindex.reindex_all(model))
    assert _search("alice") == [2, 5]


def test_file_fallback_skips_marked_episodes(ltm, tmp_path, monkeypatch):
    path = tmp_path / "fallback.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"episode_id": i, "user_id": u, "text": "garden", "narrative": f"n{i}"})
            for i, u in [(1, "alice"), (2, "alice"), (3, "global"), (4, "alice"), (5, "global")]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smf, "_FALLBACK_PATH", path)
    hits = smf.search_fallback("garden", user_id="alice", limit=10)
    assert sorted(h["episode_id"] for h in hits) == [2, 5]


def test_reflection_meeting_skips_marked_episodes(ltm, monkeypatch):
    real_connect = sqlite3.connect

    def redirect(db, *a, **kw):
        return real_connect(str(ltm) if str(db) == LIVE_DB else db, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", redirect)  # N42: kaare_reflection opens through sqlite_files.connect
    text = kaare_reflection._get_recent_interactions(n=10, user_id="alice", lang="en")
    assert "narrative 2" in text
    assert "narrative 1" not in text
    assert "narrative 4" not in text
    text_all = kaare_reflection._get_recent_interactions(n=10, user_id=None, lang="en")
    for hidden in ("narrative 1", "narrative 3", "narrative 4"):
        assert hidden not in text_all
