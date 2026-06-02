"""mDNS announcement for satellite discovery.

Announces this satellite as a _kaare-sat._tcp service
so the server can auto-discover it.
"""
from __future__ import annotations

import logging
import socket

from zeroconf import Zeroconf, ServiceInfo

log = logging.getLogger(__name__)

SERVICE_TYPE = "_kaare-sat._tcp.local."


def announce_satellite(
    satellite_id: str,
    room: str,
    http_port: int,
) -> tuple[Zeroconf, ServiceInfo]:
    """Announce this satellite via mDNS.

    Returns (zeroconf, info) -- call zeroconf.unregister_service(info)
    and zeroconf.close() on shutdown.
    """
    hostname = socket.gethostname()
    ip = _get_local_ip()

    info = ServiceInfo(
        SERVICE_TYPE,
        f"{satellite_id}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=http_port,
        properties={
            "satellite_id": satellite_id,
            "room": room,
        },
        server=f"{hostname}.local.",
    )

    zc = Zeroconf()
    zc.register_service(info)
    log.info("mDNS: announced %s on %s:%d", satellite_id, ip, http_port)
    return zc, info


def _get_local_ip() -> str:
    """Get this machine's LAN IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"
