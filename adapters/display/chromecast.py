import asyncio
import logging
from pathlib import Path

from adapters.display.base import BaseDisplayProvider

log = logging.getLogger(__name__)


def _cast_image_sync(host: str, image_url: str, discovery_timeout: float) -> bool:
    # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
    import pychromecast  # noqa: PLC0415

    chromecasts, browser = pychromecast.get_listed_chromecasts(
        known_hosts=[host],
        discovery_timeout=discovery_timeout,
    )
    try:
        if not chromecasts:
            return False
        cast = chromecasts[0]
        cast.wait(timeout=10)
        cast.media_controller.play_media(image_url, "image/jpeg", title="Kåre")
        return True
    finally:
        pychromecast.discovery.stop_discovery(browser)


class ChromecastDisplayProvider(BaseDisplayProvider):
    def __init__(self, media_base_url: str, discovery_timeout: float = 5):
        self._media_base_url = media_base_url
        self._discovery_timeout = discovery_timeout

    async def send(
        self,
        node_config: dict,
        text: str,
        title: str = "Kåre",
        image_path: str | None = None,
        image_b64: str | None = None,
        duration: int = 8,
        position: str = "bottom_right",
    ) -> dict:
        host = node_config.get("host", "")
        if not host:
            return {"ok": False, "error": "Chromecast: missing host"}
        if not image_path:
            return {
                "ok": False,
                "error": "Chromecast: requires image (no text-only overlay support)",
            }

        image_url = f"{self._media_base_url}/{Path(image_path).name}"
        try:
            ok = await asyncio.get_event_loop().run_in_executor(
                None, _cast_image_sync, host, image_url, self._discovery_timeout
            )
            if ok:
                return {"ok": True, "method": "pychromecast"}
            return {"ok": False, "error": "No Chromecast device found"}
        except Exception as e:
            log.error("ChromecastDisplay error %s: %s", host, e)
            return {"ok": False, "error": str(e)}
