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
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import jwt
import yaml
from fastapi import Header, HTTPException, Query, status
from typing import Optional

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
ALGORITHM = "HS256"

try:
    _auth_settings = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
    TOKEN_EXPIRY_HOURS: int = int(_auth_settings.get("token_expiry_hours", 4))
except Exception:
    TOKEN_EXPIRY_HOURS = 4


def _load_or_create_secret() -> str:
    """Laster JWT-hemmelighet fra fil, eller genererer og lagrer en ny."""
    if SECRET_PATH.exists():
        for line in SECRET_PATH.read_text().splitlines():
            if line.startswith("JWT_SECRET="):
                return line.split("=", 1)[1].strip()
    secret = secrets.token_hex(32)
    with open(SECRET_PATH, "a") as f:
        f.write(f"\nJWT_SECRET={secret}\n")
    SECRET_PATH.chmod(0o600)
    return secret


_SECRET = _load_or_create_secret()


# ── Token ──────────────────────────────────────────────────────────────────────

def create_token(username: str, role: str, expiry_hours: int | None = None) -> str:
    hours = expiry_hours if expiry_hours is not None else TOKEN_EXPIRY_HOURS
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token expired — please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid token.")


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


def _restore_stm_from_history(username: str, stm) -> None:
    """Load the most recent plaintext daily archive into stm if live snapshot is empty.

    Called after login when the encrypted live snapshot was written during a blank restart.
    Archives are plain JSON (pre-encryption or pre-Phase2) so no session key is needed.
    """
    import json as _j
    hist_dir = Path("/kaare/state/stm_history")
    if not hist_dir.exists():
        return
    candidates = []
    for entry in hist_dir.iterdir():
        if entry.is_dir() and len(entry.name) == 10:
            p = entry / f"{username}.json"
            if p.exists():
                candidates.append((entry.name, p))
        elif entry.suffix == ".json" and len(entry.stem) == 10:
            candidates.append((entry.stem, entry))
    if not candidates:
        return
    candidates.sort(key=lambda x: x[0], reverse=True)
    date_key, snap_path = candidates[0]
    try:
        data = _j.loads(snap_path.read_text(encoding="utf-8"))
        stm.load_from_dict(data)
        turns = stm.snapshot_counts().get("dialog_turns", 0)
        logger.info(f"[AUTH] STM restored from history archive {date_key} for {username} ({turns} turns)")
    except Exception as e:
        logger.warning(f"[AUTH] STM history restore failed for {username}: {e}")


async def unlock_session(username: str, pin: str, expires_at: float) -> bool:
    """Decrypt private key with PIN and store in RAM for this session.
    Returns True on success, False if no keypair or wrong PIN.
    Also processes any pending vault files written while the user was offline."""
    if username in SYSTEM_ACCOUNTS:
        return False
    kp = get_keypair_data(username)
    if not kp:
        # First login after crypto deploy — generate keypair for pre-existing user
        setup_keypair(username, pin)
        kp = get_keypair_data(username)
        if not kp:
            return False
    try:
        salt = base64.b64decode(kp["argon2_salt"])
        derived_key = derive_key_from_pin(pin, salt)
        private_key = decrypt_private_key(kp["encrypted_private_key"], derived_key)
        await _session_keys.store_session_key(username, private_key, expires_at)
        # Reload encrypted STM snapshot now that session key is in RAM
        try:
            from kaare_core.app_state import STM_REGISTRY
            if STM_REGISTRY is not None:
                stm = STM_REGISTRY.get(username)
                if stm.snapshot_counts()["dialog_turns"] == 0:
                    loaded = stm.load_snapshot(f"/kaare/state/stm_users/{username}.json")
                    if loaded:
                        logger.info(f"[AUTH] STM reloaded from encrypted snapshot for {username}")
                # If still empty (snapshot was written after a blank restart), fall back to
                # the most recent daily archive so the user sees their conversation history.
                if stm.snapshot_counts()["dialog_turns"] == 0:
                    _restore_stm_from_history(username, stm)
        except Exception as se:
            logger.warning(f"[AUTH] STM reload error for {username}: {se}")
        # Apply any vault entries written while user was offline (non-blocking best-effort)
        try:
            from kaare_core.users.profile_manager import process_vault_files
            count = process_vault_files(username, private_key)
            if count:
                logger.info(f"[AUTH] processed {count} vault entries for {username} on login")
        except Exception as ve:
            logger.warning(f"[AUTH] vault processing error for {username}: {ve}")
        # Migrate profile/observations to encrypted format on first login after deploy
        try:
            from scripts.migrate_encrypt_user_data import migrate_user_if_needed, finalize_encryption
            migrate_user_if_needed(username, private_key)
            finalize_encryption(username, private_key)
        except Exception as me:
            logger.warning(f"[AUTH] profile migration error for {username}: {me}")
        # Migrate existing plaintext LTM rows to encrypted format (background, fire-and-forget)
        try:
            from kaare_core.memory.long_term import migrate_user_ltm
            threading.Thread(target=migrate_user_ltm, args=(username,), daemon=True).start()
        except Exception as le:
            logger.warning(f"[AUTH] LTM migration trigger error for {username}: {le}")
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

    pin_required = bool(user.get("pin_required", 0))
    token_hours = 1 if pin_required else TOKEN_EXPIRY_HOURS
    token = create_token(user["username"], user["role"], expiry_hours=token_hours)
    safe_user = {k: v for k, v in user.items()
                 if k not in ("pin_hash", "pin_expires_at",
                              "encrypted_private_key", "argon2_salt", "public_key")}
    safe_user["is_active"] = bool(safe_user["is_active"])
    safe_user["must_change_pin"] = bool(safe_user.get("must_change_pin", 0))
    safe_user["pin_required"] = pin_required
    touch_last_seen(user["username"])
    expires_at = time.time() + token_hours * 3600
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
                            detail="Missing Authorization header.")
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
    Reads subnet ranges from configs/settings.yaml. Fails safe to 'local'.
    Docker bridge (172.16.0.0/12) is always treated as local regardless of config."""
    try:
        ip = ipaddress.ip_address(client_ip)
        if ip.is_loopback:
            return "local"
        if ip in ipaddress.ip_network("172.16.0.0/12"):
            return "local"
        settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        net = settings.get("network", {})
        # Support both local_subnets (list) and legacy local_subnet (string)
        raw = net.get("local_subnets") or [net.get("local_subnet", "192.168.0.0/24")]
        if isinstance(raw, str):
            raw = [raw]
        for subnet_str in raw:
            if ip in ipaddress.ip_network(subnet_str, strict=False):
                return "local"
        vpn_net = ipaddress.ip_network(net.get("vpn_subnet", "10.0.0.0/24"), strict=False)
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
                            detail="Admin access required.")
    touch_last_seen(payload["sub"])
    return payload


def require_image_auth(
    authorization: str = Header(default=""),
    token: Optional[str] = Query(default=None),
) -> dict:
    """Dependency for image endpoints: accepts JWT from Authorization header OR ?token= query param.
    The query-param path exists because <img src="..."> in browsers cannot send custom headers."""
    raw = token or (authorization.split(" ", 1)[1] if authorization.startswith("Bearer ") else "")
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing token.")
    payload = decode_token(raw)
    touch_last_seen(payload["sub"])
    return payload
