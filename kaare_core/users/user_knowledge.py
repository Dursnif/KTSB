"""`user_knowledge` — what the night meeting concluded about a person, encrypted like the profile (run-queue J26 Q3,
main session 2026-11-12: «en klartekstfil med frykt og OCD ved siden av en kryptert profil går ikke»).

The one way to read and write it. `user_knowledge.enc` is encrypted with the person's key (live session, or the system key
for the night's confirmed background callers, P87). Fail closed: no key → nothing is read and nothing is written — never
the plaintext. An old plaintext `user_knowledge.md` is encrypted into `.enc` the first time a key is at hand and then
renamed to `user_knowledge.md.plain.bak` (never deleted, CLAUDE.md rules 1/29 — the owner decides about it later).
The content is KTSB's own interpretation, never something the person said: callers label it as such.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kaare_core import session_keys
from kaare_core.crypto import decrypt_text, encrypt_text

log = logging.getLogger(__name__)

USERS_DIR = Path("/kaare/state/users")
ENC_NAME = "user_knowledge.enc"
PLAIN_NAME = "user_knowledge.md"
BACKUP_SUFFIX = ".plain.bak"


def _paths(user_id: str) -> tuple[Path, Path]:
    d = USERS_DIR / user_id
    return d / ENC_NAME, d / PLAIN_NAME


def _key(user_id: str, allow_system_key: bool) -> bytes | None:
    try:
        return session_keys.resolve_key_sync(user_id, allow_system_key=allow_system_key)
    except Exception:
        return None


def _migrate_plain(user_id: str, key: bytes) -> None:
    """Old plaintext file → .enc, then rename the plaintext to *.plain.bak (kept). Only when no .enc exists yet."""
    enc, plain = _paths(user_id)
    if not plain.exists():
        return
    if not enc.exists():
        enc.write_text(encrypt_text(plain.read_text(encoding="utf-8"), key), encoding="utf-8")
    backup = plain.with_name(plain.name + BACKUP_SUFFIX)
    if backup.exists():
        backup = plain.with_name(f"{plain.name}{BACKUP_SUFFIX}.{int(plain.stat().st_mtime)}")
    plain.rename(backup)
    log.info("[USER_KNOWLEDGE] %s: plaintext encrypted, old file kept as %s", user_id, backup.name)


def read(user_id: str, allow_system_key: bool = False) -> str:
    if not user_id or user_id == "global":
        return ""
    key = _key(user_id, allow_system_key)
    if not key:
        return ""
    try:
        _migrate_plain(user_id, key)
        enc, _plain = _paths(user_id)
        return decrypt_text(enc.read_text(encoding="utf-8"), key).strip() if enc.exists() else ""
    except Exception as e:
        log.warning("[USER_KNOWLEDGE] read failed for %s: %s", user_id, type(e).__name__)
        return ""


def write(user_id: str, text: str, allow_system_key: bool = False) -> bool:
    if not user_id or user_id == "global":
        return False
    key = _key(user_id, allow_system_key)
    if not key:
        log.info("[USER_KNOWLEDGE] %s: no key — not written (never plaintext)", user_id)
        return False
    try:
        _migrate_plain(user_id, key)
        enc, _plain = _paths(user_id)
        enc.parent.mkdir(parents=True, exist_ok=True)
        tmp = enc.with_suffix(".tmp")
        tmp.write_text(encrypt_text(text, key), encoding="utf-8")
        tmp.replace(enc)
        return True
    except Exception as e:
        log.warning("[USER_KNOWLEDGE] write failed for %s: %s", user_id, type(e).__name__)
        return False
