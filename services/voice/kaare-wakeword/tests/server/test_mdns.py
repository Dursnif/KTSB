"""Tests for mDNS discovery."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.mdns import KaareMDNSListener


class TestMDNSListener:
    def test_creates_listener(self):
        registry = MagicMock()
        listener = KaareMDNSListener(registry=registry)
        assert listener._registry is registry

    def test_service_added_registers_satellite(self):
        registry = MagicMock()
        listener = KaareMDNSListener(registry=registry)

        # Simulate service info
        mock_info = MagicMock()
        mock_info.parsed_addresses.return_value = ["192.168.87.199"]
        mock_info.port = 8080
        mock_info.properties = {
            b"satellite_id": b"rpi-stue",
            b"room": b"living_room",
        }

        # Test the handler logic directly
        listener._handle_service_found(mock_info)

        registry.register.assert_called_once_with(
            satellite_id="rpi-stue",
            room="living_room",
            ip="192.168.87.199",
            http_port=8080,
        )
