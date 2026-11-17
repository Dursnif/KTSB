"""YouTube search without an API key (P139) — the code finds the video, so Kåre only says what and where.

Reads the public results page (`/results?search_query=`) and its embedded `ytInitialData`: video id, title, channel,
length. No key and no account; 0.7–0.9 s live (2026-10-19). The page format is YouTube's own and can change — on any
failure this returns an empty list and the caller falls back (e.g. opening the search on the screen).
"""

import json
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.youtube.com/results"
TIMEOUT_S = 8.0
_INITIAL_DATA = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def _runs_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return str(node["simpleText"])
    return "".join(str(r.get("text") or "") for r in node.get("runs") or [] if isinstance(r, dict))


def _videos(data: Any, out: list[dict], limit: int) -> None:
    stack = [data]
    while stack and len(out) < limit:
        node = stack.pop(0)
        if isinstance(node, dict):
            v = node.get("videoRenderer")
            if isinstance(v, dict) and VIDEO_ID.fullmatch(str(v.get("videoId") or "")):
                out.append({
                    "id": v["videoId"],
                    "title": _runs_text(v.get("title")),
                    "channel": _runs_text(v.get("ownerText")),
                    "length": _runs_text(v.get("lengthText")),
                })
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def parse_results(html: str, limit: int = 5) -> list[dict]:
    m = _INITIAL_DATA.search(html or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out: list[dict] = []
    _videos(data, out, limit)
    return out


async def search(query: str, limit: int = 5) -> list[dict]:
    """[{id, title, channel, length}] in YouTube's own order, or [] when the search fails."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            r = await client.get(SEARCH_URL, params={"search_query": query}, headers={"Accept-Language": "en"})
        if r.status_code != 200:
            log.info("YouTube search HTTP %s", r.status_code)
            return []
        return parse_results(r.text, limit)
    except Exception as e:
        log.info("YouTube search failed: %s", e)
        return []


def results_link(query: str) -> str:
    return str(httpx.URL(SEARCH_URL, params={"search_query": query.strip()}))


def watch_link(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
