"""Encryption of the Frigate user's memory at rest (N19 point 3, DT04 R0.6a).

Reuses P87's per-machine system master key (`crypto.ensure_system_master_key`, `state/system_master.key`) — no new key
file, no PIN: the camera pipe runs unattended. Two sub-keys are derived from it (keyed BLAKE2b, one per purpose), so
the master key itself never encrypts anything here.

- `seal(text)`      free text / JSON → random nonce (XSalsa20-Poly1305, `nacl.secret.SecretBox`). Prefix `fs1:`.
- `seal_ref(text)`  a person or plate reference → deterministic: the nonce is a keyed hash of the plaintext, so the
                    same name always gives the same ciphertext and the store's `=` / `IN` / `GROUP BY` / unique keys keep
                    working. Leaks only «same reference», never which. Prefix `fd1:`.
- `seal_bytes(b)`   pictures (event snapshots, episode frames). Magic `KTSBfs1\\0`.

Reads accept plaintext written before encryption (no prefix → returned as is), so old rows and files keep working
until the one-time sealing pass has run. Sealing is idempotent: an already sealed value is returned unchanged.

What it protects: a copy of `frigate.db` or the picture folders without the key file (a NAS backup, a lost disk —
the key never leaves the machine, P87 Decision 1). Not a running machine. The flip side: a restore on a new machine
cannot read the camera memory; `unseal` then returns None (logged once), never raises.
"""

from __future__ import annotations

import base64
import hashlib
import logging

import nacl.exceptions
import nacl.secret

from kaare_core import crypto

log = logging.getLogger(__name__)

_TEXT = "fs1:"
_REF = "fd1:"
_MAGIC = b"KTSBfs1\0"
_keys: tuple[bytes, bytes] | None = None
_warned = False


def _master() -> bytes:
    """The machine's system master key (tests patch this)."""
    return crypto.ensure_system_master_key()


def _subkeys() -> tuple[bytes, bytes]:
    global _keys
    if _keys is None:
        master = _master()
        enc = hashlib.blake2b(b"ktsb/frigate-store/enc/v1", key=master, digest_size=32).digest()
        nonce = hashlib.blake2b(b"ktsb/frigate-store/nonce/v1", key=master, digest_size=32).digest()
        _keys = (enc, nonce)
    return _keys


def reset_keys() -> None:
    """Forget the cached sub-keys (tests switch keys)."""
    global _keys, _warned
    _keys = None
    _warned = False


def is_sealed(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith((_TEXT, _REF))


def seal(text: str | None) -> str | None:
    if not text or is_sealed(text):
        return text
    box = nacl.secret.SecretBox(_subkeys()[0])
    return _TEXT + base64.b64encode(box.encrypt(text.encode("utf-8"))).decode("ascii")


def seal_ref(text: str | None) -> str | None:
    if not text or is_sealed(text):
        return text
    enc, nonce_key = _subkeys()
    raw = text.encode("utf-8")
    nonce = hashlib.blake2b(raw, key=nonce_key, digest_size=nacl.secret.SecretBox.NONCE_SIZE).digest()
    return _REF + base64.b64encode(nacl.secret.SecretBox(enc).encrypt(raw, nonce)).decode("ascii")


def _cannot_read(what: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("[FRIGATE_SEAL] %s could not be decrypted — wrong or missing system key (restored on another "
                    "machine?); it reads as empty", what)


def unseal(value: str | None) -> str | None:
    if not is_sealed(value):
        return value  # None, '' or plaintext from before encryption
    try:
        blob = base64.b64decode(value[len(_TEXT):])  # both prefixes have the same length
        return nacl.secret.SecretBox(_subkeys()[0]).decrypt(blob).decode("utf-8")
    except (nacl.exceptions.CryptoError, ValueError):
        _cannot_read("a camera memory value")
        return None


def seal_bytes(data: bytes) -> bytes:
    if not data or data.startswith(_MAGIC):
        return data
    return _MAGIC + bytes(nacl.secret.SecretBox(_subkeys()[0]).encrypt(data))


def unseal_bytes(data: bytes | None) -> bytes | None:
    if not data or not data.startswith(_MAGIC):
        return data  # plaintext picture from before encryption
    try:
        return nacl.secret.SecretBox(_subkeys()[0]).decrypt(data[len(_MAGIC):])
    except nacl.exceptions.CryptoError:
        _cannot_read("a camera picture")
        return None


def read_picture(path) -> bytes | None:
    """A stored picture, decrypted; None when missing or unreadable. Never raises."""
    try:
        return unseal_bytes(path.read_bytes())
    except OSError:
        return None
