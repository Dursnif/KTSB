import logging
from pathlib import Path

from kaare_core.voice import cast_sender
from kaare_core.voice.base import BaseVoiceProvider
from kaare_core.voice.speak_target import TargetBusy

log = logging.getLogger(__name__)


class ChromecastProvider(BaseVoiceProvider):
    """Speech to a Cast device (a Chromecast, a Nest, or a TV with Cast built in — P139 phase 3: any node with a `cast`
    channel). Direct connect through `cast_sender` — the old `get_listed_chromecasts(known_hosts=…)` found no Google TV."""

    def __init__(self, config: dict, media_base_url: str):
        self._config = config
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> bool:
        host = node_config.get("host", "")
        if not host:
            log.error("Chromecast provider: node has no host")
            return False
        media_url = f"{self._media_base_url}/{wav_path.name}"
        result = await cast_sender.run(cast_sender.play_audio, host, media_url)
        if result.get("busy"):
            log.info("Chromecast speech on %s not sent: %s is playing", host, result["busy"])
            raise TargetBusy(str(result["busy"]))
        if not result.get("ok"):
            log.error("Chromecast speech on %s failed: %s", host, result.get("error") or result.get("after"))
        return bool(result.get("ok"))

    async def set_volume(self, volume: float, node_config: dict) -> None:
        host = node_config.get("host", "")
        if host:
            result = await cast_sender.run(cast_sender.set_volume, host, volume, timeout=10)
            if not result.get("ok"):
                log.warning("Chromecast volume on %s failed: %s", host, result.get("error"))

    async def is_available(self, node_config: dict) -> bool:
        """Reachable over Cast now (a Nest/TV in standby still answers; one switched off does not)."""
        host = node_config.get("host", "")
        if not host:
            return False
        return bool((await cast_sender.run(cast_sender.status, host, timeout=10)).get("ok"))


class CastFirstProvider(BaseVoiceProvider):
    """KTSB's own Cast first, the node type's provider (Home Assistant for a Nest) as the fallback (P139 D7: «alt lokalt
    i KTSB, andre avhengigheter er reserve»). Used for nodes with an enabled `cast` channel and an address."""

    def __init__(self, cast: ChromecastProvider, fallback: BaseVoiceProvider):
        self._cast = cast
        self._fallback = fallback

    async def is_available(self, node_config: dict) -> bool:
        return await self._cast.is_available(node_config) or await self._fallback.is_available(node_config)

    async def set_volume(self, volume: float, node_config: dict) -> None:
        if await self._cast.is_available(node_config):
            await self._cast.set_volume(volume, node_config)
        else:
            await self._fallback.set_volume(volume, node_config)

    async def speak(self, wav_path: Path, node_config: dict) -> bool:
        # TargetBusy from own Cast is not caught: the HA fallback's play_media would replace the playing app (P-D1).
        if await self._cast.speak(wav_path, node_config):
            return True
        log.warning("Own Cast speech failed for %s — falling back to %s", node_config.get("host"),
                    type(self._fallback).__name__)
        return await self._fallback.speak(wav_path, node_config)
