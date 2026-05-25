import asyncio
import logging
from pathlib import Path

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmrDevice
from async_upnp_client.search import async_search

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


async def _find_description_url(host: str, timeout: int) -> str | None:
    """Unicast SSDP M-SEARCH til kjent host — returnerer device description URL."""
    location: str | None = None

    async def _on_response(headers: dict) -> None:
        nonlocal location
        if location is None and "LOCATION" in headers:
            location = headers["LOCATION"]

    try:
        await async_search(
            async_callback=_on_response,
            timeout=timeout,
            target=(host, 1900),
        )
    except Exception as exc:
        log.debug("DLNA SSDP feilet for %s: %s", host, exc)

    return location


class DlnaProvider(BaseVoiceProvider):
    """DLNA/UPnP provider via async-upnp-client.

    Støtter de fleste WiFi-høyttalere inkl. mange Sonos-kompatible.
    Flyt: Piper → WAV → bridge HTTP → DLNA SetAVTransportURI + Play.

    Enheten henter WAV-filen fra Kåres bridge-URL — samme som HA-provider.
    """

    def __init__(self, config: dict, media_base_url: str):
        self._config = config
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        host = node_config.get("host", "")
        if not host:
            log.error("DLNA provider: mangler host i node-config")
            return

        media_url = f"{self._media_base_url}/{wav_path.name}"
        discovery_timeout = int(self._config.get("discovery_timeout", 4))

        description_url = await _find_description_url(host, timeout=discovery_timeout)
        if not description_url:
            log.error(
                "DLNA: ingen enhet svarte på SSDP fra %s. "
                "Sjekk at enheten er på nett og støtter UPnP/DLNA.",
                host,
            )
            return

        try:
            requester = AiohttpRequester(timeout=10)
            factory = UpnpFactory(requester, non_strict=True)
            device = await factory.async_create_device(description_url)

            dmr = DmrDevice(device, event_handler=None)
            await dmr.async_update()

            log.info("DLNA '%s' (%s) <- %s", device.name, host, wav_path.name)
            await dmr.async_set_transport_uri(media_url, "Kåre")
            await dmr.async_play()
        except Exception as exc:
            log.error("DLNA provider feil på %s: %s", host, exc)
