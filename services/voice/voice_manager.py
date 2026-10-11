"""
voice_manager.py – registers voice endpoints on the main Kåre API.

Adds:
  GET  /api/voice/status        – voice bridge status (port 8011), admin only

POST /api/voice/trigger/{node} and WS /ws/voice were removed (P120 Phase 3): no caller, and the
trigger let anyone on the LAN switch on a satellite's microphone.
"""

import logging
import sys

import httpx
from fastapi import Depends
from fastapi.responses import JSONResponse

sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc
from kaare_core.users.auth import require_admin as _require_admin

log = logging.getLogger("voice_manager")

VOICE_BRIDGE_URL = _svc("internal", "voice_bridge")


def register_voice_endpoints(app) -> None:
    """Called from kaare_api.py at startup."""

    @app.get("/api/voice/status")
    async def voice_status(_u=Depends(_require_admin)):
        """Is the voice bridge up?"""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{VOICE_BRIDGE_URL}/health")
                return {"voice_bridge": "ok", "detail": resp.json()}
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={"voice_bridge": "down", "error": str(e)},
            )

    log.info("Voice endpoint registered: /api/voice/status")
