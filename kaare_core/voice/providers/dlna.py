import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmrDevice
from async_upnp_client.search import async_search

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
# Description URL per host from the last successful discovery (DT04 R0.1). Inside Docker Desktop no SSDP reply reaches
# the container, but TCP to the renderer works — so a URL found once (e.g. before a move to Docker, or typed in the
# node dialog) keeps the TV reachable without discovery.
LOCATIONS_PATH = Path("/kaare/state/voice/dlna_locations.json")


def description_url_of(node: dict) -> str:
    """The DLNA description URL the user set on the node (Nodes → node → Channels → DLNA), or ''."""
    chans = node.get("channels") if isinstance(node, dict) else None
    entry = chans.get("dlna") if isinstance(chans, dict) else None
    url = str(entry.get("description_url") or "").strip() if isinstance(entry, dict) else ""
    return url if url.startswith(("http://", "https://")) else ""


def _read_locations() -> dict[str, str]:
    try:
        data = json.loads(LOCATIONS_PATH.read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember(host: str, description_url: str) -> None:
    """Keep the URL that worked for `host` — only one on that host (never a URL some other device pointed to)."""
    if urlparse(description_url).hostname != host:
        return
    try:
        data = _read_locations()
        if data.get(host) == description_url:
            return
        data[host] = description_url
        LOCATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOCATIONS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        tmp.replace(LOCATIONS_PATH)
    except Exception as exc:
        log.warning("DLNA: could not remember the description URL for %s: %s", host, exc)


async def _find_description_url(host: str, timeout: int) -> str | None:
    """The device description URL of the DLNA renderer at `host`. A TV answers a unicast M-SEARCH with whichever of
    its UPnP devices it likes (a Samsung: only its screen-sharing service), so: unicast first and prefer a
    MediaRenderer, then a multicast search for MediaRenderer filtered to this host (P139 phase 3, Samsung AU9075)."""
    found: list[tuple[str, str]] = []

    async def _on_unicast(headers: dict) -> None:
        if "LOCATION" in headers:
            found.append((str(headers.get("ST") or ""), str(headers["LOCATION"])))

    try:
        await async_search(async_callback=_on_unicast, timeout=timeout, target=(host, 1900))
    except Exception as exc:
        log.debug("DLNA unicast SSDP failed for %s: %s", host, exc)
    for st, location in found:
        if st == _RENDERER:
            return location

    renderer: str | None = None

    async def _on_multicast(headers: dict) -> None:
        nonlocal renderer
        location = str(headers.get("LOCATION") or "")
        if renderer is None and urlparse(location).hostname == host:
            renderer = location

    try:
        await async_search(async_callback=_on_multicast, timeout=timeout, search_target=_RENDERER)
    except Exception as exc:
        log.debug("DLNA multicast SSDP failed for %s: %s", host, exc)
    return renderer or (found[0][1] if found else None)


async def _play_at(description_url: str, host: str, url: str, title: str) -> tuple[bool, str]:
    """SetAVTransportURI → Play on the renderer described at `description_url`. (ok, error)."""
    try:
        requester = AiohttpRequester(timeout=10)
        factory = UpnpFactory(requester, non_strict=True)
        device = await factory.async_create_device(description_url)
        dmr = DmrDevice(device, event_handler=None)
        await dmr.async_update()
        log.info("DLNA '%s' (%s) <- %s", device.name, host, url)
        await dmr.async_set_transport_uri(url, title)
        await dmr.async_play()
        return True, ""
    except Exception as exc:
        log.error("DLNA error on %s (%s): %s", host, description_url, exc)
        return False, str(exc)


async def play_uri(
    host: str, url: str, title: str = "Kåre", discovery_timeout: int = 4, description_url: str = ""
) -> tuple[bool, str]:
    """Hand one URL (speech wav or a picture) to a DLNA renderer. Tries the description URL set on the node, then the
    one remembered from the last discovery, then SSDP (which never answers inside Docker Desktop). A URL found by
    SSDP that plays is remembered for next time. (ok, error)."""
    tried: list[str] = []
    last_err = ""
    remembered = (await asyncio.to_thread(_read_locations)).get(host, "")
    for candidate in (description_url.strip(), remembered):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        ok, last_err = await _play_at(candidate, host, url, title)
        if ok:
            if candidate != remembered:
                await asyncio.to_thread(_remember, host, candidate)
            return True, ""
    found = await _find_description_url(host, timeout=discovery_timeout)
    if found and found not in tried:
        ok, last_err = await _play_at(found, host, url, title)
        if ok:
            await asyncio.to_thread(_remember, host, found)
            return True, ""
    if not tried and not found:
        return False, (
            f"no device responded to SSDP from {host} and no DLNA description URL is set "
            "(Nodes → the node → Channels → DLNA description URL)"
        )
    return False, last_err or f"no DLNA description URL answered for {host}"


class DlnaProvider(BaseVoiceProvider):
    """DLNA/UPnP provider via async-upnp-client.

    Støtter de fleste WiFi-høyttalere inkl. mange Sonos-kompatible.
    Flyt: Piper → WAV → bridge HTTP → DLNA SetAVTransportURI + Play.

    Enheten henter WAV-filen fra Kåres bridge-URL — samme som HA-provider.
    """

    def __init__(self, config: dict, media_base_url: str):
        self._config = config
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> bool:
        host = node_config.get("host", "")
        if not host:
            log.error("DLNA provider: mangler host i node-config")
            return False

        media_url = f"{self._media_base_url}/{wav_path.name}"
        discovery_timeout = int(self._config.get("discovery_timeout", 4))
        ok, _err = await play_uri(host, media_url, "Kåre", discovery_timeout, description_url_of(node_config))
        return ok
