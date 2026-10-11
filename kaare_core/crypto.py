"""
Cryptographic primitives for Kåre per-user privacy.

Uses:
  - PyNaCl (libsodium): X25519 keypair, SealedBox (agent writes), SecretBox (session)
  - Argon2id (argon2-cffi): PIN → 32-byte key derivation
  - mnemonic (BIP39): 12-word recovery seed phrase

Design:
  - Each personal user account gets an X25519 keypair at creation time
  - The private key is encrypted with a key derived from the user's PIN (Argon2id)
  - Agents can write to a user's vault using only the public key (SealedBox)
  - The private key lives in RAM only during an active session (session_keys.py)
  - System accounts ("admin") never get a keypair
  - P87: a second copy of each user's private key is encrypted with a per-machine
    system master key (/kaare/state/system_master.key), so Kåre's own background
    jobs (reflection meeting, nightjob, STM restart-recovery) can decrypt a user's
    data without a live PIN session. Never used for interactive/GUI-facing reads
    — see session_keys.py::resolve_key_sync().
"""

import base64
import json
import os
from pathlib import Path

import nacl.public
import nacl.secret
import nacl.utils
from argon2.low_level import Type, hash_secret_raw
from mnemonic import Mnemonic

# --- Argon2id parameters (OWASP recommended for PIN derivation) ---
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 65536  # 64 MB
_ARGON2_PARALLELISM = 1
_ARGON2_HASH_LEN = 32
_ARGON2_SALT_LEN = 16

_mnemo = Mnemonic("english")

# --- System master key (P87 — unattended background access to per-user data) ---
_SYSTEM_MASTER_KEY_PATH = Path("/kaare/state/system_master.key")
_system_master_key_cache: bytes | None = None


def ensure_system_master_key() -> bytes:
    """Load the per-machine system master key, generating it on first run.

    Rotation was explicitly rejected (P87 Decision 1) — a stolen NAS backup carries
    key and data together regardless of rotation cadence, so the only real defense
    is keeping this file off the backup (see scripts/backup_to_nas.sh exclude).
    Cached in-process since the key never changes after creation.
    """
    global _system_master_key_cache
    if _system_master_key_cache is not None:
        return _system_master_key_cache
    if not _SYSTEM_MASTER_KEY_PATH.exists():
        _SYSTEM_MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _SYSTEM_MASTER_KEY_PATH.with_suffix(".tmp")
        tmp_path.write_bytes(os.urandom(32))
        tmp_path.chmod(0o600)
        os.replace(tmp_path, _SYSTEM_MASTER_KEY_PATH)
    _system_master_key_cache = _SYSTEM_MASTER_KEY_PATH.read_bytes()
    return _system_master_key_cache


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh X25519 keypair. Returns (public_key_bytes, private_key_bytes)."""
    private_key = nacl.public.PrivateKey.generate()
    return bytes(private_key.public_key), bytes(private_key)


def generate_salt() -> bytes:
    """Generate a random 16-byte Argon2 salt."""
    return os.urandom(_ARGON2_SALT_LEN)


def derive_key_from_pin(pin: str, salt: bytes) -> bytes:
    """Derive a 32-byte symmetric key from a PIN using Argon2id."""
    return hash_secret_raw(
        secret=pin.encode("utf-8"),
        salt=salt,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
        type=Type.ID,
    )


def encrypt_private_key(private_key: bytes, derived_key: bytes) -> str:
    """Encrypt private_key with a PIN-derived symmetric key. Returns base64 string."""
    box = nacl.secret.SecretBox(derived_key)
    encrypted = box.encrypt(private_key)
    return base64.b64encode(encrypted).decode("utf-8")


def decrypt_private_key(encrypted_blob: str, derived_key: bytes) -> bytes:
    """Decrypt private_key from base64 blob using PIN-derived key. Raises on wrong PIN."""
    box = nacl.secret.SecretBox(derived_key)
    encrypted = base64.b64decode(encrypted_blob)
    return box.decrypt(encrypted)


def seal(data: str, public_key_bytes: bytes) -> str:
    """
    Encrypt text for a user using only their public key (SealedBox).
    Used by agents writing to a user's vault while the user is offline.
    Returns base64 string.
    """
    public_key = nacl.public.PublicKey(public_key_bytes)
    sealed_box = nacl.public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(data.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def unseal(encrypted_blob: str, private_key_bytes: bytes) -> str:
    """Decrypt a SealedBox vault blob using the user's private key."""
    private_key = nacl.public.PrivateKey(private_key_bytes)
    sealed_box = nacl.public.SealedBox(private_key)
    encrypted = base64.b64decode(encrypted_blob)
    return sealed_box.decrypt(encrypted).decode("utf-8")


def encrypt_text(text: str, private_key_bytes: bytes) -> str:
    """
    Encrypt arbitrary text using the user's private key as symmetric seed.
    Used for profile.yaml private section and observations.md.
    Returns base64 string.
    """
    # Derive a symmetric key from the private key (deterministic, no PIN needed once unlocked)
    sym_key = private_key_bytes[:32]
    box = nacl.secret.SecretBox(sym_key)
    encrypted = box.encrypt(text.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def decrypt_text(encrypted_blob: str, private_key_bytes: bytes) -> str:
    """Decrypt text encrypted with encrypt_text."""
    sym_key = private_key_bytes[:32]
    box = nacl.secret.SecretBox(sym_key)
    encrypted = base64.b64decode(encrypted_blob)
    return box.decrypt(encrypted).decode("utf-8")


def encrypt_dict(data: dict, private_key_bytes: bytes) -> str:
    """Serialize dict to JSON and encrypt. Returns base64 string."""
    return encrypt_text(json.dumps(data, ensure_ascii=False), private_key_bytes)


def decrypt_dict(encrypted_blob: str, private_key_bytes: bytes) -> dict:
    """Decrypt and deserialize JSON dict."""
    return json.loads(decrypt_text(encrypted_blob, private_key_bytes))


def generate_seed_phrase() -> str:
    """Generate a BIP39 12-word recovery seed phrase."""
    return _mnemo.generate(strength=128)


def seed_phrase_to_private_key(
    words: str, original_salt: bytes, original_public_key: bytes
) -> bytes | None:
    """
    Recover private key from seed phrase.
    The seed phrase encodes all 32 raw private key bytes directly (24-word BIP39, not via HD
    derivation) — lossless round trip, no padding/truncation. See private_key_to_seed_phrase().
    Returns private_key bytes if the recovered key matches public_key, else None.
    """
    try:
        private_key_bytes = bytes(_mnemo.to_entropy(words))
        recovered = nacl.public.PrivateKey(private_key_bytes)
        if bytes(recovered.public_key) == original_public_key:
            return private_key_bytes
        return None
    except Exception:
        return None


def write_user_text_file(
    path_base: Path, text: str, user_id: str, allow_system_key: bool = False
) -> None:
    """Write text to path_base.enc (encrypted) if a key is available, else path_base.md (plaintext).

    Mirrors the observations.md/.enc dual-file pattern already used elsewhere in KTSB
    (profile_manager.py's _write_obs_text). Shared here (P87) so per-user state files
    written by unattended background jobs (e.g. miss_kare_portrait.md) don't each
    reimplement the same encrypt-or-fallback dance.
    """
    # PLC0415: circular: crypto ↔ session_keys
    from kaare_core.session_keys import resolve_key_sync  # noqa: PLC0415

    enc_path = path_base.with_suffix(".enc")
    md_path = path_base.with_suffix(".md")
    key = resolve_key_sync(user_id, allow_system_key=allow_system_key)
    if key:
        enc_path.write_text(encrypt_text(text, key), encoding="utf-8")
        if md_path.exists():
            md_path.unlink()
        return
    md_path.write_text(text, encoding="utf-8")


def read_user_text_file(path_base: Path, user_id: str, allow_system_key: bool = False) -> str:
    """Read text written by write_user_text_file(). Returns "" if encrypted and no key resolves."""
    enc_path = path_base.with_suffix(".enc")
    md_path = path_base.with_suffix(".md")
    if enc_path.exists():
        # PLC0415: circular: crypto ↔ session_keys
        from kaare_core.session_keys import resolve_key_sync  # noqa: PLC0415

        key = resolve_key_sync(user_id, allow_system_key=allow_system_key)
        if not key:
            return ""
        try:
            return decrypt_text(enc_path.read_text(encoding="utf-8"), key)
        except Exception:
            return ""
    if md_path.exists():
        try:
            return md_path.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def private_key_to_seed_phrase(private_key_bytes: bytes) -> str:
    """
    Convert private key bytes to a BIP39-compatible seed phrase.
    Encodes the full 32-byte private key as entropy (256 bits -> 24 words) so recovery is a
    lossless round trip. P95: a prior version used only the first 16 bytes (128 bits -> 12
    words) and reconstructed the other 16 by repeating them — those bytes are independently
    random for an X25519 key generated via generate_keypair(), so that version could never
    actually recover the correct key. Fixed by encoding all 32 bytes instead of discarding half.
    NOTE: Shown to user only once (account creation, or an explicit self-service reissue).
    """
    return _mnemo.to_mnemonic(private_key_bytes)
