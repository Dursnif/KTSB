import re
import time
import yaml
from pathlib import Path
from typing import Dict, Optional

_RULES_PATH = Path("/kaare/configs/fastpath_rules.yaml")

_cache: Optional[dict] = None
_cache_ts: float = 0.0
_CACHE_TTL: float = 30.0


def _load_rules() -> dict:
    global _cache, _cache_ts, _CACHE_TTL
    now = time.monotonic()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache
    try:
        data = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8")) or {}
        _CACHE_TTL = float((data.get("settings") or {}).get("cache_ttl_seconds", 30))
        _cache = data
        _cache_ts = now
    except Exception:
        _cache = {"reflexes": []}
        _cache_ts = now
    return _cache


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _matches(phrase: str, norm_input: str, match_mode: str) -> bool:
    phrase_n = _normalize(phrase)
    if not phrase_n:
        return False
    if match_mode == "exact":
        return phrase_n == norm_input
    return phrase_n in norm_input or norm_input in phrase_n


def match_fastpath(prompt: str) -> Optional[Dict]:
    """
    Returns a dispatch dict for the first matching active reflex, or None.

    Return shapes:
      HA:   {"route": "ha_fastpath",   "action": str, "entity_id": str, "source": str}
      MQTT: {"route": "mqtt_fastpath", "topic": str,  "payload": str,  "source": str}
      OS clock: {"route": "clock_fastpath", "source": str}
      OS other: {"route": "os_fastpath",    "action": str, "source": str}
    """
    rules = _load_rules()
    reflexes = rules.get("reflexes") or []
    if not reflexes:
        return None

    norm = _normalize(prompt)

    for reflex in reflexes:
        if not reflex.get("active", True):
            continue
        if reflex.get("learned") and reflex.get("confirmed_by") != "admin":
            continue

        phrase = reflex.get("phrase", "")
        match_mode = reflex.get("match", "contains")
        if not _matches(phrase, norm, match_mode):
            continue

        provider = reflex.get("provider", "ha")
        reflex_id = reflex.get("id", "unknown")
        source = f"reflexes:{reflex_id}"

        if provider == "ha":
            return {
                "route": "ha_fastpath",
                "action": reflex.get("action", "toggle"),
                "entity_id": reflex.get("entity_id", ""),
                "source": source,
            }

        if provider == "mqtt":
            return {
                "route": "mqtt_fastpath",
                "topic": reflex.get("topic", ""),
                "payload": reflex.get("payload", "{}"),
                "source": source,
            }

        if provider == "os":
            action = reflex.get("action", "clock")
            if action == "clock":
                return {"route": "clock_fastpath", "source": source}
            return {"route": "os_fastpath", "action": action, "source": source}

    return None


def invalidate_cache() -> None:
    """Force cache reload on next match_fastpath call."""
    global _cache
    _cache = None
