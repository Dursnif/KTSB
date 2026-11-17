"""P146-s01: a semantic-memory reindex must not silently and permanently drop an episode it cannot read.

The house's P126 reindex (2026-10-07) skipped the other user's ep 14: it is encrypted with her key, the reindex reads with the
system key, and her system-encrypted key was only backfilled at her next PIN login (2026-10-26). The skip was a bare
count — no id in the log — and nothing ever indexed the episode later, so it stayed out of semantic search for good.
"""

import asyncio
import logging
import sqlite3

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

import kaare_nightjob as nj
from kaare_core.memory import long_term, semantic_memory, semantic_reindex

VEC = [1.0, 0.0, 0.0, 0.0]
MODEL = {"key": "test", "collection": "mem_test", "min_score": 0.0, "dim": 4}


class _NoQdrant:
    def __init__(self, *a, **kw):
        raise RuntimeError("no qdrant in tests")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "memory" / "interactions.db"
    path.parent.mkdir()
    monkeypatch.setattr(long_term, "DB_PATH", path)
    monkeypatch.setattr(long_term, "_ltm", None)
    long_term.get_ltm()
    conn = sqlite3.connect(str(path))
    for ep_id, owner, narrative, status in [
        (1, "alice", "ENC:alice-blob", "ok"),
        (2, "global", "global narrative", "ok"),
        (3, "alice", "mixed narrative", "mixed"),  # plaintext (pre-P87)
    ]:
        conn.execute(
            "INSERT INTO episodes (id, ts_created, user_id, from_id, to_id, interaction_count, narrative, topics, "
            "status) VALUES (?, '2026-10-01T03:00:00+00:00', ?, ?, ?, 1, ?, '', ?)",
            (ep_id, owner, ep_id, ep_id, narrative, status),
        )
    conn.commit()
    conn.close()

    client = QdrantClient(":memory:")
    client.create_collection("mem_test", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    key = {"alice": False}

    def fake_dec(uid, text, allow_system_key=False):
        if text.startswith("ENC:"):
            return "alice narrative" if key.get(uid) else "[kryptert]"
        return text

    async def fake_embed_texts(texts, model=None, timeout=None):
        return [VEC for _ in texts]

    monkeypatch.setattr(semantic_reindex, "DB_PATH", path)
    monkeypatch.setattr(semantic_reindex, "_get_client", lambda write=False: client)
    monkeypatch.setattr(semantic_reindex, "ensure_collection", lambda m=None: None)
    monkeypatch.setattr(semantic_reindex, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(semantic_reindex, "_dec", fake_dec)
    monkeypatch.setattr(semantic_reindex, "active_model", lambda: MODEL, raising=False)
    monkeypatch.setattr(semantic_memory, "_enc_narrative", lambda text, uid: text)
    monkeypatch.setattr(semantic_memory, "_get_client", lambda write=False: client)
    monkeypatch.setattr(semantic_memory, "_emb_enabled", lambda: True)

    # The nightjob around it: tmp DB, no LLM work, no live Qdrant.
    monkeypatch.setattr(nj, "DB_PATH", path)
    monkeypatch.setattr(nj, "JING_THOUGHTS_PATH", tmp_path / "no_jing.txt")
    monkeypatch.setattr(nj.argus_size, "run_nightly", lambda *a, **kw: {"status": "test"})  # P150: Argus step, no live Qdrant
    monkeypatch.setattr(nj, "_compute_baseline", lambda: None)
    monkeypatch.setattr(nj, "ensure_collection", lambda *a, **kw: None)
    monkeypatch.setattr(nj, "save_daily_summary", lambda *a, **kw: None)
    monkeypatch.setattr(nj, "get_memory_embed_config", lambda: {"enabled": True})
    return {"client": client, "key": key}


def _ids(client):
    points, _ = client.scroll("mem_test", limit=100, with_payload=False)
    return sorted(p.id for p in points)


def test_reindex_names_the_episodes_it_skipped(setup, caplog):
    with caplog.at_level(logging.INFO):
        result = asyncio.run(semantic_reindex.reindex_all(MODEL))
    assert result["skipped_encrypted"] == 1
    assert _ids(setup["client"]) == [2, 3]  # mixed ep 3 is carried over as a marked point (P146)
    assert "ids [1]" in caplog.text, caplog.text
    assert "alice-blob" not in caplog.text


def test_skipped_episode_is_indexed_once_its_owner_can_be_read(setup):
    setup["key"]["alice"] = False
    asyncio.run(semantic_reindex.reindex_all(MODEL))
    assert 1 not in _ids(setup["client"])
    setup["key"]["alice"] = True  # the owner logged in with PIN → system key backfilled
    asyncio.run(nj.run_nightjob())
    assert 1 in _ids(setup["client"])


def test_heal_never_brings_back_a_marked_episode(setup):
    setup["key"]["alice"] = True
    asyncio.run(nj.run_nightjob())
    assert 3 not in _ids(setup["client"])
    assert sorted(_ids(setup["client"])) == [1, 2]
