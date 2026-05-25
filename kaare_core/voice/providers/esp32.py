import logging
from pathlib import Path

import aioesphomeapi

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


class Esp32Provider(BaseVoiceProvider):
    def __init__(self, media_base_url: str):
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        host = node_config.get("host")
        api_port = node_config.get("api_port", 6053)
        noise_psk = node_config.get("encryption_key")

        if not host:
            log.error("ESP32 provider: mangler host i node-config")
            return

        media_url = f"{self._media_base_url}/{wav_path.name}"
        log.info("Kobler til ESP32 %s:%s for %s", host, api_port, media_url)
        api = aioesphomeapi.APIClient(host, api_port, password=None, noise_psk=noise_psk)
        try:
            await api.connect(login=True)
            entities, _ = await api.list_entities_services()
            media_players = [e for e in entities if isinstance(e, aioesphomeapi.MediaPlayerInfo)]
            if not media_players:
                log.error("Ingen media_player funnet på ESP32 %s — sjekk ESPHome-konfig", host)
                return
            mp_key = media_players[0].key
            await api.media_player_command(mp_key, media_url=media_url, announcement=True)
            log.info("ESP32 %s spiller av %s", host, media_url)
        except Exception as exc:
            log.error("ESP32 provider feil på %s: %s", host, exc)
        finally:
            await api.disconnect()
