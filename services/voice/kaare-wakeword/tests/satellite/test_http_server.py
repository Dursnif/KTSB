"""Tests for satellite HTTP push API."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from satellite.http_server import SatelliteHTTPServer


@pytest.fixture
def mock_play_callback():
    return AsyncMock()


class TestHTTPServer:
    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        server = SatelliteHTTPServer(port=0, satellite_id="test-sat", room="living_room")
        await server.start()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{server.port}/status") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["satellite_id"] == "test-sat"
                    assert data["room"] == "living_room"
                    assert "state" in data
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_play_endpoint_accepts_wav(self, mock_play_callback):
        server = SatelliteHTTPServer(
            port=0, satellite_id="test-sat", room="test",
            on_play=mock_play_callback,
        )
        await server.start()
        try:
            import aiohttp
            wav_data = b"RIFF" + b"\x00" * 100  # minimal WAV-like payload
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{server.port}/play",
                    data=wav_data,
                    headers={"Content-Type": "audio/wav"},
                ) as resp:
                    assert resp.status == 200
            mock_play_callback.assert_called_once()
        finally:
            await server.stop()
