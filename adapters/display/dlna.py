import logging
from pathlib import Path
from typing import Optional

from adapters.display.base import BaseDisplayProvider

log = logging.getLogger(__name__)


class DlnaDisplayProvider(BaseDisplayProvider):
    def __init__(self, media_base_url: str, discovery_timeout: int = 4):
        self._media_base_url = media_base_url
        self._discovery_timeout = discovery_timeout

    async def send(
        self,
        node_config: dict,
        text: str,
        title: str = "Kåre",
        image_path: Optional[str] = None,
        image_b64: Optional[str] = None,
        duration: int = 8,
        position: str = "bottom_right",
    ) -> dict:
        host = node_config.get("host", "")
        if not host:
            return {"ok": False, "error": "DLNA: missing host"}
        if not image_path:
            return {"ok": False, "error": "DLNA: requires image"}

        from async_upnp_client.aiohttp import AiohttpRequester
        from async_upnp_client.client_factory import UpnpFactory
        from async_upnp_client.profiles.dlna import DmrDevice
        from kaare_core.voice.providers.dlna import _find_description_url

        image_url = f"{self._media_base_url}/{Path(image_path).name}"
        description_url = await _find_description_url(host, self._discovery_timeout)
        if not description_url:
            return {"ok": False, "error": f"DLNA: no device responded to SSDP from {host}"}

        try:
            requester = AiohttpRequester(timeout=10)
            factory = UpnpFactory(requester, non_strict=True)
            device = await factory.async_create_device(description_url)
            dmr = DmrDevice(device, event_handler=None)
            await dmr.async_update()
            await dmr.async_set_transport_uri(image_url, title)
            await dmr.async_play()
            return {"ok": True, "method": "dlna"}
        except Exception as e:
            log.error("DlnaDisplay error %s: %s", host, e)
            return {"ok": False, "error": str(e)}
