"""
voice_manager.py – registrerer voice-endepunkter på hoved-Kåre API.

Legger til:
  GET  /api/voice/status        – status for voice bridge (port 8011)
  POST /api/voice/trigger/{id}  – trigger voice pipeline på en node
  WS   /ws/voice                – WebSocket for GUI/klienter (sanntidseventer)
"""

import asyncio
import logging
from typing import Set

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import sys
sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc

log = logging.getLogger("voice_manager")

VOICE_BRIDGE_URL = _svc("internal", "voice_bridge")

# --- WebSocket-tilkoblinger (GUI etc.) ---
_ws_clients: Set[WebSocket] = set()


async def _broadcast(message: str) -> None:
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


def register_voice_endpoints(app) -> None:
    """Kalles fra kaare_api.py ved oppstart."""

    @app.get("/api/voice/status")
    async def voice_status():
        """Sjekk om voice bridge er oppe."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{VOICE_BRIDGE_URL}/health")
                return {"voice_bridge": "ok", "detail": resp.json()}
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={"voice_bridge": "nede", "error": str(e)},
            )

    @app.post("/api/voice/trigger/{node_id}")
    async def voice_trigger(node_id: str):
        """Trigger voice pipeline på en node via voice bridge."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{VOICE_BRIDGE_URL}/button/{node_id}")
                return resp.json()
        except Exception as e:
            log.error("Feil ved trigger av node %s: %s", node_id, e)
            return JSONResponse(
                status_code=502,
                content={"error": str(e)},
            )

    @app.websocket("/ws/voice")
    async def voice_ws(websocket: WebSocket):
        """WebSocket for GUI og klienter – mottar voice-eventer."""
        await websocket.accept()
        _ws_clients.add(websocket)
        log.info("Voice WebSocket-klient tilkoblet. Totalt: %d", len(_ws_clients))
        try:
            while True:
                # Hold tilkoblingen åpen; klienten kan sende ping
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(websocket)
            log.info("Voice WebSocket-klient frakoblet. Totalt: %d", len(_ws_clients))

    log.info("Voice-endepunkter registrert: /api/voice/status, /api/voice/trigger, /ws/voice")
