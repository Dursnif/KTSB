"""The real client address of an HTTP request (DT03 R6 H1, CLAUDE.md rule 27).

X-Forwarded-For is client-controlled. It is believed only when the direct peer is a reverse proxy
the user configured in the GUI (`network.trusted_proxies` — IP, CIDR or host name such as the
bundled `caddy`). Then the right-most address in the chain that is not itself a trusted proxy is
the client. From any other peer the header is ignored.

Before this, the Docker image ran uvicorn with `--forwarded-allow-ips=172.16.0.0/12`. Behind
Docker Desktop's NAT every LAN client arrives as the gateway (172.x), so anyone could send a new
fake X-Forwarded-For per try and walk around the login rate limit. The image now runs
`--no-proxy-headers`, so `request.client` is the real peer and this module is the only place that
reads the header.
"""

import ipaddress
import socket
import time

from kaare_core.config import get_network_settings

_DNS_TTL_S = 60.0
_dns_cache: dict[str, tuple[float, set[str]]] = {}


def _resolve(name: str) -> set[str]:
    now = time.monotonic()
    hit = _dns_cache.get(name)
    if hit and now - hit[0] < _DNS_TTL_S:
        return hit[1]
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(name, None)}
    except Exception:
        addrs = set()
    _dns_cache[name] = (now, addrs)
    return addrs


def is_trusted_proxy(addr: str, trusted: list[str] | None = None) -> bool:
    """True when `addr` matches an entry of the GUI's trusted-proxy list (IP, CIDR or host name)."""
    try:
        ip = ipaddress.ip_address(addr.strip())
    except ValueError:
        return False
    for entry in trusted if trusted is not None else get_network_settings()["trusted_proxies"]:
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
            continue
        except ValueError:
            pass
        if str(ip) in _resolve(entry):
            return True
    return False


def client_ip(request) -> str:
    """Real client address for rate limits, network context and audit logs."""
    peer = request.client.host if getattr(request, "client", None) else ""
    if not peer:
        return "unknown"
    trusted = get_network_settings()["trusted_proxies"]
    if not is_trusted_proxy(peer, trusted):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer  # malformed chain from a trusted proxy: do not guess
        if not is_trusted_proxy(hop, trusted):
            return hop
    return peer
