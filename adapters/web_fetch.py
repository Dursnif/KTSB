# /kaare/adapters/web_fetch.py
"""
Shared, safe web fetching for KTSB (P121 Phase 0) — the one place that turns a URL into text.

Used by web search (page content) and library(fetch_url). Guarantees, for every request:
  - Only http/https on the standard ports (80/443).
  - SSRF guard: the host must resolve ONLY to public (globally routable) addresses — no
    loopback, LAN, VPN, link-local, CGNAT or reserved ranges — checked before connecting, on
    every redirect hop, and again on the address actually connected to (closes DNS rebinding).
  - Redirects are followed by hand (max 5); each hop also passes the caller's `allow` policy,
    so a whitelisted page cannot redirect the fetch to a non-whitelisted site.
  - Body is streamed with a byte cap; only text content types (HTML/XHTML/plain) are read.
  - HTML → text extraction (trafilatura) runs in a worker thread, never on the event loop.

Polite by default (Phase 1): honest, versioned User-Agent with a contact URL; robots.txt is
obeyed (fetched async, parsed with the stdlib parser, cached per host); requests to the same
host are paced; successful pages are cached in memory (no disk IO).

Also owns the trusted-sources list (configs/trusted_sources.yaml): one loader, re-read when the
file changes (mtime), fail-closed when the file is unreadable.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import time
import urllib.robotparser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
import yaml

from kaare_core.config import get_service
from kaare_core.version import get_version

log = logging.getLogger(__name__)

_TRUSTED_PATH = Path("/kaare/configs/trusted_sources.yaml")
_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_REDIRECTS = 5
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_PROJECT_URL = "https://github.com/Dursnif/KTSB"
_ROBOTS_TOKEN = "KTSB"  # the product token robots.txt rules are matched against
_ROBOTS_MAX_BYTES = 500_000
_ROBOTS_TTL_S = 24 * 3600
_ROBOTS_MAX_HOSTS = 500
_DEFAULTS = {
    "fetch_timeout_s": 8.0,
    "max_download_bytes": 3_000_000,
    "per_host_interval_s": 1.0,
    "page_cache_ttl_s": 6 * 3600,
    "page_cache_max_entries": 200,
    "search_total_timeout_s": 60.0,
    "trusted_slots": 2,
    "official_timeout_s": 20.0,
    "background_timeout_s": 180.0,
}
_PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


class FetchError(Exception):
    """A fetch that was refused or failed. `reason` is a short, stable, English code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── Trusted sources ──────────────────────────────────────────────────────────
# configs/trusted_sources.yaml:
#   sources:  {<category>: [{domain, description, type, trust}, ...]}   ← whitelist (GUI)
#   blocked:  [domain, ...]                                               ← never used (GUI)
# type/trust are fixed GUI choices (P121 D8); entries without them get the defaults below.

SOURCE_TYPES = (
    "public_authority",
    "encyclopedia",
    "newsroom",
    "research",
    "official",
    "other_serious",
)
TRUST_LEVELS = ("high", "normal", "low")
DEFAULT_TYPE = "other_serious"
DEFAULT_TRUST = "normal"

_trust_cache: dict = {
    "mtime": None,
    "domains": [],
    "entries": {},
    "blocked": [],
    "state": "unreadable",
}


def normalize_domain(entry: str) -> str:
    """User input → bare host: lower case, no scheme, no path/port, no leading "www.".
    Empty string when nothing usable is left."""
    raw = (entry or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    host = (urlparse(raw).hostname or "").rstrip(".")
    return host.removeprefix("www.")


def normalize_entry(entry: dict) -> dict:
    """One whitelist entry in the current format (English keys, known type/trust values)."""
    host = normalize_domain(str(entry.get("domain", "")))
    out = {
        "domain": host,
        "description": str(entry.get("description") or entry.get("beskrivelse") or "").strip(),
    }
    out["type"] = entry.get("type") if entry.get("type") in SOURCE_TYPES else DEFAULT_TYPE
    out["trust"] = entry.get("trust") if entry.get("trust") in TRUST_LEVELS else DEFAULT_TRUST
    return out


def _load_trusted() -> None:
    try:
        mtime = _TRUSTED_PATH.stat().st_mtime
    except OSError:
        if _trust_cache["state"] != "unreadable":
            log.warning("[web_fetch] trusted_sources.yaml missing — no web pages will be fetched")
        _trust_cache.update(mtime=None, domains=[], entries={}, blocked=[], state="unreadable")
        return
    if mtime == _trust_cache["mtime"]:
        return
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        entries: dict[str, dict] = {}
        for category, items in (data.get("sources") or {}).items():
            for raw_entry in items or []:
                raw = str((raw_entry or {}).get("domain", ""))
                norm = normalize_entry(raw_entry or {})
                host = norm["domain"]
                if host and host != raw.strip().lower().removeprefix("www."):
                    log.warning(
                        "[web_fetch] trusted entry %r used as host %r (path/scheme ignored)",
                        raw,
                        host,
                    )
                if host and host not in entries:
                    entries[host] = {**norm, "category": str(category)}
        blocked = [h for h in (normalize_domain(str(b)) for b in (data.get("blocked") or [])) if h]
        _trust_cache.update(
            mtime=mtime,
            domains=list(entries),
            entries=entries,
            blocked=list(dict.fromkeys(blocked)),
            state="ok" if entries else "empty",
        )
        log.info("[web_fetch] %d trusted, %d blocked domains loaded", len(entries), len(blocked))
    except Exception as e:
        log.warning("[web_fetch] trusted_sources.yaml unreadable (%s) — failing closed", e)
        _trust_cache.update(mtime=mtime, domains=[], entries={}, blocked=[], state="unreadable")


def trust_list_state() -> str:
    """ "ok" | "empty" (valid file, no entries) | "unreadable" (missing or broken file)."""
    _load_trusted()
    return _trust_cache["state"]


def trusted_domains() -> list[str]:
    _load_trusted()
    return list(_trust_cache["domains"])


def _match(host: str, domains) -> str | None:
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return None


def listed_entry(url: str) -> dict | None:
    """The whitelist entry (domain, description, type, trust, category) covering this URL, or
    None. Unlike is_trusted(), an empty list lists nothing."""
    _load_trusted()
    if _trust_cache["state"] != "ok":
        return None
    d = _match(normalize_domain(url), _trust_cache["entries"])
    return dict(_trust_cache["entries"][d]) if d else None


def is_blocked(url: str) -> bool:
    _load_trusted()
    return _match(normalize_domain(url), _trust_cache["blocked"]) is not None


def is_trusted(url: str) -> bool:
    """True when the URL's host is a listed domain or a subdomain of one, and not blocked.
    Unreadable list → False (fail closed). A valid but empty list means no whitelist is
    configured → True (the address guard below still applies to every fetch)."""
    _load_trusted()
    state = _trust_cache["state"]
    if state == "unreadable" or is_blocked(url):
        return False
    if state == "empty":
        return True
    return listed_entry(url) is not None


def not_blocked(url: str) -> bool:
    """Fetch policy for open-web pages: anything public that the user has not blocked."""
    return _trust_cache["state"] != "unreadable" and not is_blocked(url)


# ── Address guard ────────────────────────────────────────────────────────────


def ip_is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def check_public_url(url: str) -> None:
    """Raise FetchError unless the URL is http(s) on a standard port and its host resolves
    only to public addresses."""
    p = urlparse(url)
    scheme = (p.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise FetchError("scheme")
    try:
        port = p.port
    except ValueError as exc:
        raise FetchError("port") from exc
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        raise FetchError("port")
    host = p.hostname
    if not host:
        raise FetchError("host")
    try:
        addresses = await _resolve(host, port or _DEFAULT_PORTS[scheme])
    except Exception as exc:
        raise FetchError("dns") from exc
    if not addresses or not all(ip_is_public(a) for a in addresses):
        raise FetchError("private_address")


def _check_peer(response: httpx.Response) -> None:
    """The address actually connected to must be public (DNS can change between the check and
    the connect). Skipped behind an explicit proxy, where the peer is the proxy itself."""
    if any(os.environ.get(k) for k in _PROXY_ENV):
        return
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    peer = stream.get_extra_info("server_addr")
    if peer and not ip_is_public(str(peer[0])):
        raise FetchError("private_address")


# ── Fetching ─────────────────────────────────────────────────────────────────


def setting(key: str) -> float:
    """A numeric web-search setting from services.yaml `web_search:` (GUI: Settings → Web
    Search & Weather, expert mode), falling back to the generic default. Never ≤ 0."""
    default = _DEFAULTS[key]
    try:
        value = float((get_service("web_search") or {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def open_web_enabled() -> bool:
    """Whether non-whitelisted results may be used (labelled, lower weight). Default on (D9.6)."""
    value = (get_service("web_search") or {}).get("open_web", True)
    return (
        value
        if isinstance(value, bool)
        else str(value).strip().lower() not in ("false", "0", "off", "no")
    )


def user_agent() -> str:
    """Honest identity: product, version and where to read about it (no browser disguise)."""
    return f"Mozilla/5.0 (compatible; KTSB/{get_version()}; +{_PROJECT_URL})"


# ── Politeness: pacing and robots.txt ────────────────────────────────────────

_host_next: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}


async def _pace(host: str) -> None:
    """At most one request per `per_host_interval_s` to the same host."""
    lock = _host_locks.setdefault(host, asyncio.Lock())
    async with lock:
        wait = _host_next.get(host, 0.0) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _host_next[host] = time.monotonic() + setting("per_host_interval_s")
    if len(_host_locks) > 2000:
        stale = [h for h, t in _host_next.items() if t < time.monotonic() - 60]
        for h in stale:
            _host_next.pop(h, None)
            _host_locks.pop(h, None)


_robots_cache: dict[str, tuple[float, object]] = {}
_ALLOW_ALL = "allow_all"
_DENY_ALL = "deny_all"


async def robots_allows(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """RFC 9309: robots.txt 2xx → its rules; 4xx (incl. 404) → no restrictions; 5xx or an
    unreachable file → treat as disallow for now. Cached per scheme+host."""
    p = urlparse(url)
    origin = f"{(p.scheme or '').lower()}://{(p.hostname or '').lower()}"
    if p.port:
        origin += f":{p.port}"
    hit = _robots_cache.get(origin)
    if hit is None or time.monotonic() - hit[0] > _ROBOTS_TTL_S:
        try:
            _, body, _ = await fetch_raw(
                origin + "/robots.txt",
                transport=transport,
                respect_robots=False,
                max_bytes=_ROBOTS_MAX_BYTES,
            )
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            rules: object = parser
        except FetchError as e:
            status = e.reason.removeprefix("status_")
            if status.isdigit() and 400 <= int(status) < 500:
                rules = _ALLOW_ALL
            elif e.reason == "content_type":
                rules = _ALLOW_ALL  # served as something odd (e.g. an HTML 200 page) — no rules
            else:
                rules = _DENY_ALL
        if len(_robots_cache) >= _ROBOTS_MAX_HOSTS:
            oldest = sorted(_robots_cache, key=lambda k: _robots_cache[k][0])[
                : _ROBOTS_MAX_HOSTS // 10
            ]
            for k in oldest:
                _robots_cache.pop(k, None)
        _robots_cache[origin] = hit = (time.monotonic(), rules)
    rules = hit[1]
    if rules == _ALLOW_ALL:
        return True
    if rules == _DENY_ALL:
        return False
    return rules.can_fetch(_ROBOTS_TOKEN, url)


async def robots_sitemaps(
    origin_url: str, transport: httpx.AsyncBaseTransport | None = None
) -> list[str]:
    """Sitemap URLs a site declares in its robots.txt (loaded/cached via robots_allows)."""
    await robots_allows(origin_url, transport=transport)
    p = urlparse(origin_url)
    origin = f"{(p.scheme or '').lower()}://{(p.hostname or '').lower()}" + (
        f":{p.port}" if p.port else ""
    )
    hit = _robots_cache.get(origin)
    rules = hit[1] if hit else None
    if isinstance(rules, urllib.robotparser.RobotFileParser):
        return list(rules.site_maps() or [])
    return []


# ── Page cache (memory only) ─────────────────────────────────────────────────

_page_cache: dict[str, tuple[float, str, str]] = {}


def _cache_get(url: str) -> tuple[str, str] | None:
    hit = _page_cache.get(url)
    if hit and time.monotonic() - hit[0] < setting("page_cache_ttl_s"):
        return hit[1], hit[2]
    return None


def _cache_put(url: str, final_url: str, text: str) -> None:
    limit = int(setting("page_cache_max_entries"))
    while len(_page_cache) >= limit:
        _page_cache.pop(min(_page_cache, key=lambda k: _page_cache[k][0]), None)
    _page_cache[url] = (time.monotonic(), final_url, text)


async def fetch_raw(
    url: str,
    *,
    allow: Callable[[str], bool] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    respect_robots: bool = True,
    max_bytes: int | None = None,
    accept: tuple[str, ...] = _TEXT_TYPES,
) -> tuple[str, bytes, str]:
    """GET a page safely → (final_url, body bytes, content type). Raises FetchError only —
    network/TLS/timeout errors become FetchError("network_<Kind>") so one slow or broken site
    can't take down a caller (e.g. the whole official-source lookup).
    `accept` = allowed content-type prefixes (sitemaps add XML/gzip). `transport` is for tests."""
    try:
        return await _fetch_raw(
            url,
            allow=allow,
            transport=transport,
            respect_robots=respect_robots,
            max_bytes=max_bytes,
            accept=accept,
        )
    except httpx.HTTPError as e:
        raise FetchError(f"network_{type(e).__name__}") from e


async def _fetch_raw(
    url: str, *, allow, transport, respect_robots, max_bytes, accept
) -> tuple[str, bytes, str]:
    timeout = setting("fetch_timeout_s")
    max_bytes = max_bytes or int(setting("max_download_bytes"))
    current = url
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": user_agent()},
        follow_redirects=False,
        transport=transport,
    ) as client:
        for _hop in range(_MAX_REDIRECTS + 1):
            await check_public_url(current)
            if allow is not None and not allow(current):
                raise FetchError("not_allowed")
            if respect_robots and not await robots_allows(current, transport=transport):
                raise FetchError("robots")
            await _pace((urlparse(current).hostname or "").lower())
            async with client.stream("GET", current) as r:
                _check_peer(r)
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise FetchError("bad_redirect")
                    current = urljoin(current, location)
                    continue
                if r.status_code != 200:
                    raise FetchError(f"status_{r.status_code}")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype and not ctype.startswith(accept):
                    raise FetchError("content_type")
                declared = r.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError("too_large")
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise FetchError("too_large")
                return current, bytes(body), ctype or "text/html"
        raise FetchError("too_many_redirects")


def _extract(body: bytes, ctype: str) -> str:
    if ctype == "text/plain":
        return body.decode("utf-8", errors="replace").strip()
    return (
        trafilatura.extract(body, include_comments=False, include_tables=True, no_fallback=False)
        or ""
    ).strip()


async def fetch_page_text(
    url: str,
    *,
    allow: Callable[[str], bool] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    """Safe, polite GET + main-text extraction → (final_url, text). Raises FetchError.
    Served from the memory cache when fresh and the cached final URL still passes `allow`."""
    cached = _cache_get(url)
    if cached and (allow is None or allow(cached[0])):
        return cached
    final_url, body, ctype = await fetch_raw(url, allow=allow, transport=transport)
    text = await asyncio.to_thread(_extract, body, ctype)
    if text:
        _cache_put(url, final_url, text)
    return final_url, text
