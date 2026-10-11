# /kaare/adapters/web_search_adapter.py
"""
Web search adapter — providers: SearXNG (default, self-hosted, no key) and Brave (API key).
DuckDuckGo scraping was removed in P121 Phase 1 (frozen library, unofficial scraping).

Step 1 — fetch results from configured provider (with fallback).
Step 2 — fetch actual page content (trafilatura).
Step 3 — send content + question to Miss Library for synthesis.
Returns Library's answer as plain text to Kåre's tool loop.

Provider config in configs/services.yaml under web_search:.
Brave API key in configs/kare_llm.env (BRAVE_API_KEY).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlencode

import httpx
import yaml

from adapters import web_fetch
from adapters.official_source import find_official_pages
from kaare_core.config import now_local
from kaare_core.tools.i18n import get_lang, t

log = logging.getLogger(__name__)

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_SERVICES_PATH = Path("/kaare/configs/services.yaml")

_BRAVE_KEY = os.getenv("BRAVE_API_KEY", "")


_TEMPLATE_SERVICES_PATH = Path("/kaare/configs_default/services.yaml")
_PROVIDERS = ("searxng", "brave")


def _template_searxng_url() -> str:
    try:
        tpl = yaml.safe_load(_TEMPLATE_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        return str((tpl.get("web_search") or {}).get("searxng_url") or "")
    except Exception:
        return ""


def load_websearch_config() -> dict:
    """The one reader of the web_search config (services.yaml; legacy settings.yaml websearch:).
    Also used by the settings router so GUI and search see the same values.
    Installs still set to the removed "ddg" provider are moved to SearXNG, using the shipped
    template's SearXNG address when none is configured (P121 Phase 1)."""
    defaults = {
        "provider": "searxng",
        "fallback": "",
        "fetch_count": 10,
        "max_results": 3,
        "content_max": 3000,
        "searxng_url": "",
        # Empty = let Brave decide; the GUI sets them (Settings → Web Search & Weather).
        "brave_country": "",
        "brave_search_lang": "",
    }
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        ws = svc.get("web_search", {})
        if not ws:
            # Migration: fall back to settings.yaml if services.yaml has no web_search section yet
            s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
            ws = s.get("websearch", {})
        cfg = {**defaults, **ws}
    except Exception:
        cfg = dict(defaults)
    if cfg.get("provider") not in _PROVIDERS:
        cfg["provider"] = "searxng"
        if not cfg.get("searxng_url"):
            cfg["searxng_url"] = _template_searxng_url()
    if cfg.get("fallback") not in _PROVIDERS:
        cfg["fallback"] = ""
    return cfg


_WS_CFG = load_websearch_config()


def _fetch_count() -> int:
    return int(_WS_CFG.get("fetch_count", 10))


def _max_results() -> int:
    return int(_WS_CFG.get("max_results", 3))


def _content_max() -> int:
    return int(_WS_CFG.get("content_max", 3000))


# ── Trust filter ───────────────────────────────────────────────────────────
# The trusted list lives in adapters/web_fetch.py (one loader, mtime reload, fail-closed).


def reload_config() -> None:
    """Reload websearch config (called by /api/reload). The trusted list reloads by itself."""
    global _WS_CFG, _BRAVE_KEY
    _WS_CFG = load_websearch_config()
    _BRAVE_KEY = os.getenv("BRAVE_API_KEY", "")
    log.info(
        "[web_search] Config reloaded: provider=%s, %d trusted domains",
        _WS_CFG.get("provider"),
        len(web_fetch.trusted_domains()),
    )


def _select_sources(raw: list[dict]) -> list[dict]:
    """Order one result list into sources to use (P121 Phase 2, D1): whitelisted hits take up to
    `trusted_slots` places first, open-web hits fill the rest of `max_results` (only when open
    web is on), leftover places go back to whitelisted hits. Blocked domains are never used and
    each site appears once (a single site like TripAdvisor can't take every place). Candidates
    beyond max_results follow in the same order — backups for pages that can't be fetched.
    Each source carries its tier ("trusted" | "open") and, for trusted ones, type/trust."""
    trusted, open_ = [], []
    hosts: set[str] = set()
    for item in raw:
        url = item.get("url", "")
        host = web_fetch.normalize_domain(url)
        if not url or not host or host in hosts or web_fetch.is_blocked(url):
            continue
        hosts.add(host)
        entry = web_fetch.listed_entry(url)
        if entry:
            trusted.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "tier": "trusted",
                    "type": entry["type"],
                    "trust": entry["trust"],
                }
            )
        else:
            open_.append({"title": item.get("title", ""), "url": url, "tier": "open"})
    if not web_fetch.open_web_enabled():
        open_ = []
    n = _max_results()
    first = trusted[: min(int(web_fetch.setting("trusted_slots")), n)]
    rest = open_[: n - len(first)]
    extra = trusted[len(first) : len(first) + n - len(first) - len(rest)]
    chosen = first + extra + rest
    backups = [c for c in trusted + open_ if c not in chosen]
    return chosen + backups


# ── Paid-call counter (shown in the GUI; memory only, resets on restart) ──

_paid_calls: dict = {"date": "", "counts": {}}


def _count_paid_call(provider: str) -> None:
    today = now_local().date().isoformat()
    if _paid_calls["date"] != today:
        _paid_calls.update(date=today, counts={})
    _paid_calls["counts"][provider] = _paid_calls["counts"].get(provider, 0) + 1


def paid_calls_today() -> dict:
    return (
        dict(_paid_calls["counts"]) if _paid_calls["date"] == now_local().date().isoformat() else {}
    )


# ── Providers ──────────────────────────────────────────────────────────────


async def _brave_search(query: str, count: int) -> list[dict]:
    key = _BRAVE_KEY or os.getenv("BRAVE_API_KEY", "")
    if not key:
        log.warning("[web_search] BRAVE_API_KEY not set")
        return []
    try:
        params = {"q": query, "count": count}
        country = str(_WS_CFG.get("brave_country") or "").strip()
        search_lang = str(_WS_CFG.get("brave_search_lang") or "").strip()
        if country:
            params["country"] = country
        if search_lang:
            params["search_lang"] = search_lang
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                _BRAVE_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": key,
                },
            )
            r.raise_for_status()
            raw = r.json().get("web", {}).get("results", [])
        _count_paid_call("brave")
        items = [{"title": i.get("title", ""), "url": i.get("url", "")} for i in raw]
        log.info("[web_search] Brave '%s': %d results", query[:50], len(items))
        return items
    except (httpx.HTTPError, ValueError) as exc:  # network, HTTP status, bad JSON
        log.warning("[web_search] Brave search failed: %s", exc)
        return []


async def _searxng_search(query: str, count: int) -> list[dict]:
    url = str(_WS_CFG.get("searxng_url", "")).rstrip("/")
    if not url:
        log.warning("[web_search] searxng_url not configured")
        return []
    try:
        params = urlencode(
            {"q": query, "format": "json", "language": "auto", "categories": "general"}
        )
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{url}/search?{params}")
            r.raise_for_status()
            raw = r.json().get("results", [])
        items = [{"title": i.get("title", ""), "url": i.get("url", "")} for i in raw[:count]]
        log.info("[web_search] SearXNG '%s': %d results", query[:50], len(items))
        return items
    except (httpx.HTTPError, ValueError) as exc:  # network, HTTP status, bad JSON
        log.warning("[web_search] SearXNG search failed: %s", exc)
        return []


def _provider_ready(provider: str) -> bool:
    if provider == "brave":
        return bool(_BRAVE_KEY or os.getenv("BRAVE_API_KEY", ""))
    if provider == "searxng":
        return bool(str(_WS_CFG.get("searxng_url") or "").strip())
    return False


async def _search_with_provider(provider: str, query: str, count: int) -> list[dict]:
    if provider == "brave":
        return await _brave_search(query, count)
    if provider == "searxng":
        return await _searxng_search(query, count)
    return []


# ── Page content ───────────────────────────────────────────────────────────


async def _fetch_content(url: str, tier: str = "trusted") -> str:
    """Page text through the shared safe fetcher. A whitelisted page must stay whitelisted on
    every redirect hop; an open-web page may go anywhere public that isn't blocked."""
    allow = web_fetch.is_trusted if tier == "trusted" else web_fetch.not_blocked
    try:
        final_url, text = await web_fetch.fetch_page_text(url, allow=allow)
    except web_fetch.FetchError as exc:
        log.warning("[web_search] Fetch refused/failed for %s: %s", url, exc.reason)
        return ""
    except Exception as exc:
        log.warning("[web_search] Could not fetch %s: %s", url, exc)
        return ""
    if len(text) > _content_max():
        text = text[: _content_max()] + "…"
    log.info("[web_search] Fetched %d chars from %s", len(text), final_url)
    return text


# ── Miss Library synthesis ────────────────────────────────────────────────


async def _ask_library(
    query: str,
    sources: list[dict],
    lang: str = "nb",
    subject: str = "",
    keywords: list[str] | None = None,
    stakes: str = "normal",
) -> str:
    # PLC0415: circular: web_search_adapter ↔ tools.executor_library
    from kaare_core.tools.executor_library import synthesize_web_results  # noqa: PLC0415

    try:
        return await synthesize_web_results(
            query, sources, lang, subject=subject, keywords=keywords, stakes=stakes
        )
    except Exception as exc:
        log.warning("[web_search] Library synthesis failed: %s", exc)
        parts = [
            f"{s['title']} — {s['url']}\n{s['content']}"
            for s in sources
            if s.get("content", "").strip()
        ]
        if parts:
            return t("search_library_raw", lang) + "\n\n---\n\n".join(parts[:2])
        return t("search_library_timeout", lang)


# ── Main function ──────────────────────────────────────────────────────────


async def _official(subject: str, keywords: list[str], lang: str) -> tuple[list[dict], set[str]]:
    """Official pages for the subject (P121 Phase 3) within their own time budget; any failure
    means none. Runs in parallel with the ordinary search."""
    if not subject.strip():
        return [], set()
    try:
        return await asyncio.wait_for(
            find_official_pages(subject.strip(), keywords, lang),
            timeout=web_fetch.setting("official_timeout_s"),
        )
    except TimeoutError:
        log.info("[web_search] official-source lookup for %r timed out", subject)
    except Exception as exc:
        log.warning("[web_search] official-source lookup for %r failed: %s", subject, exc)
    return [], set()


async def _provider_results(chain: list[str], query: str) -> list[dict]:
    results: list[dict] = []
    for provider in chain:
        raw = await _search_with_provider(provider, query, _fetch_count())
        results = _select_sources(raw)
        top = results[: _max_results()]
        log.info(
            "[web_search] %s: %d results → %d candidates, first %d: %d trusted, %d open",
            provider,
            len(raw),
            len(results),
            len(top),
            sum(r["tier"] == "trusted" for r in top),
            sum(r["tier"] == "open" for r in top),
        )
        if results:
            break
    return results


async def web_search(
    query: str,
    user_id: str = "global",
    subject: str = "",
    keywords: list[str] | None = None,
    stakes: str = "normal",
) -> str:
    """
    Search the web, fetch page content, and let Miss Library synthesize the answer.
    Tries primary provider first; falls back if it returns no results. With a subject, the
    subject's official site (Wikidata) is searched in parallel and its pages lead the sources.
    """
    lang = get_lang(user_id)

    if not query or not query.strip():
        return t("search_empty_query", lang)
    if web_fetch.trust_list_state() == "unreadable":
        return t("web_trust_list_unreadable", lang)

    chain = [
        p
        for p in dict.fromkeys([_WS_CFG.get("provider"), _WS_CFG.get("fallback")])
        if p and _provider_ready(p)
    ]
    if not chain:
        return t("web_search_not_configured", lang)

    results, (official_sources, official_hosts) = await asyncio.gather(
        _provider_results(chain, query), _official(subject, keywords or [], lang)
    )

    # Ordinary results that land on the official site are official too; drop pages already
    # fetched by the official lookup.
    official_urls = {s["url"] for s in official_sources}
    for r in results:
        host = web_fetch.normalize_domain(r["url"])
        if any(host == h or host.endswith("." + h) for h in official_hosts):
            r["tier"] = "official"
    results = [r for r in results if r["url"] not in official_urls]

    if not results and not official_sources:
        return t(
            "search_no_results" if not web_fetch.open_web_enabled() else "search_no_results_any",
            lang,
        )

    # Fetch the chosen candidates; when a page can't be read (403, robots, too large …) the
    # next candidate takes its place — at most two rounds, so the total deadline still holds.
    want = _max_results()
    fetched: list[dict] = []
    queue = list(results)
    for _round in range(2):
        batch, queue = queue[: want - len(fetched)], queue[want - len(fetched) :]
        if not batch:
            break
        contents = await asyncio.gather(
            *[_fetch_content(r["url"], r["tier"]) for r in batch], return_exceptions=True
        )
        fetched += [
            {**r, "content": c}
            for r, c in zip(batch, contents, strict=False)
            if isinstance(c, str) and c.strip()
        ]
        if len(fetched) >= want:
            break
    log.info("[web_search] %d of %d candidate pages read", len(fetched), len(results))

    sources = official_sources + fetched

    return await _ask_library(
        query, sources, lang=lang, subject=subject, keywords=keywords, stakes=stakes
    )
