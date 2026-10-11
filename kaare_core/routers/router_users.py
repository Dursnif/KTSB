"""
Bruker- og auth-endepunkter for Kåre.

POST /api/auth/login        → logg inn med PIN, få token
GET  /api/auth/me           → hvem er jeg? (krever token)
GET  /api/users             → liste alle brukere (admin)
POST /api/users             → opprett bruker (admin)
PUT  /api/users/{username}  → oppdater bruker (admin)
PUT  /api/users/{username}/pin → bytt PIN (admin eller seg selv)
DELETE /api/users/{username} → slett bruker (admin)
GET  /api/users/{username}/summary → grunnleggende brukerinfo (self, admin, eller forelder)
"""

import base64
import collections
import json as _json
import time
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from adapters.llm_adapter import list_personalities
from kaare_core import session_keys as _session_keys
from kaare_core.audit import audit_log as _audit
from kaare_core.client_ip import client_ip as _client_ip_of
from kaare_core.config import get_service
from kaare_core.crypto import (
    derive_key_from_pin,
    encrypt_private_key,
    generate_salt,
    private_key_to_seed_phrase,
    seed_phrase_to_private_key,
)
from kaare_core.service_auth import service_headers
from kaare_core.users import store
from kaare_core.users.auth import (
    create_token,
    end_session,
    get_all_last_seen,
    is_user_online,
    login,
    require_admin,
    require_auth,
    setup_keypair,
    unlock_session,
)
from kaare_core.users.profile_manager import (
    get_household_visible,
    get_profile_flag,
    get_unlock_config,
    init_profile,
    set_profile_flag,
    set_unlock_config,
    update_household_visible,
)
from kaare_core.users.store import reencrypt_private_key

# In-memory limit on failed logins / recoveries (DT03 R6 H1+M1), two counters per 15 min:
#  - per username: stops PIN guessing on one account however many addresses the attacker uses;
#  - per client IP (kaare_core.client_ip — X-Forwarded-For only from GUI-trusted proxies): stops
#    spraying many usernames from one address. Higher, because behind Docker Desktop's NAT every
#    LAN client shares one address and one person's typos must not lock the whole house out.
_LOGIN_FAIL_WINDOW = 900  # seconds
_LOGIN_FAIL_MAX_USER = 5
_LOGIN_FAIL_MAX_IP = 30
_login_failures: dict[str, collections.deque] = {}


def _fail_keys(ip: str, username: str | None) -> list[tuple[str, int]]:
    keys = [(f"ip:{ip}", _LOGIN_FAIL_MAX_IP)]
    if username:
        keys.append((f"user:{username.strip().lower()}", _LOGIN_FAIL_MAX_USER))
    return keys


def _check_login_rate(ip: str, username: str | None = None) -> None:
    now = time.monotonic()
    for key, limit in _fail_keys(ip, username):
        dq = _login_failures.setdefault(key, collections.deque())
        while dq and now - dq[0] > _LOGIN_FAIL_WINDOW:
            dq.popleft()
        if len(dq) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again in 15 minutes.",
            )


def _record_login_fail(ip: str, username: str | None = None) -> None:
    now = time.monotonic()
    for key, _limit in _fail_keys(ip, username):
        _login_failures.setdefault(key, collections.deque()).append(now)


def _clear_login_fail(ip: str, username: str | None = None) -> None:
    """After a success only the account's own counter is cleared — a valid login must not reset
    the per-IP counter that limits guessing at other accounts from the same address."""
    if username:
        _login_failures.pop(f"user:{username.strip().lower()}", None)


_VOICE_BRIDGE = get_service("internal", "voice_bridge")

router = APIRouter(prefix="/api")


# ── Modeller ───────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    pin: str


class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    role: str
    pin: str
    avatar: str = ""


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    avatar: str | None = None
    is_active: bool | None = None
    personality: str | None = None
    vpn_access: str | None = None
    can_manage_child_timers: bool | None = None
    notify_channel: str | None = None
    is_parent: bool | None = None
    pin_required: bool | None = None
    managed_children: str | None = None


class UpdatePinRequest(BaseModel):
    new_pin: str


class RecoverRequest(BaseModel):
    username: str
    seed_phrase: str
    new_pin: str


# ── Personligheter ────────────────────────────────────────────────────────────


@router.get("/personalities")
def api_list_personalities(_u: dict = Depends(require_auth)):
    """Returnerer tilgjengelige personlighetsvarianter."""
    return list_personalities()


# ── Auth ───────────────────────────────────────────────────────────────────────


@router.post("/auth/login")
async def api_login(req: LoginRequest, request: Request):
    client_ip = _client_ip_of(request)
    _check_login_rate(client_ip, req.username)
    # Sjekk om midlertidig PIN har utløpt før vi prøver å logge inn
    if store.check_pin_expired(req.username):
        _record_login_fail(client_ip, req.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Temporary PIN has expired. Ask an administrator for a new one.",
        )
    result = login(req.username, req.pin)
    if not result:
        _record_login_fail(client_ip, req.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Feil brukernavn eller PIN."
        )
    _clear_login_fail(client_ip, req.username)
    # Unlock session key (decrypt private key to RAM) + process vault files
    pin_for_session = result.pop("_pin_for_session", None)
    expires_at = result.pop("_expires_at", None)
    if pin_for_session and expires_at:
        _, new_seed_phrase = await unlock_session(req.username, pin_for_session, expires_at)
        if new_seed_phrase:
            # Keypair was just generated for a pre-existing account (P95 Phase 4) — surface
            # the phrase once, same shape as POST /users's response (user.seed_phrase).
            result["user"]["seed_phrase"] = new_seed_phrase
    return result


@router.post("/auth/logout")
async def api_logout(payload: dict = Depends(require_auth)):
    """Revoke the session key from RAM on explicit logout."""
    await end_session(payload["sub"])
    return {"ok": True}


@router.get("/auth/me")
def api_me(payload: dict = Depends(require_auth)):
    user = store.get_user(payload["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.get("/ping")
def api_ping(payload: dict = Depends(require_auth)):
    """Lightweight presence heartbeat — called by frontend every few minutes."""
    return {"ok": True}


# ── Brukere (admin) ────────────────────────────────────────────────────────────


@router.get("/users")
def api_list_users(payload: dict = Depends(require_admin)):
    users = store.list_users(include_system=True)
    last_seen = get_all_last_seen()
    for u in users:
        u["is_online"] = is_user_online(u["username"])
        u["last_seen"] = last_seen.get(u["username"])
        u["can_manage_child_timers"] = get_profile_flag(u["username"], "can_manage_child_timers")
        hv = get_household_visible(u["username"])
        u["notify_channel"] = hv.get("notify_channel", "sound_node") if hv else "sound_node"
    return users


@router.post("/users", status_code=201)
def api_create_user(req: CreateUserRequest, payload: dict = Depends(require_admin)):
    try:
        user = store.create_user(
            username=req.username,
            display_name=req.display_name,
            role=req.role,
            pin=req.pin,
            avatar=req.avatar,
        )
        init_profile(req.username, req.display_name)
        _audit(
            "admin_user_action",
            payload["sub"],
            f"create_user username={req.username!r} role={req.role!r}",
        )
        # Generate keypair for personal accounts (not system accounts like "admin")
        seed_phrase = setup_keypair(req.username, req.pin)
        if seed_phrase:
            user["seed_phrase"] = seed_phrase  # shown once — frontend must display and hide
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Username already taken.") from e
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/users/{username}")
def api_update_user(username: str, req: UpdateUserRequest, payload: dict = Depends(require_admin)):
    try:
        user = store.update_user(
            username,
            display_name=req.display_name,
            role=req.role,
            avatar=req.avatar,
            is_active=req.is_active,
            personality=req.personality,
            vpn_access=req.vpn_access,
            is_parent=req.is_parent,
            pin_required=req.pin_required,
            managed_children=req.managed_children,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if req.can_manage_child_timers is not None:
        set_profile_flag(username, "can_manage_child_timers", req.can_manage_child_timers)
    user["can_manage_child_timers"] = get_profile_flag(username, "can_manage_child_timers")
    if req.notify_channel is not None:
        update_household_visible(username, "notify_channel", req.notify_channel)
    return user


@router.post("/users/{username}/generate_temp_pin")
def api_generate_temp_pin(username: str, payload: dict = Depends(require_admin)):
    """Admin-only: generate a temporary PIN for a user who has granted permission.

    Requires allow_admin_pin_reset=True in the user's profile.
    Returns the plaintext PIN once — never stored. User must change it on next login.
    """
    allowed = get_profile_flag(username, "allow_admin_pin_reset")
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="User has not granted permission for admin PIN recovery.",
        )
    user = store.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    temp_pin = store.generate_temp_pin(username)
    _audit(f"admin_temp_pin_generated: {username} by {payload.get('sub', '?')}")
    return {"temp_pin": temp_pin, "expires_minutes": store.ADMIN_TEMP_PIN_TTL_MINUTES}


@router.put("/users/{username}/pin")
def api_update_pin(username: str, req: UpdatePinRequest, payload: dict = Depends(require_auth)):
    # Admin kan bytte PIN for hvem som helst, bruker kun sin egen
    caller = payload["sub"]
    caller_role = payload["role"]
    if caller != username and caller_role != "admin":
        raise HTTPException(status_code=403, detail="You can only change your own PIN.")
    try:
        ok = store.update_pin(username, req.new_pin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")
    action = "admin_pin_reset" if caller != username else "pin_change"
    _audit("admin_user_action", caller, f"{action} target={username!r}")
    # Re-encrypt stored private key with new PIN (only when user changes their own PIN)
    if caller == username:
        try:
            pk = _session_keys.get_session_key_sync(username)
            if pk:
                reencrypt_private_key(username, req.new_pin, pk)
        except Exception:
            pass  # non-fatal — user will re-derive key correctly on next full login
    return {"ok": True}


@router.delete("/users/{username}")
def api_delete_user(username: str, payload: dict = Depends(require_admin)):
    try:
        ok = store.delete_user(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")
    _audit("admin_user_action", payload["sub"], f"delete_user username={username!r}")
    return {"ok": True}


# ── Voice enrollment (proxy → voice_bridge) ────────────────────────────────────


@router.get("/users/{username}/voice/status")
async def api_voice_status(username: str, _=Depends(require_admin)):
    """Check whether a voiceprint exists for a user."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_VOICE_BRIDGE}/speaker/status/{username}", headers=service_headers()
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="Voice bridge not available.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/users/{username}/voice/enroll")
async def api_voice_enroll(username: str, file: UploadFile = File(...), _=Depends(require_admin)):
    """Upload audio and create a voiceprint for the user."""
    if not store.get_user(username):
        raise HTTPException(status_code=404, detail="User not found.")
    audio_bytes = await file.read()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_VOICE_BRIDGE}/speaker/enroll/{username}",
                files={"file": (file.filename or "audio.wav", audio_bytes, "audio/wav")},
                headers=service_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="Voice bridge not available.") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/users/{username}/voice")
async def api_voice_delete(username: str, _=Depends(require_admin)):
    """Remove stored voiceprint for a user."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{_VOICE_BRIDGE}/speaker/enroll/{username}", headers=service_headers()
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="Voice bridge not available.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── User-settable profile flags ───────────────────────────────────────────────

_USER_SETTABLE_FLAGS = {"allow_admin_pin_reset"}


@router.get("/users/{username}/flags")
def api_get_user_flags(username: str, payload: dict = Depends(require_auth)):
    """Return user-settable profile flags. Self or admin only."""
    caller = payload["sub"]
    if caller != username and payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")
    return {flag: get_profile_flag(username, flag) for flag in _USER_SETTABLE_FLAGS}


@router.put("/users/{username}/flags")
def api_put_user_flags(username: str, body: dict, payload: dict = Depends(require_auth)):
    """Set user-settable profile flags. Self only — admin cannot override."""
    caller = payload["sub"]
    if caller != username:
        raise HTTPException(status_code=403, detail="Users can only set their own flags.")
    for key, val in body.items():
        if key not in _USER_SETTABLE_FLAGS:
            raise HTTPException(status_code=400, detail=f"Unknown flag: {key}")
        set_profile_flag(username, key, bool(val))
    return {flag: get_profile_flag(username, flag) for flag in _USER_SETTABLE_FLAGS}


# ── Parent child-summary access ───────────────────────────────────────────────


@router.get("/users/{username}/summary")
def api_user_summary(username: str, payload: dict = Depends(require_auth)):
    """Return basic display info for a user. Self, admin, or authenticated parent (has username in managed_children)."""

    caller = payload["sub"]
    caller_role = payload.get("role", "")
    if caller != username and caller_role != "admin":
        caller_user = store.get_user(caller)
        children_raw = caller_user.get("managed_children") if caller_user else None
        try:
            children: list = _json.loads(children_raw) if children_raw else []
        except Exception:
            children = []
        if username not in children:
            raise HTTPException(status_code=403, detail="Access denied.")
    user = store.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "username": user["username"],
        "display_name": user.get("display_name", user["username"]),
        "avatar": user.get("avatar", "👤"),
        "role": user.get("role", "child"),
        "pin_required": bool(user.get("pin_required", 0)),
    }


# ── Recovery & household-visible ──────────────────────────────────────────────


@router.get("/users/{username}/household-visible")
def api_household_visible(username: str, payload: dict = Depends(require_auth)):
    """Return household_visible profile section for a user. Admin or self only."""
    caller = payload["sub"]
    caller_role = payload.get("role", "")
    if caller != username and caller_role != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")
    user = store.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"username": username, "household_visible": get_household_visible(username)}


@router.get("/users/{username}/unlock")
def api_get_unlock(username: str, payload: dict = Depends(require_auth)):
    """Return voice unlock config for a user. Admin or self only."""
    caller = payload["sub"]
    caller_role = payload.get("role", "")
    if caller != username and caller_role != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")
    if not store.get_user(username):
        raise HTTPException(status_code=404, detail="User not found.")
    return {"username": username, **get_unlock_config(username)}


class UnlockConfigRequest(BaseModel):
    method: str
    phrase: str = ""
    pin: str = ""
    global_lists: bool = False


@router.put("/users/{username}/unlock")
def api_put_unlock(username: str, req: UnlockConfigRequest, payload: dict = Depends(require_auth)):
    """Update voice unlock config. Admin or self only. PIN length 4-12 if provided."""
    caller = payload["sub"]
    caller_role = payload.get("role", "")
    if caller != username and caller_role != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")
    if not store.get_user(username):
        raise HTTPException(status_code=404, detail="User not found.")
    if req.method not in ("phrase", "pin", "both", "none"):
        raise HTTPException(status_code=400, detail="method must be phrase|pin|both|none.")
    if req.pin and (len(req.pin) < 4 or len(req.pin) > 12):
        raise HTTPException(status_code=400, detail="PIN must be 4–12 characters.")
    set_unlock_config(username, req.method, req.phrase, req.pin, req.global_lists)
    return {"ok": True}


@router.post("/users/recover")
async def api_recover(req: RecoverRequest, request: Request):
    """Recover account using seed phrase. No auth required.
    Validates seed phrase against stored public key, re-encrypts private key
    with new PIN, updates PIN hash, and returns a JWT so the user is logged in.
    Shares the login rate limit (5 failures / 15 min per IP, P120): each try runs an
    Argon2 key derivation, so unlimited tries were also a cheap way to load the CPU."""
    client_ip = _client_ip_of(request)
    _check_login_rate(client_ip, req.username)
    user = store.get_user(req.username)
    if not user or store.is_system_identity(req.username):
        _record_login_fail(client_ip, req.username)
        raise HTTPException(status_code=400, detail="Invalid username or seed phrase.")
    kp = store.get_keypair_data(req.username)
    if not kp:
        raise HTTPException(status_code=400, detail="Account has no encryption keypair.")
    try:
        pub_bytes = base64.b64decode(kp["public_key"])
        salt_bytes = base64.b64decode(kp["argon2_salt"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Keypair data corrupted.") from exc

    private_key = seed_phrase_to_private_key(req.seed_phrase.strip().lower(), salt_bytes, pub_bytes)
    if not private_key:
        _record_login_fail(client_ip, req.username)
        raise HTTPException(status_code=400, detail="Invalid username or seed phrase.")
    _clear_login_fail(client_ip, req.username)

    ok, msg = store.validate_pin_strength(req.new_pin)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # Re-encrypt private key with new PIN + fresh salt
    new_salt = generate_salt()
    new_derived = derive_key_from_pin(req.new_pin, new_salt)
    new_encrypted_pk = encrypt_private_key(private_key, new_derived)
    store.store_keypair(
        req.username,
        kp["public_key"],
        new_encrypted_pk,
        base64.b64encode(new_salt).decode(),
    )
    store.update_pin(req.username, req.new_pin)

    expires_at = (datetime.now(UTC) + timedelta(hours=12)).timestamp()
    await unlock_session(req.username, req.new_pin, expires_at)

    token = create_token(req.username, user["role"])
    # Same user shape as /auth/login (secrets stripped by the store), so the GUI gets
    # is_parent / managed_children etc. right away after a recovery.
    return {"token": token, "user": store.get_user(req.username) or user}


@router.post("/users/{username}/reissue_seed_phrase")
def api_reissue_seed_phrase(username: str, payload: dict = Depends(require_auth)):
    """Recompute and return the caller's own recovery seed phrase, shown once.

    Self only — never admin-on-behalf-of, never the P87 system key (see P95 Decision 2).
    Exports a user's single most sensitive secret, so it must require the exact same live,
    PIN-derived session as the private key itself. Requires an active session (private key
    already decrypted to RAM by a real PIN login) — recomputes the phrase from that key via
    the fixed private_key_to_seed_phrase() (P95 Phase 1), it is not read from storage.
    """
    caller = payload["sub"]
    if caller != username:
        raise HTTPException(status_code=403, detail="You can only reissue your own recovery key.")
    private_key = _session_keys.get_session_key_sync(username)
    if not private_key:
        raise HTTPException(
            status_code=401, detail="Active session required — please log in again."
        )
    seed_phrase = private_key_to_seed_phrase(private_key)
    _audit("user_action", caller, "reissue_seed_phrase")
    return {"seed_phrase": seed_phrase}
