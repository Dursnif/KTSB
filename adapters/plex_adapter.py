"""Plex Media Server adapter for Kåre."""

import os
import yaml
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kaare_core.tools.i18n import t

_SERVICES_PATH = Path("/kaare/configs/services.yaml")


def _plex_cfg() -> dict:
    return yaml.safe_load(_SERVICES_PATH.read_text())["media"]["plex"]


def _token() -> str:
    # Try environment first (set via systemd EnvironmentFile), then read file directly
    token = os.environ.get("PLEX_TOKEN", "")
    if token:
        return token
    env_path = Path("/kaare/configs/plex.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("PLEX_TOKEN="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token
    raise ValueError("PLEX_TOKEN not found — check configs/plex.env")


def _headers() -> dict:
    return {
        "X-Plex-Token": _token(),
        "Accept": "application/json",
        "X-Plex-Client-Identifier": "kaare",
    }


def _fmt_duration(ms: int | None) -> str:
    if not ms:
        return "?"
    s = ms // 1000
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    if h:
        return f"{h}t {m}m"
    return f"{m}m {s}s"


def _fmt_progress(view_offset: int | None, duration: int | None) -> str:
    if not view_offset or not duration or duration == 0:
        return ""
    pct = int(view_offset / duration * 100)
    elapsed = _fmt_duration(view_offset)
    total = _fmt_duration(duration)
    return f"{elapsed} av {total} ({pct}%)"


def _fmt_timestamp(unix_ts: int | None) -> str:
    if not unix_ts:
        return "?"
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc).astimezone()
    return dt.strftime("%d.%m.%Y %H:%M")


async def get_sessions(lang: str = "nb") -> str:
    """Return formatted string of active Plex playback sessions."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/sessions", headers=_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return t("plex_unreachable", lang, error=exc)

    metadata = data.get("MediaContainer", {}).get("Metadata") or []
    if not metadata:
        return t("plex_nothing_playing", lang)

    lines = [f"Aktive Plex-sesjoner ({len(metadata)}):"]
    for item in metadata:
        user = item.get("User", {}).get("title", "Ukjent bruker")
        device = item.get("Player", {}).get("title", "Ukjent enhet")
        state = item.get("Player", {}).get("state", "")
        state_label = {"playing": "▶ spiller", "paused": "⏸ pauset", "buffering": "⏳ buffrer"}.get(state, state)

        item_type = item.get("type", "")
        if item_type == "episode":
            show = item.get("grandparentTitle", "")
            season = item.get("parentIndex", "?")
            ep = item.get("index", "?")
            title = item.get("title", "")
            content = f"{show} — S{season:02d}E{ep:02d} «{title}»"
        elif item_type == "movie":
            title = item.get("title", "Ukjent film")
            year = item.get("year", "")
            content = f"{title} ({year})" if year else title
        elif item_type == "track":
            artist = item.get("grandparentTitle", "")
            track = item.get("title", "")
            content = f"{artist} — {track}" if artist else track
        else:
            content = item.get("title", "Ukjent")

        progress = _fmt_progress(item.get("viewOffset"), item.get("duration"))
        progress_str = f" [{progress}]" if progress else ""

        lines.append(t("plex_session_line", lang, user=user, device=device,
                       content=content, progress=progress_str, state=state_label))

    return "\n".join(lines)


async def get_history(user: str | None = None, limit: int = 20) -> str:
    """Return formatted watch history, optionally filtered by username."""
    cfg = _plex_cfg()
    url = cfg["url"]
    timeout = cfg.get("timeout", 10)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Fetch accounts for id→name mapping
            acc_resp = await client.get(f"{url}/accounts", headers=_headers())
            acc_resp.raise_for_status()
            accounts: dict[int, str] = {}
            for acc in acc_resp.json().get("MediaContainer", {}).get("Account") or []:
                accounts[acc.get("id", 0)] = acc.get("name") or acc.get("title", "Ukjent")

            params = {"sort": "viewedAt:desc", "limit": limit}
            hist_resp = await client.get(
                f"{url}/status/sessions/history/all",
                headers=_headers(),
                params=params,
            )
            hist_resp.raise_for_status()
            data = hist_resp.json()
    except Exception as exc:
        return f"Kunne ikke hente historikk fra Plex ({exc})."

    metadata = data.get("MediaContainer", {}).get("Metadata") or []
    if not metadata:
        return "Ingen seerhistorikk funnet."

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
            return f"Ingen historikk funnet for «{user}». Kjente brukere: {known}."
        metadata = filtered

    lines = [f"Seerhistorikk{f' for {user}' if user else ''} (siste {len(metadata)}):"]
    for item in metadata:
        acc_id = item.get("accountID", 0)
        acc_name = accounts.get(acc_id, "Ukjent")
        ts = _fmt_timestamp(item.get("viewedAt"))
        device_id = item.get("deviceID")

        item_type = item.get("type", "")
        if item_type == "episode":
            show = item.get("grandparentTitle", "")
            season = item.get("parentIndex", "?")
            ep = item.get("index", "?")
            title = item.get("title", "")
            content = f"{show} S{season:02d}E{ep:02d} «{title}»"
        elif item_type == "movie":
            title = item.get("title", "Ukjent film")
            year = item.get("year", "")
            content = f"{title} ({year})" if year else title
        else:
            content = item.get("title", "Ukjent")

        lines.append(f"  • {ts}  {acc_name}: {content}")

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
            type_label = {
                "show": "Serie", "movie": "Film", "season": "Sesong",
                "episode": "Episode", "artist": "Artist", "album": "Album", "track": "Sang",
            }.get(item.get("type", hub_type), hub_type)

            year_str = f" ({year})" if year else ""
            key_str = f" [id:{rating_key}]" if rating_key else ""
            results.append(f"  • {type_label}: {title}{year_str}{key_str}")

    if not results:
        return f"Ingen resultater for «{query}»."

    return t("plex_search_header", lang, query=query) + "\n" + "\n".join(results)


async def get_libraries() -> str:
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
        return f"Kunne ikke hente biblioteker ({exc})."

    dirs = data.get("MediaContainer", {}).get("Directory") or []
    if not dirs:
        return "Ingen Plex-biblioteker funnet."

    type_label = {"movie": "Film", "show": "TV-serier", "artist": "Musikk", "photo": "Bilder"}
    lines = ["Plex-biblioteker:"]
    for d in dirs:
        t = type_label.get(d.get("type", ""), d.get("type", ""))
        count = d.get("count", "")
        count_str = f" ({count} elementer)" if count else ""
        lines.append(f"  • {d.get('title', '?')} [{t}]{count_str}  [id:{d.get('key', '?')}]")

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

    lines = [f"Tilgjengelige Plex-spillere ({len(devices)}):"]
    for d in devices:
        name = d.get("name", "?")
        product = d.get("product", "")
        platform = d.get("platform", "")
        machine_id = d.get("machineIdentifier", "?")
        platform_str = f" ({product}, {platform})" if product else ""
        lines.append(f"  • {name}{platform_str}  [id:{machine_id}]")

    return "\n".join(lines)


async def play_on_client(client_name_or_id: str, rating_key: str, offset_ms: int = 0, lang: str = "nb") -> str:
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
                return (
                    f"Fant ingen Plex-klient som matcher «{client_name_or_id}». "
                    f"Tilgjengelige: {available}."
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
                offset_str = f" fra {_fmt_duration(offset_ms)}" if offset_ms > 0 else ""
                return t("plex_playing_on", lang, offset=offset_str, client=client_name)

            return f"Plex svarte {play_resp.status_code}: {play_resp.text[:300]}"

    except Exception as exc:
        return f"Feil ved Plex-avspilling: {exc}"


async def get_children(rating_key: str) -> str:
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
        return f"Kunne ikke hente innhold ({exc})."

    container = data.get("MediaContainer", {})
    metadata = container.get("Metadata") or []
    parent_title = container.get("title2") or container.get("title1") or ""

    if not metadata:
        return "Ingen underelementer funnet."

    lines = [f"Innhold for «{parent_title}»:" if parent_title else "Innhold:"]
    for item in metadata:
        item_type = item.get("type", "")
        title = item.get("title", "?")
        key = item.get("ratingKey", "")
        key_str = f" [id:{key}]" if key else ""

        if item_type == "season":
            ep_count = item.get("leafCount", "?")
            seen = item.get("viewedLeafCount", 0)
            seen_str = f" — {seen}/{ep_count} sett" if ep_count != "?" else ""
            lines.append(f"  • {title}{seen_str}{key_str}")
        elif item_type == "episode":
            idx = item.get("index", "?")
            seen_marker = " ✓" if item.get("viewCount", 0) else ""
            duration = _fmt_duration(item.get("duration"))
            lines.append(f"  • E{idx:02d} «{title}» ({duration}){seen_marker}{key_str}")
        else:
            lines.append(f"  • {title}{key_str}")

    return "\n".join(lines)
