"""HTTP server for receiving audio push from Kaare server.

Endpoints:
    POST /play    - receive WAV audio, play immediately
    POST /volume  - adjust playback volume
    GET  /status  - return satellite state
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

import numpy as np
from aiohttp import web

log = logging.getLogger(__name__)


class SatelliteHTTPServer:
    """HTTP server for satellite audio push.

    Args:
        port: Port to listen on (0 = auto-assign).
        satellite_id: This satellite's identifier.
        room: Room this satellite is in.
        on_play: Async callback when audio is received.
        volume: Initial playback volume (0.0-1.0).
    """

    def __init__(
        self,
        port: int = 8080,
        satellite_id: str = "unknown",
        room: str = "unknown",
        on_play: Callable[[bytes, int], Awaitable[None]] | None = None,
        volume: float = 1.0,
    ):
        self._port = port
        self._satellite_id = satellite_id
        self._room = room
        self._on_play = on_play
        self._volume = volume
        self._state = "idle"
        self._app = web.Application()
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_post("/play", self._handle_play)
        self._app.router.add_post("/volume", self._handle_volume)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def port(self) -> int:
        """Actual port (useful when port=0)."""
        if self._site and self._site._server:
            for sock in self._site._server.sockets:
                return sock.getsockname()[1]
        return self._port

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str):
        self._state = value

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "satellite_id": self._satellite_id,
            "room": self._room,
            "state": self._state,
            "volume": self._volume,
        })

    async def _handle_play(self, request: web.Request) -> web.Response:
        wav_data = await request.read()
        sample_rate = int(request.headers.get("X-Sample-Rate", "22050"))
        log.info("Received %d bytes audio (rate=%d)", len(wav_data), sample_rate)

        if self._on_play:
            await self._on_play(wav_data, sample_rate)
        else:
            await self._default_play(wav_data, sample_rate)

        return web.json_response({"status": "ok"})

    async def _handle_volume(self, request: web.Request) -> web.Response:
        data = await request.json()
        self._volume = max(0.0, min(1.0, float(data.get("volume", self._volume))))
        return web.json_response({"volume": self._volume})

    async def _default_play(self, wav_data: bytes, sample_rate: int) -> None:
        """Play audio via sounddevice (default handler)."""
        try:
            import sounddevice as sd
            audio_i16 = np.frombuffer(wav_data, dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32768.0
            audio_f32 *= self._volume
            sd.play(audio_f32, samplerate=sample_rate, blocking=False)
        except Exception as exc:
            log.warning("Failed to play pushed audio: %s", exc)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await self._site.start()
        log.info("Satellite HTTP server started on port %d", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
