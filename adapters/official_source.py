# /kaare/adapters/official_source.py
"""
Official-source tier for web search (P121 Phase 3).

The best source for a specific thing is usually its own website, which no static whitelist can
anticipate. Wikidata knows it (property P856), so this is resolved mechanically:

  subject (named by Kåre) → Wikidata entity → official website(s) → follow redirects safely →
  robots.txt → sitemap(s) → rank page URLs by Kåre's keywords → fetch the best 1–2 pages.

The official domain(s) are trusted for THIS request only — never added to the whitelist.
Everything goes through adapters.web_fetch (address guard, robots.txt, pacing, byte caps).
Every failure means "no official pages", never an error for the user.
"""

from __future__ import annotations

import logging
import re
import time
import zlib
from urllib.parse import unquote, urlparse

from adapters import web_fetch
from kaare_core.tools import wikidata

log = logging.getLogger(__name__)

_MAX_SITES = 2  # P856 values followed
_MAX_PAGES = 2  # official pages fetched per search
_MAX_SITEMAP_FILES = 6  # sitemap files read per site (index + children)
_MAX_SITEMAP_URLS = 20_000  # page URLs kept per site
_SITEMAP_MAX_BYTES = 5_000_000
_SITEMAP_TTL_S = 24 * 3600
_SITEMAP_TYPES = (
    "application/xml",
    "text/xml",
    "application/gzip",
    "application/x-gzip",
    "application/octet-stream",
    "text/plain",
    "text/html",
)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

_sitemap_cache: dict[str, tuple[float, list[str]]] = {}


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{(p.scheme or 'https').lower()}://{(p.hostname or '').lower()}"


def _gunzip(body: bytes) -> bytes:
    """Decompress a gzip sitemap with a hard output cap (no decompression bombs)."""
    if body[:2] != b"\x1f\x8b":
        return body
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(body, _SITEMAP_MAX_BYTES)
    if d.unconsumed_tail:
        raise web_fetch.FetchError("too_large")
    return out


async def _final_site(url: str, allow, transport=None) -> str | None:
    """Follow the official URL's redirects (safely) and return the final origin."""
    try:
        final_url, _, _ = await web_fetch.fetch_raw(url, allow=allow, transport=transport)
        return _origin(final_url)
    except web_fetch.FetchError as e:
        log.info("[official] %s not reachable: %s", url, e.reason)
        return None


async def _sitemap_urls(origin: str, allow, transport=None) -> list[str]:
    """Page URLs from a site's sitemap(s), cached per origin. Sitemap indexes are followed one
    level (children first in file order), within file/URL caps."""
    hit = _sitemap_cache.get(origin)
    if hit and time.monotonic() - hit[0] < _SITEMAP_TTL_S:
        return hit[1]
    queue = await web_fetch.robots_sitemaps(origin + "/", transport=transport) or [
        origin + "/sitemap.xml"
    ]
    pages: list[str] = []
    read = 0
    while queue and read < _MAX_SITEMAP_FILES and len(pages) < _MAX_SITEMAP_URLS:
        sm = queue.pop(0)
        read += 1
        try:
            _, body, _ = await web_fetch.fetch_raw(
                sm,
                allow=allow,
                transport=transport,
                max_bytes=_SITEMAP_MAX_BYTES,
                accept=_SITEMAP_TYPES,
            )
            text = _gunzip(body).decode("utf-8", errors="replace")
        except (web_fetch.FetchError, OSError, zlib.error) as e:
            log.info("[official] sitemap %s skipped: %s", sm, getattr(e, "reason", e))
            continue
        locs = [u.replace("&amp;", "&") for u in _LOC_RE.findall(text)]
        if "<sitemapindex" in text[:2000].lower():
            queue.extend(locs)
        else:
            pages.extend(u for u in locs if allow(u))
    pages = list(dict.fromkeys(pages))[:_MAX_SITEMAP_URLS]
    _sitemap_cache[origin] = (time.monotonic(), pages)
    return pages


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(unquote(text or "")) if len(w) >= 3}


def rank_pages(urls: list[str], keywords: list[str]) -> list[str]:
    """URLs whose path shares words with Kåre's keywords, best first. A keyword phrase counts
    once per word; ties go to the shorter (more general) path. No match → empty."""
    want = set()
    for k in keywords:
        want |= _words(k)
    if not want:
        return []
    scored = []
    for u in urls:
        path_words = _words(urlparse(u).path)
        score = len(want & path_words)
        if score:
            scored.append((-score, len(urlparse(u).path), u))
    return [u for _, _, u in sorted(scored)]


def _title_from_url(url: str) -> str:
    segment = (
        unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]) or urlparse(url).hostname or url
    )
    return re.sub(r"[-_]+", " ", segment).strip().capitalize()


async def find_official_pages(
    subject: str, keywords: list[str], lang: str, transport=None
) -> tuple[list[dict], set[str]]:
    """→ (sources, official_hosts). Each source: {title, url, tier: "official", content}.
    official_hosts are the bare hosts trusted for this request (also used to label ordinary
    search results that land on the official site)."""
    ents = await wikidata.subject_entities(subject, lang)
    if not ents:
        return [], set()
    sites = (await wikidata.official_websites(ents[0]["qid"]))[:_MAX_SITES]
    if not sites:
        return [], set()
    hosts = {web_fetch.normalize_domain(u) for u in sites} - {""}

    def allow(url: str) -> bool:
        host = web_fetch.normalize_domain(url)
        return (not web_fetch.is_blocked(url)) and any(
            host == h or host.endswith("." + h) for h in hosts
        )

    def allow_any_public(url: str) -> bool:
        return not web_fetch.is_blocked(url)

    origins = []
    for site in sites:
        # The first hop may move to another domain (kopalnia.pl → its English site); that final
        # domain joins the per-request trust.
        origin = await _final_site(site, allow_any_public, transport)
        if origin:
            origins.append(origin)
            hosts.add(web_fetch.normalize_domain(origin))
    if not origins:
        return [], hosts

    candidates: list[str] = []
    for origin in dict.fromkeys(origins):
        candidates += rank_pages(await _sitemap_urls(origin, allow, transport), keywords)
    sources = []
    for url in candidates:
        if len(sources) >= _MAX_PAGES:
            break
        try:
            final_url, text = await web_fetch.fetch_page_text(url, allow=allow, transport=transport)
        except web_fetch.FetchError as e:
            log.info("[official] page %s skipped: %s", url, e.reason)
            continue
        if text:
            sources.append(
                {
                    "title": _title_from_url(final_url),
                    "url": final_url,
                    "tier": "official",
                    "content": text,
                }
            )
    log.info(
        "[official] %s → %s: %d sitemap candidates, %d pages used",
        subject,
        sorted(hosts),
        len(candidates),
        len(sources),
    )
    return sources, hosts
