"""Plex Media Server adapter for Kåre."""

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from kaare_core.tools.i18n import t

_SERVICES_PATH = Path("/kaare/configs/services.yaml")
_PLEX_ENV_PATH = Path("/kaare/configs/plex.env")  # written by the GUI (Integrations → Plex)

# Active playback lives at /status/sessions — /sessions is an (empty) plugins container (N31).
SESSIONS_PATH = "/status/sessions"


def _plex_cfg() -> dict:
    return yaml.safe_load(_SERVICES_PATH.read_text())["media"]["plex"]


def get_token() -> str:
    """The Plex token: environment first (systemd EnvironmentFile), else the GUI-written plex.env.

    Read on every call, so a token changed in the GUI works without a restart. "" when not set.
    """
    token = os.environ.get("PLEX_TOKEN", "")
    if token:
        return token
    try:
        for line in _PLEX_ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PLEX_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"')
                if token:
                    return token
    except OSError:
        pass
    return ""


def _token() -> str:
    token = get_token()
    if not token:
        raise ValueError("PLEX_TOKEN not found — check configs/plex.env")
    return token


def _headers() -> dict:
    return {
        "X-Plex-Token": _token(),
        "Accept": "application/json",
        "X-Plex-Client-Identifier": "kaare",
    }


def _fmt_duration(ms: int | None, lang: str = "nb") -> str:
    if not ms:
        return "?"
    s = ms // 1000
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    if h:
        return t("plex_dur_hm", lang, h=h, m=m)
    return t("plex_dur_ms", lang, m=m, s=s)


def _fmt_progress(view_offset: int | None, duration: int | None, lang: str = "nb") -> str:
    if not view_offset or not duration or duration == 0:
        return ""
    pct = int(view_offset / duration * 100)
    elapsed = _fmt_duration(view_offset, lang)
    total = _fmt_duration(duration, lang)
    return t("plex_progress", lang, elapsed=elapsed, total=total, pct=pct)


def _fmt_timestamp(unix_ts: int | None) -> str:
    if not unix_ts:
        return "?"
    dt = datetime.fromtimestamp(unix_ts, tz=UTC).astimezone()
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_se(season: object, episode: object) -> str:
    """«S02E05», tolerant of a missing or non-numeric season/episode number."""

    def part(prefix: str, value: object) -> str:
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            return f"{prefix}{int(value):02d}"
        return f"{prefix}?"

    return part("S", season) + part("E", episode)


def _type_label(item_type: str, lang: str) -> str:
    key = f"plex_type_{item_type}"
    label = t(key, lang)
    return item_type if label == key else label


def _content_label(item: dict, lang: str) -> str:
    item_type = item.get("type", "")
    title = item.get("title") or t("plex_unknown_title", lang)
    if item_type == "episode":
        show = item.get("grandparentTitle", "")
        se = fmt_se(item.get("parentIndex"), item.get("index"))
        return f"{show} — {se} «{title}»"
    if item_type == "movie":
        year = item.get("year", "")
        return f"{title} ({year})" if year else title
    if item_type == "track":
        artist = item.get("grandparentTitle", "")
        return f"{artist} — {title}" if artist else title
    return title


async def get_sessions(lang: str = "nb", screen_of: Callable[[str], str] | None = None) -> str:
    """Return formatted string of active Plex playback sessions. `screen_of` maps a player's IP to the KTSB screen
    (node) — Plex names every cast «Chromecast», so without it Kåre cannot tell which TV plays (P140 A2)."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}{SESSIONS_PATH}", headers=_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return t("plex_unreachable", lang, error=exc)

    metadata = data.get("MediaContainer", {}).get("Metadata") or []
    if not metadata:
        return t("plex_nothing_playing", lang)

    screens: dict[str, str] = {}
    if screen_of:
        addresses = {str((m.get("Player") or {}).get("address") or "") for m in metadata} - {""}
        try:
            screens = await asyncio.to_thread(lambda: {a: screen_of(a) for a in addresses})
        except Exception:
            screens = {}
    lines = [t("plex_sessions_header", lang, n=len(metadata))]
    for item in metadata:
        user = (item.get("User") or {}).get("title") or t("plex_unknown_user", lang)
        device = (item.get("Player") or {}).get("title") or t("plex_unknown_device", lang)
        if screen := screens.get(str((item.get("Player") or {}).get("address") or "")):
            device = f"{screen} ({device})"
        state = (item.get("Player") or {}).get("state", "")
        state_key = f"plex_state_{state}"
        state_label = t(state_key, lang)
        if state_label == state_key:
            state_label = state

        progress = _fmt_progress(item.get("viewOffset"), item.get("duration"), lang)
        progress_str = f" [{progress}]" if progress else ""

        lines.append(
            t(
                "plex_session_line",
                lang,
                user=user,
                device=device,
                content=_content_label(item, lang),
                progress=progress_str,
                state=state_label,
            )
        )

    return "\n".join(lines)


async def get_history(user: str | None = None, limit: int = 20, lang: str = "nb") -> str:
    """Return formatted watch history, optionally filtered by username."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)
    unknown = t("plex_unknown_user", lang)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Fetch accounts for id→name mapping
            acc_resp = await client.get(f"{url}/accounts", headers=_headers())
            acc_resp.raise_for_status()
            accounts: dict[int, str] = {}
            for acc in acc_resp.json().get("MediaContainer", {}).get("Account") or []:
                accounts[acc.get("id", 0)] = acc.get("name") or acc.get("title") or unknown

            params = {"sort": "viewedAt:desc", "limit": limit}
            hist_resp = await client.get(
                f"{url}/status/sessions/history/all",
                headers=_headers(),
                params=params,
            )
            hist_resp.raise_for_status()
            data = hist_resp.json()
    except Exception as exc:
        return t("plex_history_failed", lang, error=exc)

    metadata = data.get("MediaContainer", {}).get("Metadata") or []
    if not metadata:
        return t("plex_history_empty", lang)

    # Filter by user if specified
    if user:
        user_lower = user.lower()
        filtered = []
        for item in metadata:
            acc_id = item.get("accountID", 0)
            acc_name = accounts.get(acc_id, "").lower()
            if user_lower in acc_name:
                filtered.append(item)
        if not filtered:
            known = ", ".join(sorted(set(accounts.values())))
            return t("plex_history_no_user", lang, user=user, known=known)
        metadata = filtered

    if user:
        lines = [t("plex_history_header_user", lang, user=user, n=len(metadata))]
    else:
        lines = [t("plex_history_header", lang, n=len(metadata))]
    for item in metadata:
        acc_name = accounts.get(item.get("accountID", 0), unknown)
        ts = _fmt_timestamp(item.get("viewedAt"))
        lines.append(f"  • {ts}  {acc_name}: {_content_label(item, lang)}")
    lines.append(t("plex_history_no_screen", lang))
    return "\n".join(lines)


async def search(query: str, limit: int = 8, lang: str = "nb") -> str:
    """Search Plex library. Returns title, type, year, and ratingKey for follow-up calls."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{url}/hubs/search",
                headers=_headers(),
                params={"query": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return t("plex_search_failed", lang, error=exc)

    hubs = data.get("MediaContainer", {}).get("Hub") or []
    results: list[str] = []

    for hub in hubs:
        hub_type = hub.get("type", "")
        if hub_type not in ("show", "movie", "season", "episode", "artist", "album", "track"):
            continue
        for item in hub.get("Metadata") or []:
            title = item.get("title", "?")
            year = item.get("year", "")
            rating_key = item.get("ratingKey", "")
            type_label = _type_label(item.get("type", hub_type), lang)
            year_str = f" ({year})" if year else ""
            key_str = f" [id:{rating_key}]" if rating_key else ""
            results.append(f"  • {type_label}: {title}{year_str}{key_str}")

    if not results:
        return t("plex_search_no_results", lang, query=query)

    return t("plex_search_header", lang, query=query) + "\n" + "\n".join(results)


async def get_libraries(lang: str = "nb") -> str:
    """Return list of all Plex libraries."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/library/sections", headers=_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return t("plex_libraries_failed", lang, error=exc)

    dirs = data.get("MediaContainer", {}).get("Directory") or []
    if not dirs:
        return t("plex_libraries_empty", lang)

    lines = [t("plex_libraries_header", lang)]
    for d in dirs:
        kind = t(f"plex_lib_{d.get('type', '')}", lang)
        if kind.startswith("plex_lib_"):
            kind = d.get("type", "")
        count = d.get("count", "")
        count_str = t("plex_lib_count", lang, n=count) if count else ""
        lines.append(f"  • {d.get('title', '?')} [{kind}]{count_str}  [id:{d.get('key', '?')}]")

    return "\n".join(lines)


async def get_metadata(rating_key: str) -> dict:
    """Fetch structured metadata for a Plex item (episode, movie, show) by rating key."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{url}/library/metadata/{rating_key}",
            headers=_headers(),
        )
        resp.raise_for_status()
        items = resp.json().get("MediaContainer", {}).get("Metadata") or []
        if not items:
            return {}
        item = items[0]
        return {
            "type": item.get("type", ""),
            "title": item.get("title", ""),
            "show_name": item.get("grandparentTitle", ""),
            "season_number": item.get("parentIndex"),
            "episode_number": item.get("index"),
            "library_name": item.get("librarySectionTitle", ""),
            "year": item.get("year"),
        }


async def get_server_machine_id() -> str:
    """Return the Plex server's machineIdentifier (used for play commands)."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()["MediaContainer"]["machineIdentifier"]


async def get_clients(lang: str = "nb") -> str:
    """Return list of registered Plex clients currently running the Plex app."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/clients", headers=_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return t("plex_unreachable", lang, error=exc)

    devices = data.get("MediaContainer", {}).get("Device") or []
    if not devices:
        return t("plex_no_clients", lang)

    lines = [t("plex_clients_header", lang, n=len(devices))]
    for d in devices:
        name = d.get("name", "?")
        product = d.get("product", "")
        platform = d.get("platform", "")
        machine_id = d.get("machineIdentifier", "?")
        platform_str = f" ({product}, {platform})" if product else ""
        lines.append(f"  • {name}{platform_str}  [id:{machine_id}]")

    return "\n".join(lines)


async def play_on_client(
    client_name_or_id: str, rating_key: str, offset_ms: int = 0, lang: str = "nb"
) -> str:
    """
    Trigger Plex playback on a registered client.

    Matches client by machineIdentifier or partial name (case-insensitive).
    rating_key comes from plex_search or plex_episodes.
    offset_ms is the resume position in milliseconds (0 = from start).
    """
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 15)

    parsed = urlparse(url)
    server_ip = parsed.hostname or "localhost"
    server_port = parsed.port or 32400

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Fetch server machine ID
            server_resp = await client.get(url, headers=_headers())
            server_resp.raise_for_status()
            server_machine_id = server_resp.json()["MediaContainer"]["machineIdentifier"]

            # Fetch available clients
            clients_resp = await client.get(f"{url}/clients", headers=_headers())
            clients_resp.raise_for_status()
            devices = clients_resp.json().get("MediaContainer", {}).get("Device") or []

            # Match by machineIdentifier or partial name
            target = None
            needle = client_name_or_id.lower()
            for d in devices:
                if d.get("machineIdentifier", "").lower() == needle:
                    target = d
                    break
                if needle in d.get("name", "").lower():
                    target = d
                    break

            if not target:
                names = [d.get("name", "?") for d in devices]
                available = ", ".join(names) if names else t("plex_no_clients_hint", lang)
                return t(
                    "plex_client_not_found", lang, client=client_name_or_id, available=available
                )

            client_machine_id = target["machineIdentifier"]
            client_name = target.get("name", client_machine_id)

            play_headers = {
                **_headers(),
                "X-Plex-Target-Client-Identifier": client_machine_id,
            }
            play_params = {
                "key": f"/library/metadata/{rating_key}",
                "offset": str(offset_ms),
                "machineIdentifier": server_machine_id,
                "address": server_ip,
                "port": str(server_port),
                "protocol": "http",
                "commandID": "1",
                "type": "video",
            }

            play_resp = await client.get(
                f"{url}/player/playback/playMedia",
                headers=play_headers,
                params=play_params,
            )

            if play_resp.status_code in (200, 204):
                offset_str = (
                    t("plex_from_offset", lang, offset=_fmt_duration(offset_ms, lang))
                    if offset_ms > 0
                    else ""
                )
                return t("plex_playing_on", lang, offset=offset_str, client=client_name)

            return t(
                "plex_http_status", lang, status=play_resp.status_code, text=play_resp.text[:300]
            )

    except Exception as exc:
        return t("plex_play_error", lang, error=exc)


async def get_children(rating_key: str, lang: str = "nb") -> str:
    """Return seasons (for a show) or episodes (for a season)."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{url}/library/metadata/{rating_key}/children",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return t("plex_children_failed", lang, error=exc)

    container = data.get("MediaContainer", {})
    metadata = container.get("Metadata") or []
    parent_title = container.get("title2") or container.get("title1") or ""

    if not metadata:
        return t("plex_children_empty", lang)

    if parent_title:
        lines = [t("plex_children_header", lang, title=parent_title)]
    else:
        lines = [t("plex_children_header_plain", lang)]
    for item in metadata:
        item_type = item.get("type", "")
        title = item.get("title", "?")
        key = item.get("ratingKey", "")
        key_str = f" [id:{key}]" if key else ""

        if item_type == "season":
            ep_count = item.get("leafCount", "?")
            seen = item.get("viewedLeafCount", 0)
            seen_str = (
                t("plex_season_seen", lang, seen=seen, total=ep_count) if ep_count != "?" else ""
            )
            lines.append(f"  • {title}{seen_str}{key_str}")
        elif item_type == "episode":
            ep = fmt_se(item.get("parentIndex"), item.get("index")).split("E", 1)[1]
            seen_marker = " ✓" if item.get("viewCount", 0) else ""
            duration = _fmt_duration(item.get("duration"), lang)
            lines.append(f"  • E{ep} «{title}» ({duration}){seen_marker}{key_str}")
        else:
            lines.append(f"  • {title}{key_str}")

    return "\n".join(lines)


async def find_item(query: str, season: int | None = None, episode: int | None = None) -> dict:
    """The Plex item a user's words name (P139 phase 4 — so Kåre needs no search round first).

    Show + season + episode → that episode; a film title → the film. Returns {"rating_key", "label", "type"}, or
    {"candidates": [labels]} when the words fit several shows/films, or {"show": label} when a show is named without
    season/episode, or {} when nothing matches."""
    cfg = _plex_cfg()
    url, timeout = cfg["url"], cfg.get("timeout", 10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{url}/hubs/search", headers=_headers(), params={"query": query, "limit": 8})
        resp.raise_for_status()
        hubs = resp.json().get("MediaContainer", {}).get("Hub") or []
        shows = [i for h in hubs if h.get("type") == "show" for i in h.get("Metadata") or []]
        movies = [i for h in hubs if h.get("type") == "movie" for i in h.get("Metadata") or []]
        key = query.strip().lower()
        if season is not None and episode is not None:
            exact = [s for s in shows if str(s.get("title", "")).strip().lower() == key] or shows
            if len(exact) > 1:
                return {"candidates": [f"{s.get('title')} ({s.get('year', '?')})" for s in exact[:6]]}
            if not exact:
                return {}
            show = exact[0]
            resp = await client.get(f"{url}/library/metadata/{show['ratingKey']}/allLeaves", headers=_headers())
            resp.raise_for_status()
            for ep in resp.json().get("MediaContainer", {}).get("Metadata") or []:
                if ep.get("parentIndex") == int(season) and ep.get("index") == int(episode):
                    return {
                        "rating_key": str(ep["ratingKey"]),
                        "label": f"{show.get('title')} {fmt_se(season, episode)} «{ep.get('title', '')}»",
                        "type": "episode",
                    }
            return {}
        exact_movies = [m for m in movies if str(m.get("title", "")).strip().lower() == key]
        exact_shows = [x for x in shows if str(x.get("title", "")).strip().lower() == key]
        if len(exact_movies) == 1 and not exact_shows:
            m = exact_movies[0]
            return {"rating_key": str(m["ratingKey"]), "label": f"{m.get('title')} ({m.get('year', '?')})", "type": "movie"}
        if exact_shows:  # «Futurama» = the show, not the films whose title only contains it
            return {"show": str(exact_shows[0].get("title"))}
        loose = exact_movies or movies
        if len(loose) == 1 and not shows:
            m = loose[0]
            return {"rating_key": str(m["ratingKey"]), "label": f"{m.get('title')} ({m.get('year', '?')})", "type": "movie"}
        if loose or len(shows) > 1:
            return {"candidates": [f"{x.get('title')} ({x.get('year', '?')})" for x in [*shows, *loose][:6]]}
        if shows:
            return {"show": str(shows[0].get("title"))}
        return {}


class PlexUnexpectedReply(ValueError):
    """Plex answered, but without a field KTSB needs (DT04-S05g: a reply without `key` became a bare KeyError, and the
    tool's text then guessed a cause). `missing` names the fields, so the reply can say exactly what was wrong."""

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__(f"Plex answered without: {', '.join(self.missing)}")


def _require(obj: dict, *fields: str) -> None:
    missing = [f for f in fields if obj.get(f) in (None, "")]
    if missing:
        raise PlexUnexpectedReply(missing)


async def cast_session(rating_key: str, offset_s: int = 0, resume: bool = False) -> dict:
    """Everything a Cast device needs to play one Plex item by itself (P139 phase 4): server address, a short-lived
    delegation token (not the server token), machine id, version, a fresh play queue. The username is only handed
    to the Cast receiver — never logged or shown to the model."""
    cfg = _plex_cfg()
    url, timeout = cfg["url"], cfg.get("timeout", 10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        srv = resp.json().get("MediaContainer") or {}
        _require(srv, "machineIdentifier")
        resp = await client.get(f"{url}/library/metadata/{rating_key}", headers=_headers())
        resp.raise_for_status()
        item = (resp.json().get("MediaContainer", {}).get("Metadata") or [{}])[0]
        if not item:
            raise ValueError(f"Plex has no item {rating_key}")
        _require(item, "key")
        list_type = "audio" if item.get("type") in ("track", "album", "artist") else "video"
        resp = await client.post(
            f"{url}/playQueues",
            headers=_headers(),
            params={
                "type": list_type,
                "uri": f"server://{srv['machineIdentifier']}/com.plexapp.plugins.library{item['key']}",
                "shuffle": 0,
                "repeat": 0,
                "continuous": 1,
                "own": 1,
            },
        )
        resp.raise_for_status()
        queue = resp.json().get("MediaContainer") or {}
        _require(queue, "playQueueID")
        queue_id = queue["playQueueID"]
        token = _token()
        try:
            resp = await client.get(
                f"{url}/security/token", headers=_headers(), params={"type": "delegation", "scope": "all"}
            )
            token = resp.json().get("MediaContainer", {}).get("token") or token
        except Exception:
            pass
    if resume and not offset_s and item.get("viewOffset"):
        offset_s = int(item["viewOffset"]) // 1000
    parsed = httpx.URL(url)
    return {
        "protocol": parsed.scheme,
        "address": parsed.host,
        "port": parsed.port or 32400,
        "token": token,
        "machine_id": srv["machineIdentifier"],
        "version": srv.get("version") or "",
        "username": srv.get("myPlexUsername") or "",
        "plex_pass": bool(srv.get("myPlexSubscription")),
        "play_queue_id": int(queue_id),
        "key": item["key"],
        "list_type": list_type,
        "offset": int(offset_s or 0),
    }


async def session_for(rating_key: str) -> dict:
    """The Plex server's own view of a session playing this item: {"state", "offset_s", "player"} or {} (P139 phase 4 —
    the truth check; a stuck Cast receiver says PLAYING while the server sees nothing)."""
    cfg = _plex_cfg()
    async with httpx.AsyncClient(timeout=cfg.get("timeout", 10)) as client:
        resp = await client.get(f"{cfg['url']}/status/sessions", headers=_headers())
        resp.raise_for_status()
        for m in resp.json().get("MediaContainer", {}).get("Metadata") or []:
            if str(m.get("ratingKey")) == str(rating_key):
                player = m.get("Player") or {}
                return {
                    "state": player.get("state") or "",
                    "offset_s": int(m.get("viewOffset") or 0) // 1000,
                    "player": player.get("product") or player.get("title") or "",
                }
    return {}


async def fetch(path: str, params: dict | None = None) -> dict:
    """GET a Plex path → its MediaContainer (raises on HTTP errors). For P140's watch tool."""
    cfg = _plex_cfg()
    async with httpx.AsyncClient(timeout=cfg.get("timeout", 10)) as client:
        resp = await client.get(f"{cfg['url']}{path}", headers=_headers(), params=params or {})
        resp.raise_for_status()
        return resp.json().get("MediaContainer", {}) or {}
