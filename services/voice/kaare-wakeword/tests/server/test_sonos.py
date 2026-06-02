"""Tests for Sonos output via HA scripts."""
from __future__ import annotations

from datetime import time
from unittest.mock import patch, MagicMock

import pytest

from server.sonos import SonosOutput, load_sonos_config


class TestQuietHours:
    def test_not_quiet_during_day(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="test")
        cfg = {"entity_id": "media_player.kids", "quiet_after": "21:00", "quiet_before": "07:00"}
        with patch("server.sonos.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(14, 0)
            assert sonos._is_quiet_hour(cfg) is False

    def test_quiet_after_bedtime(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="test")
        cfg = {"entity_id": "media_player.kids", "quiet_after": "21:00", "quiet_before": "07:00"}
        with patch("server.sonos.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(22, 0)
            assert sonos._is_quiet_hour(cfg) is True

    def test_no_quiet_config_never_quiet(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="test")
        cfg = {"entity_id": "media_player.living_room"}
        assert sonos._is_quiet_hour(cfg) is False


class TestRoomMapping:
    def test_satellite_maps_to_room(self):
        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            satellites={"rpi-stue": "living_room"},
        )
        assert sonos._get_room("rpi-stue") == "living_room"

    def test_unknown_satellite_uses_default(self):
        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            satellites={"default": "living_room"},
        )
        assert sonos._get_room("unknown-sat") == "living_room"


class TestPlayTTS:
    @patch("server.sonos.requests.post")
    def test_play_calls_ha_script(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test-token",
            speakers={"living_room": {"entity_id": "media_player.living_room"}},
            satellites={"default": "living_room"},
        )
        result = sonos.play_tts("God morgen!", satellite_id="default")
        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "script/turn_on" in call_args[0][0] or "script" in str(call_args)

    @patch("server.sonos.requests.post")
    def test_play_skips_quiet_room(self, mock_post):
        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            speakers={"kids_bedroom": {
                "entity_id": "media_player.kids_bedroom",
                "quiet_after": "21:00", "quiet_before": "07:00",
            }},
            satellites={"rpi-barnerom": "kids_bedroom"},
        )
        with patch("server.sonos.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(22, 0)
            result = sonos.play_tts("Sov godt!", satellite_id="rpi-barnerom")
        assert result is False
        mock_post.assert_not_called()

    def test_play_fails_without_token(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="")
        result = sonos.play_tts("Test")
        assert result is False


class TestBroadcast:
    @patch("server.sonos.requests.post")
    def test_broadcast_hits_all_speakers(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            speakers={
                "living_room": {"entity_id": "media_player.living_room"},
                "garage": {"entity_id": "media_player.garage"},
            },
        )
        count = sonos.broadcast("Det er middag!")
        assert count >= 1  # At least the broadcast script called
