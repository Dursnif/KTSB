"""
Cross-process fallback state for Kåre's 9B backup model.

When the main 27B model (port 11440 via proxy 11441) is unreachable,
Kåre switches to the shared 9B model (port 11445). This module owns
the state flag that all processes (kaare.service, kaare-agents.service)
can read.

Flag file: /kaare/runtime/kare_9b_fallback.json
  {
    "active":           true,
    "ts_start":         "2026-05-05T14:23:00+00:00",   # ISO, UTC
    "ts_last_failure":  1746451380.0,                   # time.time()
    "turn_count":       0
  }

Retry cooldown: 60 s between main-model re-attempts.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_FLAG_PATH = Path("/kaare/runtime/kare_9b_fallback.json")
_RETRY_COOLDOWN = 60  # seconds between main-model retry attempts


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> Optional[dict]:
    try:
        return json.loads(_FLAG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(data: dict) -> None:
    try:
        _FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FLAG_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.error("[fallback] write failed: %s", e)


# ── Public API ────────────────────────────────────────────────────────────────

def is_fallback_active() -> bool:
    """True if the 9B fallback is currently in effect."""
    return _FLAG_PATH.exists()


def get_fallback_info() -> Optional[dict]:
    """Returns the current fallback state dict, or None if not active."""
    return _read()


def activate_fallback() -> None:
    """Mark that the main model is unreachable. Idempotent."""
    if is_fallback_active():
        return
    _write({
        "active":          True,
        "ts_start":        _utc_iso(),
        "ts_last_failure": time.time(),
        "turn_count":      0,
    })
    log.warning("[fallback] activated — main model unreachable, switching to 9B backup")


def deactivate_fallback() -> dict:
    """Remove the fallback flag and return the final session info."""
    info = _read() or {}
    try:
        _FLAG_PATH.unlink(missing_ok=True)
        log.info("[fallback] deactivated — main model recovered (turns=%s)", info.get("turn_count", 0))
    except Exception as e:
        log.error("[fallback] deactivate failed: %s", e)
    return info


def increment_turn() -> int:
    """Increment the turn counter. Returns new count."""
    info = _read() or {}
    info["turn_count"] = info.get("turn_count", 0) + 1
    _write(info)
    return info["turn_count"]


def should_retry_main() -> bool:
    """True when the cooldown has elapsed and we should probe the main model."""
    info = _read()
    if not info:
        return False
    return time.time() - info.get("ts_last_failure", 0) > _RETRY_COOLDOWN


def update_last_failure() -> None:
    """Reset the cooldown timer after a failed recovery attempt."""
    info = _read() or {}
    info["ts_last_failure"] = time.time()
    _write(info)
