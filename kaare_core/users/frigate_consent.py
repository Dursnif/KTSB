"""«Frigate may track me» — per-person consent for Frigate's face identity (P144-s02).

Frigate puts a recognised face's name in an event's ``sub_label``. A person who says no keeps
being seen, but as an anonymous «person»: KTSB drops the name where Frigate events enter
(``adapters/mqtt_adapter.py``, ``adapters/frigate_adapter.py``). Only names linked to an
opted-out person are dropped — ``sub_label`` also carries licence plates and other people's names.

Frigate's own face library and history, and Home Assistant's Frigate integration, are outside
KTSB and are not changed. Past log lines are not rewritten.

Who decides (fail closed, ``can_set_tracking``):
  - young adult / adult / admin: only themselves;
  - child / teen: their guardians (``is_parent`` with the child in ``managed_children``);
    with no guardian assigned, an admin.
"""

import json
import logging
import time

from kaare_core.users import store

log = logging.getLogger(__name__)

# Other processes (and edits made straight in the DB) are picked up within this many seconds;
# the API process is updated at once through invalidate().
_CACHE_TTL = 10.0
_cache: dict = {"faces": frozenset(), "at": 0.0}

SELF_DECIDING_ROLES = {"young_adult", "adult", "admin"}
GUARDED_ROLES = {"child", "teen"}


def invalidate() -> None:
    _cache["at"] = 0.0
    _looks_cache["at"] = 0.0
    _any_off_cache["at"] = 0.0


_any_off_cache: dict = {"off": False, "at": 0.0}


def someone_opted_out() -> bool:
    """DT04 R0.6d (P144 R05): at least one person has «Frigate may track me» off — a yes/no for the one notice line
    KTSB gets («an unknown person may be one of them — do not guess»). Never who, never how many. Never raises: on a
    DB error the answer is no (no line), the storage gate itself fails closed elsewhere."""
    now = time.monotonic()
    if _any_off_cache["at"] and now - _any_off_cache["at"] < _CACHE_TTL:
        return _any_off_cache["off"]
    try:
        _any_off_cache["off"] = bool(store.any_frigate_tracking_off())
        _any_off_cache["at"] = now
    except Exception as e:
        log.warning("frigate consent: could not read whether someone opted out: %s", e)
        return False
    return _any_off_cache["off"]


def untracked_faces() -> frozenset[str]:
    """Casefolded face names that must not enter KTSB. Never raises — on a DB error the last
    known set is kept."""
    now = time.monotonic()
    if _cache["at"] and now - _cache["at"] < _CACHE_TTL:
        return _cache["faces"]
    try:
        _cache["faces"] = frozenset(store.list_untracked_frigate_faces())
        _cache["at"] = now
    except Exception as e:
        log.warning("frigate consent: could not read opted-out faces: %s", e)
    return _cache["faces"]


_looks_cache: dict = {"off": frozenset(), "at": 0.0}


def expressions_off() -> frozenset[str] | None:
    """Usernames who said no to «Uttrykk og gester» (P144-s27b). None when it cannot be read: the caller fails closed."""
    now = time.monotonic()
    if _looks_cache["at"] and now - _looks_cache["at"] < _CACHE_TTL:
        return _looks_cache["off"]
    try:
        _looks_cache["off"] = frozenset(store.list_expressions_off())
        _looks_cache["at"] = now
    except Exception as e:
        log.warning("frigate consent: could not read the expression switches: %s", e)
        return None
    return _looks_cache["off"]


def _name_of(sub_label) -> str:
    if isinstance(sub_label, (list, tuple)):
        return str(sub_label[0]) if sub_label and sub_label[0] else ""
    return sub_label if isinstance(sub_label, str) else ""


def is_untracked(sub_label) -> bool:
    """True when this sub_label (a name, or Frigate's [name, score]) is an opted-out person."""
    name = _name_of(sub_label).strip()
    return bool(name) and name.casefold() in untracked_faces()


def strip_event(ev: dict) -> dict:
    """Drop an opted-out person's name from a Frigate REST event (in place; also returned)."""
    if not isinstance(ev, dict):
        return ev
    if is_untracked(ev.get("sub_label")):
        ev["sub_label"] = None
    data = ev.get("data")
    if isinstance(data, dict) and is_untracked(data.get("sub_label")):
        data["sub_label"] = None
        data.pop("sub_label_score", None)
    return ev


def children_of(guardian: str) -> list[str]:
    """The children/teens this user is guardian of ([] unless is_parent)."""
    user = store.get_user(guardian)
    if not user or not user.get("is_parent") or not user.get("is_active"):
        return []
    try:
        children = json.loads(user.get("managed_children") or "[]")
    except (TypeError, ValueError):
        return []
    return [c for c in children if isinstance(c, str)] if isinstance(children, list) else []


def guardians_of(username: str) -> list[str]:
    return [u["username"] for u in store.list_users() if username in children_of(u["username"])]


def decided_by(target: dict) -> str:
    """Who decides for this person: "self" | "guardian" | "admin" | "none" (system identity)."""
    role = target.get("role", "")
    if role in SELF_DECIDING_ROLES:
        return "self"
    if role in GUARDED_ROLES:
        return "guardian" if guardians_of(target["username"]) else "admin"
    return "none"


def can_set_tracking(caller: str, target_username: str) -> bool:
    """Fail closed: unknown caller/target, inactive caller or an unknown role → False."""
    caller_user = store.get_user(caller)
    target = store.get_user(target_username)
    if not caller_user or not target or not caller_user.get("is_active"):
        return False
    who = decided_by(target)
    if who == "self":
        return caller == target_username
    if who == "guardian":
        return caller in guardians_of(target_username)
    if who == "admin":
        return caller_user.get("role") == "admin"
    return False
