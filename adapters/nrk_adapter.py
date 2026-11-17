"""NRK TV lookup without a key (P139) — the code turns «dagens Dagsrevyen» into a playable id, so Kåre does not
web-search for it.

NRK's open programme API (psapi.nrk.no, ~0.2 s, 2026-10-19):
- `/playback/manifest/channel/<id>` answers 200 for a live channel (nrk1, nrk2 …),
- `/playback/metadata/program/<id>` gives title + playability for a programme/episode id (`NNFA19101926`),
- `/tv/catalog/series/<slug>`: a news-type series (Dagsrevyen, Debatten) lists its episodes newest first — the first
  can be tomorrow's («coming»), so the first «available» one is taken; a sequential series (Side om side) has seasons,
  `/seasons/<n>` lists the episodes by number,
- `/search?q=` finds a series from free text («side om side» → `side-om-side`).
Any failure gives None and the caller says so; nothing here raises.
"""

import logging
import re

import httpx

log = logging.getLogger(__name__)

API = "https://psapi.nrk.no"
TIMEOUT_S = 6.0
_ID = re.compile(r"[A-Za-z0-9_\-]{1,80}")


def slug_of(text: str) -> str:
    s = re.sub(r"[^a-z0-9æøå]+", "-", (text or "").strip().lower()).strip("-")
    return s.replace("æ", "ae").replace("ø", "o").replace("å", "a")


async def _get(client: httpx.AsyncClient, path: str, **params) -> dict | None:
    try:
        r = await client.get(f"{API}{path}", params=params or None)
    except Exception as e:
        log.info("NRK API %s failed: %s", path, e)
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _available(ep: dict) -> bool:
    av = ep.get("availability")
    return isinstance(av, dict) and av.get("status") == "available" and bool(_ID.fullmatch(str(ep.get("prfId") or "")))


async def _titles(client: httpx.AsyncClient, prf_id: str) -> tuple[str, str]:
    meta = await _get(client, f"/playback/metadata/program/{prf_id}") or {}
    titles = (meta.get("preplay") or {}).get("titles") or {}
    return str(titles.get("title") or ""), str(titles.get("subtitle") or "")


async def _from_series(client: httpx.AsyncClient, slug: str, season: int | None, episode: int | None) -> dict | None:
    data = await _get(client, f"/tv/catalog/series/{slug}")
    if not data:
        return None
    emb = data.get("_embedded") or {}
    pick: dict | None = None
    if isinstance(emb.get("instalments"), dict):  # news-type: newest first
        episodes = (emb["instalments"].get("_embedded") or {}).get("instalments") or []
        pick = next((ep for ep in episodes if _available(ep)), None)
    else:  # sequential: by season and episode number
        seasons = emb.get("seasons") or []
        if not season or not episode:
            return {"id": "", "kind": "needs_episode", "series": slug, "title": slug, "subtitle": "",
                    "seasons": len(seasons)}
        page = await _get(client, f"/tv/catalog/series/{slug}/seasons/{int(season)}") or {}
        episodes = (page.get("_embedded") or {}).get("episodes") or []
        pick = next((ep for ep in episodes if ep.get("sequenceNumber") == int(episode) and _available(ep)), None)
        if pick is None:
            return {"id": "", "kind": "no_episode", "series": slug, "title": slug, "subtitle": "",
                    "seasons": len(seasons)}
    if pick is None:
        return None
    title, subtitle = await _titles(client, pick["prfId"])
    return {"id": pick["prfId"], "kind": "episode", "series": slug, "title": title or slug,
            "subtitle": subtitle or (pick.get("titles") or {}).get("title") or ""}


async def resolve_tv(nrk_id: str, season: int | None = None, episode: int | None = None) -> dict | None:
    """A live channel, a programme id, or a series → {id, kind, title, subtitle}. kind: live / programme / episode
    (news series: the newest available; sequential: season + episode) / needs_episode / no_episode (id empty).
    None when NRK has nothing for it."""
    raw = (nrk_id or "").strip()
    if not raw:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
        low = raw.lower()
        if _ID.fullmatch(low) and await _get(client, f"/playback/manifest/channel/{low}") is not None:
            return {"id": low, "kind": "live", "title": low.upper(), "subtitle": ""}
        if _ID.fullmatch(raw):
            meta = await _get(client, f"/playback/metadata/program/{raw}")
            if meta and meta.get("playability") == "playable":
                titles = (meta.get("preplay") or {}).get("titles") or {}
                return {"id": raw, "kind": "programme", "title": titles.get("title") or raw,
                        "subtitle": titles.get("subtitle") or ""}
        slug = slug_of(raw)
        hit = await _from_series(client, slug, season, episode) if slug else None
        if hit:
            return hit
        # free text: only a series whose title is the words given (NRK's search also returns loose matches)
        found = await _get(client, "/search", q=raw)
        for h in (found or {}).get("hits") or []:
            body = h.get("hit") or {}
            url = str(body.get("url") or "")
            if h.get("type") == "serie" and url.startswith("serie/") and slug_of(str(body.get("title") or "")) == slug:
                hit = await _from_series(client, url.split("/", 1)[1], season, episode)
                if hit:
                    return hit
    return None
