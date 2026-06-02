"""Tests for proactive speech REST API."""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from server.api import create_app


class TestSpeakEndpoint:
    @pytest.mark.asyncio
    async def test_speak_returns_200(self):
        mock_pipeline = MagicMock()
        mock_pipeline._tts.synthesize.return_value = b"fake-audio"
        mock_registry = MagicMock()
        mock_registry.get_by_room.return_value = []
        mock_sonos = MagicMock()

        app = create_app(
            pipeline=mock_pipeline,
            registry=mock_registry,
            sonos=mock_sonos,
        )
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/speak", json={
                "text": "God morgen!",
                "room": "living_room",
            })
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_speak_requires_text(self):
        app = create_app(pipeline=MagicMock(), registry=MagicMock(), sonos=None)
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/speak", json={"room": "living_room"})
            assert resp.status == 400
