"""
Unified notification abstraction for KTSB.

Single entry point: notify_user(user_id, message, urgency, source_key)
Channels: sound_node (TTS) and chat (STM injection).
Push channel is dormant until ha_notify_entity is configured in services.yaml.

Urgency tiers:
  LOW      → inject into user STM (next chat pickup)
  MEDIUM   → TTS if outside quiet hours, else STM
  HIGH     → TTS immediately (any hour)
  CRITICAL → TTS immediately + push (when configured)

Deduplication: in-memory per (user_id, source_key) with per-urgency TTL.
State is lost on restart — one duplicate per event class per restart is acceptable.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
import yaml

from kaare_core.config import get_settings, get_service

logger = logging.getLogger("notify")

_NODES_PATH = Path("/kaare/configs/nodes.yaml")


class Urgency(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


# Dedup TTL per urgency level (CRITICAL is never deduped)
_DEDUP_TTL: dict[Urgency, Optional[timedelta]] = {
    Urgency.LOW:      timedelta(minutes=60),
    Urgency.MEDIUM:   timedelta(minutes=30),
    Urgency.HIGH:     timedelta(minutes=10),
    Urgency.CRITICAL: None,
}

# In-memory dedup state: (user_id, source_key) → last fired time
_dedup: dict[tuple, datetime] = {}


def _is_deduped(user_id: str, source_key: str, urgency: Urgency) -> bool:
    ttl = _DEDUP_TTL[urgency]
    if ttl is None:
        return False
    key = (user_id, source_key)
    last = _dedup.get(key)
    if last and datetime.now() - last < ttl:
        return True
    return False


def _record_dedup(user_id: str, source_key: str) -> None:
    _dedup[(user_id, source_key)] = datetime.now()


def _is_quiet_hours() -> bool:
    try:
        settings = get_settings()
        notif = settings.get("notifications", {})
        start = int(notif.get("quiet_hours_start", 22))
        end   = int(notif.get("quiet_hours_end",   7))
        hour  = datetime.now().hour
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end
    except Exception:
        return False


def _resolve_sound_node(user_id: str) -> Optional[str]:
    """Return the best audio node ID for a user, or None if not found."""
    try:
        data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
        nodes = data.get("nodes", {})
        uid_lower = user_id.lower()
        for node_id, cfg in nodes.items():
            if not cfg.get("enabled", True):
                continue
            if not cfg.get("has_audio", False):
                continue
            raw_users = cfg.get("default_user", "") or ""
            assigned = [u.strip().lower() for u in str(raw_users).split(",") if u.strip()]
            if uid_lower in assigned:
                return node_id
    except Exception as e:
        logger.warning("[notify] _resolve_sound_node failed: %s", e)
    return None


async def _tts(node_id: str, message: str, lang: str = "nb") -> None:
    try:
        voice_bridge = get_service("internal", "voice_bridge")
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{voice_bridge}/speak",
                json={"text": message, "target": node_id, "lang": lang},
            )
            resp.raise_for_status()
        logger.info("[notify] TTS sent to node '%s'", node_id)
    except Exception as e:
        logger.warning("[notify] TTS failed for node '%s': %s", node_id, e)


def _get_user_lang(user_id: str) -> str:
    try:
        from kaare_core.tools.i18n import get_lang
        return get_lang(user_id)
    except Exception:
        return "nb"


def _get_notify_channel(user_id: str) -> str:
    try:
        from kaare_core.users.profile_manager import load_profile
        profile = load_profile(user_id)
        hv = profile.get("household_visible", {})
        return hv.get("notify_channel", "sound_node") or "sound_node"
    except Exception:
        return "sound_node"


def _inject_stm(user_id: str, message: str) -> None:
    try:
        from kaare_core.memory.short_term import get_stm
        stm = get_stm(user_id)
        stm.add_assistant(f"[Varsling]: {message}", source="notify")
    except Exception as e:
        logger.warning("[notify] STM inject failed for user '%s': %s", user_id, e)


async def _push_notify(user_id: str, message: str) -> None:
    try:
        from kaare_ha_gateway import call_ha_service
        services = get_service("home_assistant") or {}
        entity = (services.get("ha_notify_entity") or "").strip()
        if not entity:
            return
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_ha_service(entity, {"message": message, "title": "Kåre"}),
        )
        logger.info("[notify] Push sent via '%s' for user '%s'", entity, user_id)
    except Exception as e:
        logger.warning("[notify] Push failed for user '%s': %s", user_id, e)


async def notify_user(
    user_id: str,
    message: str,
    urgency: Urgency = Urgency.MEDIUM,
    source_key: str = "generic",
) -> None:
    """
    Route a notification to a user through the appropriate channel.
    Never raises — all errors are caught and logged.
    """
    try:
        channel = _get_notify_channel(user_id)
        if channel == "silent":
            logger.debug("[notify] user '%s' is silent — suppressed", user_id)
            return

        if _is_deduped(user_id, source_key, urgency):
            logger.debug("[notify] deduped: user=%s source_key=%s urgency=%s", user_id, source_key, urgency)
            return

        _record_dedup(user_id, source_key)
        lang = _get_user_lang(user_id)
        node = _resolve_sound_node(user_id)

        if urgency == Urgency.LOW:
            _inject_stm(user_id, message)

        elif urgency == Urgency.MEDIUM:
            if not _is_quiet_hours() and node and channel != "chat":
                await _tts(node, message, lang)
            else:
                _inject_stm(user_id, message)

        elif urgency == Urgency.HIGH:
            if node and channel != "chat":
                await _tts(node, message, lang)
            else:
                _inject_stm(user_id, message)

        elif urgency == Urgency.CRITICAL:
            if node and channel != "chat":
                await _tts(node, message, lang)
            _inject_stm(user_id, message)
            await _push_notify(user_id, message)

        logger.info(
            "[notify] delivered: user=%s urgency=%s source_key=%s channel=%s node=%s",
            user_id, urgency, source_key, channel, node or "none",
        )

    except Exception as e:
        logger.error("[notify] notify_user failed unexpectedly: user=%s error=%s", user_id, e)
