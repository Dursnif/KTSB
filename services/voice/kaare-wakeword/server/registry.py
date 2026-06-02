"""Satellite registry - tracks connected satellites and enables audio push."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)


@dataclass
class SatelliteInfo:
    """Info about a registered satellite."""
    satellite_id: str
    room: str
    ip: str
    http_port: int
    online: bool = True
    last_seen: float = field(default_factory=time.time)
    capabilities: list[str] = field(default_factory=lambda: ["speaker", "mic"])


class SatelliteRegistry:
    """Track connected satellites and push audio to them.

    Satellites register via WebSocket or mDNS discovery.
    Audio is pushed to satellites via HTTP POST to their /play endpoint.
    """

    def __init__(self):
        self._satellites: dict[str, SatelliteInfo] = {}

    def register(
        self,
        satellite_id: str,
        room: str,
        ip: str,
        http_port: int,
        capabilities: list[str] | None = None,
    ) -> None:
        """Register or update a satellite."""
        self._satellites[satellite_id] = SatelliteInfo(
            satellite_id=satellite_id,
            room=room,
            ip=ip,
            http_port=http_port,
            capabilities=capabilities or ["speaker", "mic"],
        )
        log.info("Satellite registered: %s (room=%s, ip=%s:%d)", satellite_id, room, ip, http_port)

    def unregister(self, satellite_id: str) -> None:
        """Remove a satellite from the registry."""
        if satellite_id in self._satellites:
            del self._satellites[satellite_id]
            log.info("Satellite unregistered: %s", satellite_id)

    def get(self, satellite_id: str) -> SatelliteInfo | None:
        """Get info for a specific satellite."""
        return self._satellites.get(satellite_id)

    def get_by_room(self, room: str) -> list[SatelliteInfo]:
        """Get all online satellites in a room."""
        return [s for s in self._satellites.values() if s.room == room and s.online]

    def get_all_online(self) -> list[SatelliteInfo]:
        """Get all online satellites."""
        return [s for s in self._satellites.values() if s.online]

    def push_audio(self, satellite_id: str, wav_bytes: bytes, sample_rate: int = 22050) -> bool:
        """Push audio to a satellite via HTTP POST.

        Args:
            satellite_id: Target satellite.
            wav_bytes: Raw PCM audio bytes.
            sample_rate: Audio sample rate.

        Returns:
            True if push was successful.
        """
        info = self._satellites.get(satellite_id)
        if not info or not info.online:
            log.warning("Cannot push to %s: not registered or offline", satellite_id)
            return False

        try:
            resp = requests.post(
                f"http://{info.ip}:{info.http_port}/play",
                data=wav_bytes,
                headers={
                    "Content-Type": "audio/wav",
                    "X-Sample-Rate": str(sample_rate),
                },
                timeout=10,
            )
            resp.raise_for_status()
            info.last_seen = time.time()
            log.info("Audio pushed to %s (%d bytes)", satellite_id, len(wav_bytes))
            return True
        except requests.RequestException as exc:
            log.warning("Audio push to %s failed: %s", satellite_id, exc)
            info.online = False
            return False

    def broadcast_audio(
        self, wav_bytes: bytes, rooms: list[str] | None = None, sample_rate: int = 22050,
    ) -> int:
        """Push audio to all satellites (optionally filtered by room).

        Args:
            wav_bytes: Raw PCM audio bytes.
            rooms: If set, only broadcast to these rooms.
            sample_rate: Audio sample rate.

        Returns:
            Number of satellites that received the audio.
        """
        targets = self.get_all_online()
        if rooms:
            targets = [s for s in targets if s.room in rooms]

        count = 0
        for sat in targets:
            if self.push_audio(sat.satellite_id, wav_bytes, sample_rate):
                count += 1
        return count
