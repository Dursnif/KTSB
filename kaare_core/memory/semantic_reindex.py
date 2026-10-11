"""
Rebuild a semantic-memory collection from the episodes in SQLite (P126).

The episodes table (interactions.db) is the source of truth; a Qdrant collection is only a search
index over it. After switching embedding model the new model's collection is filled from here —
the previous collection stays untouched, so switching back needs no rebuild.

Personal episodes are encrypted at rest; they are read with the system key (P87,
allow_system_key=True — this is a background job, not an interactive read), so this must run in
a process that can read state/system_master.key (the API, or the `kaare` user). An episode that
still cannot be decrypted is skipped and counted, never indexed as «[kryptert]».
"""

import asyncio
import logging
import sqlite3

from kaare_core.memory.long_term import DB_PATH, _dec
from kaare_core.memory.semantic_memory import (
    _get_client,
    embed_texts,
    ensure_collection,
    episode_point,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 32
EMBED_BATCH_TIMEOUT = 120.0
_UNREADABLE = "[kryptert]"


def _load_episodes() -> list[tuple]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return conn.execute(
            """SELECT id, ts_created, user_id, from_id, to_id, interaction_count, narrative, topics
               FROM episodes ORDER BY id"""
        ).fetchall()
    finally:
        conn.close()


async def reindex_all(model: dict, progress: dict | None = None) -> dict:
    """Embed every episode with `model` and upsert into its collection.

    `progress` (optional) is updated in place for a GUI poll: total, done, indexed,
    skipped_encrypted, failed. Returns the same counts."""
    progress = progress if progress is not None else {}
    await asyncio.to_thread(ensure_collection, model)
    rows = await asyncio.to_thread(_load_episodes)
    progress.update(total=len(rows), done=0, indexed=0, skipped_encrypted=0, failed=0)
    client = _get_client(write=True)

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        items = []
        for ep_id, ts, user_id, from_id, to_id, count, narrative, topics in batch:
            user_id = user_id or "global"
            narrative = _dec(user_id, narrative or "", allow_system_key=True)
            topics = _dec(user_id, topics or "", allow_system_key=True)
            if _UNREADABLE in (narrative, topics) or not narrative:
                progress["skipped_encrypted"] += 1 if narrative else 0
                continue
            text = f"{narrative} [{topics}]" if topics else narrative
            items.append(
                (
                    ep_id,
                    ts or "",
                    user_id,
                    from_id or 0,
                    to_id or 0,
                    count or 0,
                    narrative,
                    topics or "",
                    text,
                )
            )

        if items:
            vectors = await embed_texts(
                [it[-1] for it in items], model, timeout=EMBED_BATCH_TIMEOUT
            )
            if vectors is None:
                progress["failed"] += len(items)
            else:
                points = [
                    episode_point(ep_id, vec, narrative, topics, ts, from_id, to_id, count, user_id)
                    for (
                        ep_id,
                        ts,
                        user_id,
                        from_id,
                        to_id,
                        count,
                        narrative,
                        topics,
                        _,
                    ), vec in zip(items, vectors, strict=False)
                ]
                try:
                    await asyncio.to_thread(
                        client.upsert, collection_name=model["collection"], points=points
                    )
                    progress["indexed"] += len(points)
                except Exception as e:
                    logger.warning("Reindex upsert failed (%d episodes): %s", len(points), e)
                    progress["failed"] += len(points)
        progress["done"] += len(batch)

    result = {k: progress[k] for k in ("total", "indexed", "skipped_encrypted", "failed")}
    logger.info("Semantic memory reindex into '%s': %s", model["collection"], result)
    return result
