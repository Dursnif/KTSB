import logging
from pathlib import Path

import httpx

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


class HaProvider(BaseVoiceProvider):
    def __init__(self, ha_url: str, ha_token: str, media_base_url: str):
        self._ha_url = ha_url
        self._ha_token = ha_token
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        entity_id = node_config.get("entity_id", "")
        if not entity_id:
            log.error("HA provider: mangler entity_id i node-config")
            return
        if not self._ha_url or not self._ha_token:
            log.error("HA provider: HA-credentials ikke konfigurert")
            return

        media_url = f"{self._media_base_url}/{wav_path.name}"
        payload = {
            "entity_id": entity_id,
            "media_content_id": media_url,
            "media_content_type": "music",
            "announce": True,
        }
        log.info("HA media_player '%s' <- %s", entity_id, media_url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._ha_url}/api/services/media_player/play_media",
                    headers={"Authorization": f"Bearer {self._ha_token}"},
                    json=payload,
                )
                resp.raise_for_status()
        except Exception as exc:
            log.error("HA provider feil på '%s': %s", entity_id, exc)

    async def set_volume(self, volume: float, node_config: dict) -> None:
        entity_id = node_config.get("entity_id", "")
        if not entity_id or not self._ha_url or not self._ha_token:
            return
        volume = max(0.0, min(1.0, volume))
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._ha_url}/api/services/media_player/volume_set",
                    headers={"Authorization": f"Bearer {self._ha_token}"},
                    json={"entity_id": entity_id, "volume_level": round(volume, 2)},
                )
                resp.raise_for_status()
                log.info("HA volume %.0f%% på '%s'", volume * 100, entity_id)
        except Exception as exc:
            log.error("HA volume feil på '%s': %s", entity_id, exc)
