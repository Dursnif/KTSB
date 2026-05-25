import asyncio
import logging
from pathlib import Path

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


def _cast_and_play_sync(host: str, media_url: str, discovery_timeout: float) -> None:
    """Blokkerende Chromecast-oppkobling og avspilling (kjøres i thread executor)."""
    import pychromecast

    chromecasts, browser = pychromecast.get_listed_chromecasts(
        known_hosts=[host],
        discovery_timeout=discovery_timeout,
    )
    try:
        if not chromecasts:
            log.error("Chromecast: ingen enhet funnet på %s (timeout=%ss)", host, discovery_timeout)
            return

        cast = chromecasts[0]
        cast.wait(timeout=10)

        log.info("Chromecast '%s' (%s) <- %s", cast.name, host, media_url)
        cast.media_controller.play_media(media_url, "audio/wav", title="Kåre")
    finally:
        try:
            pychromecast.discovery.stop_discovery(browser)
        except Exception:
            pass


class ChromecastProvider(BaseVoiceProvider):
    def __init__(self, config: dict, media_base_url: str):
        self._config = config
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        host = node_config.get("host", "")
        if not host:
            log.error("Chromecast provider: mangler host i node-config")
            return

        media_url = f"{self._media_base_url}/{wav_path.name}"
        discovery_timeout = float(self._config.get("discovery_timeout", 5))

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _cast_and_play_sync, host, media_url, discovery_timeout)
        except Exception as exc:
            log.error("Chromecast provider feil på %s: %s", host, exc)
