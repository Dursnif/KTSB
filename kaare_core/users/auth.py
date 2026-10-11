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
import json as _j
import logging
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import yaml
from fastapi import Header, HTTPException, Query, status

from kaare_core import (
    app_state,  # app_state.STM_REGISTRY is set by kaare_api at startup — read it per call
    config_store,
)
from kaare_core import session_keys as _session_keys
from kaare_core.config import get_network_settings
from kaare_core.crypto import (
    decrypt_private_key,
    derive_key_from_pin,
    encrypt_private_key,
    ensure_system_master_key,
    generate_keypair,
    generate_salt,
    private_key_to_seed_phrase,
)
from kaare_core.memory.long_term import migrate_user_ltm
from kaare_core.users.profile_manager import process_vault_files
from kaare_core.users.store import (
    SYSTEM_IDENTITIES,
    get_keypair_data,
    get_system_encrypted_key,
    get_user,
    get_user_with_hash,
    has_keypair,
    store_keypair,
    store_system_encrypted_key,
    verify_pin,
)
from scripts.migrate_encrypt_user_data import finalize_encryption, migrate_user_if_needed

logger = logging.getLogger(__name__)

# Accounts that are system-only and never get a personal keypair
SYSTEM_ACCOUNTS: set[str] = {"admin"} | set(SYSTEM_IDENTITIES)

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

    def _existing() -> str:
        for line in config_store.read_text(SECRET_PATH).splitlines():
            if line.startswith("JWT_SECRET="):
                return line.split("=", 1)[1].strip()
        return ""

    found = _existing()
    if found:
        return found
    # Processes/containers importing this at the same first start must end up with one shared secret (P134).
    with config_store.lock(SECRET_PATH):
        found = _existing()
        if found:
            return found
        secret = secrets.token_hex(32)
        config_store.set_env_keys(
            SECRET_PATH, {"JWT_SECRET": secret}, "jwt-secret-created", mode=0o600
        )
    SECRET_PATH.chmod(0o600)
    return secret


_SECRET = _load_or_create_secret()


# ── Token ──────────────────────────────────────────────────────────────────────


def create_token(
    username: str, role: str, expiry_hours: int | None = None, pin_change: bool = False
) -> str:
    hours = expiry_hours if expiry_hours is not None else TOKEN_EXPIRY_HOURS
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(UTC) + timedelta(hours=hours),
        "iat": datetime.now(UTC),
    }
    if pin_change:
        payload["pin_change"] = True  # restricted until the PIN is changed (pin_change_blocks)
    return jwt.encode(payload, _SECRET, algorithm=ALGORITHM)


def pin_change_blocks(method: str, path: str, payload: dict) -> bool:
    """True when a token issued with must_change_pin may not reach this route yet (DT03 R6 H2).

    Before, must_change_pin was only a hint to the frontend: the default admin (PIN 1234) got a
    full admin token, so anyone on the LAN could take over a fresh install through the API. Now
    such a token may only change its own PIN or log out. The flag in the database is checked —
    not just the token claim — so the same token works the moment the PIN has been changed
    (the frontend keeps using it). Only tokens carrying the claim pay for the lookup."""
    if not payload.get("pin_change"):
        return False
    user = payload.get("sub", "")
    if method == "PUT" and path == f"/api/users/{user}/pin":
        return False
    if method == "POST" and path == "/api/auth/logout":
        return False
    try:
        record = get_user(user)
    except Exception:
        return True  # fail closed
    return bool(record and record.get("must_change_pin"))


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired — please log in again."
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token."
        ) from exc


# ── Keypair setup (called after user creation) ─────────────────────────────────


def setup_keypair(username: str, pin: str) -> str | None:
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
    _store_system_key_copy(username, private_key_bytes)
    seed_phrase = private_key_to_seed_phrase(private_key_bytes)
    logger.info(f"[AUTH] keypair generated and stored for {username}")
    return seed_phrase


def _store_system_key_copy(username: str, private_key_bytes: bytes) -> None:
    """Store a second copy of the private key encrypted with the system master key (P87).

    Lets Kåre's own background jobs (reflection meeting, dev meeting, nightjob,
    STM restart-recovery) decrypt this user's data without a live PIN session —
    see session_keys.py::resolve_key_sync(). Never used for interactive reads.
    """
    master_key = ensure_system_master_key()
    system_blob = encrypt_private_key(private_key_bytes, master_key)
    store_system_encrypted_key(username, system_blob)


def _restore_stm_from_history(username: str, stm) -> None:
    """Load the most recent plaintext daily archive into stm if live snapshot is empty.

    Called after login when the encrypted live snapshot was written during a blank restart.
    Archives are plain JSON (pre-encryption or pre-Phase2) so no session key is needed.
    """

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
        logger.info(
            f"[AUTH] STM restored from history archive {date_key} for {username} ({turns} turns)"
        )
    except Exception as e:
        logger.warning(f"[AUTH] STM history restore failed for {username}: {e}")


async def unlock_session(username: str, pin: str, expires_at: float) -> tuple[bool, str | None]:
    """Decrypt private key with PIN and store in RAM for this session.
    Returns (success, new_seed_phrase). new_seed_phrase is only non-None when a keypair was
    just generated for this user right here (first login for a pre-existing account created
    before crypto deploy) — the caller must surface it to the user immediately, same as at
    account creation, since it is never shown again (P95 Phase 4 — previously this phrase was
    silently discarded, leaving the user with no recovery path at all).
    Also processes any pending vault files written while the user was offline."""
    if username in SYSTEM_ACCOUNTS:
        return False, None
    kp = get_keypair_data(username)
    new_seed_phrase = None
    if not kp:
        # First login after crypto deploy — generate keypair for pre-existing user
        new_seed_phrase = setup_keypair(username, pin)
        kp = get_keypair_data(username)
        if not kp:
            return False, None
    try:
        salt = base64.b64decode(kp["argon2_salt"])
        derived_key = derive_key_from_pin(pin, salt)
        private_key = decrypt_private_key(kp["encrypted_private_key"], derived_key)
        await _session_keys.store_session_key(username, private_key, expires_at)
        # P87 lazy backfill: users created before the system key existed have a
        # keypair but no system_encrypted_private_key column value yet. Populate
        # it now that we have the decrypted private key in hand from a real PIN login.
        # Also repair a copy that no longer opens: after a restore onto another machine
        # users.db carries the old copy, but system_master.key is new (it never leaves the
        # machine, P87 Decision 1) — background jobs would stay locked out (P116 B11).
        if get_system_encrypted_key(username) is None:
            _store_system_key_copy(username, private_key)
            logger.info(f"[AUTH] system key backfilled for {username}")
        elif _session_keys.get_system_key_sync(username) != private_key:
            _store_system_key_copy(username, private_key)
            logger.info(f"[AUTH] system key copy repaired for {username} (master key changed)")
        # Reload encrypted STM snapshot now that session key is in RAM
        try:
            if app_state.STM_REGISTRY is not None:
                stm = app_state.STM_REGISTRY.get(username)
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
            count = process_vault_files(username, private_key)
            if count:
                logger.info(f"[AUTH] processed {count} vault entries for {username} on login")
        except Exception as ve:
            logger.warning(f"[AUTH] vault processing error for {username}: {ve}")
        # Migrate profile/observations to encrypted format on first login after deploy
        try:
            migrate_user_if_needed(username, private_key)
            finalize_encryption(username, private_key)
        except Exception as me:
            logger.warning(f"[AUTH] profile migration error for {username}: {me}")
        # Migrate existing plaintext LTM rows to encrypted format (background, fire-and-forget)
        try:
            threading.Thread(target=migrate_user_ltm, args=(username,), daemon=True).start()
        except Exception as le:
            logger.warning(f"[AUTH] LTM migration trigger error for {username}: {le}")
        return True, new_seed_phrase
    except Exception as e:
        logger.warning(f"[AUTH] failed to unlock session key for {username}: {e}")
        return False, None


async def end_session(username: str) -> None:
    """Revoke in-RAM private key on logout."""
    await _session_keys.revoke_session_key(username)


# ── Login ──────────────────────────────────────────────────────────────────────


def login(username: str, pin: str) -> dict | None:
    """
    Verifiserer brukernavn og PIN.
    Returnerer {token, user, must_change_pin, _pin_for_session} eller None ved feil.
    _pin_for_session is used by the API to call unlock_session() after login.
    """
    if username in SYSTEM_IDENTITIES:
        return None  # system identities (global_user, unknown_user) can never log in (P120)
    user = get_user_with_hash(username)
    if not user:
        return None
    if not user.get("is_active"):
        return None
    if not verify_pin(pin, user["pin_hash"]):
        return None

    pin_required = bool(user.get("pin_required", 0))
    token_hours = 1 if pin_required else TOKEN_EXPIRY_HOURS
    token = create_token(
        user["username"],
        user["role"],
        expiry_hours=token_hours,
        pin_change=bool(user.get("must_change_pin", 0)),
    )
    safe_user = {
        k: v
        for k, v in user.items()
        if k
        not in (
            "pin_hash",
            "pin_expires_at",
            "encrypted_private_key",
            "argon2_salt",
            "system_encrypted_private_key",
            "public_key",
        )
    }
    safe_user["is_active"] = bool(safe_user["is_active"])
    safe_user["must_change_pin"] = bool(safe_user.get("must_change_pin", 0))
    safe_user["pin_required"] = pin_required
    touch_last_seen(user["username"])
    expires_at = time.time() + token_hours * 3600
    return {
        "token": token,
        "user": safe_user,
        "must_change_pin": safe_user["must_change_pin"],
        "_pin_for_session": pin,  # consumed by API, never sent to client
        "_expires_at": expires_at,  # consumed by API
    }


# ── FastAPI dependencies ───────────────────────────────────────────────────────


def _extract_token(authorization: str) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header."
        )
    return authorization.split(" ", 1)[1]


# ── Presence tracking (in-memory) ─────────────────────────────────────────────

_last_seen: dict[str, datetime] = {}
_ONLINE_WINDOW = 600  # seconds — matches the 10-minute frontend inactivity timeout


def touch_last_seen(username: str) -> None:
    _last_seen[username] = datetime.now(UTC)


def is_user_online(username: str) -> bool:
    ts = _last_seen.get(username)
    if not ts:
        return False
    return (datetime.now(UTC) - ts).total_seconds() < _ONLINE_WINDOW


def get_all_last_seen() -> dict[str, str]:
    return {u: ts.isoformat(timespec="seconds") for u, ts in _last_seen.items()}


# ── Network context ────────────────────────────────────────────────────────────


def get_network_context(client_ip: str) -> str:
    """Returns 'local', 'vpn', or 'external' based on the client IP (pass
    kaare_core.client_ip.client_ip(request), never a raw X-Forwarded-For).
    Subnets come from the GUI (Settings → General → Network and access) via
    get_network_settings(). Docker bridge (172.16.0.0/12) counts as local: behind Docker Desktop's
    NAT every LAN client arrives from the gateway, so the real address is not visible there.
    Fails closed to 'external' (DT03 R6) — before, any parse error made the caller 'local'."""
    try:
        ip = ipaddress.ip_address(client_ip)
        if ip.is_loopback:
            return "local"
        if ip in ipaddress.ip_network("172.16.0.0/12"):
            return "local"
        net = get_network_settings()
        for subnet_str in net["local_subnets"]:
            try:
                if ip in ipaddress.ip_network(subnet_str, strict=False):
                    return "local"
            except ValueError:
                continue
        if ip in ipaddress.ip_network(net["vpn_subnet"], strict=False):
            return "vpn"
        return "external"
    except Exception:
        return "external"


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    touch_last_seen(payload["sub"])
    return payload


def require_image_auth(
    authorization: str = Header(default=""),
    token: str | None = Query(default=None),
) -> dict:
    """Dependency for image endpoints: accepts JWT from Authorization header OR ?token= query param.
    The query-param path exists because <img src="..."> in browsers cannot send custom headers."""
    raw = token or (authorization.split(" ", 1)[1] if authorization.startswith("Bearer ") else "")
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")
    payload = decode_token(raw)
    touch_last_seen(payload["sub"])
    return payload
