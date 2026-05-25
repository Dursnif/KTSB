"""
Fil-basert fallback for semantisk minne.

Aktiveres automatisk av semantic_memory.py når embedding-tjenesten er utilgjengelig
eller deaktivert. Lagrer episoder i en JSONL-fil og søker med naiv ordmatch.

Ingen vektorer — formålet er å ikke miste data ved nedetid, ikke å erstatte Qdrant.
Mønster: identisk med think_cache.py (rullerende JSONL med trim).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml as _yaml

logger = logging.getLogger(__name__)


def _load_settings() -> dict:
    try:
        with open("/kaare/configs/settings.yaml", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:
        return {}


_cfg = _load_settings().get("semantic_memory", {})
_FALLBACK_PATH = Path("/kaare/state/semantic_memory_fallback.jsonl")
_MAX_ENTRIES: int = int(_cfg.get("fallback_max_entries", 500))
_TRIM_MARGIN: int = int(_cfg.get("trim_margin", 50))
_TRIM_THRESHOLD: int = _MAX_ENTRIES + _TRIM_MARGIN

_write_count = 0


def append_episode(
    episode_id: int,
    user_id: str,
    text: str,
    narrative: str,
    topics: str = "",
    ts: str = "",
) -> None:
    """Append one episode to the fallback JSONL file. Never raises."""
    global _write_count
    try:
        entry = {
            "episode_id": episode_id,
            "user_id": user_id,
            "text": text,
            "narrative": narrative,
            "topics": topics,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
        }
        _FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_FALLBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _write_count += 1
        if _write_count % 50 == 0:
            _trim()
    except Exception as e:
        logger.warning("semantic_memory_file.append_episode feilet: %s", e)


def search_fallback(query: str, user_id: str = "global", limit: int = 3) -> list[dict]:
    """
    Naiv ordmatch mot fallback-filen. Returnerer samme format som search_memory().
    Filtrerer på user_id — inkluderer alltid 'global'-episoder.
    Sorterer treff etter andel query-ord som finnes i teksten.
    """
    if not query or not _FALLBACK_PATH.exists():
        return []
    try:
        query_words = set(query.lower().split())
        candidates: list[tuple[float, dict]] = []
        lines = _FALLBACK_PATH.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            uid = entry.get("user_id", "global")
            if uid != user_id and uid != "global":
                continue
            haystack = (entry.get("text", "") + " " + entry.get("narrative", "")).lower()
            score = sum(1 for w in query_words if w in haystack) / max(len(query_words), 1)
            if score > 0:
                candidates.append((score, entry))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "score": round(sc, 3),
                "narrative": e.get("narrative", ""),
                "topics": e.get("topics", ""),
                "ts": e.get("ts", ""),
                "episode_id": e.get("episode_id", 0),
                "user_id": e.get("user_id", "global"),
            }
            for sc, e in candidates[:limit]
        ]
    except Exception as e:
        logger.warning("semantic_memory_file.search_fallback feilet: %s", e)
        return []


def _trim() -> None:
    """Keep only the last _MAX_ENTRIES lines in the fallback file."""
    try:
        if not _FALLBACK_PATH.exists():
            return
        lines = _FALLBACK_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > _TRIM_THRESHOLD:
            _FALLBACK_PATH.write_text(
                "\n".join(lines[-_MAX_ENTRIES:]) + "\n",
                encoding="utf-8",
            )
    except Exception:
        pass
