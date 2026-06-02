"""Tests for Home Assistant API client.

All tests mock the HTTP layer -- no network calls are made.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.ha import HomeAssistantClient


class TestHomeAssistantClient:
    def test_init_with_url_and_token(self):
        client = HomeAssistantClient(
            url="http://ha.local:8123",
            token="test-token",
        )
        assert client.url == "http://ha.local:8123"

    def test_headers_include_bearer_token(self):
        client = HomeAssistantClient(
            url="http://ha.local:8123",
            token="my-token",
        )
        headers = client._headers()
        assert headers["Authorization"] == "Bearer my-token"
        assert "application/json" in headers["Content-Type"]

    @patch("server.ha.requests.get")
    def test_list_entities_returns_entity_ids(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "switch.tv", "state": "off"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = HomeAssistantClient(url="http://ha.local:8123", token="t")
        entities = client.list_entities()
        assert "light.kitchen" in entities
        assert "switch.tv" in entities

    @patch("server.ha.requests.post")
    def test_call_service(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        client = HomeAssistantClient(url="http://ha.local:8123", token="t")
        client.call_service("light", "turn_on", "light.kitchen")
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "light/turn_on" in call_url
