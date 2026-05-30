import json as _json
import subprocess as _sp
import yaml
import httpx
from pathlib import Path
from typing import Any, Dict

from adapters.plex_adapter import (
    get_sessions as _plex_get_sessions,
    get_history as _plex_get_history,
    search as _plex_search,
    get_libraries as _plex_get_libraries,
    get_children as _plex_get_children,
    get_clients as _plex_get_clients,
    play_on_client as _plex_play_on_client,
    get_metadata as _plex_get_metadata,
)
from kaare_core.config import get_service as _svc
from kaare_core.tools.executor_ha import get_ha_token, resolve_node_entity
from kaare_core.tools.i18n import t, get_lang

MEDIA_TOOLS = {"media"}


async def _play_media_via_ha(entity_id: str, content_type: str, content_id: str) -> str:
    ha_url = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text())["home_assistant"]["url"]
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


async def _handle_media(arguments: Dict[str, Any], lang: str = "nb") -> str:
    action = arguments.get("action", "")

    if action == "plex_sessions":
        return await _plex_get_sessions(lang=lang)

    if action == "plex_history":
        return await _plex_get_history(
            user=arguments.get("user"),
            limit=int(arguments.get("limit") or 20),
        )

    if action == "plex_search":
        query = arguments.get("query", "").strip()
        if not query:
            return t("media_no_query", lang)
        return await _plex_search(query, lang=lang)

    if action == "plex_library":
        return await _plex_get_libraries()

    if action == "plex_episodes":
        key = arguments.get("rating_key", "").strip()
        if not key:
            return t("media_no_rating_key", lang)
        return await _plex_get_children(key)

    if action == "plex_clients":
        return await _plex_get_clients(lang=lang)

    if action == "plex_play":
        client = arguments.get("client", "").strip()
        key = arguments.get("rating_key", "").strip()
        if not client:
            return t("media_no_client", lang)
        if not key:
            return t("media_no_rating_key_play", lang)
        offset_s = int(arguments.get("offset") or 0)
        resume = bool(arguments.get("resume", False))

        entity_id = resolve_node_entity(client)
        if not entity_id:
            return t("media_node_not_found", lang, client=client)

        try:
            meta = await _plex_get_metadata(key)
        except Exception as exc:
            return t("media_plex_meta_error", lang, key=key, error=exc)

        if not meta:
            return t("media_no_plex_meta", lang, key=key)

        item_type = meta.get("type", "")
        library_name = meta.get("library_name", "")

        if item_type == "episode":
            content: dict = {"library_name": library_name, "show_name": meta["show_name"]}
            if meta.get("season_number") is not None:
                content["season_number"] = meta["season_number"]
            if meta.get("episode_number") is not None:
                content["episode_number"] = meta["episode_number"]
            ha_type = "episode"
            label = f"{meta['show_name']} S{meta.get('season_number','?'):02d}E{meta.get('episode_number','?'):02d}"
        elif item_type == "movie":
            content = {"library_name": library_name, "title": meta["title"]}
            ha_type = "movie"
            label = meta["title"]
        else:
            return t("media_unsupported_type", lang, media_type=item_type)

        if resume:
            content["resume"] = True
        if offset_s > 0:
            content["offset"] = offset_s

        content_id = f"plex://{_json.dumps(content, ensure_ascii=False)}"

        try:
            result = await _play_media_via_ha(entity_id, ha_type, content_id)
        except Exception as exc:
            return t("media_cast_error", lang, error=exc)

        if result == "ok":
            return t("media_casting", lang, label=label, entity_id=entity_id)
        return t("media_ha_response", lang, result=result)

    if action == "radio_status":
        try:
            result = _sp.run(["mpc", "status"], capture_output=True, text=True, timeout=5)
            if result.returncode != 0 or not result.stdout.strip():
                return t("media_mpd_not_running", lang)
            current = _sp.run(["mpc", "current"], capture_output=True, text=True, timeout=5)
            lines = result.stdout.strip().splitlines()
            status_line = next((l for l in lines if "[" in l), lines[0] if lines else "")
            current_title = current.stdout.strip()
            if current_title:
                return f"Radio: {current_title}\n{status_line}"
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
            _sp.run(["mpd", f"{Path.home()}/.mpdconf"], capture_output=True, timeout=5)
            _sp.run(["mpc", "clear"], capture_output=True, timeout=5)
            _sp.run(["mpc", "add", url], capture_output=True, text=True, timeout=5)
            play_result = _sp.run(["mpc", "play"], capture_output=True, text=True, timeout=5)
            station_name = next(
                (s["name"] for s in _load_radio_stations() if s["url"] == url),
                station_input,
            )
            if play_result.returncode == 0:
                return t("media_playing", lang, station=station_name)
            return t("media_start_failed", lang, station=station_name, error=play_result.stderr.strip())
        except Exception as exc:
            return t("media_radio_start_error", lang, error=exc)

    if action == "radio_stop":
        try:
            _sp.run(["mpc", "stop"], capture_output=True, timeout=5)
            return t("media_radio_stopped", lang)
        except Exception as exc:
            return t("media_radio_stop_error", lang, error=exc)

    if action == "radio_volume":
        vol = arguments.get("volume")
        if vol is None:
            return t("media_no_volume", lang)
        vol = max(0, min(100, int(vol)))
        try:
            _sp.run(["mpc", "volume", str(vol)], capture_output=True, timeout=5)
            return t("media_volume_set", lang, vol=vol)
        except Exception as exc:
            return t("media_radio_volume_error", lang, error=exc)

    return t("media_unknown_action", lang, action=action)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))
    if name == "media":
        return await _handle_media(arguments, lang)
    return f"[executor_media] Unknown tool: {name}"
