"""
JWT-basert autentisering for Kåre.

Token-flyt:
  POST /api/auth/login {username, pin} → {token, user}
  Alle beskyttede endepunkter: Authorization: Bearer <token>

Hemmeligheten lastes fra /kaare/configs/auth.env (JWT_SECRET).
Genereres automatisk ved første oppstart og lagres.
"""

import base64
import ipaddress
import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import jwt
import yaml
from fastapi import Header, HTTPException, status

from kaare_core.users.store import get_user_with_hash, verify_pin, get_keypair_data, store_keypair, has_keypair
from kaare_core.crypto import (
    generate_keypair, generate_salt, derive_key_from_pin,
    encrypt_private_key, decrypt_private_key, private_key_to_seed_phrase,
)
from kaare_core import session_keys as _session_keys

logger = logging.getLogger(__name__)

# Accounts that are system-only and never get a personal keypair
SYSTEM_ACCOUNTS: set[str] = {"admin"}

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

SECRET_PATH = Path("/kaare/configs/auth.env")
TOKEN_EXPIRY_HOURS = 8
ALGORITHM = "HS256"


def _load_or_create_secret() -> str:
    """Laster JWT-hemmelighet fra fil, eller genererer og lagrer en ny."""
    if SECRET_PATH.exists():
        for line in SECRET_PATH.read_text().splitlines():
            if line.startswith("JWT_SECRET="):
                return line.split("=", 1)[1].strip()
    secret = secrets.token_hex(32)
    with open(SECRET_PATH, "a") as f:
        f.write(f"\nJWT_SECRET={secret}\n")
    return secret


_SECRET = _load_or_create_secret()


# ── Token ──────────────────────────────────────────────────────────────────────

def create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token utløpt — logg inn igjen.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Ugyldig token.")


# ── Keypair setup (called after user creation) ─────────────────────────────────

def setup_keypair(username: str, pin: str) -> Optional[str]:
    """Generate and store a keypair for a personal user account.
    Returns the BIP39 seed phrase (shown once to user), or None for system accounts."""
    if username in SYSTEM_ACCOUNTS:
        return None
    if has_keypair(username):
        logger.info(f"[AUTH] {username} already has keypair, skipping")
        return None
    public_key_bytes, private_key_bytes = generate_keypair()
    salt = generate_salt()
    derived_key = derive_key_from_pin(pin, salt)
    encrypted_pk = encrypt_private_key(private_key_bytes, derived_key)
    public_key_b64 = base64.b64encode(public_key_bytes).decode()
    argon2_salt_b64 = base64.b64encode(salt).decode()
    store_keypair(username, public_key_b64, encrypted_pk, argon2_salt_b64)
    seed_phrase = private_key_to_seed_phrase(private_key_bytes)
    logger.info(f"[AUTH] keypair generated and stored for {username}")
    return seed_phrase


async def unlock_session(username: str, pin: str, expires_at: float) -> bool:
    """Decrypt private key with PIN and store in RAM for this session.
    Returns True on success, False if no keypair or wrong PIN.
    Also processes any pending vault files written while the user was offline."""
    if username in SYSTEM_ACCOUNTS:
        return False
    kp = get_keypair_data(username)
    if not kp:
        return False
    try:
        salt = base64.b64decode(kp["argon2_salt"])
        derived_key = derive_key_from_pin(pin, salt)
        private_key = decrypt_private_key(kp["encrypted_private_key"], derived_key)
        await _session_keys.store_session_key(username, private_key, expires_at)
        # Apply any vault entries written while user was offline (non-blocking best-effort)
        try:
            from kaare_core.users.profile_manager import process_vault_files
            count = process_vault_files(username, private_key)
            if count:
                logger.info(f"[AUTH] processed {count} vault entries for {username} on login")
        except Exception as ve:
            logger.warning(f"[AUTH] vault processing error for {username}: {ve}")
        return True
    except Exception as e:
        logger.warning(f"[AUTH] failed to unlock session key for {username}: {e}")
        return False


async def end_session(username: str) -> None:
    """Revoke in-RAM private key on logout."""
    await _session_keys.revoke_session_key(username)


# ── Login ──────────────────────────────────────────────────────────────────────

def login(username: str, pin: str) -> Optional[dict]:
    """
    Verifiserer brukernavn og PIN.
    Returnerer {token, user, must_change_pin, _pin_for_session} eller None ved feil.
    _pin_for_session is used by the API to call unlock_session() after login.
    """
    user = get_user_with_hash(username)
    if not user:
        return None
    if not user.get("is_active"):
        return None
    if not verify_pin(pin, user["pin_hash"]):
        return None

    token = create_token(user["username"], user["role"])
    safe_user = {k: v for k, v in user.items()
                 if k not in ("pin_hash", "pin_expires_at",
                              "encrypted_private_key", "argon2_salt", "public_key")}
    safe_user["is_active"] = bool(safe_user["is_active"])
    safe_user["must_change_pin"] = bool(safe_user.get("must_change_pin", 0))
    touch_last_seen(user["username"])
    expires_at = time.time() + TOKEN_EXPIRY_HOURS * 3600
    return {
        "token": token,
        "user": safe_user,
        "must_change_pin": safe_user["must_change_pin"],
        "_pin_for_session": pin,         # consumed by API, never sent to client
        "_expires_at": expires_at,       # consumed by API
    }


# ── FastAPI dependencies ───────────────────────────────────────────────────────

def _extract_token(authorization: str) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Mangler Authorization-header.")
    return authorization.split(" ", 1)[1]


# ── Presence tracking (in-memory) ─────────────────────────────────────────────

_last_seen: dict[str, datetime] = {}
_ONLINE_WINDOW = 600  # seconds — matches the 10-minute frontend inactivity timeout


def touch_last_seen(username: str) -> None:
    _last_seen[username] = datetime.now(timezone.utc)


def is_user_online(username: str) -> bool:
    ts = _last_seen.get(username)
    if not ts:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() < _ONLINE_WINDOW


def get_all_last_seen() -> dict[str, str]:
    return {u: ts.isoformat(timespec="seconds") for u, ts in _last_seen.items()}


# ── Network context ────────────────────────────────────────────────────────────

def get_network_context(client_ip: str) -> str:
    """Returns 'local', 'vpn', or 'external' based on client IP.
    Reads subnet ranges from configs/settings.yaml. Fails safe to 'local'."""
    try:
        ip = ipaddress.ip_address(client_ip)
        if ip.is_loopback:
            return "local"
        settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        net = settings.get("network", {})
        local_net = ipaddress.ip_network(net.get("local_subnet", "192.168.0.0/24"), strict=False)
        vpn_net = ipaddress.ip_network(net.get("vpn_subnet", "10.0.0.0/24"), strict=False)
        if ip in local_net:
            return "local"
        if ip in vpn_net:
            return "vpn"
        return "external"
    except Exception:
        return "local"


# ── FastAPI dependencies ───────────────────────────────────────────────────────

def require_auth(authorization: str = Header(default="")) -> dict:
    """Dependency: requires valid token. Updates presence on each call."""
    payload = decode_token(_extract_token(authorization))
    touch_last_seen(payload["sub"])
    return payload


def require_admin(authorization: str = Header(default="")) -> dict:
    """Dependency: requires admin role."""
    payload = decode_token(_extract_token(authorization))
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Krever admin-tilgang.")
    touch_last_seen(payload["sub"])
    return payload
