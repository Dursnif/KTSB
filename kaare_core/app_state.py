from __future__ import annotations
import time as _time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from kaare_core.memory.short_term import ShortTermMemory, STMRegistry

STM_REGISTRY: "STMRegistry | None" = None

# ---------------------------------------------------------------------------
# Node voice-session unlock state
# ---------------------------------------------------------------------------
# Keyed by node_id. A session is created when a user unlocks via phrase/PIN.
# Expires after NODE_SESSION_TIMEOUT seconds of inactivity (rolling).
# ---------------------------------------------------------------------------

NODE_SESSION_TIMEOUT: float = 120.0  # seconds

_node_sessions: dict[str, dict] = {}


def unlock_node(node_id: str, user_id: Optional[str], method: str) -> None:
    """Mark a node as unlocked for user_id via method (phrase/pin/voice)."""
    _node_sessions[node_id] = {
        "user_id": user_id,
        "expires_at": _time.time() + NODE_SESSION_TIMEOUT,
        "unlocked_by": method,
    }


def is_unlocked(node_id: str) -> bool:
    """Return True if the node has a valid (non-expired) unlock session."""
    if not node_id:
        return False
    session = _node_sessions.get(node_id)
    if not session:
        return False
    if _time.time() > session["expires_at"]:
        _node_sessions.pop(node_id, None)
        return False
    return True


def touch_session(node_id: str) -> None:
    """Reset the rolling expiry timer for an existing session."""
    session = _node_sessions.get(node_id)
    if session and _time.time() <= session["expires_at"]:
        session["expires_at"] = _time.time() + NODE_SESSION_TIMEOUT


def get_session_user(node_id: str) -> Optional[str]:
    """Return the user_id for an unlocked node, or None if locked/expired."""
    if not is_unlocked(node_id):
        return None
    return _node_sessions.get(node_id, {}).get("user_id")


def lock_node(node_id: str) -> None:
    """Immediately invalidate a node's unlock session."""
    _node_sessions.pop(node_id, None)


def get_stm(user_id: str) -> "ShortTermMemory":
    return STM_REGISTRY.get(user_id)

CAPABILITY_MAP: dict = {}
ALIASES: dict = {}
LANG_NORMALIZE: dict = {}

_AGENT_ENABLED: dict[str, bool] = {}
_OLLAMA_PULL_STATUS: dict[str, dict] = {}

_MEETING_STATUS: dict = {
    "reflection": {"running": False, "progress": 0, "round": 0, "max_rounds": 6,
                   "step": "", "log": [], "started_at": None, "source": None},
    "dev":        {"running": False, "progress": 0, "round": 0, "max_rounds": 6,
                   "step": "", "log": [], "started_at": None, "source": None},
}
_MEETING_PROCS: dict = {}
_NIGHTJOB_STATUS: dict = {
    "running": False, "episodes": 0, "compressed": 0,
    "step": "", "log": [], "started_at": None, "finished_at": None, "error": None,
}
_NIGHTJOB_PROC = None

_REFLECTION_ENABLED: bool = False
_JANG_INTERVAL_S: int = 600

_last_jang_run_time: float = 0.0
_last_user_prompt_time: float = 0.0
