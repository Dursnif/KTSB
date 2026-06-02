"""Tests for satellite registry."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.registry import SatelliteRegistry, SatelliteInfo


class TestRegistry:
    def test_register_satellite(self):
        reg = SatelliteRegistry()
        reg.register("rpi-stue", room="living_room", ip="192.168.87.199", http_port=8080)
        info = reg.get("rpi-stue")
        assert info is not None
        assert info.room == "living_room"
        assert info.ip == "192.168.87.199"
        assert info.online is True

    def test_unregister_satellite(self):
        reg = SatelliteRegistry()
        reg.register("rpi-stue", room="living_room", ip="192.168.87.199", http_port=8080)
        reg.unregister("rpi-stue")
        assert reg.get("rpi-stue") is None

    def test_get_by_room(self):
        reg = SatelliteRegistry()
        reg.register("rpi-stue", room="living_room", ip="192.168.87.199", http_port=8080)
        reg.register("rpi-kjokken", room="kitchen", ip="192.168.87.200", http_port=8080)
        sats = reg.get_by_room("living_room")
        assert len(sats) == 1
        assert sats[0].satellite_id == "rpi-stue"

    def test_get_all_online(self):
        reg = SatelliteRegistry()
        reg.register("sat-1", room="room1", ip="10.0.0.1", http_port=8080)
        reg.register("sat-2", room="room2", ip="10.0.0.2", http_port=8080)
        assert len(reg.get_all_online()) == 2

    @patch("server.registry.requests.post")
    def test_push_audio(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        reg = SatelliteRegistry()
        reg.register("sat-1", room="room1", ip="10.0.0.1", http_port=8080)
        result = reg.push_audio("sat-1", b"fake-wav-data")
        assert result is True
        mock_post.assert_called_once()

    @patch("server.registry.requests.post")
    def test_broadcast_audio(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        reg = SatelliteRegistry()
        reg.register("sat-1", room="room1", ip="10.0.0.1", http_port=8080)
        reg.register("sat-2", room="room2", ip="10.0.0.2", http_port=8080)
        count = reg.broadcast_audio(b"fake-wav-data")
        assert count == 2

    def test_push_to_unknown_satellite_fails(self):
        reg = SatelliteRegistry()
        result = reg.push_audio("nonexistent", b"data")
        assert result is False
