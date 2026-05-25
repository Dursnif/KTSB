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
"""

import base64
import json
import os

from argon2 import PasswordHasher
from argon2.low_level import Type, hash_secret_raw
import nacl.public
import nacl.secret
import nacl.utils
from mnemonic import Mnemonic


# --- Argon2id parameters (OWASP recommended for PIN derivation) ---
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 65536  # 64 MB
_ARGON2_PARALLELISM = 1
_ARGON2_HASH_LEN = 32
_ARGON2_SALT_LEN = 16

_mnemo = Mnemonic("english")


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


def seed_phrase_to_private_key(words: str, original_salt: bytes, original_public_key: bytes) -> bytes | None:
    """
    Recover private key from seed phrase.
    The seed phrase encodes the raw private key bytes directly (not via HD derivation).
    Returns private_key bytes if the recovered key matches public_key, else None.
    """
    try:
        entropy = _mnemo.to_entropy(words)
        # entropy is 16 bytes for 12-word phrase — pad to 32 bytes for X25519
        private_key_bytes = (entropy * 3)[:32]
        recovered = nacl.public.PrivateKey(private_key_bytes)
        if bytes(recovered.public_key) == original_public_key:
            return private_key_bytes
        return None
    except Exception:
        return None


def private_key_to_seed_phrase(private_key_bytes: bytes) -> str:
    """
    Convert private key bytes to a BIP39-compatible seed phrase.
    Uses the first 16 bytes of the private key as entropy (128 bits → 12 words).
    NOTE: Shown to user only once at account creation.
    """
    entropy = private_key_bytes[:16]
    return _mnemo.to_mnemonic(entropy)
