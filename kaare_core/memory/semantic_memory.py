"""
Kåres semantiske minnelag — lag 3 av trelagsmodellen.

Bruker:
  - Semantic embed-server (port 11500) /embed for vektorgenerering (384-dim, CPU)
  - Qdrant (port 6333) for lagring og søk

Brukes av:
  - kaare_nightjob.py: indekserer nye episoder etter komprimering
  - router_generate.py: henter relevante episoder før LLM-kall (RAG)
"""

import asyncio
import base64
import logging
from typing import Optional
import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

logger = logging.getLogger(__name__)

from kaare_core.config import get_service as _svc, is_embedding_enabled as _emb_enabled, get_qdrant_api_key as _qdrant_key
from kaare_core.crypto import seal, unseal
from kaare_core.session_keys import get_session_key_sync
from kaare_core.users.store import get_public_key_b64
import kaare_core.memory.semantic_memory_file as _smf

_ENC_PREFIX = "ENC:"


def _enc_narrative(narrative: str, user_id: str) -> str:
    """Encrypt narrative with user's public key (SealedBox). Returns 'ENC:<base64>'."""
    try:
        pub_b64 = get_public_key_b64(user_id)
        if not pub_b64:
            return narrative
        pub_bytes = base64.b64decode(pub_b64)
        return _ENC_PREFIX + seal(narrative, pub_bytes)
    except Exception as e:
        logger.warning("Qdrant narrative encrypt failed for %s: %s", user_id, e)
        return narrative


def _dec_narrative(narrative: str, user_id: str) -> str:
    """Decrypt narrative if prefixed with ENC:. Falls back to plaintext on any failure."""
    if not narrative.startswith(_ENC_PREFIX):
        return narrative
    try:
        private_key = get_session_key_sync(user_id)
        if not private_key:
            return "[kryptert]"
        return unseal(narrative[len(_ENC_PREFIX):], private_key)
    except Exception as e:
        logger.warning("Qdrant narrative decrypt failed for %s: %s", user_id, e)
        return "[kryptert]"

QDRANT_URL = _svc("storage", "qdrant")
EMBED_URL  = _svc("internal", "semantic_embed") + "/embed"
COLLECTION    = "kaare_memory"
VECTOR_DIM    = 384
EMBED_TIMEOUT = 10.0
MIN_SCORE     = 0.35


def _get_client(write: bool = False) -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, api_key=_qdrant_key(write=write))


def ensure_collection() -> None:
    client = _get_client(write=True)
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("Qdrant collection '%s' opprettet.", COLLECTION)


async def embed(text: str) -> Optional[list[float]]:
    if not _emb_enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
            r = await client.post(EMBED_URL, json={"texts": [text]})
            r.raise_for_status()
            embeddings = r.json().get("embeddings", [])
            return embeddings[0] if embeddings else None
    except Exception as e:
        logger.warning("embed() feilet: %s", e)
        return None


async def index_episode(
    episode_id: int,
    narrative: str,
    topics: str = "",
    ts: str = "",
    from_id: int = 0,
    to_id: int = 0,
    interaction_count: int = 0,
    user_id: str = "global",
) -> bool:
    text_to_embed = f"{narrative} [{topics}]" if topics else narrative
    if not _emb_enabled():
        _smf.append_episode(episode_id, user_id, text_to_embed, narrative, topics, ts)
        return False
    vector = await embed(text_to_embed)
    if not vector:
        logger.warning("Embedding utilgjengelig for episode %d — lagrer i fil-fallback.", episode_id)
        _smf.append_episode(episode_id, user_id, text_to_embed, narrative, topics, ts)
        return False

    stored_narrative = narrative if user_id == "global" else _enc_narrative(narrative, user_id)
    try:
        client = _get_client(write=True)
        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=episode_id,
                vector=vector,
                payload={
                    "narrative": stored_narrative,
                    "topics": topics,
                    "ts": ts,
                    "from_id": from_id,
                    "to_id": to_id,
                    "interaction_count": interaction_count,
                    "user_id": user_id,
                },
            )],
        )
        logger.info("Episode %d indeksert i Qdrant (user=%s).", episode_id, user_id)
        return True
    except Exception as e:
        logger.warning("Qdrant upsert feilet for episode %d: %s", episode_id, e)
        return False


async def search_memory(query: str, limit: int = 3, user_id: str = "global") -> list[dict]:
    if not query or not query.strip():
        return []
    if not _emb_enabled():
        return _smf.search_fallback(query, user_id, limit)

    vector = await embed(query)
    if not vector:
        return _smf.search_fallback(query, user_id, limit)

    # Filtrer slik at en bruker kun ser egne episoder + globale episoder.
    # user_id='global' (ukjent/TTS) ser kun globale episoder.
    query_filter = Filter(
        should=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="user_id", match=MatchValue(value="global")),
        ]
    )

    try:
        client = _get_client()
        results = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=limit,
            score_threshold=MIN_SCORE,
            query_filter=query_filter,
            with_payload=True,
        ).points
        return [
            {
                "score": round(r.score, 3),
                "narrative": _dec_narrative(r.payload.get("narrative", ""), r.payload.get("user_id", "global")),
                "topics": r.payload.get("topics", ""),
                "ts": r.payload.get("ts", ""),
                "episode_id": r.id,
                "user_id": r.payload.get("user_id", "global"),
            }
            for r in results
        ]
    except Exception as e:
        logger.warning("Qdrant search feilet: %s", e)
        return []


def format_for_context(hits: list[dict]) -> str:
    if not hits:
        return ""
    lines = ["### LANGTIDSMINNE (relevante episoder)"]
    for h in hits:
        ts_short = h["ts"][:10] if h["ts"] else "?"
        lines.append(f"[{ts_short}] {h['narrative']}")
    return "\n".join(lines)
