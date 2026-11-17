"""
Rebuild a semantic-memory collection from the episodes in SQLite (P126).

The episodes table (interactions.db) is the source of truth; a Qdrant collection is only a search
index over it. After switching embedding model the new model's collection is filled from here —
the previous collection stays untouched, so switching back needs no rebuild.

Personal episodes are encrypted at rest; they are read with the system key (P87,
allow_system_key=True — this is a background job, not an interactive read), so this must run in
a process that can read state/system_master.key (the API, or the `kaare` user). An episode that
still cannot be decrypted is skipped, counted and named in the log (ids only), never indexed as «[kryptert]».
A skipped episode is not lost: the nightjob calls reindex_missing(), which indexes every 'ok' episode the active
collection lacks once it can be read (e.g. after the owner's first PIN login backfills the system key — the house's
ep 14 stayed out of semantic search for good before this, P146-s01).
"""

import asyncio
import logging

from kaare_core.memory.long_term import DB_PATH, EPISODE_OK, _dec
from kaare_core.memory.memory_embed_models import active_model
from kaare_core.memory.semantic_memory import (
    _get_client,
    embed_texts,
    ensure_collection,
    episode_point,
)
from kaare_core.storage import sqlite_files

logger = logging.getLogger(__name__)

BATCH_SIZE = 32
EMBED_BATCH_TIMEOUT = 120.0
_UNREADABLE = "[kryptert]"


def _load_episodes(only_ok: bool = False) -> list[tuple]:
    conn = sqlite_files.connect(str(DB_PATH))
    try:
        return conn.execute(
            """SELECT id, ts_created, user_id, from_id, to_id, interaction_count, narrative, topics, status
               FROM episodes"""
            + (" WHERE status = ?" if only_ok else "")
            + " ORDER BY id",
            (EPISODE_OK,) if only_ok else (),
        ).fetchall()
    finally:
        conn.close()


async def _index_rows(rows: list[tuple], model: dict, progress: dict) -> list[int]:
    """Embed and upsert episode rows into the model's collection; returns the ids skipped as unreadable/empty."""
    client = _get_client(write=True)
    skipped: list[int] = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        items = []
        for ep_id, ts, user_id, from_id, to_id, count, narrative, topics, status in batch:
            user_id = user_id or "global"
            narrative = _dec(user_id, narrative or "", allow_system_key=True)
            topics = _dec(user_id, topics or "", allow_system_key=True)
            if _UNREADABLE in (narrative, topics) or not narrative:
                progress["skipped_encrypted"] += 1 if narrative else 0
                skipped.append(ep_id)
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
                    status,
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
                    episode_point(
                        ep_id, vec, narrative, topics, ts, from_id, to_id, count, user_id, status
                    )
                    for (
                        ep_id,
                        ts,
                        user_id,
                        from_id,
                        to_id,
                        count,
                        narrative,
                        topics,
                        status,
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
    if skipped:
        logger.warning(
            "Semantic memory into '%s': skipped %d unreadable/empty episode(s): ids %s — the nightjob indexes "
            "them once they can be read",
            model["collection"],
            len(skipped),
            skipped,
        )
    return skipped


async def reindex_all(model: dict, progress: dict | None = None) -> dict:
    """Embed every episode with `model` and upsert into its collection.

    `progress` (optional) is updated in place for a GUI poll: total, done, indexed,
    skipped_encrypted, failed. Returns the same counts."""
    progress = progress if progress is not None else {}
    await asyncio.to_thread(ensure_collection, model)
    rows = await asyncio.to_thread(_load_episodes)
    progress.update(total=len(rows), done=0, indexed=0, skipped_encrypted=0, failed=0)
    await _index_rows(rows, model, progress)

    result = {k: progress[k] for k in ("total", "indexed", "skipped_encrypted", "failed")}
    logger.info("Semantic memory reindex into '%s': %s", model["collection"], result)
    return result


def _present_ids(collection: str, ids: list[int]) -> set[int]:
    client = _get_client(write=False)
    present: set[int] = set()
    for start in range(0, len(ids), 256):
        present.update(
            int(p.id)
            for p in client.retrieve(collection, ids=ids[start : start + 256], with_payload=False)
        )
    return present


async def reindex_missing(model: dict | None = None) -> dict:
    """Index the 'ok' episodes the active collection lacks (P146-s01) — the nightjob's safety net for episodes an
    earlier index or reindex had to skip. Marked episodes are never brought back. Returns counts (ids only)."""
    model = model or active_model()
    await asyncio.to_thread(ensure_collection, model)
    rows = await asyncio.to_thread(_load_episodes, True)
    present = await asyncio.to_thread(_present_ids, model["collection"], [r[0] for r in rows])
    missing = [r for r in rows if r[0] not in present]
    progress = {"total": len(missing), "done": 0, "indexed": 0, "skipped_encrypted": 0, "failed": 0}
    if missing:
        await _index_rows(missing, model, progress)
        logger.info(
            "Semantic memory heal into '%s': %d missing, %d indexed, ids %s",
            model["collection"],
            len(missing),
            progress["indexed"],
            [r[0] for r in missing],
        )
    return {k: progress[k] for k in ("total", "indexed", "skipped_encrypted", "failed")}
