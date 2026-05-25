"""
Bruker- og auth-endepunkter for Kåre.

POST /api/auth/login        → logg inn med PIN, få token
GET  /api/auth/me           → hvem er jeg? (krever token)
GET  /api/users             → liste alle brukere (admin)
POST /api/users             → opprett bruker (admin)
PUT  /api/users/{username}  → oppdater bruker (admin)
PUT  /api/users/{username}/pin → bytt PIN (admin eller seg selv)
DELETE /api/users/{username} → slett bruker (admin)
"""

import httpx
from fastapi import APIRouter, HTTPException, Depends, status, UploadFile, File
from pydantic import BaseModel
from typing import Optional

from kaare_core.users import store
from kaare_core.users.profile_manager import init_profile
from kaare_core.users.auth import (
    login,
    require_auth,
    require_admin,
    is_user_online,
    get_all_last_seen,
    setup_keypair,
    unlock_session,
    end_session,
)
from kaare_core.config import get_service
from adapters.llm_adapter import list_personalities

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

class UpdatePinRequest(BaseModel):
    new_pin: str


# ── Personligheter ────────────────────────────────────────────────────────────

@router.get("/personalities")
def api_list_personalities():
    """Returnerer tilgjengelige personlighetsvarianter."""
    return list_personalities()


# ── Auth ───────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def api_login(req: LoginRequest):
    # Sjekk om midlertidig PIN har utløpt før vi prøver å logge inn
    if store.check_pin_expired(req.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Den midlertidige PIN-koden har utløpt. Be en administrator om ny.",
        )
    result = login(req.username, req.pin)
    if not result:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Feil brukernavn eller PIN.")
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
        raise HTTPException(status_code=404, detail="Bruker ikke funnet.")
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
        # Generate keypair for personal accounts (not system accounts like "admin")
        seed_phrase = setup_keypair(req.username, req.pin)
        if seed_phrase:
            user["seed_phrase"] = seed_phrase  # shown once — frontend must display and hide
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Brukernavnet er allerede tatt.")
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="Bruker ikke funnet.")
    return user


@router.put("/users/{username}/pin")
def api_update_pin(username: str, req: UpdatePinRequest,
                   payload: dict = Depends(require_auth)):
    # Admin kan bytte PIN for hvem som helst, bruker kun sin egen
    caller = payload["sub"]
    caller_role = payload["role"]
    if caller != username and caller_role != "admin":
        raise HTTPException(status_code=403, detail="Du kan bare bytte din egen PIN.")
    try:
        ok = store.update_pin(username, req.new_pin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Bruker ikke funnet.")
    return {"ok": True}


@router.delete("/users/{username}")
def api_delete_user(username: str, payload: dict = Depends(require_admin)):
    try:
        ok = store.delete_user(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Bruker ikke funnet.")
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
        raise HTTPException(status_code=503, detail="Voice bridge er ikke tilgjengelig.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/users/{username}/voice/enroll")
async def api_voice_enroll(username: str, file: UploadFile = File(...), _=Depends(require_admin)):
    """Upload audio and create a voiceprint for the user."""
    if not store.get_user(username):
        raise HTTPException(status_code=404, detail="Bruker ikke funnet.")
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
        raise HTTPException(status_code=503, detail="Voice bridge er ikke tilgjengelig.")
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
        raise HTTPException(status_code=503, detail="Voice bridge er ikke tilgjengelig.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
