"""REST API for proactive speech and external integrations.

Endpoints:
    POST /api/speak  - trigger TTS playback on satellites/Sonos
    GET  /api/satellites - list registered satellites
"""
from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger(__name__)


def create_app(pipeline, registry, sonos=None) -> web.Application:
    """Create the REST API application.

    Args:
        pipeline: ServerPipeline instance (for TTS).
        registry: SatelliteRegistry instance.
        sonos: SonosOutput instance (optional).
    """
    app = web.Application()
    app["pipeline"] = pipeline
    app["registry"] = registry
    app["sonos"] = sonos

    app.router.add_post("/api/speak", handle_speak)
    app.router.add_get("/api/satellites", handle_list_satellites)

    return app


async def handle_speak(request: web.Request) -> web.Response:
    """Handle proactive speech request.

    Body:
        text: Text to speak (required).
        room: Target room (optional - broadcasts to all if omitted).
        targets: ["satellite", "sonos"] (default: both).
        priority: "normal" or "urgent" (default: normal).
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    text = data.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)

    room = data.get("room")
    targets = data.get("targets", ["satellite", "sonos"])
    priority = data.get("priority", "normal")

    pipeline = request.app["pipeline"]
    registry = request.app["registry"]
    sonos = request.app["sonos"]

    results = {"text": text, "room": room, "delivered": []}

    # Generate TTS audio
    audio = pipeline._tts.synthesize(text)

    # Push to satellites
    if "satellite" in targets and audio:
        if room:
            sats = registry.get_by_room(room)
        else:
            sats = registry.get_all_online()

        for sat in sats:
            if registry.push_audio(sat.satellite_id, audio):
                results["delivered"].append(f"satellite:{sat.satellite_id}")

    # Push to Sonos
    if "sonos" in targets and sonos:
        if room:
            if sonos.play_tts(text, satellite_id=room):
                results["delivered"].append(f"sonos:{room}")
        else:
            count = sonos.broadcast(text)
            if count:
                results["delivered"].append(f"sonos:broadcast({count})")

    log.info("Proactive speak: %s -> %s", text[:60], results["delivered"])
    return web.json_response(results)


async def handle_list_satellites(request: web.Request) -> web.Response:
    """List all registered satellites."""
    registry = request.app["registry"]
    sats = [
        {
            "satellite_id": s.satellite_id,
            "room": s.room,
            "ip": s.ip,
            "http_port": s.http_port,
            "online": s.online,
        }
        for s in registry.get_all_online()
    ]
    return web.json_response({"satellites": sats})
