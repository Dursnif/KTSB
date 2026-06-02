# /kaare/adapters/web_search_adapter.py
"""
Web search adapter — multi-provider: DDG (default), SearXNG, Brave.

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
from urllib.parse import urlencode, urlparse

import httpx
import yaml

from kaare_core.config import get_service
from kaare_core.tools.i18n import t, get_lang

log = logging.getLogger(__name__)

_BRAVE_URL    = "https://api.search.brave.com/res/v1/web/search"
_TRUSTED_PATH = Path("/kaare/configs/trusted_sources.yaml")
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_SERVICES_PATH = Path("/kaare/configs/services.yaml")

_BRAVE_KEY    = os.getenv("BRAVE_API_KEY", "")


def _load_websearch_config() -> dict:
    """Read web_search config from services.yaml; fall back to settings.yaml websearch: for migration."""
    defaults = {
        "provider": "ddg",
        "fallback": "ddg",
        "fetch_count": 10,
        "max_results": 3,
        "content_max": 3000,
        "searxng_url": "",
        "brave_country": "NO",
        "brave_search_lang": "nb",
    }
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        ws = svc.get("web_search", {})
        if ws:
            return {**defaults, **ws}
        # Migration: fall back to settings.yaml if services.yaml has no web_search section yet
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return {**defaults, **s.get("websearch", {})}
    except Exception:
        return defaults


_WS_CFG = _load_websearch_config()


def _fetch_count() -> int:
    return int(_WS_CFG.get("fetch_count", 10))

def _max_results() -> int:
    return int(_WS_CFG.get("max_results", 3))

def _content_max() -> int:
    return int(_WS_CFG.get("content_max", 3000))


# ── Trust filter ───────────────────────────────────────────────────────────

def _load_trusted_domains() -> list[str]:
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8"))
        domains = []
        for category in data.get("sources", {}).values():
            for entry in category:
                domains.append(entry["domain"].lower().lstrip("www."))
        log.info("[web_search] %d trusted domains loaded", len(domains))
        return domains
    except Exception as exc:
        log.warning("[web_search] Could not load trusted_sources.yaml: %s", exc)
        return []


_TRUSTED: list[str] = _load_trusted_domains()


def reload_config() -> None:
    """Reload trusted domains and websearch config (called by /api/reload)."""
    global _TRUSTED, _WS_CFG, _BRAVE_KEY
    _TRUSTED = _load_trusted_domains()
    _WS_CFG = _load_websearch_config()
    _BRAVE_KEY = os.getenv("BRAVE_API_KEY", "")
    log.info("[web_search] Config reloaded: provider=%s, %d trusted domains", _WS_CFG.get("provider"), len(_TRUSTED))


def _is_trusted(url: str) -> bool:
    if not _TRUSTED:
        return True
    try:
        host = (urlparse(url).hostname or "").lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in _TRUSTED)
    except Exception:
        return False


def _filter_trusted(raw: list[dict]) -> list[dict]:
    """Filter a list of {title, url} dicts to trusted domains, up to max_results."""
    results, skipped = [], 0
    for item in raw:
        url = item.get("url", "")
        if not _is_trusted(url):
            skipped += 1
            continue
        results.append({"title": item.get("title", ""), "url": url})
        if len(results) >= _max_results():
            break
    return results


# ── Providers ──────────────────────────────────────────────────────────────

async def _brave_search(query: str, count: int) -> list[dict]:
    key = _BRAVE_KEY or os.getenv("BRAVE_API_KEY", "")
    if not key:
        log.warning("[web_search] BRAVE_API_KEY ikke satt")
        return []
    try:
        params = {
            "q": query,
            "count": count,
            "country": str(_WS_CFG.get("brave_country", "NO")),
            "search_lang": str(_WS_CFG.get("brave_search_lang", "nb")),
        }
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
        items = [{"title": i.get("title", ""), "url": i.get("url", "")} for i in raw]
        results = _filter_trusted(items)
        log.info("[web_search] Brave '%s': %d treff", query[:50], len(results))
        return results
    except httpx.RequestError as exc:
        log.warning("[web_search] Brave-søk feilet: %s", exc)
        return []


async def _ddg_search(query: str, count: int) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: DDGS().text(query, max_results=count))
        items = [{"title": r.get("title", ""), "url": r.get("href", "")} for r in (raw or [])]
        results = _filter_trusted(items)
        log.info("[web_search] DDG '%s': %d treff", query[:50], len(results))
        return results
    except ImportError:
        log.error("[web_search] duckduckgo-search ikke installert: pip install duckduckgo-search")
        return []
    except Exception as exc:
        log.warning("[web_search] DDG-søk feilet: %s", exc)
        return []


async def _searxng_search(query: str, count: int) -> list[dict]:
    url = str(_WS_CFG.get("searxng_url", "")).rstrip("/")
    if not url:
        log.warning("[web_search] searxng_url ikke konfigurert")
        return []
    try:
        params = urlencode({"q": query, "format": "json", "language": "auto", "categories": "general"})
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{url}/search?{params}")
            r.raise_for_status()
            raw = r.json().get("results", [])
        items = [{"title": i.get("title", ""), "url": i.get("url", "")} for i in raw[:count]]
        results = _filter_trusted(items)
        log.info("[web_search] SearXNG '%s': %d treff", query[:50], len(results))
        return results
    except httpx.RequestError as exc:
        log.warning("[web_search] SearXNG-søk feilet: %s", exc)
        return []


async def _search_with_provider(provider: str, query: str, count: int) -> list[dict]:
    if provider == "brave":
        return await _brave_search(query, count)
    if provider == "searxng":
        return await _searxng_search(query, count)
    return await _ddg_search(query, count)


# ── Page content ───────────────────────────────────────────────────────────

async def _fetch_content(url: str) -> str:
    try:
        import trafilatura
        async with httpx.AsyncClient(
            timeout=6.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Kaare/1.0)"},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                log.warning("[web_search] Henting av %s ga status %d", url, r.status_code)
                return ""
            html = r.text

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        ) or ""

        text = text.strip()
        if len(text) > _content_max():
            text = text[:_content_max()] + "…"

        log.info("[web_search] Hentet %d tegn fra %s", len(text), url)
        return text
    except Exception as exc:
        log.warning("[web_search] Kunne ikke hente %s: %s", url, exc)
        return ""


# ── Miss Library synthesis ────────────────────────────────────────────────

async def _ask_library(query: str, sources: list[dict], lang: str = "nb") -> str:
    from kaare_core.tools.executor_library import synthesize_web_results
    try:
        return await synthesize_web_results(query, sources, lang)
    except Exception as exc:
        log.warning("[web_search] Library synthesis failed: %s", exc)
        parts = [
            f"{s['title']} — {s['url']}\n{s['content']}"
            for s in sources if s.get("content", "").strip()
        ]
        if parts:
            return t("search_library_raw", lang) + "\n\n---\n\n".join(parts[:2])
        return t("search_library_timeout", lang)


# ── Main function ──────────────────────────────────────────────────────────

async def søk_nett(query: str, user_id: str = "global") -> str:
    """
    Search the web, fetch page content, and let Miss Library synthesize the answer.
    Tries primary provider first; falls back if it returns no results.
    """
    lang = get_lang(user_id)

    if not query or not query.strip():
        return t("search_empty_query", lang)

    primary = str(_WS_CFG.get("provider", "ddg"))
    fallback = str(_WS_CFG.get("fallback", "ddg"))
    count = _fetch_count()

    results = await _search_with_provider(primary, query, count)

    if not results and fallback != primary:
        log.info("[web_search] Primary '%s' ga 0 treff — prøver fallback '%s'", primary, fallback)
        results = await _search_with_provider(fallback, query, count)

    if not results:
        if primary == "brave" and not (_BRAVE_KEY or os.getenv("BRAVE_API_KEY", "")):
            return t("search_disabled", lang)
        return t("search_no_results", lang)

    contents = await asyncio.gather(
        *[_fetch_content(r["url"]) for r in results],
        return_exceptions=True,
    )

    sources = [
        {"title": r["title"], "url": r["url"], "content": c if isinstance(c, str) else ""}
        for r, c in zip(results, contents)
    ]

    return await _ask_library(query, sources, lang=lang)
