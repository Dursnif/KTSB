"""
Wikidata lookups for subject matching (P121 Phase A).

Kåre names what a question is about (`subject`, e.g. "Wieliczka Salt Mine"); code resolves it
to Wikidata entities and compares:
  - wiki articles by QID (article title → sitelink → QID),
  - web pages by whether they mention one of the subject's labels/aliases.
Pure ID/string comparison — no classifier (cP74). Every failure returns "unknown" (empty), so
the caller simply doesn't mark anything; a lookup never blocks or breaks an answer.

Privacy: only the subject string and article titles leave the machine — never the question,
the user, or the sources.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from adapters import web_fetch

_API = "https://www.wikidata.org/w/api.php"
_TIMEOUT = 4.0
_TTL = 24 * 3600
_MAX_ENTRIES = 2000
_cache: dict[tuple, tuple[float, object]] = {}


def _cache_get(key: tuple):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    return None


def _cache_put(key: tuple, value) -> None:
    if len(_cache) >= _MAX_ENTRIES:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[: _MAX_ENTRIES // 10]:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic(), value)


async def _get(params: dict) -> dict:
    # Wikimedia requires an identifying User-Agent (10 req/min without, 200 with).
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": web_fetch.user_agent()}
    ) as client:
        r = await client.get(_API, params={**params, "format": "json"})
        r.raise_for_status()
        return r.json()


async def subject_entities(subject: str, lang: str, limit: int = 3) -> list[dict]:
    """Top candidate entities for a subject name: [{"qid": str, "names": [str, ...]}].
    names = labels + aliases in all languages. Searched in the user's language, then English."""
    subject = (subject or "").strip()
    if not subject:
        return []
    key = ("subject", subject.lower(), lang)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        qids: list[str] = []
        for search_lang in dict.fromkeys([lang or "en", "en"]):
            data = await _get(
                {
                    "action": "wbsearchentities",
                    "search": subject,
                    "language": search_lang,
                    "uselang": search_lang,
                    "type": "item",
                    "limit": limit,
                }
            )
            qids = [h["id"] for h in data.get("search", []) if h.get("id")]
            if qids:
                break
        if not qids:
            _cache_put(key, [])
            return []
        data = await _get(
            {"action": "wbgetentities", "ids": "|".join(qids), "props": "labels|aliases"}
        )
        out = []
        for qid in qids:
            ent = (data.get("entities") or {}).get(qid) or {}
            names = {v.get("value", "") for v in (ent.get("labels") or {}).values()}
            for aliases in (ent.get("aliases") or {}).values():
                names.update(a.get("value", "") for a in aliases)
            out.append({"qid": qid, "names": sorted(n for n in names if n)})
        _cache_put(key, out)
        return out
    except Exception as e:
        print(f"[wikidata] subject lookup failed ({subject!r}): {e}")
        return []


async def article_qids(site: str, titles: list[str]) -> dict[str, str]:
    """{title: qid} for articles on a wiki site (e.g. "nowiki"). Missing/unknown titles are
    left out. One request for up to 50 titles."""
    want = [t for t in dict.fromkeys(titles) if t]
    if not want:
        return {}
    out: dict[str, str] = {}
    todo = []
    for title in want:
        cached = _cache_get(("article", site, title))
        if cached is not None:
            if cached:
                out[title] = cached
        else:
            todo.append(title)
    if not todo:
        return out
    try:
        data = await _get(
            {
                "action": "wbgetentities",
                "sites": site,
                "titles": "|".join(todo[:50]),
                "props": "sitelinks",
                "sitefilter": site,
            }
        )
        by_title: dict[str, str] = {}
        for qid, ent in (data.get("entities") or {}).items():
            if not qid.startswith("Q"):
                continue  # "-1" etc. = title not found
            link = ((ent.get("sitelinks") or {}).get(site) or {}).get("title", "")
            if link:
                by_title[link] = qid
        for title in todo:
            qid = by_title.get(title) or by_title.get(title.replace("_", " "), "")
            _cache_put(("article", site, title), qid)
            if qid:
                out[title] = qid
    except Exception as e:
        print(f"[wikidata] article lookup failed ({site}): {e}")
    return out


async def official_websites(qid: str) -> list[str]:
    """Official website(s) of an entity (property P856), deprecated statements left out."""
    if not qid:
        return []
    key = ("official", qid)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = await _get({"action": "wbgetclaims", "entity": qid, "property": "P856"})
        urls = [
            c["mainsnak"]["datavalue"]["value"]
            for c in (data.get("claims") or {}).get("P856", [])
            if c.get("rank") != "deprecated" and (c.get("mainsnak") or {}).get("datavalue")
        ]
        urls = [u for u in dict.fromkeys(urls) if isinstance(u, str)]
        _cache_put(key, urls)
        return urls
    except Exception as e:
        print(f"[wikidata] official website lookup failed ({qid}): {e}")
        return []


async def gather_safe(*aws, timeout: float):
    """asyncio.gather with an overall deadline; on timeout every result is None."""
    try:
        return await asyncio.wait_for(asyncio.gather(*aws), timeout=timeout)
    except Exception:
        return [None] * len(aws)
