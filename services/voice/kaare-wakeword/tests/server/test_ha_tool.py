"""Tests for Home Assistant API tool."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.tools.ha_tool import HomeAssistantTool


class TestHomeAssistantTool:
    def test_get_state(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "entity_id": "sensor.temperature_living_room",
            "state": "21.5",
            "attributes": {"unit_of_measurement": "\u00b0C", "friendly_name": "Stuetemperatur"},
        }
        with patch("server.tools.ha_tool.requests.request", return_value=mock_resp):
            result = tool.handle({
                "method": "GET",
                "path": "/api/states/sensor.temperature_living_room",
            })
            assert "21.5" in result

    def test_no_token_returns_error(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="")
        result = tool.handle({"method": "GET", "path": "/api/states/light.kitchen"})
        assert "ikke konfigurert" in result.lower()

    def test_call_service(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{}]
        with patch("server.tools.ha_tool.requests.request", return_value=mock_resp):
            result = tool.handle({
                "method": "POST",
                "path": "/api/services/light/turn_on",
                "body": {"entity_id": "light.kitchen"},
            })
            assert result

    def test_request_failure(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        import requests as req
        with patch("server.tools.ha_tool.requests.request", side_effect=req.ConnectionError):
            result = tool.handle({"method": "GET", "path": "/api/states/light.kitchen"})
            assert "feil" in result.lower()


class TestEntitySearch:
    """Tests for ha_list entity search with Norwegian->English translation."""

    def _make_tool_with_entities(self, entities: list[dict]) -> HomeAssistantTool:
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        tool._entity_cache = entities
        return tool

    def _sample_entities(self) -> list[dict]:
        return [
            {"entity_id": "light.light_livingroom_dinner_table1", "name": "light_livingroom_dinner_table_1", "state": "off", "domain": "light"},
            {"entity_id": "light.light_livingroom_dinner_table2", "name": "light_livingroom_dinner_table2", "state": "off", "domain": "light"},
            {"entity_id": "light.light_bedroom_ceiling", "name": "Bedroom Ceiling", "state": "on", "domain": "light"},
            {"entity_id": "light.light_kitchen_counter", "name": "Kitchen Counter", "state": "off", "domain": "light"},
            {"entity_id": "light.conecto_smart_led", "name": "Lys Hovedtrappen Ute", "state": "unavailable", "domain": "light"},
            {"entity_id": "sensor.temperature_living_room", "name": "Stuetemperatur", "state": "21.5", "domain": "sensor"},
        ]

    def test_norwegian_stue_finds_livingroom(self):
        tool = self._make_tool_with_entities(self._sample_entities())
        result = tool.handle_list({"query": "stue lys", "domain": "light"})
        assert "light_livingroom_dinner_table" in result

    def test_norwegian_kjokken_finds_kitchen(self):
        tool = self._make_tool_with_entities(self._sample_entities())
        result = tool.handle_list({"query": "kjøkken", "domain": "light"})
        assert "kitchen_counter" in result

    def test_norwegian_soverom_finds_bedroom(self):
        tool = self._make_tool_with_entities(self._sample_entities())
        result = tool.handle_list({"query": "soverom", "domain": "light"})
        assert "bedroom_ceiling" in result

    def test_english_query_still_works(self):
        tool = self._make_tool_with_entities(self._sample_entities())
        result = tool.handle_list({"query": "dinner table", "domain": "light"})
        assert "dinner_table1" in result
        assert "dinner_table2" in result

    def test_stue_ranks_livingroom_above_ute(self):
        """'stue lys' should rank livingroom entities above 'Lys Hovedtrappen Ute'."""
        tool = self._make_tool_with_entities(self._sample_entities())
        result = tool.handle_list({"query": "stue lys", "domain": "light"})
        lines = result.strip().split("\n")
        # Livingroom entities should come first (more hits: living + light)
        assert "livingroom" in lines[0]

    def test_expand_words(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        expanded = tool._expand_words(["stue", "bord"])
        assert "livingroom" in expanded
        assert "living_room" in expanded
        assert "table" in expanded
        assert "stue" in expanded  # Original preserved

    def test_no_token_returns_error(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="")
        result = tool.handle_list({"query": "stue"})
        assert "ikke konfigurert" in result.lower()
