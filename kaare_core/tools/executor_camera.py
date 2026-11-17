"""
Camera executor module — Frigate snapshots, events, VLM analysis.
Exported: CAMERA_TOOLS, dispatch()
"""

import asyncio
import base64
import logging
import re
from datetime import datetime

from adapters.display_adapter import get_display_nodes, send_display
from adapters.frigate_adapter import (
    camera_display_name,
    fetch_event_snapshot,
    fetch_events,
    fetch_face_events,
    fetch_snapshot_b64,
    get_cameras,
)
from adapters.llm_adapter import ask_llm, ask_llm_role
from kaare_core import model_enforcement, receipts, rooms
from kaare_core.audit import audit_log as _audit
from kaare_core.config import get_local_tz, local_from_ts, now_local, role_may_analyse_camera
from kaare_core.domain import frigate_store as domain_store
from kaare_core.domain.frigate_store import camera_config, cleanup_snapshots, save_snapshot
from kaare_core.frigate_user import labels as frigate_labels
from kaare_core.frigate_user import query as frigate_query
from kaare_core.frigate_user import questions as frigate_questions
from kaare_core.frigate_user import sealing as frigate_sealing
from kaare_core.frigate_user import store as frigate_store_db
from kaare_core.frigate_user import things as frigate_things
from kaare_core.image_store import save_image as _save_image
from kaare_core.memory import chat_questions
from kaare_core.tools.executor_ha import choices_text, node_image_targets, resolve_display_node
from kaare_core.tools.i18n import get_lang, t
from kaare_core.users import frigate_consent
from kaare_core.users import store as _user_store
from kaare_core.voice import cast_client

logger = logging.getLogger(__name__)

CAMERA_TOOLS = {
    "camera",
    "kamera",
    "hent_snapshot",
    "hent_frigate_hendelser",
    "les_kamerahendelser",
    "liste_kameraer",
}


async def describe_snapshot(camera: str, prompt: str) -> str | None:
    """
    Fetch a Frigate snapshot and return a VLM description.
    Returns None on any failure — never raises.
    Used by weather_adapter and any other caller that needs a single-camera description.
    """
    try:
        img_b64 = await fetch_snapshot_b64(camera)
        vlm_result = await ask_llm(prompt=prompt, images=[img_b64])
        if not vlm_result.get("ok"):
            return None
        return vlm_result.get("text", "").strip() or None
    except Exception as exc:
        logger.warning("[camera] describe_snapshot failed for '%s': %s", camera, exc)
        return None


def _may_analyse(uid: str) -> bool:
    """N41: may this user start a NEW camera analysis (image model / GPU)? From their role's `camera_analyse` flag. No
    record → the most restricted role. Any error → no (fail closed)."""
    try:
        return role_may_analyse_camera(((_user_store.get_user(uid) or {}).get("role")) or "stranger")
    except Exception as e:
        logger.warning("[camera] role check failed: %s", type(e).__name__)
        return False


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in ("camera", "kamera"):
        return await _kamera(arguments, lang)

    if name == "hent_snapshot":  # the old tool name always analyses — N41 gate
        if not _may_analyse(arguments.get("_user_id", "global")):
            return t("cam_analyse_not_for_role", lang)
        return await _compat_hent_snapshot(arguments, lang)

    if name == "hent_frigate_hendelser":
        return await _compat_hent_frigate_hendelser(arguments, lang)

    if name == "les_kamerahendelser":
        return await _compat_les_kamerahendelser(arguments, lang)

    if name == "liste_kameraer":
        return await _compat_liste_kameraer(arguments, lang)

    return f"[executor_camera] Unknown tool: '{name}'"


def _camera_display(camera: str) -> str:
    """J24: card → services.yaml → Frigate's friendly_name (cache only: the tool runs on the event loop) → raw id."""
    try:
        return camera_display_name(camera, camera_config().get("cameras", {}) or {}, fetch=False)
    except Exception:
        return camera


_WHICH_ITEM = re.compile(r"^- event_id (\S+):", re.M)
QUESTION_PICTURES_MAX = 5  # «which one?» lists more seldom; past this the list stays text


def _question_pictures(uid: str, tool_text: str, lang: str, source: str = "gui") -> str:
    """J29b: the pictures of the events a camera question reply is about — the receipt's event (`ref=`), each of the
    user's own question cards named in it (N39: card qid → that card's event, nothing else), or (admin's «which
    member?» list) each listed event — one chat image line each (chat_questions.picture_line: J24 access, never an
    opted-out person's event). A refusal names no event, so it gets no picture. Voice (`stt`): never a picture line
    (N39, option A). "" or "\n<lines>". Never raises."""
    if source == "stt":
        return ""
    try:
        eids = ([ref for kind, ref in receipts.MARKER_RE.findall(tool_text) if kind.startswith("camera_")]
                or chat_questions.card_targets(uid, tool_text) or _WHICH_ITEM.findall(tool_text))
        if not eids:
            return ""
        user = frigate_questions._user(uid) or {}
        lines = [chat_questions.picture_line(uid, e, lang, role=user.get("role")) for e in eids[:QUESTION_PICTURES_MAX]]
        lines = [ln for ln in lines if ln]
        return ("\n" + "\n".join(lines)) if lines else ""
    except Exception as e:
        logger.warning("[camera] question pictures failed: %s", type(e).__name__)
        return ""


def _stored_words(event_id: str) -> str:
    """P144 phase 6: what the pipe stored for this event in frigate.db — an old module-1 still text where one is stored,
    else L3's description + analysis. "" when the event is not there. Read only; never raises."""
    try:
        ev = frigate_store_db.event_by_id(event_id)
    except Exception:
        return ""
    if ev is None:
        return ""
    still = (ev.check or {}).get("still") if isinstance(ev.check, dict) else None
    if isinstance(still, dict) and still.get("text"):
        return str(still["text"])
    return " ".join(w for w in (ev.description, ev.analysis) if w)


def _read_words(words: dict) -> str:
    """The image model's own fields as written (P144 6e) — description / analysis, or per person place, posture, carried,
    expression, gesture. No translation (G7)."""
    parts = [str(words[key]) for key in ("description", "analysis") if words.get(key)]
    for key in ("persons", "looks"):
        for pid, fields in (words.get(key) or {}).items():
            if isinstance(fields, dict):
                shown = ", ".join(f"{k}: {v}" for k, v in fields.items() if v not in ("", None))
                if shown:
                    parts.append(f"{pid} {shown}")
    return " · ".join(parts) or "—"


def _read_entries(antall: int, lang: str) -> list[str]:
    """P144 6e: the image model's single-image reads that already exist (L3 per event, module 4 per frame), newest first —
    the same rows as Integrations → Frigate → «Siste deteksjoner». Never a new model call."""
    tz = get_local_tz()
    rows, _more = frigate_store_db.recent_model_reads(limit=antall)
    out = []
    for r in rows:
        when = datetime.fromtimestamp(r["ts"], tz).strftime("%Y-%m-%d %H:%M:%S")
        step = (t("cam_read_l3", lang, label=r.get("label") or "?") if r["kind"] == "l3"
                else t("cam_read_frame", lang, n=r.get("frame"), episode=r.get("episode_id")))
        ref = f"event_id: {r['event_id']}" if r.get("event_id") else f"episode_id: {r.get('episode_id')}"
        out.append(f"[{when}] {_camera_display(r['camera'])} — {step}\n{t('cam_read_words', lang)} {_read_words(r['words'])}\n{ref}")
    return out


async def _kamera(arguments: dict, lang: str = "nb") -> str:
    action = arguments.get("action", "")
    _uid = arguments.get("_user_id", "global")
    _src = str(arguments.get("_source") or "gui")  # N39: "stt" = voice — no picture lines, cards read aloud

    if action in ("live", "live_stop"):  # P151 phase 4 — live camera on a screen (the voice bridge does the work)
        return await _live_on_screen(action, arguments, _uid, lang)

    if action == "snapshot":
        _audit("camera_access", _uid, f"action=snapshot scope={arguments.get('scope', 'one')}")
        scope = arguments.get("scope", "one")
        ts = now_local().strftime("%d.%m.%Y %H:%M:%S")
        may = _may_analyse(_uid)  # N41: a new analysis needs the role's camera_analyse flag
        if scope in ("all", "alle") and not may:
            return t("cam_analyse_not_for_role", lang)  # «all cameras» is analysis only — nothing to show without it
        if scope in ("all", "alle"):
            prompt_text = _vlm_prompt(t("cam_prompt_all", lang), arguments, lang)
            cams = await get_cameras()
            if not cams:
                return t("cam_no_cameras", lang)

            async def _fetch(cam):
                try:
                    b64 = await fetch_snapshot_b64(cam["api_name"])
                    return cam["friendly_name"], b64
                except Exception:
                    return cam["friendly_name"], None

            fetched = await asyncio.gather(*[_fetch(c) for c in cams])
            images = [b64 for _, b64 in fetched if b64]
            cam_names = [n for n, b64 in fetched if b64]
            failed = [n for n, b64 in fetched if b64 is None]
            if not images:
                return t("cam_fetch_failed", lang, ts=ts)
            prompt = t("cam_prefix_all", lang, cam_list=", ".join(cam_names)) + prompt_text
            try:
                vlm_result = await ask_llm(prompt=prompt, images=images)
                if not vlm_result.get("ok"):
                    return t(
                        "cam_analysis_failed", lang, ts=ts, error="VLM returned empty response"
                    )
                description = vlm_result.get("text", "").strip() or t("cam_empty_vlm", lang)
            except Exception as e:
                return t("cam_analysis_failed", lang, ts=ts, error=e)
            result = f"[Alle kameraer — {ts}]\n{description}"
            if failed:
                result += f"\n\nKunne ikke hente: {', '.join(failed)}"
            return result

        kamera_navn = (arguments.get("camera") or arguments.get("kamera") or "").strip()
        prompt_text = _vlm_prompt(t("cam_prompt_single", lang), arguments, lang)
        if not kamera_navn:
            cams = await get_cameras()
            cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
            return t("cam_specify_name", lang, cam_list=cam_list)
        try:
            img_b64 = await fetch_snapshot_b64(kamera_navn)
        except ValueError as e:
            cams = await get_cameras()
            cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
            return t("cam_not_found", lang, error=e, cam_list=cam_list)
        except Exception as e:
            return t("cam_snapshot_error", lang, camera=kamera_navn, error=e)
        header = f"[{kamera_navn} — {ts}]"
        # A fresh camera picture on a named screen too (P139: «vis et bilde fra ringeklokka på TV-en»). The picture
        # goes out first — the model never holds it back (P139 reopened 2026-11-16).
        show = (asyncio.create_task(_show_on_screens(arguments, img_b64, header, lang))
                if str(arguments.get("screen") or "").strip() else None)
        if show:
            await asyncio.sleep(0)  # let the screen send start before the model call
        if _wants_analysis(arguments) and may:
            try:
                vlm_result = await ask_llm(prompt=prompt_text, images=[img_b64])
                if vlm_result.get("ok"):
                    description = vlm_result.get("text", "").strip() or t("cam_empty_vlm", lang)
                else:
                    description = t("cam_snapshot_analysis_failed", lang, ts=ts, error="VLM returned empty response")
            except Exception as e:
                description = t("cam_snapshot_analysis_failed", lang, ts=ts, error=e)
            out = f"{header}\n{description}"
        else:
            refused = t("cam_analyse_not_for_role", lang) + "\n" if _wants_analysis(arguments) else ""
            out = f"{header}\n{refused}{t('cam_not_analysed', lang)}"
            picture = _chat_picture(img_b64, _uid, lang)
            out += f"\n\n{picture}" if picture else ""
        shown = await show if show else ""
        return out + (f"\n\n{shown}" if shown else "")

    if action == "events":  # J21b reader 2: frigate.db (module 0)
        _audit("camera_access", _uid, "action=events")
        return await _noticed(_face_events(arguments, _uid, lang), lang)

    if action == "stationary":  # J8 (d): the stationary gate's facts — what stands where, unchanged since when
        _audit("camera_access", _uid, "action=stationary")
        return await asyncio.to_thread(frigate_things.text, lang)

    if action == "frigate":
        kamera_navn = (arguments.get("camera") or arguments.get("kamera") or "").strip() or None
        label, unknown = _label(arguments.get("label"), lang)
        if unknown:
            return unknown
        antall = min(int(arguments.get("count", arguments.get("antall", 10))), 50)
        kun_ansikter = bool(arguments.get("faces_only", arguments.get("kun_ansikter", False)))
        try:
            if kun_ansikter:
                events = await fetch_face_events(limit=antall)
            else:
                events = await fetch_events(camera=kamera_navn, label=label, limit=antall)
        except Exception as e:
            return t("cam_frigate_error", lang, error=e)
        if not events:
            return t("cam_no_frigate_events", lang)
        lines = []
        for ev in events:
            ts_raw = ev.get("start_time") or ev.get("ts", 0)
            try:
                ts_str = local_from_ts(float(ts_raw)).strftime("%d.%m %H:%M:%S")
            except Exception:
                ts_str = str(ts_raw)
            cam = ev.get("camera_friendly") or ev.get("camera", "?")
            lbl = ev.get("label", "?")
            conf = event_score(ev)
            face = ev.get("_face_name") or ev.get("sub_label") or ""
            face_str = f" — ansikt: {face}" if face else ""
            eid = ev.get("id", "")
            eid_str = f" [event_id: {eid}]" if eid else ""
            lines.append(f"[{ts_str}] {cam}: {lbl} ({int(float(conf) * 100)}%){face_str}{eid_str}")
        return "\n".join(lines)

    if action in ("last_seen", "episodes", "episode"):
        return await _memory(action, arguments, _uid, lang)

    if action == "answer_question":  # J11 / N39: the user's answer to ONE question card — bound by code to its target
        qid = str(arguments.get("question_id") or "").strip()  # never an event_id (N39)
        _audit("camera_answer_question", _uid, f"question={qid}")
        verdict = str(arguments.get("answer") or "").strip().lower()
        res = await asyncio.to_thread(chat_questions.answer, _uid, qid, verdict)
        if not res.get("ok") and res.get("error") == "not_a_question":
            return t("fv_not_question", lang)
        kind, target = res.get("kind"), str(res.get("target") or "")
        if kind == "correct":  # N39 b: the receipt says what it was and what it is now
            return await asyncio.to_thread(frigate_questions.corrected_text, _uid, target, verdict, res, lang)
        if kind == "theme":
            if not res.get("ok"):
                return t("fv_not_question", lang)
            if res.get("saved") is False:  # N39: never «Lagret» when the save failed
                return t("rcpt_not_saved", lang)
            return receipts.build("theme_answer", ref=target, what=t("rcpt_theme_what", lang, answer=verdict),
                                  where=t("rcpt_theme_where", lang), who=_uid, channel="chat", lang=lang) + \
                "\n" + receipts.instruction(lang)
        # J22: exactly what was stored (2026-11-07: «Svaret er lagret.» became «Lagt inn: det var <navn>» while «nei» was)
        return await asyncio.to_thread(frigate_questions.stored_text, _uid, target, verdict, res, lang)

    if action == "correct":  # N39 b: the user's own recent answers become cards; they pick, code corrects
        _audit("camera_correct", _uid, "list")
        out = await asyncio.to_thread(chat_questions.correction_cards, _uid, lang, _src)
        return out + await asyncio.to_thread(_question_pictures, _uid, out, lang, _src)

    if action == "ask":  # J9: Kåre's own «who was that?» — one event into this user's question list (G17)
        _audit("camera_ask", _uid, f"event={arguments.get('event_id')}")
        qid = str(arguments.get("event_id") or "").strip()
        res = await asyncio.to_thread(frigate_questions.queue_for, _uid, qid)
        if res.get("ok") and res.get("saved") is False:  # N39: never «Lagret» when the save failed
            return t("rcpt_not_saved", lang)
        if res.get("ok"):  # J22 part 4: the receipt; J29b: + the event's picture (whoever answers must see it)
            card = await asyncio.to_thread(chat_questions.add_card, _uid, "camera", qid, source=_src, origin="ask")
            out = receipts.build("camera_question_queued", ref=qid, what=t("rcpt_queued_what", lang),
                                 where=t("rcpt_queued_where", lang), who=_uid, channel="chat", lang=lang) + \
                "\n" + receipts.instruction(lang) + "\n" + chat_questions.card_lines(_uid, [card["qid"]], lang)
            return out + await asyncio.to_thread(_question_pictures, _uid, out, lang, _src)
        return (
            t("fv_not_allowed", lang) if res.get("error") in ("not_allowed", "off") else t("fv_not_question", lang))

    if action == "verify":  # P144 #9 / P149: the user's own «Var det deg?» answer for one event or episode
        _audit("camera_verify", _uid, f"event={arguments.get('event_id')} episode={arguments.get('episode_id')}")
        about = str(arguments.get("about") or "").strip() or None
        eid = str(arguments.get("event_id") or "").strip() or None
        verdict = str(arguments.get("answer") or "").strip().lower()
        if about:  # J22: admin about a member — unchanged
            out = await asyncio.to_thread(frigate_questions.verify, _uid, event_id=eid,
                                          episode_id=arguments.get("episode_id"), verdict=verdict, lang=lang, about=about)
        else:  # N39: several open events → one card each, nothing stored, the LLM never picks
            out = await asyncio.to_thread(chat_questions.verify, _uid, event_id=eid,
                                          episode_id=arguments.get("episode_id"), verdict=verdict, lang=lang,
                                          source=_src)
        return out + await asyncio.to_thread(_question_pictures, _uid, out, lang, _src)  # J29b

    if action == "list":
        try:
            cams = await get_cameras()
        except Exception as e:
            return t("cam_list_error", lang, error=e)
        if not cams:
            return t("cam_no_frigate_events", lang)
        lines = [f"  {c['friendly_name']} → {c['api_name']}" for c in cams]
        return t("cam_list_header", lang, count=len(cams)) + "\n" + "\n".join(lines)

    if action == "analyze":  # P144 6e: the image model's existing single-image reads (L3 + module 4), never a new call
        antall = min(int(arguments.get("count", arguments.get("antall", 10))), 50)
        try:
            entries = await asyncio.to_thread(_read_entries, antall, lang)
        except Exception as e:
            return t("cam_analysis_log_error", lang, error=e)
        if not entries:
            return t("cam_no_analysis", lang)
        return "\n\n---\n\n".join(entries)

    if action == "show_event":
        event_id = arguments.get("event_id", "").strip()
        if not event_id:
            return t("cam_event_id_required", lang)

        snap_path = domain_store.SNAPSHOT_DIR / f"{event_id}.jpg"  # the pipe's snapshot store (read per call)
        img_bytes: bytes | None = None

        if snap_path.exists():
            try:
                img_bytes = frigate_sealing.unseal_bytes(snap_path.read_bytes())  # sealed at rest (R0.6a)
            except Exception as e:
                return t("cam_snapshot_read_error", lang, error=e)
        else:
            # Local cache miss — the event may predate the cache, or its
            # camera/label combo was never gated `analyze: true` in
            # frigate_cameras.yaml. Fetch live from Frigate's REST API
            # instead of giving up. Frigate may not have written the snapshot
            # yet, so a 404 is retried once.
            try:
                img_bytes = await asyncio.wait_for(fetch_event_snapshot(event_id), timeout=10)
            except Exception as e:
                if "404" in str(e):
                    await asyncio.sleep(1.5)
                    try:
                        img_bytes = await asyncio.wait_for(
                            fetch_event_snapshot(event_id), timeout=10
                        )
                    except Exception as e2:
                        logger.warning(
                            "[vis_hendelse] REST fallback retry failed for %s: %s", event_id, e2
                        )
                else:
                    logger.warning("[vis_hendelse] REST fallback failed for %s: %s", event_id, e)

            if not img_bytes:
                return t("cam_snapshot_not_found", lang, event_id=event_id)

            # Cache locally for next time, respecting the same size cap the
            # automatic-analysis write path already enforces.
            try:
                save_snapshot(event_id, base64.b64encode(img_bytes).decode())
                snap_max = camera_config().get("storage", {}).get("snapshots_max_mb", 500)
                cleanup_snapshots(snap_max)
            except Exception as e:
                logger.warning("[vis_hendelse] local cache write failed for %s: %s", event_id, e)

        img_b64 = base64.b64encode(img_bytes).decode()

        # Always route through image_store → /api/image/{id}. Never fall back to
        # /api/frigate_snapshot/{event_id} as the chat-facing URL — neither
        # Home.tsx's image regex nor router_generate.py's safety-net regex
        # recognize that URL shape, so it would render as dead text, not a
        # picture (confirmed via full-codebase sweep, see plan Research).
        _img_url = ""
        try:
            _uid = arguments.get("_user_id", "global")
            _img_id = _save_image(img_bytes, _uid, "frigate", ext="jpg")  # the folder decides the kind (camera)
            _img_url = f"/api/image/{_img_id}"
        except Exception as _e:
            logger.warning(
                "[vis_hendelse] save_image feilet for %s (user=%s): %s",
                event_id,
                arguments.get("_user_id"),
                _e,
            )

        stored_analysis = await asyncio.to_thread(_stored_words, event_id)  # P144 phase 6: frigate.db, not the old log
        # The picture goes out first (P139 reopened 2026-11-16); its caption is the stored words, which cost nothing.
        show = asyncio.create_task(_show_on_screens(arguments, img_b64, stored_analysis or "", lang))
        await asyncio.sleep(0)  # let the screen send start before the model call
        if _wants_analysis(arguments) and _may_analyse(arguments.get("_user_id", "global")):
            context = t("cam_prompt_show_event", lang, event_id=event_id)
            if stored_analysis:
                context += t("cam_prompt_show_event_prior_analysis", lang, analysis=stored_analysis)
            context += t("cam_prompt_show_event_question", lang)
            prompt_text = _vlm_prompt(context, arguments, lang)
            try:  # P144 phase 6: the camera pipe's model path — the `frigate` role (Settings → LLM/Models), tag stripped
                result = await ask_llm_role("frigate", prompt_text, images=[img_b64],
                                            rid=f"rid-frigate-show-{event_id}")
                analysis = (model_enforcement.strip_language_tag(result.get("text") or "").strip()
                            or t("cam_empty_vlm_response", lang))
            except Exception as e:
                analysis = t("cam_vlm_error", lang, error=e)
        else:
            analysis = t("cam_not_analysed", lang)
            if _wants_analysis(arguments):  # N41: wanted, but not for this role — said, and the stored words shown
                analysis = t("cam_analyse_not_for_role", lang) + "\n" + analysis
            if stored_analysis:
                analysis += "\n" + t("cam_stored_analysis", lang, analysis=stored_analysis)
        shown = await show
        out = analysis + (f"\n\n{shown}" if shown else "")
        if _img_url:
            return f"{out}\n\n" + t("exec_image_ready", lang, image_id=_img_url.rsplit("/", 1)[-1])
        return out

    return f"Unknown action for camera: '{action}'. Valid: snapshot, events, frigate, list, analyze, show_event."


CAMERA_PICTURE_S = 30  # how long a camera picture stays on a TV unless Kåre gives `duration`


async def _noticed(reading, lang: str) -> str:
    """DT04 R0.6d (P144 R05): a reading of who the cameras saw, plus one line when someone in the household opted out of
    recognition — so an unknown person is not turned into a theory. No person, time, camera or count in the line."""
    text = await reading
    if not isinstance(text, str) or not await asyncio.to_thread(frigate_consent.someone_opted_out):
        return text
    return f"{text}\n\n{t('fq_opt_out_notice', lang)}"


async def _memory(action: str, arguments: dict, uid: str, lang: str) -> str:
    return await _noticed(_memory_read(action, arguments, uid, lang), lang)


async def _memory_read(action: str, arguments: dict, uid: str, lang: str) -> str:
    """The Frigate memory (P144-s08): last_seen / episodes / episode — read only, names only for logged-in household
    users (frigate_user/query.py). Store reads run in a thread (rule 10)."""
    _audit("camera_access", uid, f"action={action}")
    names = await asyncio.to_thread(frigate_query.names_allowed, uid)
    if action == "last_seen":
        person = str(arguments.get("name") or arguments.get("navn") or "").strip()
        return await asyncio.to_thread(frigate_query.last_seen, person, names=names, lang=lang)
    if action == "episode":
        return await asyncio.to_thread(frigate_query.episode, arguments.get("episode_id"), names=names, lang=lang)
    tz = get_local_tz()
    raw_from, raw_to = str(arguments.get("from") or "").strip(), str(arguments.get("to") or "").strip()
    now = now_local()
    start = (frigate_query.parse_local(raw_from, tz) if raw_from
             else now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    end = frigate_query.parse_local(raw_to, tz, end=True) if raw_to else now.timestamp()
    for value, parsed in ((raw_from, start), (raw_to, end)):
        if parsed is None:
            return t("fq_bad_time", lang, value=value)
    try:
        limit = int(arguments.get("count") or frigate_query.DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = frigate_query.DEFAULT_LIMIT
    place = str(arguments.get("camera") or arguments.get("place") or "").strip() or None
    person = str(arguments.get("name") or "").strip() or None
    return await asyncio.to_thread(frigate_query.episodes, start, end, place=place, person=person, limit=limit,
                                   names=names, lang=lang)


def _duration(arguments: dict) -> int:
    try:
        return max(1, min(int(arguments.get("duration") or CAMERA_PICTURE_S), 3600))
    except (TypeError, ValueError):
        return CAMERA_PICTURE_S


_NO = ("false", "0", "no", "nei", "nein", "off")
_YES = ("true", "1", "yes", "ja", "on")


def _wants_analysis(arguments: dict) -> bool:
    """P139 reopened (owner decision 2026-11-16: pictures with and without analysis): `analyse` decides; not given → a picture sent to a
    screen with no question is shown only, everything else is analysed (the old behaviour)."""
    raw = arguments.get("analyse")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in _NO:
        return False
    if isinstance(raw, str) and raw.strip().lower() in _YES:
        return True
    screen = str(arguments.get("screen") or "").strip()
    has_query = bool(str(arguments.get("query") or arguments.get("spørsmål") or "").strip())
    return not (screen and screen.lower() not in ("none", "ingen", "keine") and not has_query)


def _vlm_prompt(base: str, arguments: dict, lang: str) -> str:
    """The camera's own prompt, plus the user's question about the picture when there is one. `query` never replaces
    the prompt (BUG-log 2026-10-19: an instruction there made the model answer the instruction)."""
    query = str(arguments.get("query") or arguments.get("spørsmål") or "").strip()
    return base + t("cam_prompt_user_question", lang, query=query) if query else base


def _chat_picture(img_b64: str, uid: str, lang: str) -> str:
    """The picture in the chat too (when no analysis was asked for, the picture is the answer)."""
    try:
        img_id = _save_image(base64.b64decode(img_b64), uid, "frigate", ext="jpg")  # camera picture → frigate folder
        return t("exec_image_ready", lang, image_id=img_id)
    except Exception as exc:
        logger.warning("[camera] save_image failed (user=%s): %s", uid, exc)
        return ""


def _match_camera(name: str, cams: list[dict]) -> tuple[str, str] | list[dict]:
    """(api_name, display) for the user's words — api name, friendly name, or a word in it; several hits → the hits."""
    q = name.lower().strip()
    if not q:
        return cams
    exact = [c for c in cams if q in (c["api_name"].lower(), c["friendly_name"].lower())]
    hits = exact or [c for c in cams if q in c["friendly_name"].lower() or q in c["api_name"].lower()]
    if len(hits) == 1:
        return hits[0]["api_name"], hits[0]["friendly_name"]
    return hits or cams


async def _live_screen_targets(arguments: dict) -> tuple[list[str], list]:
    """Screen node(s) for `screen` (user's words / node:<id> / place) — else where the user is (input node's
    image_out). Same lookup the still-picture path uses (`_show_on_screens`), without changing it."""
    screen = str(arguments.get("screen") or "").strip()
    displays = [n["id"] for n in await get_display_nodes()]
    if not screen:
        return node_image_targets(str(arguments.get("_source_node") or ""), displays), []
    if screen.startswith("node:"):
        nid = screen[5:]
        return ([nid] if nid in displays else []), []
    if screen in displays:
        return [screen], []
    targets, choices = resolve_display_node(screen, displays)
    if choices:
        return [], choices
    if not targets:
        place = rooms.get_register().resolve_place(screen)
        targets = [d for d in displays if place and rooms.node_place(d)[0] == place]
    return targets, []


def _restored_text(restored: dict, lang: str) -> str:
    if not restored:
        return ""
    if restored.get("skipped") and "left alone" in str(restored.get("skipped")):
        return t("cam_live_restored_skipped", lang)
    if restored.get("standby") or restored.get("action") == "power_off":
        return t("cam_live_restored_standby", lang)
    if restored.get("relaunched"):
        return t("cam_live_restored_app", lang, app=restored["relaunched"])
    return t("cam_live_restored_closed", lang)


def _before_text(before, screen: str, lang: str) -> str:
    """DT04-S05q: what the screen showed before the live picture, as the bridge saw it — never «as it was» (with an old
    video in the chat history the model filled that in as a video that would carry on)."""
    b = before if isinstance(before, dict) else {}
    kind = b.get("kind")
    if kind == "app" and b.get("app"):
        return t("cam_live_before_app", lang, screen=screen, app=b["app"])
    if kind in ("idle", "standby"):
        return t(f"cam_live_before_{kind}", lang, screen=screen)
    return t("cam_live_before_unknown", lang, screen=screen)


async def _live_on_screen(action: str, arguments: dict, uid: str, lang: str) -> str:
    """P151: `live` starts a live camera on one screen, `live_stop` ends it. Kåre only says «showing» when the screen
    page itself reported a moving picture (N26). Only when the user asks (P139 D3)."""
    targets, choices = await _live_screen_targets(arguments)
    if choices:
        return t("media_target_ambiguous", lang, client=arguments.get("screen"), choices=choices_text(choices))

    if action == "live_stop":
        if not targets:
            act = await cast_client.call("/screen/active", {"node": ""}, timeout=10)
            targets = [s["node"] for s in act.get("sessions") or [] if s.get("kind") == "camera"]
            if not targets:
                return t("cam_live_nothing", lang, where="")
        lines = []
        for nid in targets:
            res = await cast_client.call("/screen/stop", {"node": nid}, timeout=60)
            if not res.get("stopped"):
                lines.append(t("cam_live_nothing", lang, where=f" ({nid})"))
                continue
            _audit("camera_access", uid, f"action=live_stop screen={nid}")
            restored = res.get("restored") or {}
            text = _restored_text(restored, lang)
            if restored.get("needs_standby") is False and not restored.get("relaunched"):
                text += " " + t("cam_live_input_note", lang)
            lines.append(t("cam_live_stopped", lang, screen=nid, restored=text.strip()))
        return "\n".join(lines)

    cams = await get_cameras()
    picked = _match_camera(str(arguments.get("camera") or ""), cams)
    if isinstance(picked, list):
        return t("cam_live_which_camera", lang, cams=", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in picked))
    api, display = picked
    if len(targets) != 1:
        screens = ", ".join(n["id"] for n in await get_display_nodes())
        return t("cam_live_need_screen", lang, screens=screens)
    nid = targets[0]
    payload: dict = {"node": nid, "camera": api}
    try:
        if arguments.get("minutes") not in (None, ""):
            payload["minutes"] = float(arguments["minutes"])
    except (TypeError, ValueError):
        pass
    _audit("camera_access", uid, f"action=live camera={api} screen={nid}")
    res = await cast_client.call("/screen/camera", payload, timeout=90)
    if res.get("off"):
        return t("cam_live_off", lang)
    err = str(res.get("error") or "")
    if err in ("not_paired", "page_not_open"):
        key = "cam_live_not_paired" if err == "not_paired" else "cam_live_page_not_open"
        return t(key, lang, screen=nid, url=res.get("setup_url") or "")
    sess = res.get("session") or {}
    if res.get("ok") and res.get("showing"):
        how = t("cam_live_how_direct" if sess.get("mode") == "mse" else "cam_live_how_reencoded", lang)
        return t("cam_live_ok", lang, camera=display, screen=nid, how=how, size=sess.get("size") or "?",
                 minutes=f"{float(res.get('minutes') or 0):g}", before=_before_text(res.get("before"), nid, lang))
    if res.get("ok"):
        return t("cam_live_not_confirmed", lang, camera=display, screen=nid, error=err or "?")
    return t("cam_live_failed", lang, camera=display, screen=nid, error=err or "?")


async def _show_on_screens(arguments: dict, img_b64: str, caption: str, lang: str) -> str:
    """P139 phase 3: the event picture full-screen on a screen too — the one named in `screen`, else the screen where
    the user is (the input node's `image_out`); «none» or a chat with no node = chat only. Says what happened (N26)."""
    screen = str(arguments.get("screen") or "").strip()
    if screen.lower() in ("none", "ingen", "keine"):
        return ""
    displays = [n["id"] for n in await get_display_nodes()]
    if screen:
        targets = [screen] if screen in displays else []
        if not targets:
            targets, choices = resolve_display_node(screen, displays)
            if choices:
                return t("media_target_ambiguous", lang, client=screen, choices=choices_text(choices))
        if not targets:
            place = rooms.get_register().resolve_place(screen)
            targets = [d for d in displays if place and rooms.node_place(d)[0] == place]
        if not targets:
            return t("cam_screen_not_found", lang, screen=screen)
    else:
        targets = node_image_targets(str(arguments.get("_source_node") or ""), displays)
    lines = []
    for nid in targets:
        res = await send_display(nid, text=caption[:300], title="Kamera", image_b64=img_b64, job="picture",
                                 duration=_duration(arguments))
        if res.get("ok"):
            lines.append(t("cam_shown_on", lang, node=nid, channel=res.get("channel") or res.get("method") or "?"))
        else:
            lines.append(t("cam_show_failed", lang, node=nid, error=res.get("error") or "?"))
    return "\n".join(lines)


async def _compat_hent_snapshot(arguments: dict, lang: str = "nb") -> str:
    scope = arguments.get("scope", "one")
    ts = now_local().strftime("%d.%m.%Y %H:%M:%S")
    if scope in ("all", "alle"):
        prompt_text = arguments.get("spørsmål", "").strip() or t("cam_prompt_all", lang)
        cams = await get_cameras()
        if not cams:
            return t("cam_no_cameras", lang)

        async def _fetch(cam):
            try:
                b64 = await fetch_snapshot_b64(cam["api_name"])
                return cam["friendly_name"], b64
            except Exception:
                return cam["friendly_name"], None

        fetched = await asyncio.gather(*[_fetch(c) for c in cams])
        images = [b64 for _, b64 in fetched if b64]
        cam_names = [n for n, b64 in fetched if b64]
        failed = [n for n, b64 in fetched if b64 is None]
        if not images:
            return t("cam_fetch_failed", lang, ts=ts)
        prompt = t("cam_prefix_all", lang, cam_list=", ".join(cam_names)) + prompt_text
        try:
            vlm_result = await ask_llm(prompt=prompt, images=images)
            description = vlm_result.get("text", "").strip() or t("cam_empty_vlm", lang)
        except Exception as e:
            return t("cam_analysis_failed", lang, ts=ts, error=e)
        result = f"[Alle kameraer — {ts}]\n{description}"
        if failed:
            result += f"\n\nKunne ikke hente: {', '.join(failed)}"
        return result

    kamera = arguments.get("kamera", "").strip()
    prompt_text = arguments.get("spørsmål", "").strip() or t("cam_prompt_single", lang)
    if not kamera:
        cams = await get_cameras()
        cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
        return t("cam_specify_name", lang, cam_list=cam_list)
    try:
        img_b64 = await fetch_snapshot_b64(kamera)
    except ValueError as e:
        cams = await get_cameras()
        cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
        return t("cam_not_found", lang, error=e, cam_list=cam_list)
    except Exception as e:
        return t("cam_snapshot_error", lang, camera=kamera, error=e)
    try:
        vlm_result = await ask_llm(prompt=prompt_text, images=[img_b64])
        description = vlm_result.get("text", "").strip() or t("cam_empty_vlm", lang)
    except Exception as e:
        return t("cam_snapshot_analysis_failed", lang, ts=ts, error=e)
    return f"[{kamera} — {ts}]\n{description}"


def event_score(ev: dict) -> float:
    """R141: Frigate 0.14+ keeps the score in `data` (top_score / score); older events at the top level."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for v in (ev.get("top_score"), data.get("top_score"), ev.get("score"), data.get("score"), ev.get("confidence")):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v)
    return 0.0


def _label(raw, lang: str) -> tuple[str | None, str | None]:
    """R142: (Frigate label, None) for the user's word — or (None, message listing the known labels) when no camera
    tracks it. No word = no filter."""
    word = str(raw or "").strip()
    if not word:
        return None, None
    known = frigate_labels.known_labels()
    if not known:
        return word, None  # no camera cards: pass the word on as before
    hits = frigate_labels.resolve(word, known)
    if hits:
        return hits[0], None
    return None, t("cam_label_unknown", lang, word=word, labels=", ".join(known))


async def _compat_hent_frigate_hendelser(arguments: dict, lang: str = "nb") -> str:
    kamera = arguments.get("kamera", "").strip() or None
    label, unknown = _label(arguments.get("label"), lang)
    if unknown:
        return unknown
    antall = min(int(arguments.get("antall", 10)), 50)
    kun_ansikter = bool(arguments.get("kun_ansikter", False))
    try:
        if kun_ansikter:
            events = await fetch_face_events(limit=antall)
        else:
            events = await fetch_events(camera=kamera, label=label, limit=antall)
    except Exception as e:
        return t("cam_frigate_error", lang, error=e)
    if not events:
        return t("cam_no_frigate_events", lang)
    lines = []
    for ev in events:
        ts_raw = ev.get("start_time") or ev.get("ts", 0)
        try:
            ts_str = local_from_ts(float(ts_raw)).strftime("%d.%m %H:%M:%S")
        except Exception:
            ts_str = str(ts_raw)
        cam = ev.get("camera_friendly") or ev.get("camera", "?")
        lbl = ev.get("label", "?")
        conf = event_score(ev)
        face = ev.get("_face_name") or ev.get("sub_label") or ""
        face_str = f" — ansikt: {face}" if face else ""
        eid = ev.get("id", "")
        eid_str = f" [event_id: {eid}]" if eid else ""
        lines.append(f"[{ts_str}] {cam}: {lbl} ({int(float(conf) * 100)}%){face_str}{eid_str}")
    return "\n".join(lines)


async def _face_events(arguments: dict, uid: str | None, lang: str) -> str:
    """J21b reader 2: who the cameras saw by name, last N hours, from frigate.db (module 0) — one line per person per
    visit; names only for household users, nobody who opted out. Module 0 off → it says so. Store reads in a thread."""
    raw_name = str(arguments.get("name") or arguments.get("navn") or "").strip()
    hours = min(int(arguments.get("hours_back", arguments.get("timer_tilbake", 24))), 48)
    names = await asyncio.to_thread(frigate_query.names_allowed, uid)
    if not await asyncio.to_thread(frigate_query.available):
        return t("fq_unavailable", lang)
    found = await asyncio.to_thread(frigate_query.person_sessions, hours, raw_name, names=names, lang=lang)
    if isinstance(found, str):
        return found
    if not found:
        label = f" for «{raw_name}»" if raw_name else ""
        return t("cam_no_events_filtered", lang, label=label, hours=hours)
    header = t("cam_events_header", lang, hours=hours, count=len(found), suffix="r" if len(found) != 1 else "")
    return header + "\n" + "\n".join(found)


async def _compat_les_kamerahendelser(arguments: dict, lang: str = "nb") -> str:
    return await _noticed(_face_events(arguments, arguments.get("_user_id"), lang), lang)  # J21b reader 2


async def _compat_liste_kameraer(arguments: dict, lang: str = "nb") -> str:
    try:
        cams = await get_cameras()
    except Exception as e:
        return t("cam_list_error", lang, error=e)
    if not cams:
        return t("cam_no_frigate_events", lang)
    lines = [f"  {c['friendly_name']} → {c['api_name']}" for c in cams]
    return t("cam_list_header", lang, count=len(cams)) + "\n" + "\n".join(lines)
