"""One seam for every volume KTSB sets (I64 preparation, owner decision 2026-11-16).

Today it changes nothing: `target_volume` returns the requested level. It exists so that automatic volume — per-node
mode (fixed / automatic), floor and ceiling, the room's sound level, time and who is there ([[I64]]) — can later be
decided in one place without touching the call sites.

`purpose` separates a wish from a system choice. Owner decision (I64 option A): Kåre may always follow the user's own wish — a
level the user asked for (`user_request`) always goes through unchanged; only system choices (`announce` without a
wish, `duck` while listening) may ever be adjusted by a policy. Levels are fractions 0–1.

Callers today: `ha_control set_volume` (user_request), the voice bridge's `/speak` (announce) and `_new_duck` (duck).
"""

from __future__ import annotations

PURPOSE_USER_REQUEST = "user_request"  # Kåre sets the level because someone asked for it — never overridden
PURPOSE_ANNOUNCE = "announce"  # a spoken announcement's level chosen without an explicit wish
PURPOSE_DUCK = "duck"  # other speakers lowered while KTSB listens or speaks
PURPOSES = (PURPOSE_USER_REQUEST, PURPOSE_ANNOUNCE, PURPOSE_DUCK)


def _system_policy(node_id: str, node_cfg: dict, requested: float, purpose: str) -> float | None:
    """The I64 hook for system choices. None = keep the requested level (today: always)."""
    return None


def target_volume(node_id: str, node_cfg: dict | None, requested: float | None, purpose: str) -> float | None:
    """The level to set on `node_id` for `purpose`. Today: `requested` unchanged. A user's wish is never adjusted."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown volume purpose: {purpose!r}")
    if requested is None or purpose == PURPOSE_USER_REQUEST:
        return requested
    adjusted = _system_policy(node_id, node_cfg or {}, requested, purpose)
    return requested if adjusted is None else adjusted
