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
import time
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, Request, status, UploadFile, File
from pydantic import BaseModel
from typing import Optional

from kaare_core.audit import audit_log as _audit
from kaare_core.config import get_service
from kaare_core.crypto import (
    seed_phrase_to_private_key,
    generate_salt,
    derive_key_from_pin,
    encrypt_private_key,
)
from kaare_core.users import store
from kaare_core.users.auth import (
    login,
    require_auth,
    require_admin,
    is_user_online,
    get_all_last_seen,
    setup_keypair,
    unlock_session,
    end_session,
    create_token,
)
from kaare_core.users.profile_manager import (
    init_profile, get_profile_flag, set_profile_flag, get_household_visible,
    update_household_visible, get_unlock_config, set_unlock_config,
)
from adapters.llm_adapter import list_personalities

# Simple in-memory rate limiter for login: max 5 failures per 15 min per IP.
_LOGIN_FAIL_WINDOW = 900  # seconds
_LOGIN_FAIL_MAX = 5
_login_failures: dict[str, collections.deque] = {}


def _check_login_rate(ip: str) -> None:
    now = time.monotonic()
    dq = _login_failures.setdefault(ip, collections.deque())
    while dq and now - dq[0] > _LOGIN_FAIL_WINDOW:
        dq.popleft()
    if len(dq) >= _LOGIN_FAIL_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again in 15 minutes.",
        )


def _record_login_fail(ip: str) -> None:
    _login_failures.setdefault(ip, collections.deque()).append(time.monotonic())


def _clear_login_fail(ip: str) -> None:
    _login_failures.pop(ip, None)

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
    display_name: Optional[str] = None
    role: Optional[str] = None
    avatar: Optional[str] = None
    is_active: Optional[bool] = None
    personality: Optional[str] = None
    vpn_access: Optional[str] = None
    can_manage_child_timers: Optional[bool] = None
    notify_channel: Optional[str] = None
    is_parent: Optional[bool] = None
    pin_required: Optional[bool] = None
    managed_children: Optional[str] = None

class UpdatePinRequest(BaseModel):
    new_pin: str


class RecoverRequest(BaseModel):
    username: str
    seed_phrase: str
    new_pin: str


# ── Personligheter ────────────────────────────────────────────────────────────

@router.get("/personalities")
def api_list_personalities():
    """Returnerer tilgjengelige personlighetsvarianter."""
    return list_personalities()


# ── Auth ───────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def api_login(req: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_login_rate(client_ip)
    # Sjekk om midlertidig PIN har utløpt før vi prøver å logge inn
    if store.check_pin_expired(req.username):
        _record_login_fail(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Temporary PIN has expired. Ask an administrator for a new one.",
        )
    result = login(req.username, req.pin)
    if not result:
        _record_login_fail(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Feil brukernavn eller PIN.")
    _clear_login_fail(client_ip)
    # Unlock session key (decrypt private key to RAM) + process vault files
    pin_for_session = result.pop("_pin_for_session", None)
    expires_at = result.pop("_expires_at", None)
    if pin_for_session and expires_at:
        await unlock_session(req.username, pin_for_session, expires_at)
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
    users = store.list_users()
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
        _audit("admin_user_action", payload["sub"], f"create_user username={req.username!r} role={req.role!r}")
        # Generate keypair for personal accounts (not system accounts like "admin")
        seed_phrase = setup_keypair(req.username, req.pin)
        if seed_phrase:
            user["seed_phrase"] = seed_phrase  # shown once — frontend must display and hide
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Username already taken.")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/users/{username}")
def api_update_user(username: str, req: UpdateUserRequest,
                    payload: dict = Depends(require_admin)):
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
        raise HTTPException(status_code=400, detail=str(e))
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
def api_update_pin(username: str, req: UpdatePinRequest,
                   payload: dict = Depends(require_auth)):
    # Admin kan bytte PIN for hvem som helst, bruker kun sin egen
    caller = payload["sub"]
    caller_role = payload["role"]
    if caller != username and caller_role != "admin":
        raise HTTPException(status_code=403, detail="You can only change your own PIN.")
    try:
        ok = store.update_pin(username, req.new_pin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")
    action = "admin_pin_reset" if caller != username else "pin_change"
    _audit("admin_user_action", caller, f"{action} target={username!r}")
    # Re-encrypt stored private key with new PIN (only when user changes their own PIN)
    if caller == username:
        try:
            from kaare_core import session_keys as _session_keys
            from kaare_core.users.store import reencrypt_private_key
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
        raise HTTPException(status_code=400, detail=str(e))
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
            resp = await client.get(f"{_VOICE_BRIDGE}/speaker/status/{username}")
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Voice bridge not available.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Voice bridge not available.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/users/{username}/voice")
async def api_voice_delete(username: str, _=Depends(require_admin)):
    """Remove stored voiceprint for a user."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"{_VOICE_BRIDGE}/speaker/enroll/{username}")
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Voice bridge not available.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
    import json as _json
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
async def api_recover(req: RecoverRequest):
    """Recover account using seed phrase. No auth required.
    Validates seed phrase against stored public key, re-encrypts private key
    with new PIN, updates PIN hash, and returns a JWT so the user is logged in."""
    user = store.get_user(req.username)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid username or seed phrase.")
    kp = store.get_keypair_data(req.username)
    if not kp:
        raise HTTPException(status_code=400, detail="Account has no encryption keypair.")
    try:
        pub_bytes = base64.b64decode(kp["public_key"])
        salt_bytes = base64.b64decode(kp["argon2_salt"])
    except Exception:
        raise HTTPException(status_code=500, detail="Keypair data corrupted.")

    private_key = seed_phrase_to_private_key(req.seed_phrase.strip().lower(), salt_bytes, pub_bytes)
    if not private_key:
        raise HTTPException(status_code=400, detail="Invalid username or seed phrase.")

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

    expires_at = (datetime.now(timezone.utc) + timedelta(hours=12)).timestamp()
    await unlock_session(req.username, req.new_pin, expires_at)

    token = create_token(req.username, user["role"])
    return {"token": token, "user": {"username": req.username, "role": user["role"],
                                     "display_name": user.get("display_name", req.username),
                                     "avatar": user.get("avatar", "👤")}}
