"""
Kåre's semantic memory — layer 3 of the three-layer memory model.

Uses:
  - an embedding model chosen in the GUI (memory_embed_models.py: MiniLM int8 / mpnet-base /
    BGE-M3) — vector size, Qdrant collection and score threshold all come from that model
  - Qdrant (port 6333) for storage and search

Used by:
  - kaare_nightjob.py: indexes new episodes after compression
  - router_generate.py: fetches relevant episodes before the LLM call (RAG)
  - semantic_reindex.py: rebuilds a model's collection from the episodes in SQLite
"""

import base64
import logging

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

import kaare_core.memory.semantic_memory_file as _smf
from kaare_core.config import get_memory_embed_config
from kaare_core.config import get_qdrant_api_key as _qdrant_key
from kaare_core.config import get_service as _svc
from kaare_core.crypto import seal, unseal
from kaare_core.memory.memory_embed_models import active_model
from kaare_core.session_keys import resolve_key_sync
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users.store import get_public_key_b64

logger = logging.getLogger(__name__)

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


def _dec_narrative(narrative: str, user_id: str, allow_system_key: bool = False) -> str:
    """Decrypt narrative (or topics, same ENC: format) if prefixed with ENC:. Falls back to plaintext on any failure.

    allow_system_key=True (P87): confirmed background callers only — see
    session_keys.py::resolve_key_sync() for the interactive/GUI-facing restriction.
    """
    if not narrative.startswith(_ENC_PREFIX):
        return narrative
    try:
        private_key = resolve_key_sync(user_id, allow_system_key=allow_system_key)
        if not private_key:
            return "[kryptert]"
        return unseal(narrative[len(_ENC_PREFIX) :], private_key)
    except Exception as e:
        logger.warning("Qdrant narrative decrypt failed for %s: %s", user_id, e)
        return "[kryptert]"


QDRANT_URL = _svc("storage", "qdrant")
EMBED_TIMEOUT = 10.0


def _emb_enabled() -> bool:
    """Semantic memory's own switch (GUI: Settings → LLM/Model → Semantic memory).
    Not embedding.enabled — that is the BGE-M3 service, a separate component."""
    return bool(get_memory_embed_config().get("enabled", False))


def _embed_url(model: dict) -> str:
    if model["backend"] == "embedding_service":
        return _svc("ollama", "embed") + "/api/embed"
    return _svc("internal", "semantic_embed") + "/embed"


def _get_client(write: bool = False) -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, api_key=_qdrant_key(write=write))


def ensure_collection(model: dict | None = None) -> None:
    """Create the model's collection (vector size from the model) if it does not exist yet."""
    model = model or active_model()
    client = _get_client(write=True)
    existing = [c.name for c in client.get_collections().collections]
    if model["collection"] not in existing:
        client.create_collection(
            collection_name=model["collection"],
            vectors_config=VectorParams(size=model["dim"], distance=Distance.COSINE),
        )
        logger.info("Qdrant collection '%s' created (%d-dim).", model["collection"], model["dim"])


async def embed_texts(
    texts: list[str], model: dict | None = None, timeout: float = EMBED_TIMEOUT
) -> list[list[float]] | None:
    """Vectors for texts from the given (default: active) model, or None if unavailable.

    A vector of the wrong size counts as unavailable: right after a model switch the embed
    service may still run the previous model until it has been restarted."""
    model = model or active_model()
    if not texts:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if model["backend"] == "embedding_service":
                r = await client.post(_embed_url(model), json={"model": "bge-m3", "input": texts})
            else:
                r = await client.post(_embed_url(model), json={"texts": texts})
            r.raise_for_status()
            vectors = r.json().get("embeddings", [])
    except Exception as e:
        logger.warning("embed() failed: %s", e)
        return None
    if len(vectors) != len(texts) or any(len(v) != model["dim"] for v in vectors):
        logger.warning(
            "embed(): got %s-dim vectors, model '%s' needs %d — embed service not yet on this model?",
            len(vectors[0]) if vectors else 0,
            model["key"],
            model["dim"],
        )
        return None
    return vectors


async def embed(text: str) -> list[float] | None:
    if not _emb_enabled():
        return None
    vectors = await embed_texts([text])
    return vectors[0] if vectors else None


def episode_point(
    episode_id: int,
    vector: list[float],
    narrative: str,
    topics: str,
    ts: str,
    from_id: int,
    to_id: int,
    interaction_count: int,
    user_id: str,
) -> PointStruct:
    """One episode as a Qdrant point. Personal episodes are stored encrypted with the user's key."""
    stored_narrative = narrative if user_id == "global" else _enc_narrative(narrative, user_id)
    stored_topics = topics if user_id == "global" else _enc_narrative(topics, user_id)
    return PointStruct(
        id=episode_id,
        vector=vector,
        payload={
            "narrative": stored_narrative,
            "topics": stored_topics,
            "ts": ts,
            "from_id": from_id,
            "to_id": to_id,
            "interaction_count": interaction_count,
            "user_id": user_id,
        },
    )


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
        logger.warning(
            "Embedding unavailable for episode %d — stored in the file fallback.", episode_id
        )
        _smf.append_episode(episode_id, user_id, text_to_embed, narrative, topics, ts)
        return False

    model = active_model()
    try:
        client = _get_client(write=True)
        client.upsert(
            collection_name=model["collection"],
            points=[
                episode_point(
                    episode_id,
                    vector,
                    narrative,
                    topics,
                    ts,
                    from_id,
                    to_id,
                    interaction_count,
                    user_id,
                )
            ],
        )
        logger.info("Episode %d indexed in Qdrant (user=%s).", episode_id, user_id)
        return True
    except Exception as e:
        logger.warning("Qdrant upsert failed for episode %d: %s", episode_id, e)
        return False


async def search_memory(query: str, limit: int = 3, user_id: str = "global") -> list[dict]:
    if not query or not query.strip():
        return []
    if not _emb_enabled():
        return _smf.search_fallback(query, user_id, limit)

    model = active_model()
    vector = await embed(query)
    if not vector:
        return _smf.search_fallback(query, user_id, limit)

    # A user sees only their own episodes + global ones; user_id='global' (unknown/TTS) sees
    # only global episodes.
    query_filter = Filter(
        should=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="user_id", match=MatchValue(value="global")),
        ]
    )

    try:
        client = _get_client()
        results = client.query_points(
            collection_name=model["collection"],
            query=vector,
            limit=limit,
            score_threshold=model["min_score"],
            query_filter=query_filter,
            with_payload=True,
        ).points
        return [
            {
                "score": round(r.score, 3),
                "narrative": _dec_narrative(
                    r.payload.get("narrative", ""), r.payload.get("user_id", "global")
                ),
                "topics": _dec_narrative(
                    r.payload.get("topics", ""), r.payload.get("user_id", "global")
                ),
                "ts": r.payload.get("ts", ""),
                "episode_id": r.id,
                "user_id": r.payload.get("user_id", "global"),
            }
            for r in results
        ]
    except Exception as e:
        logger.warning("Qdrant search failed: %s", e)
        return []


def format_for_context(hits: list[dict], lang: str = "nb") -> str:
    if not hits:
        return ""
    lines = [_t_i18n("ltm_header", lang)]
    for h in hits:
        ts_short = h["ts"][:10] if h["ts"] else "?"
        lines.append(f"[{ts_short}] {h['narrative']}")
    return "\n".join(lines)
