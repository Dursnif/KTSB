"""
Tool executor for Kåre's tool calls. Returns results as plain text
that is fed back to the LLM in the next round.
"""

import json as _json
import logging
import time as _time
import yaml

import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

from adapters.weather_adapter import fetch_weather as _fetch_weather
from kaare_core.config import get_service as _svc, get_llm_config as _llm_cfg
from kaare_core.tools.i18n import t, get_lang
from kaare_core.tools.notisblokk import skriv_notat, les_notater, slett_notat, tøm_notater
from kaare_core.tools.lister import (
    handle_legg_til, handle_les, handle_merk_kjøpt, handle_slett, handle_tøm_kjøpte, handle_tøm,
    huske_husk, huske_les, huske_ferdig, huske_slett, huske_tøm,
    kare_husk, kare_les, kare_ferdig, kare_tøm,
)
from kaare_core.tools.timer_service import sett_timer, avbryt_timer, liste_timere
from kaare_core.tools import (
    executor_world, executor_memory, executor_personality,
    executor_ha, executor_media, executor_library,
    executor_agents, executor_system, executor_camera,
)

logger = logging.getLogger(__name__)

_VOICE_BRIDGE_URL = _svc("internal", "voice_bridge")
_TOOL_LOG         = Path("/kaare/logs/tool_calls.log")
_SETTINGS_PATH    = Path("/kaare/configs/settings.yaml")


def _local_tz() -> ZoneInfo:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text())
        loc = cfg.get("location") or cfg.get("lokasjon", {})
        return ZoneInfo(loc.get("timezone", "Europe/Oslo"))
    except Exception:
        return ZoneInfo("Europe/Oslo")


def _fmt_ts_local(ts_raw: str) -> str:
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")


def _log_tool(name: str, arguments: Dict, result: str, duration_ms: int, source: str = "kare"):
    try:
        safe_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "rid": arguments.get("_rid", ""),
            "source": source,
            "tool": name,
            "args": safe_args,
            "result_preview": str(result)[:120],
            "duration_ms": duration_ms,
        }
        with open(_TOOL_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _dispatch(name: str, arguments: Dict[str, Any]) -> str:
    """Dispatcher — kalles fra execute_tool. Returnerer alltid streng."""
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in executor_ha.HA_TOOLS:
        return await executor_ha.dispatch(name, arguments)

    if name in executor_library.LIBRARY_TOOLS:
        return await executor_library.dispatch(name, arguments)

    if name == "timer":
        action = arguments.get("action", "")
        if action == "clock":
            now = datetime.now()
            return t("timer_clock", lang, time=now.strftime('%H:%M'), date=now.strftime('%d.%m.%Y'))
        if action == "set":
            return sett_timer(
                prompt=arguments.get("prompt", ""),
                in_seconds=int(arguments.get("in_seconds", 0)),
                notify=bool(arguments.get("notify", True)),
                repeat=arguments.get("repeat") or None,
                at_time=arguments.get("at_time") or None,
                lang=lang,
            )
        if action == "cancel":
            return avbryt_timer(arguments.get("timer_id", ""), lang=lang)
        if action == "list":
            return liste_timere(lang=lang)
        return f"Unknown action for timer: '{action}'. Valid: clock, set, cancel, list."

    if name in executor_memory.MEMORY_TOOLS:
        return await executor_memory.dispatch(name, arguments)

    if name in executor_agents.AGENT_TOOLS:
        return await executor_agents.dispatch(name, arguments)

    if name in executor_personality.PERSONALITY_TOOLS:
        return await executor_personality.dispatch(name, arguments)

    if name in executor_world.WORLD_TOOLS:
        return await executor_world.dispatch(name, arguments)

    if name == "notat":
        action = arguments.get("action", "")
        liste = arguments.get("list_name", arguments.get("liste", "arkitekt"))
        user_id = arguments.get("_user_id", "global")

        if liste == "handle":
            if action in ("write", "add"):
                return handle_legg_til(
                    tekst=(arguments.get("text") or arguments.get("tekst") or ""),
                    mengde=(arguments.get("quantity") or arguments.get("mengde") or ""),
                    enhet=(arguments.get("unit") or arguments.get("enhet") or ""),
                    lagt_til_av=user_id,
                )
            if action == "read":
                return handle_les()
            if action == "mark_bought":
                return handle_merk_kjøpt((arguments.get("note_id") or arguments.get("notat_id") or ""))
            if action == "delete":
                return handle_slett((arguments.get("note_id") or arguments.get("notat_id") or ""))
            if action in ("clear", "clear_bought"):
                return handle_tøm_kjøpte()
            if action == "clear_all":
                return handle_tøm()
            return f"Unknown action for handle-liste: '{action}'. Valid: write, read, mark_bought, delete, clear, clear_all."

        if liste == "huske":
            if action in ("write", "add"):
                return huske_husk(
                    tekst=(arguments.get("text") or arguments.get("tekst") or ""),
                    user_id=user_id,
                    påminn_ved_login=bool(arguments.get("remind_on_login", arguments.get("påminn_ved_login", False))),
                )
            if action == "read":
                return huske_les(user_id=user_id)
            if action == "done":
                return huske_ferdig((arguments.get("note_id") or arguments.get("notat_id") or ""), user_id=user_id)
            if action == "delete":
                return huske_slett((arguments.get("note_id") or arguments.get("notat_id") or ""), user_id=user_id)
            if action == "clear":
                return huske_tøm(user_id=user_id)
            return f"Unknown action for huskeliste: '{action}'. Valid: write, read, done, delete, clear."

        if liste == "kare":
            if action in ("write", "add"):
                return kare_husk(
                    tekst=(arguments.get("text") or arguments.get("tekst") or ""),
                    kontekst=(arguments.get("context") or arguments.get("kontekst") or ""),
                )
            if action == "read":
                return kare_les()
            if action in ("done", "delete"):
                return kare_ferdig((arguments.get("note_id") or arguments.get("notat_id") or ""))
            if action == "clear":
                return kare_tøm()
            return f"Unknown action for kare-liste: '{action}'. Valid: write, read, done, delete, clear."

        if action == "write":
            return skriv_notat(
                tekst=(arguments.get("text") or arguments.get("tekst") or ""),
                kategori=(arguments.get("category") or arguments.get("kategori") or "diverse"),
                lang=lang,
            )
        if action == "read":
            return les_notater((arguments.get("category") or arguments.get("kategori")), lang=lang)
        if action == "delete":
            return slett_notat((arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang)
        if action == "clear":
            return tøm_notater((arguments.get("category") or arguments.get("kategori")), lang=lang)
        return f"Unknown action for notat: '{action}'. Valid: write, read, delete, clear."

    if name in executor_system.SYSTEM_TOOLS:
        return await executor_system.dispatch(name, arguments)

    if name in executor_camera.CAMERA_TOOLS:
        return await executor_camera.dispatch(name, arguments)

    if name in ("get_weather", "hent_yr_varsel"):
        return await _fetch_weather(arguments.get("location"), lang=lang)

    if name == "hent_klokke":
        now = datetime.now()
        return t("timer_clock", lang, time=now.strftime('%H:%M'), date=now.strftime('%d.%m.%Y'))

    if name == "skriv_notat":
        return skriv_notat(
            tekst=arguments.get("tekst", ""),
            kategori=arguments.get("kategori", "diverse"),
            lang=lang,
        )

    if name == "les_notater":
        return les_notater(arguments.get("kategori"), lang=lang)

    if name == "slett_notat":
        return slett_notat(arguments.get("notat_id", ""), lang=lang)

    if name == "tøm_notater":
        return tøm_notater(arguments.get("kategori"), lang=lang)

    if name == "sett_timer":
        return sett_timer(
            prompt=arguments.get("prompt", ""),
            in_seconds=int(arguments.get("in_seconds", 0)),
            notify=bool(arguments.get("notify", True)),
            repeat=arguments.get("repeat") or None,
            at_time=arguments.get("at_time") or None,
            lang=lang,
        )

    if name == "avbryt_timer":
        return avbryt_timer(arguments.get("timer_id", ""), lang=lang)

    if name == "liste_timere":
        return liste_timere(lang=lang)

    if name == "kare_image":
        if not _llm_cfg("image_edit").get("enabled", True):
            return t("exec_image_disabled", lang)
        from adapters.image_generation_adapter import generate_image, edit_image
        mode = arguments.get("mode", "generate")
        prompt = arguments.get("prompt", "").strip()
        negative_prompt = arguments.get("negative_prompt", "").strip()
        image_b64 = arguments.get("image_b64", "").strip()
        uid = arguments.get("_user_id", "global")
        if not prompt:
            return t("exec_image_no_prompt", lang)
        if mode == "edit":
            if not image_b64:
                return t("exec_image_no_input", lang)
            res = await edit_image(image_b64, prompt, negative_prompt, user_id=uid)
        else:
            res = await generate_image(prompt, negative_prompt, user_id=uid)
        if not res.get("ok"):
            return t("exec_image_failed", lang, error=res.get('error', 'ukjent feil'))
        return t("exec_image_ready", lang, image_id=res['image_id'])

    if name == "se_bilder":
        import base64 as _b64
        from kaare_core.image_store import list_images, find_image
        uid = arguments.get("user_id") or arguments.get("_user_id", "global")
        folder = arguments.get("folder", "all")
        limit = int(arguments.get("limit", 10))
        image_id = arguments.get("image_id", "").strip()
        mode = arguments.get("mode", "vis").strip()

        if image_id:
            path = find_image(image_id)
            if not path:
                return t("exec_image_not_found", lang, image_id=image_id)
            if mode == "analyser":
                b64 = _b64.b64encode(path.read_bytes()).decode()
                return f"[VISION:{b64}]"
            return f"Bildet er klart. Inkluder denne URL-en ordrett i svaret ditt: /api/image/{image_id}"

        imgs = list_images(uid, folder, limit)
        if not imgs:
            return t("exec_no_images", lang, uid=uid, folder=folder)
        lines = [f"{i['folder']}/{i['id']} ({i['size_kb']} KB)" for i in imgs]
        return t("exec_images_list", lang, uid=uid) + "\n" + "\n".join(lines)

    if name == "announce":
        text = arguments.get("text", "").strip()
        target = arguments.get("target", "local").strip() or "local"
        raw_volume = arguments.get("volume")
        volume = float(raw_volume) if raw_volume is not None else None
        if not text:
            return "No text provided for announcement."
        payload: dict = {"text": text, "target": target}
        if volume is not None:
            payload["volume"] = max(0.0, min(1.0, volume))
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{_VOICE_BRIDGE_URL}/speak",
                    json=payload,
                )
                resp.raise_for_status()
            target_label = t("exec_announce_all_rooms", lang) if target in ("all", "alle") else f"'{target}'"
            vol_label = t("exec_announce_volume_label", lang, pct=int(volume * 100)) if volume is not None else ""
            return t("exec_announce_sent", lang, target=target_label, vol_label=vol_label)
        except Exception as exc:
            return f"Could not reach voice bridge: {exc}"

    if name in executor_media.MEDIA_TOOLS:
        return await executor_media.dispatch(name, arguments)

    return t("exec_unknown_tool", lang, name=name)


async def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """
    Utfører ett tool-kall og logger det.
    Returnerer alltid en streng — aldri exception.
    """
    t0 = _time.time()
    try:
        result = await _dispatch(name, arguments)
    except Exception as e:
        result = f"Tool '{name}' feilet uventet: {e}"
    duration_ms = int((_time.time() - t0) * 1000)

    # timer tools log themselves via timer_service
    if name not in ("sett_timer", "avbryt_timer", "liste_timere"):
        _log_tool(name, arguments, result, duration_ms)

    return result
