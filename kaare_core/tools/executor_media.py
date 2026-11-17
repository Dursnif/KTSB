import asyncio
import json as _json
import re as _re
import shutil
import subprocess as _sp
from pathlib import Path
from typing import Any

import httpx
import yaml

from adapters import nrk_adapter, youtube_adapter
from adapters.plex_adapter import PlexUnexpectedReply
from adapters.plex_adapter import (
    cast_session as _plex_cast_session,
)
from adapters.plex_adapter import (
    find_item as _plex_find_item,
)
from adapters.plex_adapter import (
    fmt_se as _plex_fmt_se,
)
from adapters.plex_adapter import (
    get_children as _plex_get_children,
)
from adapters.plex_adapter import (
    get_clients as _plex_get_clients,
)
from adapters.plex_adapter import (
    get_history as _plex_get_history,
)
from adapters.plex_adapter import (
    get_libraries as _plex_get_libraries,
)
from adapters.plex_adapter import (
    get_metadata as _plex_get_metadata,
)
from adapters.plex_adapter import (
    get_sessions as _plex_get_sessions,
)
from adapters.plex_adapter import (
    search as _plex_search,
)
from adapters.plex_adapter import (
    session_for as _plex_session_for,
)
from kaare_core.config import get_service
from kaare_core.ha import action_truth as truth
from kaare_core.ha.entity_check import entity_status
from kaare_core.media import plex_listener, plex_watch_tool
from kaare_core.service_auth import service_headers
from kaare_core.tools.executor_ha import (
    cast_choices,
    choices_text,
    get_ha_token,
    node_entity,
    node_for_address,
    node_image_targets,
    remote_choices,
    resolve_cast_node,
    resolve_remote_node,
    resolve_screens,
)
from kaare_core.tools.i18n import get_lang, t
from kaare_core.voice import cast_client, channels

MEDIA_TOOLS = {"media"}


async def _available_screens(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Of several candidate players, the ones HA has and that are available; one candidate is returned as it is
    (`_play_media_via_ha` checks it and says what is wrong)."""
    if len(candidates) <= 1:
        return candidates
    try:
        ha_url = (get_service("home_assistant", "url") or "").rstrip("/")
        headers = {"Authorization": f"Bearer {get_ha_token()}"}
    except Exception:
        return candidates
    async with httpx.AsyncClient(timeout=5) as client:
        states = await asyncio.gather(*(entity_status(client, ha_url, headers, eid) for eid, _n in candidates))
    ok = [c for c, st in zip(candidates, states, strict=True) if st not in (truth.NOT_FOUND, truth.UNAVAILABLE)]
    return ok or candidates[:0]


async def _play_media_via_ha(entity_id: str, content_type: str, content_id: str) -> str:
    ha_url = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text())["home_assistant"][
        "url"
    ]
    headers = {
        "Authorization": f"Bearer {get_ha_token()}",
        "Content-Type": "application/json",
    }
    payload = {
        "entity_id": entity_id,
        "media_content_type": content_type,
        "media_content_id": content_id,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        # N26: HA answers 200 for a player it does not have — look it up first.
        status = await entity_status(client, ha_url, headers, entity_id)
        if status in (truth.NOT_FOUND, truth.UNAVAILABLE):
            return status
        resp = await client.post(
            f"{ha_url}/api/services/media_player/play_media",
            headers=headers,
            json=payload,
        )
        if resp.status_code in (200, 201):
            return "ok"
        return f"HA {resp.status_code}: {resp.text[:200]}"


def _load_radio_stations() -> list[dict]:
    path = Path("/kaare/configs/radio_stations.yaml")
    try:
        return yaml.safe_load(path.read_text()).get("stations", [])
    except Exception:
        return []


def _resolve_station(name_or_url: str) -> str | None:
    if name_or_url.startswith(("http://", "https://")):
        return name_or_url
    needle = name_or_url.lower().strip()
    for station in _load_radio_stations():
        if needle == station.get("name", "").lower():
            return station["url"]
        for alias in station.get("aliases", []):
            if needle == alias.lower():
                return station["url"]
    return None


def _on_screen(after: dict, lang: str) -> str:
    """What the Cast device says is on it now (N26): app, title, player state."""
    parts = [str(x) for x in (after.get("app"), after.get("title")) if x]
    if after.get("state") and (parts or str(after["state"]).upper() not in ("UNKNOWN", "IDLE")):
        parts.append(str(after["state"]).lower())
    return ", ".join(parts) or t("media_cast_nothing", lang)


def _ask_which(client: str, lang: str, remote: bool = False) -> str:
    """When the words fit several screens (a room with a Nest Hub and a TV) — ask instead of guessing; "" otherwise."""
    if not client:
        return ""
    choices = remote_choices(client) if remote else cast_choices(client)
    return t("media_target_ambiguous", lang, client=client, choices=choices_text(choices)) if choices else ""


async def _the_one_playing(client: str) -> tuple[str, dict] | None:
    """Several screens fit, but only one has something running — that is the one to stop (P139 live test)."""
    choices = cast_choices(client)
    if not choices:
        return None
    states = await asyncio.gather(*(cast_client.call("/cast/status", {"host": str(cfg["host"]).strip()}, timeout=15)
                                    for _nid, cfg in choices))
    busy = [c for c, st in zip(choices, states, strict=True) if st.get("ok") and not _idle_app(st.get("after") or {})]
    return busy[0] if len(busy) == 1 else None


def _idle_app(after: dict) -> bool:
    return not after.get("app_id") or after.get("app") in ("Backdrop",)


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# Cast actions on what already plays: bridge route (own Cast) and the HA service used as the fallback (D7).
# P139 reopened #6 added pause/play — a Nest Hub without HA can be paused too.
_CAST_MEDIA = {
    "cast_stop": ("/cast/stop", "media_stop"),
    "cast_pause": ("/cast/pause", "media_pause"),
    "cast_play": ("/cast/play", "media_play"),
}


async def _handle_cast(action: str, arguments: dict[str, Any], lang: str) -> str:
    """P139 phase 3: NRK TV/Radio and stop on a Cast device — KTSB's own Cast first, HA as the fallback (D6)."""
    client = str(arguments.get("client") or "").strip()
    node = resolve_cast_node(client, str(arguments.get("_source_node") or ""))
    if not node and action in _CAST_MEDIA:
        node = await _the_one_playing(client)
    if not node and (ask := _ask_which(client, lang)):
        return ask
    found = ""
    if action == "nrk_play":
        kind = str(arguments.get("nrk_kind") or "tv").strip().lower()
        media_id = str(arguments.get("nrk_id") or "").strip() if kind in ("tv", "radio") else ""
        if kind not in ("tv", "radio") or not _re.fullmatch(r"[A-Za-z0-9_\- æøåÆØÅ]{1,80}", media_id):
            return t("media_nrk_bad_args", lang)
        if kind == "tv":
            # The code finds what to play (a live channel, a programme id, a series' newest or given episode) — Kåre
            # gives the name, no web search (P139, chat test 2026-10-19: «dagens Dagsrevyen»).
            hit = await nrk_adapter.resolve_tv(media_id, _int_or_none(arguments.get("season")),
                                               _int_or_none(arguments.get("episode")))
            if not hit:
                return t("media_nrk_not_found", lang, name=media_id)
            if hit["kind"] == "needs_episode":
                return t("media_nrk_needs_episode", lang, name=media_id, seasons=hit.get("seasons") or "?")
            if hit["kind"] == "no_episode":
                return t("media_nrk_no_episode", lang, name=media_id, season=arguments.get("season"),
                         episode=arguments.get("episode"))
            media_id = hit["id"]
            found = " — ".join(x for x in (hit.get("title"), hit.get("subtitle")) if x)
        else:
            media_id = media_id.lower()
            if not _re.fullmatch(r"[a-z0-9_\-]{1,80}", media_id):
                return t("media_nrk_bad_args", lang)
        app = "nrktv" if kind == "tv" else "nrkradio"
    tried = ""
    if node:
        nid, cfg = node
        host = str(cfg["host"]).strip()
        if action == "nrk_play":
            res = await cast_client.call("/cast/nrk", {"host": host, "kind": kind, "media_id": media_id})
        else:
            res = await cast_client.call(_CAST_MEDIA[action][0], {"host": host})
        if res.get("ok"):
            return t(f"media_{action}_ok", lang, target=nid, via=t("media_via_own_cast", lang),
                     now=_on_screen(res.get("after") or {}, lang)) + (
                (" " + t("media_nrk_found", lang, found=found)) if found else "")
        tried = f"{nid}: {res.get('error') or 'failed'}"
        entity = str(cfg.get("entity_id") or "").strip()
    else:
        entity = ""
    if not entity and client:
        found, other_room = resolve_screens(client)
        entity = found[0][0] if len(found) == 1 and not other_room else ""
    if not entity:
        if tried:
            return t("media_cast_failed", lang, tried=tried)
        return t("media_cast_no_target", lang, client=client or "—")
    # HA fallback: its Cast integration starts the same apps (media_content_type «cast», app_name + media_id).
    if action == "nrk_play":
        result = await _play_media_via_ha(entity, "cast", _json.dumps({"app_name": app, "media_id": media_id}))
    else:
        result = await _ha_service_via_gateway(entity, _CAST_MEDIA[action][1])
    if result == "ok":
        key = "media_cast_ha_after_fail" if tried else "media_cast_ha"
        return t(key, lang, target=entity, tried=tried, action=t(f"media_{action}_label", lang))
    return t("media_cast_failed", lang, tried="; ".join(x for x in (tried, f"{entity}: {result}") if x))


async def _youtube_play(arguments: dict[str, Any], lang: str) -> str:
    """P139: a YouTube video in one round — the code searches YouTube (no key), picks the top hit (or the given
    youtube_id) and plays it: KTSB's own Cast first (Nest screens, Chromecast, Google TV), then an Android TV's own
    remote with the watch link. If the search fails, an Android TV gets the search opened on screen (N26: said so)."""
    query = str(arguments.get("query") or "").strip()
    video_id = str(arguments.get("youtube_id") or "").strip()
    if video_id and not youtube_adapter.VIDEO_ID.fullmatch(video_id):
        video_id = ""
    if not query and not video_id:
        return t("media_yt_no_query", lang)
    client = str(arguments.get("client") or "").strip()
    source_node = str(arguments.get("_source_node") or "")
    cast = resolve_cast_node(client, source_node)
    if not cast and (ask := _ask_which(client, lang)):
        return ask
    remote = resolve_remote_node(client, source_node)
    if remote and "atv_remote" not in channels.enabled_channels(remote[1]):
        remote = None  # a Samsung remote can open the YouTube app but not a given video
    if cast and remote and remote[0] != cast[0]:
        remote = None
    if not cast and not remote:
        return t("media_yt_no_target", lang, client=client or "—")
    hits = [{"id": video_id, "title": "", "channel": "", "length": ""}] if video_id else \
        await youtube_adapter.search(query, 4)
    if not hits:
        if remote:
            res = await cast_client.call("/tv/act", {"node": remote[0], "action": "app",
                                                     "value": youtube_adapter.results_link(query)}, timeout=40)
            if res.get("ok"):
                return "SENT: " + t("media_yt_search_opened", lang, node=remote[0], query=query)
        return t("media_yt_search_failed", lang, query=query)
    hit = hits[0]
    label = " — ".join(x for x in (hit["title"] or hit["id"], ", ".join(y for y in (hit["channel"], hit["length"]) if y))
                       if x)
    more = "; ".join(f"{h['title']} ({h['channel']}) youtube_id={h['id']}" for h in hits[1:])
    more = ("\n" + t("media_yt_more", lang, more=more)) if more else ""
    tried = []
    if cast:
        nid, cfg = cast
        res = await cast_client.call("/cast/youtube", {"host": str(cfg["host"]).strip(), "video_id": hit["id"]},
                                     timeout=70)
        if res.get("ok"):
            return "OK: " + t("media_yt_ok", lang, video=label, node=nid, via=t("media_via_own_cast", lang),
                              now=_on_screen(res.get("after") or {}, lang)) + more
        tried.append(f"{nid} (Cast): {res.get('error') or 'failed'}")
    if remote:
        nid = remote[0]
        res = await cast_client.call("/tv/act", {"node": nid, "action": "app",
                                                 "value": youtube_adapter.watch_link(hit["id"])}, timeout=40)
        if res.get("ok"):
            app = (res.get("after") or {}).get("app") or "—"
            return "OK: " + t("media_yt_ok", lang, video=label, node=nid, via=t("media_via_tv_remote", lang),
                              now=app) + (("\n" + t("media_yt_tried", lang, tried="; ".join(tried))) if tried else "") \
                + more
        tried.append(f"{nid} (remote): {res.get('error') or res.get('outcome') or 'failed'}")
    return t("media_yt_failed", lang, video=label, tried="; ".join(tried))


# media tool action → (tv_control action, value from which argument)
_TV_ACTIONS = {"tv_power", "tv_volume", "tv_mute", "tv_key", "tv_app"}


async def _handle_tv(action: str, arguments: dict[str, Any], lang: str) -> str:
    """P139 phase 5: a TV's own remote protocol (Android TV Remote / Samsung) — only when the user asked (D3). The reply
    says what the TV reports afterwards (N26)."""
    node = resolve_remote_node(str(arguments.get("client") or ""), str(arguments.get("_source_node") or ""))
    if not node:
        return _ask_which(str(arguments.get("client") or ""), lang, remote=True) or t(
            "media_tv_no_remote", lang, client=arguments.get("client") or "—")
    value = str(arguments.get("tv_value") or "").strip().lower()
    if action == "tv_power":
        if value not in ("on", "off"):
            return t("media_tv_bad_value", lang, action=action, allowed="on, off")
        tv_action = f"power_{value}"
    elif action == "tv_volume":
        if value not in ("up", "down"):
            return t("media_tv_bad_value", lang, action=action, allowed="up, down")
        tv_action = f"volume_{value}"
    elif action == "tv_mute":
        tv_action = "mute"
    elif action == "tv_key":
        tv_action = "key"
    else:
        tv_action = "app"
    nid, _cfg = node
    res = await cast_client.call("/tv/act", {"node": nid, "action": tv_action, "value": value}, timeout=70)
    outcome = str(res.get("outcome") or "")
    after = res.get("after") or {}
    state = ", ".join(
        f"{k} {after[k]}" for k in ("on", "app", "volume", "muted") if after.get(k) not in (None, "")
    ) or "—"
    if outcome in ("confirmed", "already", "accepted"):
        return "OK: " + t(f"media_tv_{outcome}", lang, node=nid, action=t(f"media_tv_act_{tv_action}", lang), state=state)
    if outcome == "not_confirmed":
        return "SENT: " + t("media_tv_not_confirmed", lang, node=nid, action=t(f"media_tv_act_{tv_action}", lang),
                            state=state)
    if outcome == "not_paired":
        return t("media_tv_not_paired", lang, node=nid)
    if outcome == "unreachable":
        return t("media_tv_unreachable", lang, node=nid, error=res.get("error") or "")
    return t("media_tv_failed", lang, node=nid, error=res.get("error") or outcome or "?")


async def _plex_play(arguments: dict[str, Any], lang: str) -> str:
    """P139 phase 4 — one round: the code finds the item (show/season/episode or film, or rating_key), the screen
    (room / TV / node / entity, or where the user is), wakes the TV by starting Plex on it over KTSB's own Cast, and
    falls back to Home Assistant's Plex→Cast path. The reply names the path and what the device reports (N26)."""
    client = str(arguments.get("client") or "").strip()
    source_node = str(arguments.get("_source_node") or "")
    key = str(arguments.get("rating_key") or "").strip()
    offset_s = int(arguments.get("offset") or 0)
    resume = bool(arguments.get("resume", False))
    label = ""
    if not key:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return t("media_no_rating_key_play", lang)
        season, episode = arguments.get("season"), arguments.get("episode")
        try:
            hit = await _plex_find_item(
                query, int(season) if season not in (None, "") else None, int(episode) if episode not in (None, "") else None
            )
        except Exception as exc:
            return t("media_plex_meta_error", lang, key=query, error=exc)
        if hit.get("candidates"):
            return t("media_plex_candidates", lang, query=query, names="; ".join(hit["candidates"]))
        if hit.get("show"):
            return t("media_plex_need_episode", lang, show=hit["show"])
        if not hit.get("rating_key"):
            return t("plex_search_no_results", lang, query=query)
        key, label = hit["rating_key"], hit["label"]

    # Screen: KTSB's own Cast node first, else an HA player (P139 resolve_screens / the input node's image_out).
    node = resolve_cast_node(client, source_node)
    if not node and (ask := _ask_which(client, lang)):
        return ask
    entity_id = str(node[1].get("entity_id") or "").strip() if node else ""
    here = [] if client else node_image_targets(source_node)
    if not node and not entity_id:
        if client:
            found, other_room = resolve_screens(client)
            candidates = await _available_screens(found)
            if candidates and other_room:
                names = ", ".join(f"{name} ({eid})" for eid, name in candidates)
                return t("media_screen_other_room", lang, place=other_room, names=names)
            if len(candidates) > 1:
                names = ", ".join(f"{name} ({eid})" for eid, name in candidates)
                return t("media_several_screens", lang, client=client, names=names)
            entity_id = candidates[0][0] if candidates else ""
        elif here:
            entity_id = node_entity(here[0]) or ""
        if not entity_id:
            return t("media_node_not_found", lang, client=client or (here[0] if here else "—")) if (client or here) \
                else t("media_no_client", lang)

    tried = ""
    if node:
        nid, cfg = node
        try:
            session = await _plex_cast_session(key, offset_s, resume)
            res = await cast_client.call("/cast/plex", {"host": str(cfg["host"]).strip(), "session": session}, timeout=75)
        except PlexUnexpectedReply as exc:  # DT04-S05g: say what Plex lacked, not a bare KeyError
            res = {"ok": False, "error": t("media_plex_unexpected_reply", lang, missing=", ".join(exc.missing))}
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
        if res.get("ok"):
            plex_listener.expect_ktsb_start(key, arguments.get("_user_id") or "", nid)  # P140: source = ktsb
            seen = await _plex_confirm(key)
            if seen.get("state") == "playing":
                return t("media_plex_cast_ok", lang, label=label or key, target=nid,
                         via=t("media_via_own_cast", lang), player=seen.get("player") or "Plex")
            return t("media_plex_not_confirmed", lang, label=label or key, target=nid,
                     state=seen.get("state") or t("media_cast_nothing", lang))
        tried = f"{nid}: {res.get('error') or 'failed'}"
        if not entity_id:
            return t("media_cast_failed", lang, tried=tried)

    # HA fallback: its Plex integration casts with the same Plex Cast app.
    try:
        meta = await _plex_get_metadata(key)
    except Exception as exc:
        return t("media_plex_meta_error", lang, key=key, error=exc)
    if not meta:
        return t("media_no_plex_meta", lang, key=key)
    item_type = meta.get("type", "")
    if item_type == "episode":
        content: dict = {"library_name": meta.get("library_name", ""), "show_name": meta["show_name"]}
        if meta.get("season_number") is not None:
            content["season_number"] = meta["season_number"]
        if meta.get("episode_number") is not None:
            content["episode_number"] = meta["episode_number"]
        ha_type = "episode"
        label = label or f"{meta['show_name']} {_plex_fmt_se(meta.get('season_number'), meta.get('episode_number'))}"
    elif item_type == "movie":
        content = {"library_name": meta.get("library_name", ""), "title": meta["title"]}
        ha_type = "movie"
        label = label or meta["title"]
    else:
        return t("media_unsupported_type", lang, media_type=item_type)
    if resume:
        content["resume"] = True
    if offset_s > 0:
        content["offset"] = offset_s
    try:
        result = await _play_media_via_ha(entity_id, ha_type, f"plex://{_json.dumps(content, ensure_ascii=False)}")
    except Exception as exc:
        return t("media_cast_error", lang, error=exc)
    if result == "ok":
        plex_listener.expect_ktsb_start(key, arguments.get("_user_id") or "")  # P140: source = ktsb
        if tried:
            return t("media_cast_ha_after_fail", lang, target=entity_id, tried=tried, action=label)
        return t("media_casting", lang, label=label, entity_id=entity_id)
    if result == truth.NOT_FOUND:
        return t("ha_outcome_not_found", lang, entity_id=entity_id)
    if result == truth.UNAVAILABLE:
        return t("ha_outcome_unavailable", lang, entity_id=entity_id)
    return t("media_ha_response", lang, result=result)


async def _plex_confirm(rating_key: str, limit: float = 20.0) -> dict:
    """Wait for the Plex server to report this item playing (N26 — the Cast device's PLAYING alone is not proof)."""
    t0 = asyncio.get_running_loop().time()
    seen: dict = {}
    while asyncio.get_running_loop().time() - t0 < limit:
        try:
            seen = await _plex_session_for(rating_key)
        except Exception:
            seen = {}
        if seen.get("state") == "playing":
            return seen
        await asyncio.sleep(2)
    return seen


async def _ha_service_via_gateway(entity_id: str, action: str) -> str:
    """One media_player action through the HA gateway's checked path (P139 phase 1); "ok" when HA shows it."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{get_service('internal', 'ha_gateway')}/api/ha_apply",
                json={"action": action, "entity_id": entity_id, "params": {}, "source": "kare-tool"},
                headers=service_headers(),
            )
        data = r.json()
    except Exception as e:
        return str(e)
    return "ok" if data.get("outcome") in truth.DONE else str(data.get("outcome") or data.get("status"))


async def _handle_media(arguments: dict[str, Any], lang: str = "nb") -> str:
    action = arguments.get("action", "")

    if action == "plex_sessions":
        return await _plex_get_sessions(lang=lang, screen_of=node_for_address)

    if action == "plex_history":
        return await _plex_get_history(
            user=arguments.get("user"),
            limit=int(arguments.get("limit") or 20),
            lang=lang,
        )

    if action == "plex_search":
        query = arguments.get("query", "").strip()
        if not query:
            return t("media_no_query", lang)
        return await _plex_search(query, lang=lang)

    if action == "plex_library":
        return await _plex_get_libraries(lang=lang)

    if action == "plex_episodes":
        key = arguments.get("rating_key", "").strip()
        if not key:
            return t("media_no_rating_key", lang)
        return await _plex_get_children(key, lang=lang)

    if action == "plex_clients":
        return await _plex_get_clients(lang=lang)

    # P140: KTSB's own watch record next to what Plex claims, for the asking user
    user_id = arguments.get("_user_id") or "global"
    if action == "plex_continue":
        return await plex_watch_tool.continue_text(
            user_id,
            (arguments.get("query") or "").strip(),
            lang,
            client=(arguments.get("client") or "").strip(),
        )
    if action == "plex_skipped":
        return await plex_watch_tool.skipped_text(
            user_id, (arguments.get("query") or "").strip(), lang
        )
    if action == "plex_mark":
        return await plex_watch_tool.mark_text(
            user_id,
            (arguments.get("rating_key") or "").strip(),
            (arguments.get("status") or "").strip(),
            lang,
        )

    if action == "plex_play":
        return await _plex_play(arguments, lang)

    if action == "youtube_play":
        return await _youtube_play(arguments, lang)

    if action == "nrk_play" or action in _CAST_MEDIA:
        return await _handle_cast(action, arguments, lang)

    if action in _TV_ACTIONS:
        return await _handle_tv(action, arguments, lang)

    # No MPD client on this install (a Docker image has none — DT04 R5a): say so instead of an «[Errno 2]» text
    if action in ("radio_status", "radio_play", "radio_stop", "radio_volume") and shutil.which("mpc") is None:
        return t("media_mpd_missing", lang)

    if action == "radio_status":
        try:
            result = await asyncio.to_thread(
                _sp.run, ["mpc", "status"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                return t("media_mpd_not_running", lang)
            current = await asyncio.to_thread(
                _sp.run, ["mpc", "current"], capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().splitlines()
            status_line = next((ln for ln in lines if "[" in ln), lines[0] if lines else "")
            current_title = current.stdout.strip()
            volume_str = ""
            vol_match = _re.search(r"volume:\s*(\d+)%", result.stdout)
            if vol_match:
                volume_str = f" (volume: {vol_match.group(1)}%)"
            if current_title:
                return f"Radio: {current_title}{volume_str}\n{status_line}"
            return "\n".join(lines)
        except Exception as exc:
            return t("media_radio_status_error", lang, error=exc)

    if action == "radio_play":
        station_input = arguments.get("station", "").strip()
        if not station_input:
            return t("media_no_station", lang)
        url = _resolve_station(station_input)
        if not url:
            known = ", ".join(s["name"] for s in _load_radio_stations())
            return t("media_unknown_station", lang, station=station_input, known=known)
        try:
            await asyncio.to_thread(
                _sp.run, ["mpd", f"{Path.home()}/.mpdconf"], capture_output=True, timeout=5
            )
            await asyncio.to_thread(_sp.run, ["mpc", "clear"], capture_output=True, timeout=5)
            await asyncio.to_thread(
                _sp.run, ["mpc", "add", url], capture_output=True, text=True, timeout=5
            )
            play_result = await asyncio.to_thread(
                _sp.run, ["mpc", "play"], capture_output=True, text=True, timeout=5
            )
            station_name = next(
                (s["name"] for s in _load_radio_stations() if s["url"] == url),
                station_input,
            )
            if play_result.returncode == 0:
                return t("media_playing", lang, station=station_name)
            return t(
                "media_start_failed", lang, station=station_name, error=play_result.stderr.strip()
            )
        except Exception as exc:
            return t("media_radio_start_error", lang, error=exc)

    if action == "radio_stop":
        try:
            await asyncio.to_thread(_sp.run, ["mpc", "stop"], capture_output=True, timeout=5)
            return t("media_radio_stopped", lang)
        except Exception as exc:
            return t("media_radio_stop_error", lang, error=exc)

    if action == "radio_volume":
        vol = arguments.get("volume")
        if vol is None:
            return t("media_no_volume", lang)
        vol = max(0, min(100, int(vol)))
        try:
            await asyncio.to_thread(
                _sp.run, ["mpc", "volume", str(vol)], capture_output=True, timeout=5
            )
            return t("media_volume_set", lang, vol=vol)
        except Exception as exc:
            return t("media_radio_volume_error", lang, error=exc)

    return t("media_unknown_action", lang, action=action)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))
    if name == "media":
        return await _handle_media(arguments, lang)
    return f"[executor_media] Unknown tool: {name}"
