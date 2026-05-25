"""Tests for WebSocket server."""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
import websockets

from server.server import VoiceServer
from server.config import ServerConfig


@pytest_asyncio.fixture
async def server():
    """Start a test server on an ephemeral port."""
    config = ServerConfig(host="127.0.0.1", port=0)
    srv = VoiceServer(config)
    task = asyncio.create_task(srv.start())
    # Wait for server to bind
    await asyncio.sleep(0.2)
    yield srv
    srv.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestVoiceServer:
    @pytest.mark.asyncio
    async def test_server_accepts_connection(self, server):
        """Server should accept a WebSocket connection."""
        url = f"ws://{server.config.host}:{server.port}"
        # If the connection is established without exception, the server is accepting
        async with websockets.connect(url) as ws:
            # websockets 14+ uses .state; being inside the context manager means connected
            assert ws is not None

    @pytest.mark.asyncio
    async def test_audio_start_acknowledged(self, server):
        """Server should log audio_start without crashing."""
        url = f"ws://{server.config.host}:{server.port}"
        async with websockets.connect(url) as ws:
            msg = json.dumps({
                "type": "audio_start",
                "satellite_id": "test",
                "sample_rate": 16000,
                "channels": 1,
                "format": "float32",
                "pre_roll_ms": 500,
            })
            await ws.send(msg)
            # Server shouldn't close the connection; no exception means connection alive
            await asyncio.sleep(0.1)
            # Verify connection still alive by checking state (websockets 14+)
            from websockets.protocol import State
            assert ws.state == State.OPEN


class TestRegistration:
    def test_register_message_adds_to_registry(self):
        from server.registry import SatelliteRegistry
        registry = SatelliteRegistry()
        msg = {
            "type": "register",
            "satellite_id": "rpi-stue",
            "room": "living_room",
            "http_port": 8080,
        }
        registry.register(
            satellite_id=msg["satellite_id"],
            room=msg["room"],
            ip="192.168.87.199",
            http_port=msg["http_port"],
        )
        assert registry.get("rpi-stue") is not None
        assert registry.get("rpi-stue").room == "living_room"
