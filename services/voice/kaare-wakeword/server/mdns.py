"""mDNS listener for satellite auto-discovery."""
from __future__ import annotations

import logging

from zeroconf import Zeroconf, ServiceBrowser, ServiceListener, ServiceInfo

from server.registry import SatelliteRegistry

log = logging.getLogger(__name__)

SERVICE_TYPE = "_kaare-sat._tcp.local."


class KaareMDNSListener(ServiceListener):
    """Listens for satellite mDNS announcements and registers them."""

    def __init__(self, registry: SatelliteRegistry):
        self._registry = registry
        self._zc: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None

    def start(self) -> None:
        """Start listening for satellite announcements."""
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self)
        log.info("mDNS: listening for %s", SERVICE_TYPE)

    def stop(self) -> None:
        """Stop listening."""
        if self._zc:
            self._zc.close()

    def _handle_service_found(self, info: ServiceInfo) -> None:
        """Handle a discovered satellite service."""
        props = info.properties or {}
        satellite_id = props.get(b"satellite_id", b"unknown").decode()
        room = props.get(b"room", b"unknown").decode()
        addresses = info.parsed_addresses()
        ip = addresses[0] if addresses else "unknown"
        port = info.port

        self._registry.register(
            satellite_id=satellite_id,
            room=room,
            ip=ip,
            http_port=port,
        )

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name) if zc else None
        if info:
            self._handle_service_found(info)
            log.info("mDNS: satellite discovered: %s", name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        log.info("mDNS: satellite removed: %s", name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name) if zc else None
        if info:
            self._handle_service_found(info)
