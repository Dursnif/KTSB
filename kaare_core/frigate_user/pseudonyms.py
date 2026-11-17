"""Stable person ids for the inner voices (J21b reader 3; 2026-11-12 via the main session).

Jing and Jang think about people as ids, never names — KTSB knows who an id is. An id is `person-<6 hex>`: an HMAC of the
person's canonical ref (the household username when a Frigate face is linked to one, else the stored ref, casefolded)
under a random key kept in frigate.db's meta table. The same person gets the same id on every call; the id cannot be
derived from the name without the key. The way back (id → ref) lives only here, in kaare_core. Nobody who opted out gets
an id (callers check consent first).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from kaare_core.frigate_user import sealing, store
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

KEY_NAME = "pseudonym_key"
PREFIX = "person-"
_HEX = 6


def _key() -> bytes:
    """The HMAC key, sealed at rest (DT04 R0.6c: with the key and a guessed name, anyone holding frigate.db could confirm
    who `person-…` is). A key stored in plaintext before is sealed in place on first read — same key, so every id stays."""
    stored = store.meta_get(KEY_NAME)
    if not stored:
        stored = store.meta_set_once(KEY_NAME, sealing.seal(secrets.token_hex(32)))
    elif not sealing.is_sealed(stored):
        store.meta_replace(KEY_NAME, stored, sealing.seal(stored))
    k = sealing.unseal(stored)
    if not k:
        raise RuntimeError("pseudonym key unreadable (system key missing?)")
    return bytes.fromhex(k)


def canonical(ref: str) -> str:
    try:
        owner = user_store.get_username_for_frigate_face(ref)
    except Exception:
        owner = ""
    return (owner or ref).strip().casefold()


def pid(ref: str, key: bytes | None = None) -> str:
    digest = hmac.new(key or _key(), canonical(ref).encode(), hashlib.sha256).hexdigest()
    return PREFIX + digest[:_HEX]


VEHICLE_PREFIX = "vehicle-"  # P150 6b: the same id path for an unknown, verified licence plate


def canonical_plate(text: str) -> str:
    """A plate as one spelling: upper case, no spaces or hyphens, O→0 and I→1 (LPR mixes them up). Country-neutral."""
    t = "".join(ch for ch in (text or "").upper() if not ch.isspace() and ch != "-")
    return t.replace("O", "0").replace("I", "1")


def vehicle_id(plate: str, key: bytes | None = None) -> str:
    """`vehicle-<6 hex>`: the HMAC of the normalised plate under the same key as `person-…`. The plate cannot be read
    back from the id, and nothing maps it back (a named vehicle carries its household name in frigate_vehicles.yaml)."""
    digest = hmac.new(key or _key(), canonical_plate(plate).encode(), hashlib.sha256).hexdigest()
    return VEHICLE_PREFIX + digest[:_HEX]


def owner(person_id: str) -> str | None:
    """The canonical ref behind an id, or None (kaare_core only — never handed to an inner voice)."""
    try:
        key = _key()
        refs = {u["username"] for u in user_store.list_users()} | store.known_refs()
    except Exception as e:
        logger.warning("[PSEUDONYM] lookup failed: %s", e)
        return None
    for ref in refs:
        if ref and pid(ref, key) == person_id:
            return canonical(ref)
    return None
