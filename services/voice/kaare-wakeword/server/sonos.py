"""Sonos TTS output via Home Assistant scripts.

Routes TTS text to Sonos speakers via HA's script system.
Room-based routing with quiet hours support.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def load_sonos_config(config_file: str) -> tuple[dict, dict]:
    """Load speakers and satellites config from JSON file."""
    path = Path(config_file)
    if not path.exists():
        log.warning("Sonos config not found: %s", path)
        return {}, {}
    data = json.loads(path.read_text())
    return data.get("speakers", {}), data.get("satellites", {})


class SonosOutput:
    """Manages Sonos TTS playback via Home Assistant scripts.

    Sends text to HA scripts which handle TTS + Sonos playback.
    No HTTP file server needed — works across networks.

    Args:
        ha_url: Home Assistant base URL.
        ha_token: Long-lived access token.
        speakers: Room -> speaker config mapping.
        satellites: satellite_id -> room mapping.
        volume: Playback volume (0.0-1.0).
        tts_script: HA script entity for single-speaker TTS.
        broadcast_script: HA script entity for broadcast.
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        speakers: dict | None = None,
        satellites: dict | None = None,
        volume: float = 0.4,
        tts_script: str = "script.sonos_tts_norwegian_speak",
        broadcast_script: str = "script.sonos_broadcast",
    ):
        self._ha_url = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._volume = volume
        self._tts_script = tts_script
        self._broadcast_script = broadcast_script

        self._speakers = speakers or {
            "living_room": {"entity_id": "media_player.living_room"},
            "garage": {"entity_id": "media_player.garage"},
            "basement": {"entity_id": "media_player.basement"},
            "kids_bedroom": {
                "entity_id": "media_player.kids_bedroom",
                "quiet_after": "21:00",
                "quiet_before": "07:00",
            },
        }
        self._satellites = satellites or {"default": "living_room"}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }

    def _is_quiet_hour(self, speaker_cfg: dict) -> bool:
        """Check if current time falls in speaker's quiet hours."""
        quiet_after = speaker_cfg.get("quiet_after")
        quiet_before = speaker_cfg.get("quiet_before")
        if not quiet_after:
            return False

        now = datetime.now().time()
        after = time.fromisoformat(quiet_after)
        before = time.fromisoformat(quiet_before) if quiet_before else time(7, 0)

        if after > before:
            return now >= after or now < before
        return after <= now < before

    def _get_room(self, satellite_id: str) -> str:
        """Map satellite_id to room name."""
        return self._satellites.get(
            satellite_id, self._satellites.get("default", "living_room")
        )

    def _get_active_speaker(self, room: str) -> str | None:
        """Get the speaker entity_id for a room, respecting quiet hours."""
        cfg = self._speakers.get(room)
        if not cfg:
            log.warning("No speaker configured for room '%s'", room)
            return None
        if self._is_quiet_hour(cfg):
            log.info("Speaker %s is in quiet hours, skipping", cfg["entity_id"])
            return None
        return cfg["entity_id"]

    def play_tts(self, text: str, satellite_id: str = "default") -> bool:
        """Play TTS text on the Sonos speaker in the satellite's room.

        Args:
            text: Text to speak.
            satellite_id: Which satellite triggered this.

        Returns:
            True if playback was triggered successfully.
        """
        if not self._ha_token:
            return False

        room = self._get_room(satellite_id)
        entity_id = self._get_active_speaker(room)
        if not entity_id:
            return False

        try:
            resp = requests.post(
                f"{self._ha_url}/api/services/script/turn_on",
                headers=self._headers(),
                json={
                    "entity_id": self._tts_script,
                    "variables": {
                        "target_player": entity_id,
                        "message": text,
                        "volume": self._volume,
                    },
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Sonos TTS triggered: %s -> %s", entity_id, text[:60])
            return True
        except requests.RequestException as exc:
            log.warning("Sonos TTS failed for %s: %s", entity_id, exc)
            return False

    def broadcast(self, text: str, skip_quiet: bool = True) -> int:
        """Broadcast text to all Sonos speakers.

        Args:
            text: Text to broadcast.
            skip_quiet: If True, skip speakers in quiet hours.

        Returns:
            Number of speakers that received the broadcast.
        """
        if not self._ha_token:
            return 0

        try:
            resp = requests.post(
                f"{self._ha_url}/api/services/script/turn_on",
                headers=self._headers(),
                json={
                    "entity_id": self._broadcast_script,
                    "variables": {
                        "message": text,
                        "volume": self._volume,
                    },
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Sonos broadcast triggered: %s", text[:60])
            return len(self._speakers)
        except requests.RequestException as exc:
            log.warning("Sonos broadcast failed: %s", exc)
            return 0
