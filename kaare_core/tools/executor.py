"""
Tool executor for Kåre's tool calls. Returns results as plain text
that is fed back to the LLM in the next round.
"""

import asyncio
import base64 as _b64
import logging
import time as _time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from adapters.display_adapter import get_display_nodes, send_display
from adapters.image_generation_adapter import edit_image, generate_image
from adapters.weather_adapter import fetch_weather as _fetch_weather
from kaare_core.config import get_llm_config as _llm_cfg
from kaare_core.config import get_local_tz, now_local
from kaare_core.config import get_service as _svc
from kaare_core.image_store import find_image, list_images
from kaare_core.memory import learning_store
from kaare_core.service_auth import service_headers
from kaare_core.tools import (
    executor_agents,
    executor_camera,
    executor_ha,
    executor_household,
    executor_library,
    executor_media,
    executor_memory,
    executor_personality,
    executor_reflexes,
    executor_world,
)
from kaare_core.tools.agenda import (
    agenda_add,
    agenda_read,
    observation_add,
    observation_read,
    reopen,
    resolve,
    resolved_read,
)
from kaare_core.tools.i18n import get_lang, t
from kaare_core.tools.lessons import lesson_add, lesson_delete, lesson_edit, lesson_read
from kaare_core.tools.lister import (
    kare_note_add,
    kare_note_clear,
    kare_note_done,
    kare_note_edit,
    kare_note_pin,
    kare_note_read,
    kare_note_unpin,
    reminder_add,
    reminder_clear,
    reminder_delete,
    reminder_done,
    reminder_read,
    shopping_add,
    shopping_clear,
    shopping_clear_bought,
    shopping_delete,
    shopping_mark_bought,
    shopping_read,
)
from kaare_core.tools.note_move import move_kare_note
from kaare_core.tools.notisblokk import (
    les_notater,
    marker_notat_viktig,
    skriv_notat,
    slett_notat,
    tøm_notater,
)
from kaare_core.tools.shared_tools import log_tool_call
from kaare_core.tools.timer_service import ack_timer, cancel_timer, list_timers, set_timer
from kaare_core.tools.trace_reader import (
    format_patterns_for_kare,
    format_trace_for_kare,
    get_recent_traces,
    get_trace,
)
from kaare_core.users.profile_manager import get_unlock_config

logger = logging.getLogger(__name__)

_VOICE_BRIDGE_URL = _svc("internal", "voice_bridge")

# Tools that require an unlocked voice session when called from a mic node.
# All tool aliases (nb/en variants) are included.
_LOCKED_TOOLS: frozenset[str] = frozenset(
    {
        # Memory
        "memory",
        "minne",
        "read_meeting",
        "inner_thoughts",
        "thought_history",
        # Profile & identity
        "user_profile",
        "brukerprofil",
        "self_image",
        "selvbilde",
        "les_selvbilde",
        "slett_fra_selvbilde",
        # World model
        "world",
        "verden",
        "les_verden",
        "oppdater_felt_i_verden",
        "legg_til_i_verden",
        # Camera
        "camera",
        "kamera",
        "hent_snapshot",
        "hent_frigate_hendelser",
        "les_kamerahendelser",
        # Agents with full context
        "mechanic",
        "spør_mechanic",
        "deleger_til_mechanic",
        # Self-improvement trace
        "self_trace",
    }
)

# These tools are locked by default but can be opened globally (no unlock needed)
# if the user has enabled global_lists in their unlock config.
_GLOBAL_LIST_TOOLS: frozenset[str] = frozenset({"note", "notat"})
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def _local_tz() -> ZoneInfo:
    # Cached config, no disk read; legacy lokasjon.tidssone fallback; UTC when unset (P116 B19)
    return get_local_tz()


def _fmt_ts_local(ts_raw: str) -> str:
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")


def _log_tool(name: str, arguments: dict, result: str, duration_ms: int, source: str = "kare"):
    log_tool_call(name, arguments, result, duration_ms, source=source)


# The note tool's schema offers English list names (definitions.py); older prompts, logs and
# the dispatcher below use the Norwegian ones. Both map to the same list. Before this map,
# 'remember'/'shopping' silently fell through to the architect notes (BUG-log 2026-10-03).
_LIST_NAME_ALIASES = {
    "handle": "handle",
    "shopping": "handle",
    "huske": "huske",
    "remember": "huske",
    "kare": "kare",
    "agenda": "agenda",
    # P133 drawers
    "observations": "observations",
    "observation": "observations",
    "observasjoner": "observations",
    "lessons": "lessons",
    "lesson": "lessons",
    "lærdommer": "lessons",
    "laerdommer": "lessons",
    "resolved": "resolved",
    "løst": "resolved",
    "arkitekt": "arkitekt",
    "architect": "arkitekt",
}


def normalize_list_name(raw) -> str:
    """Map a note list_name (English or Norwegian, possibly with leaked tool-call junk after
    it, e.g. «huske\n<parameter=text>…») to the dispatcher's key. Unknown → 'arkitekt'
    (the documented default list)."""
    first = str(raw or "").strip().split()[0].lower() if str(raw or "").strip() else ""
    first = first.split("<")[0]
    return _LIST_NAME_ALIASES.get(first, "arkitekt")


async def _dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Dispatcher — kalles fra execute_tool. Returnerer alltid streng."""
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in executor_ha.HA_TOOLS:
        return await executor_ha.dispatch(name, arguments)

    if name in executor_library.LIBRARY_TOOLS:
        return await executor_library.dispatch(name, arguments)

    if name == "timer":
        action = arguments.get("action", "")
        if action == "clock":
            now = now_local()
            return t("timer_clock", lang, time=now.strftime("%H:%M"), date=now.strftime("%d.%m.%Y"))
        if action == "set":
            return set_timer(
                prompt=arguments.get("prompt", ""),
                in_seconds=int(arguments.get("in_seconds", 0)),
                notify=bool(arguments.get("notify", True)),
                repeat=arguments.get("repeat") or None,
                at_time=arguments.get("at_time") or None,
                lang=lang,
                user_id=arguments.get("_user_id", "global"),
                source_node=arguments.get("_source_node") or None,
                target_node=arguments.get("target_node") or None,
                action=arguments.get("action_type", "tts_response"),
                notify_via=arguments.get("notify_via") or None,
                tts_text=arguments.get("tts_text", ""),
                ha_payload=arguments.get("ha_payload") or None,
                for_user_id=arguments.get("for_user_id") or None,
            )
        if action == "cancel":
            return cancel_timer(arguments.get("timer_id", ""), lang=lang)
        if action == "list":
            uid = arguments.get("_user_id") or None
            return list_timers(lang=lang, user_id=uid)
        if action == "ack":
            return ack_timer(
                notif_id=arguments.get("notif_id", ""),
                user_id=arguments.get("_user_id", "global"),
                lang=lang,
            )
        return t(
            "agent_unknown_action",
            lang,
            tool="timer",
            action=action,
            valid="clock, set, cancel, list, ack",
        )

    if name in executor_memory.MEMORY_TOOLS:
        return await executor_memory.dispatch(name, arguments)

    if name in executor_agents.AGENT_TOOLS:
        return await executor_agents.dispatch(name, arguments)

    if name in executor_personality.PERSONALITY_TOOLS:
        return await executor_personality.dispatch(name, arguments)

    if name in executor_world.WORLD_TOOLS:
        return await executor_world.dispatch(name, arguments)

    if name in ("note", "notat"):
        action = arguments.get("action", "")
        liste = normalize_list_name(arguments.get("list_name", arguments.get("liste", "arkitekt")))
        user_id = arguments.get("_user_id", "global")

        # resolve/reopen act on a topic number in learning.db, not on a list. Without an explicit list (or
        # with 'resolved', where a reopen naturally starts) they go by topic_id — KTSB's own echo hint asks
        # for exactly `note action='reopen', topic_id=N` (BUG-log 2026-10-09). An explicit other list (kare,
        # shopping …) is still refused below, so a note id is never taken for a topic number (P133 C2).
        explicit_list = arguments.get("list_name") or arguments.get("liste")
        if action in ("resolve", "reopen") and (not explicit_list or liste == "resolved"):
            liste = "observations"

        if liste in ("agenda", "observations"):
            # Dev-meeting agenda (P118 B1) and observations (P133 C1): append-only inbox in
            # learning.db, never injected. Resolve/reopen = the P133 C2 lifecycle.
            writer = agenda_add if liste == "agenda" else observation_add
            reader = agenda_read if liste == "agenda" else observation_read
            if action in ("write", "add"):
                return await asyncio.to_thread(
                    writer,
                    (arguments.get("text") or arguments.get("tekst") or ""),
                    (arguments.get("context") or arguments.get("kontekst") or ""),
                    user_id=user_id,
                    rid=arguments.get("_rid", ""),
                    source=arguments.get("_source", ""),
                    pointer=arguments.get("_pointer", ""),
                    lang=lang,
                    theme=str(arguments.get("theme") or ""),
                    title=str(arguments.get("title") or ""),
                )
            if action == "read":
                return await asyncio.to_thread(reader, lang)
            if action == "resolve":
                return await asyncio.to_thread(
                    resolve,
                    arguments.get("topic_id"),
                    str(arguments.get("conclusion") or arguments.get("text") or ""),
                    user_id=user_id,
                    rid=arguments.get("_rid", ""),
                    lang=lang,
                )
            if action == "reopen":
                return await asyncio.to_thread(
                    reopen,
                    arguments.get("topic_id"),
                    str(arguments.get("text") or ""),
                    lang=lang,
                )
            return t(
                "agent_unknown_action",
                lang,
                tool=liste,
                action=action,
                valid="write, read, resolve, reopen",
            )

        if liste == "resolved":
            if action == "read":
                return await asyncio.to_thread(resolved_read, lang)
            return t("agent_unknown_action", lang, tool="resolved", action=action, valid="read")

        if liste == "lessons":
            # P133 C1/D7: rules Kåre learned — always in the prompt as short prose.
            note_id = arguments.get("note_id") or arguments.get("notat_id") or ""
            text = arguments.get("text") or arguments.get("tekst") or ""
            title = str(arguments.get("title") or "")
            if action in ("write", "add"):
                return await asyncio.to_thread(lesson_add, text, title, lang)
            if action == "read":
                return await asyncio.to_thread(lesson_read, lang)
            if action == "edit":
                return await asyncio.to_thread(lesson_edit, note_id, text, title, lang)
            if action in ("delete", "done"):
                return await asyncio.to_thread(lesson_delete, note_id, lang)
            return t(
                "agent_unknown_action",
                lang,
                tool="lessons",
                action=action,
                valid="write, read, edit, delete",
            )

        if liste == "handle":
            if action in ("write", "add"):
                return shopping_add(
                    text=(arguments.get("text") or arguments.get("tekst") or ""),
                    amount=(arguments.get("quantity") or arguments.get("mengde") or ""),
                    unit=(arguments.get("unit") or arguments.get("enhet") or ""),
                    added_by=user_id,
                    lang=lang,
                )
            if action == "read":
                return shopping_read(lang=lang)
            if action == "mark_bought":
                return shopping_mark_bought(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
                )
            if action == "delete":
                return shopping_delete(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
                )
            if action in ("clear", "clear_bought"):
                return shopping_clear_bought(lang=lang)
            if action == "clear_all":
                return shopping_clear(lang=lang)
            return t(
                "agent_unknown_action",
                lang,
                tool="handle-liste",
                action=action,
                valid="write, read, mark_bought, delete, clear, clear_all",
            )

        if liste == "huske":
            # The reminder list belongs to a user. On any path without one (internal reflection,
            # background jobs, …) a write would land in a 'global' list nobody reads — tell Kåre
            # where it belongs instead.
            if action in ("write", "add") and user_id in ("", "global", "kare"):
                return t("list_reminder_needs_user", lang)
            if action in ("write", "add"):
                return reminder_add(
                    text=(arguments.get("text") or arguments.get("tekst") or ""),
                    user_id=user_id,
                    remind_on_login=bool(
                        arguments.get("remind_on_login", arguments.get("påminn_ved_login", False))
                    ),
                    lang=lang,
                )
            if action == "read":
                return reminder_read(user_id=user_id, lang=lang)
            if action == "done":
                return reminder_done(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""),
                    user_id=user_id,
                    lang=lang,
                )
            if action == "delete":
                return reminder_delete(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""),
                    user_id=user_id,
                    lang=lang,
                )
            if action == "clear":
                return reminder_clear(user_id=user_id, lang=lang)
            return t(
                "agent_unknown_action",
                lang,
                tool="huskeliste",
                action=action,
                valid="write, read, done, delete, clear",
            )

        if liste == "kare":
            if action in ("write", "add"):
                return kare_note_add(
                    text=(arguments.get("text") or arguments.get("tekst") or ""),
                    context=(arguments.get("context") or arguments.get("kontekst") or ""),
                    lang=lang,
                    title=str(arguments.get("title") or ""),
                )
            if action == "read":
                ids = arguments.get("note_ids") or []
                if isinstance(ids, str):
                    ids = [x for x in ids.replace(",", " ").split() if x]
                return kare_note_read(
                    search=(arguments.get("search") or ""),
                    date_from=(arguments.get("date_from") or ""),
                    date_to=(arguments.get("date_to") or ""),
                    lang=lang,
                    note_ids=ids,
                )
            if action == "move":
                return await asyncio.to_thread(
                    move_kare_note,
                    (arguments.get("note_id") or arguments.get("notat_id") or ""),
                    str(arguments.get("to") or ""),
                    title=str(arguments.get("title") or ""),
                    theme=str(arguments.get("theme") or ""),
                    text=str(arguments.get("text") or ""),
                    user_id=user_id,
                    rid=arguments.get("_rid", ""),
                    source=arguments.get("_source", ""),
                    lang=lang,
                )
            if action in ("done", "delete"):
                return kare_note_done(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
                )
            if action == "edit":
                return kare_note_edit(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""),
                    (arguments.get("text") or arguments.get("tekst") or ""),
                    lang=lang,
                    title=str(arguments.get("title") or ""),
                )
            if action == "clear":
                return kare_note_clear(lang=lang)
            if action == "pin":
                return kare_note_pin(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
                )
            if action == "unpin":
                return kare_note_unpin(
                    (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
                )
            return t(
                "agent_unknown_action",
                lang,
                tool="kare-liste",
                action=action,
                valid="write, read, done, delete, clear, pin, unpin, edit, move",
            )

        if action == "write":
            return skriv_notat(
                tekst=(arguments.get("text") or arguments.get("tekst") or ""),
                kategori=(arguments.get("category") or arguments.get("kategori") or "diverse"),
                lang=lang,
            )
        if action == "read":
            return les_notater((arguments.get("category") or arguments.get("kategori")), lang=lang)
        if action == "delete":
            return slett_notat(
                (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
            )
        if action == "clear":
            return tøm_notater((arguments.get("category") or arguments.get("kategori")), lang=lang)
        if action == "mark_important":
            return marker_notat_viktig(
                (arguments.get("note_id") or arguments.get("notat_id") or ""), lang=lang
            )
        return t(
            "agent_unknown_action",
            lang,
            tool="note",
            action=action,
            valid="write, read, delete, clear, mark_important",
        )

    if name == "dev_proposals":
        # Dev-slot proposals (P118 B4, T2-D13): list / record a human decision. Never applies anything.
        action = arguments.get("action", "")
        if action == "list":
            rows = await asyncio.to_thread(learning_store.list_proposals, "pending")
            if not rows:
                return t("dev_proposals_none", lang)
            lines = [t("dev_proposals_title", lang, count=len(rows))]
            for r in rows:
                label = f" ({r['topic_label']})" if r.get("topic_label") else ""
                lines.append(f"  #{r['id']}{label}: {r['text']}")
                if r.get("finding"):
                    lines.append(f"     ← {r['finding']} {r.get('evidence_refs') or ''}")
            return "\n".join(lines)
        if action in ("confirm", "reject"):
            try:
                pid = int(arguments.get("proposal_id"))
            except (TypeError, ValueError):
                return t("dev_proposals_needs_id", lang)
            user = arguments.get("_user_id", "global")
            status = "confirmed" if action == "confirm" else "rejected"
            row = await asyncio.to_thread(learning_store.decide_proposal, pid, status, user)
            if row is None:
                return t("dev_proposals_not_pending", lang, id=pid)
            key = "dev_proposals_confirmed" if status == "confirmed" else "dev_proposals_rejected"
            return t(key, lang, id=pid, user=user)
        return t(
            "agent_unknown_action",
            lang,
            tool="dev_proposals",
            action=action,
            valid="list, confirm, reject",
        )

    if name == "self_trace":
        # Kåre's own self-improvement trace view — deliberately narrow (fetch_trace/
        # trace_patterns only). Code/system exploration and shell access moved to Mechanic
        # in Fase B (P104) — this is the one piece of the old inspect_system tool Kåre keeps.
        action = arguments.get("action", "")
        if action == "fetch_trace":
            rid_arg = arguments.get("rid", "").strip()
            if not rid_arg:
                return t("inspiser_system_hent_trace_mangler_rid", lang)
            trace = get_trace(rid_arg)
            if (
                not trace.get("stages")
                and not trace.get("llm_calls")
                and not trace.get("tool_calls")
            ):
                return t("inspiser_system_hent_trace_ikke_funnet", lang, rid=rid_arg)
            return format_trace_for_kare(trace, lang=lang)
        if action == "trace_patterns":
            n = min(int(arguments.get("count", 50)), 200)
            source = arguments.get("source", "all")
            if source not in ("user", "refl", "meet", "all"):
                source = "all"
            traces = get_recent_traces(n, source=source)
            if not traces:
                return t("inspiser_system_trace_mønstre_ingen", lang)
            return format_patterns_for_kare(traces, lang=lang)
        return t(
            "agent_unknown_action",
            lang,
            tool="self_trace",
            action=action,
            valid="fetch_trace, trace_patterns",
        )

    if name in executor_camera.CAMERA_TOOLS:
        return await executor_camera.dispatch(name, arguments)

    if name in executor_household.HOUSEHOLD_TOOLS:
        return await executor_household.dispatch(
            name, arguments, user_id=arguments.get("_user_id", "global")
        )

    if name in ("get_weather", "hent_yr_varsel"):
        return await _fetch_weather(arguments.get("location"), lang=lang)

    if name == "hent_klokke":
        now = now_local()
        return t("timer_clock", lang, time=now.strftime("%H:%M"), date=now.strftime("%d.%m.%Y"))

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

    if name == "set_timer":
        return set_timer(
            prompt=arguments.get("prompt", ""),
            in_seconds=int(arguments.get("in_seconds", 0)),
            notify=bool(arguments.get("notify", True)),
            repeat=arguments.get("repeat") or None,
            at_time=arguments.get("at_time") or None,
            lang=lang,
            user_id=arguments.get("_user_id", "global"),
        )

    if name == "cancel_timer":
        return cancel_timer(arguments.get("timer_id", ""), lang=lang)

    if name == "list_timers":
        return list_timers(lang=lang, user_id=arguments.get("_user_id") or None)

    if name == "kare_image":
        if not _llm_cfg("image_edit").get("enabled", True):
            return t("exec_image_disabled", lang)

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
            return t("exec_image_failed", lang, error=res.get("error", "ukjent feil"))
        return t("exec_image_ready", lang, image_id=res["image_id"])

    if name in ("view_images", "se_bilder"):
        uid = arguments.get("user_id") or arguments.get("_user_id", "global")
        folder = arguments.get("folder", "all")
        limit = int(arguments.get("limit", 10))
        image_id = arguments.get("image_id", "").strip()
        mode = arguments.get("mode", "view").strip()

        if image_id:
            path = find_image(image_id)
            if not path:
                return t("exec_image_not_found", lang, image_id=image_id)
            if mode in ("analyze", "analyser"):
                b64 = _b64.b64encode(path.read_bytes()).decode()
                return f"[VISION:{b64}]"
            return t("exec_image_url_ready", lang, image_id=image_id)

        imgs = list_images(uid, folder, limit)
        if not imgs:
            return t("exec_no_images", lang, uid=uid, folder=folder)
        lines = [f"{i['folder']}/{i['id']} ({i['size_kb']} KB)" for i in imgs]
        return t("exec_images_list", lang, uid=uid) + "\n" + "\n".join(lines)

    if name == "announce":
        action = arguments.get("action", "say")

        if action == "list_display":
            nodes = await get_display_nodes()
            if not nodes:
                return t("announce_no_display_nodes", lang)
            lines = [
                f"- **{n['id']}** ({n.get('type', '?')}) — {n.get('room', '?')}" for n in nodes
            ]
            return t("announce_display_list", lang) + "\n" + "\n".join(lines)

        if action == "display":
            text = arguments.get("text", "").strip()
            target = arguments.get("target", "").strip()
            image_id = arguments.get("image_id")
            title = arguments.get("title", "Kåre")
            duration = int(arguments.get("duration", 8))
            position = arguments.get("position", "bottom_right")

            image_path = None
            if image_id:
                found = find_image(image_id)
                image_path = str(found) if found else None

            all_display = await get_display_nodes()
            display_ids = [n["id"] for n in all_display]

            if target in display_ids:
                node_ids = [target]
            elif target in ("alle", "all", ""):
                node_ids = display_ids
            else:
                tn = target.lower().replace(" ", "_")
                node_ids = [
                    n["id"]
                    for n in all_display
                    if n.get("room", "").lower().replace(" ", "_") == tn
                ]

            if not node_ids:
                return t("announce_display_target_not_found", lang, target=target or "all")

            results = []
            for nid in node_ids:
                res = await send_display(
                    nid,
                    text=text,
                    title=title,
                    image_path=image_path,
                    duration=duration,
                    position=position,
                )
                results.append((nid, res))

            ok = sum(1 for _, r in results if r.get("ok"))
            fail = len(results) - ok
            if ok == len(results):
                return t("announce_display_ok", lang, count=ok)
            if ok > 0:
                return t("announce_display_partial", lang, ok=ok, fail=fail)
            errors = "; ".join(r.get("error", "?") for _, r in results)
            return t("announce_display_failed", lang, errors=errors)

        # Default: action == "say"
        text = arguments.get("text", "").strip()
        target = arguments.get("target", "local").strip() or "local"
        raw_volume = arguments.get("volume")
        volume = float(raw_volume) if raw_volume is not None else None
        if not text:
            return "No text provided for announcement."
        payload: dict = {"text": text, "target": target, "lang": lang}
        if volume is not None:
            payload["volume"] = max(0.0, min(1.0, volume))
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{_VOICE_BRIDGE_URL}/speak",
                    json=payload,
                    headers=service_headers(),
                )
                resp.raise_for_status()
            target_label = (
                t("exec_announce_all_rooms", lang) if target in ("all", "alle") else f"'{target}'"
            )
            vol_label = (
                t("exec_announce_volume_label", lang, pct=int(volume * 100))
                if volume is not None
                else ""
            )
            return t("exec_announce_sent", lang, target=target_label, vol_label=vol_label)
        except Exception as exc:
            return f"Could not reach voice bridge: {exc}"

    if name in executor_media.MEDIA_TOOLS:
        return await executor_media.dispatch(name, arguments)

    if name in executor_reflexes.REFLEX_TOOLS:
        return await executor_reflexes.dispatch(name, arguments)

    return t("exec_unknown_tool", lang, name=name)


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """
    Utfører ett tool-kall og logger det.
    Returnerer alltid en streng — aldri exception.
    """
    lang = get_lang(arguments.get("_user_id", "global"))

    # Voice node gating: block personal tools when session is locked
    if arguments.get("_node_locked"):
        if name in _LOCKED_TOOLS:
            return t("voice_tool_blocked", lang)
        if name in _GLOBAL_LIST_TOOLS:
            uid = arguments.get("_user_id", "global")
            try:
                if not get_unlock_config(uid).get("global_lists", False):
                    return t("voice_tool_blocked", lang)
            except Exception:
                return t("voice_tool_blocked", lang)

    t0 = _time.time()
    try:
        result = await _dispatch(name, arguments)
    except Exception as e:
        result = f"Tool '{name}' feilet uventet: {e}"
    duration_ms = int((_time.time() - t0) * 1000)

    # timer tools log themselves via timer_service
    if name not in ("set_timer", "cancel_timer", "list_timers", "timer"):
        _log_tool(name, arguments, result, duration_ms)

    return result
