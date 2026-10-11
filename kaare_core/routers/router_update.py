"""In-app updates (P57) — the API side of «Oppdater KTSB» on the Maintenance page.

The work itself happens in the `kaare-updater` container (services/updater/updater.py), which can replace every
other container, this one included. This router only: answers the updater's health probe, relays admin requests
over the updater's Unix socket, and — before an update starts, while this API is certainly alive — takes the
config snapshot and the pre-update save point.

Docker installs only. A bare-metal install (and the future OS edition, P57 decision 6) reports `docker: false`.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kaare_core import config_store
from kaare_core.audit import audit_log
from kaare_core.routers.router_backup import create_pre_update_point
from kaare_core.service_auth import get_service_token, is_service_request
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.version import get_version
from kaare_night_runner import is_sequence_running

log = logging.getLogger(__name__)
router = APIRouter()

_IN_DOCKER = os.path.exists("/.dockerenv")
_SOCK = Path("/kaare/state/updater/updater.sock")
_MEETING_LOCK = Path("/kaare/state/meeting_active.lock")
_SOCK_TIMEOUT_S = 60  # `check` talks to GitHub


class UpdateRequest(BaseModel):
    version: str


class CheckRequest(BaseModel):
    force: bool = False


async def _ask_updater(cmd: str, **fields) -> dict:
    """One JSON line out, one back. Raises HTTPException(503) when the updater is not there."""
    if not _IN_DOCKER:
        raise HTTPException(status_code=409, detail="not_docker")
    if not _SOCK.exists():
        raise HTTPException(status_code=503, detail="updater_unreachable")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(_SOCK)), timeout=5)
        req = {"cmd": cmd, "token": get_service_token(), **fields}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=_SOCK_TIMEOUT_S)
        writer.close()
        return json.loads(line or b"{}")
    except (OSError, TimeoutError, ValueError) as e:
        log.warning("[UPDATE] updater not reachable: %s", e)
        raise HTTPException(status_code=503, detail="updater_unreachable") from e


def _require_service(request: Request) -> None:
    """Only KTSB's own services (here: the updater's health probe) — same lock as the HA gateway (P120)."""
    if not is_service_request(request.headers):
        raise HTTPException(status_code=401, detail="Service token required.")


@router.get("/api/update/alive")
async def api_update_alive(_s: None = Depends(_require_service)):
    """Health probe for the updater: which release this API runs. Service token only."""
    return {"version": get_version()}


@router.get("/api/update/status")
async def api_update_status(_u=Depends(_require_admin)):
    base = {"docker": _IN_DOCKER, "running_version": get_version()}
    if not _IN_DOCKER:
        return {**base, "reachable": False}
    try:
        st = await _ask_updater("status")
    except HTTPException:
        return {**base, "reachable": False}
    return {**base, "reachable": bool(st.get("ok")), **{k: v for k, v in st.items() if k != "ok"}}


@router.post("/api/update/check")
async def api_update_check(body: CheckRequest, _u=Depends(_require_admin)):
    return await _ask_updater("check", force=body.force)


@router.post("/api/update/start")
async def api_update_start(body: UpdateRequest, request: Request, user: dict = Depends(_require_admin)):
    # Fast refusals here; the updater checks everything again (it does not trust this side).
    # night_sequence.lock stays on disk between runs — only a held lock counts (is_sequence_running).
    if is_sequence_running() or _MEETING_LOCK.exists():
        raise HTTPException(status_code=409, detail="night_sequence_running")
    st = await _ask_updater("status")
    if st.get("problem"):
        raise HTTPException(status_code=409, detail=st["problem"])
    if st.get("busy"):
        raise HTTPException(status_code=409, detail="busy")
    username = user.get("sub") or user.get("username", "")  # JWT carries the user in "sub"
    try:
        await asyncio.to_thread(config_store.snapshot, f"pre-update-{body.version}")
        point_id = await create_pre_update_point(body.version)
    except Exception as e:
        log.error("[UPDATE] pre-update save point failed: %s", e)
        raise HTTPException(status_code=500, detail="save_point_failed") from e
    audit_log("update_requested", username, f"to={body.version} save_point={point_id}",
              request.client.host if request.client else "")  # fmt: skip
    resp = await _ask_updater("update", version=body.version)
    if not resp.get("ok"):
        raise HTTPException(status_code=409, detail=resp.get("error", "refused"))
    return {**resp, "save_point": point_id}


@router.post("/api/update/rollback")
async def api_update_rollback(request: Request, user: dict = Depends(_require_admin)):
    audit_log("rollback_requested", user.get("sub") or user.get("username", ""), "admin",
              request.client.host if request.client else "")  # fmt: skip
    resp = await _ask_updater("rollback")
    if not resp.get("ok"):
        raise HTTPException(status_code=409, detail=resp.get("error", "refused"))
    return resp
