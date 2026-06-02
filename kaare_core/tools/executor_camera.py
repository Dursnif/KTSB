"""
Camera executor module — Frigate snapshots, events, VLM analysis.
Exported: CAMERA_TOOLS, dispatch()
"""

import asyncio
import base64
import json as _json
import logging
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

from adapters.frigate_adapter import fetch_snapshot_b64, get_cameras, fetch_events, fetch_face_events
from adapters.llm_adapter import ask_llm
from kaare_core.tools.i18n import t, get_lang

logger = logging.getLogger(__name__)

CAMERA_TOOLS = {
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


async def dispatch(name: str, arguments: Dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "kamera":
        return await _kamera(arguments, lang)

    if name == "hent_snapshot":
        return await _compat_hent_snapshot(arguments, lang)

    if name == "hent_frigate_hendelser":
        return await _compat_hent_frigate_hendelser(arguments, lang)

    if name == "les_kamerahendelser":
        return await _compat_les_kamerahendelser(arguments, lang)

    if name == "liste_kameraer":
        return await _compat_liste_kameraer(arguments, lang)

    return f"[executor_camera] Unknown tool: '{name}'"


async def _kamera(arguments: Dict, lang: str = "nb") -> str:
    action = arguments.get("action", "")

    if action == "snapshot":
        scope = arguments.get("scope", "ett")
        ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if scope == "alle":
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
            prompt = t("cam_prefix_all", lang, cam_list=', '.join(cam_names)) + prompt_text
            try:
                vlm_result = await ask_llm(prompt=prompt, images=images)
                if not vlm_result.get("ok"):
                    return t("cam_analysis_failed", lang, ts=ts, error="VLM returned empty response")
                description = vlm_result.get("text", "").strip() or t("cam_empty_vlm", lang)
            except Exception as e:
                return t("cam_analysis_failed", lang, ts=ts, error=e)
            result = f"[Alle kameraer — {ts}]\n{description}"
            if failed:
                result += f"\n\nKunne ikke hente: {', '.join(failed)}"
            return result

        kamera_navn = (arguments.get("camera") or arguments.get("kamera") or "").strip()
        prompt_text = (arguments.get("query") or arguments.get("spørsmål") or "").strip() or t("cam_prompt_single", lang)
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
        try:
            vlm_result = await ask_llm(prompt=prompt_text, images=[img_b64])
            if not vlm_result.get("ok"):
                return t("cam_snapshot_analysis_failed", lang, ts=ts, error="VLM returned empty response")
            description = vlm_result.get("text", "").strip() or t("cam_empty_vlm", lang)
        except Exception as e:
            return t("cam_snapshot_analysis_failed", lang, ts=ts, error=e)
        return f"[{kamera_navn} — {ts}]\n{description}"

    if action == "events":
        navn_filter = (arguments.get("name") or arguments.get("navn") or "").strip().lower()
        timer_tilbake = min(int(arguments.get("hours_back", arguments.get("timer_tilbake", 24))), 48)
        face_path = Path("/kaare/state/argus/face_events.txt")
        if not face_path.exists():
            return t("cam_no_events", lang)
        try:
            raw_lines = [l for l in face_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except Exception as e:
            return t("cam_events_read_error", lang, error=e)
        if not raw_lines:
            return t("cam_no_events", lang)
        try:
            cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
            _loc = cfg.get("location") or cfg.get("lokasjon", {})
            local_tz = ZoneInfo(_loc.get("timezone") or _loc.get("tidssone", "Europe/Oslo"))
        except Exception:
            local_tz = ZoneInfo("Europe/Oslo")
        cutoff = datetime.now(local_tz) - timedelta(hours=timer_tilbake)
        filtered = []
        for line in raw_lines:
            try:
                inner = line[1: line.index("]")]
                ts_part = inner.split("→")[0].strip()
                dt = datetime.strptime(ts_part, "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
                if dt < cutoff:
                    continue
            except Exception:
                pass
            if navn_filter and navn_filter not in line.lower():
                continue
            filtered.append(line)
        if not filtered:
            label = f" for «{arguments.get('name') or arguments.get('navn', '')}»" if (arguments.get("name") or arguments.get("navn")) else ""
            return t("cam_no_events_filtered", lang, label=label, hours=timer_tilbake)
        count = len(filtered)
        suffix = "r" if count != 1 else ""
        header = t("cam_events_header", lang, hours=timer_tilbake, count=count, suffix=suffix)
        return header + "\n" + "\n".join(filtered)

    if action == "frigate":
        kamera_navn = (arguments.get("camera") or arguments.get("kamera") or "").strip() or None
        label = arguments.get("label", "").strip() or None
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
                ts_str = datetime.fromtimestamp(float(ts_raw)).strftime("%d.%m %H:%M:%S")
            except Exception:
                ts_str = str(ts_raw)
            cam = ev.get("camera_friendly") or ev.get("camera", "?")
            lbl = ev.get("label", "?")
            conf = ev.get("top_score") or ev.get("score") or ev.get("confidence") or 0
            face = ev.get("_face_name") or ev.get("sub_label") or ""
            face_str = f" — ansikt: {face}" if face else ""
            lines.append(f"[{ts_str}] {cam}: {lbl} ({int(float(conf)*100)}%){face_str}")
        return "\n".join(lines)

    if action == "list":
        try:
            cams = await get_cameras()
        except Exception as e:
            return t("cam_list_error", lang, error=e)
        if not cams:
            return t("cam_no_frigate_events", lang)
        lines = [f"  {c['friendly_name']} → {c['api_name']}" for c in cams]
        return t("cam_list_header", lang, count=len(cams)) + "\n" + "\n".join(lines)

    if action == "analyze":
        antall = min(int(arguments.get("count", arguments.get("antall", 10))), 50)
        log_path = Path("/kaare/logs/frigate_analysis.log")
        if not log_path.exists():
            return t("cam_no_analysis", lang)
        try:
            raw_lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            entries = [_json.loads(l) for l in raw_lines][-antall:]
        except Exception as e:
            return t("cam_analysis_log_error", lang, error=e)
        if not entries:
            return t("cam_no_analysis_in_log", lang)
        parts = []
        for e in reversed(entries):
            ts = (e.get("ts") or "")[:16].replace("T", " ")
            cam = e.get("display_name") or e.get("camera", "?")
            label = e.get("label", "?")
            dur = float(e.get("duration", 0))
            sub = f" — {e['sub_label']}" if e.get("sub_label") else ""
            analysis = e.get("analysis", "")
            eid = e.get("event_id", "")
            parts.append(f"[{ts}] {cam} — {label}{sub} ({dur:.0f}s)\n{analysis}\nevent_id: {eid}")
        return "\n\n---\n\n".join(parts)

    if action == "show_event":
        event_id = arguments.get("event_id", "").strip()
        if not event_id:
            return t("cam_event_id_required", lang)
        snap_path = Path("/kaare/state/frigate_snapshots") / f"{event_id}.jpg"
        if not snap_path.exists():
            return t("cam_snapshot_not_found", lang, event_id=event_id)
        try:
            img_bytes = snap_path.read_bytes()
            img_b64 = base64.b64encode(img_bytes).decode()
        except Exception as e:
            return t("cam_snapshot_read_error", lang, error=e)

        _img_url = ""
        try:
            from kaare_core.image_store import save_image as _save_image
            _uid = arguments.get("_user_id", "global")
            _img_id = _save_image(img_bytes, _uid, "input", ext="jpg")
            _img_url = f"/api/image/{_img_id}"
        except Exception as _e:
            logger.warning("[vis_hendelse] save_image feilet for %s (user=%s): %s",
                           event_id, arguments.get("_user_id"), _e)
        if not _img_url:
            _img_url = f"/api/frigate_snapshot/{event_id}"

        stored_analysis = ""
        try:
            log_path = Path("/kaare/logs/frigate_analysis.log")
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = _json.loads(line)
                if entry.get("event_id") == event_id:
                    stored_analysis = entry.get("analysis", "")
                    break
        except Exception:
            pass
        context = t("cam_prompt_show_event", lang, event_id=event_id)
        if stored_analysis:
            context += t("cam_prompt_show_event_prior_analysis", lang, analysis=stored_analysis)
        context += t("cam_prompt_show_event_question", lang)
        prompt_text = (arguments.get("query") or arguments.get("spørsmål") or "").strip() or context
        try:
            result = await ask_llm(prompt_text, images=[img_b64])
            analysis = result.get("text", "").strip() or t("cam_empty_vlm_response", lang)
        except Exception as e:
            return t("cam_vlm_error", lang, error=e)
        if _img_url:
            return f"{analysis}\n\nBildet er klart: {_img_url}"
        return analysis

    return f"Unknown action for kamera: '{action}'. Valid: snapshot, events, frigate, list, analyze, show_event."


async def _compat_hent_snapshot(arguments: Dict, lang: str = "nb") -> str:
    scope = arguments.get("scope", "ett")
    ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    if scope == "alle":
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
        prompt = t("cam_prefix_all", lang, cam_list=', '.join(cam_names)) + prompt_text
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


async def _compat_hent_frigate_hendelser(arguments: Dict, lang: str = "nb") -> str:
    kamera = arguments.get("kamera", "").strip() or None
    label = arguments.get("label", "").strip() or None
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
            ts_str = datetime.fromtimestamp(float(ts_raw)).strftime("%d.%m %H:%M:%S")
        except Exception:
            ts_str = str(ts_raw)
        cam = ev.get("camera_friendly") or ev.get("camera", "?")
        lbl = ev.get("label", "?")
        conf = ev.get("top_score") or ev.get("score") or ev.get("confidence") or 0
        face = ev.get("_face_name") or ev.get("sub_label") or ""
        face_str = f" — ansikt: {face}" if face else ""
        lines.append(f"[{ts_str}] {cam}: {lbl} ({int(float(conf)*100)}%){face_str}")
    return "\n".join(lines)


async def _compat_les_kamerahendelser(arguments: Dict, lang: str = "nb") -> str:
    navn_filter   = (arguments.get("navn") or "").strip().lower()
    timer_tilbake = min(int(arguments.get("timer_tilbake", 24)), 48)
    face_path     = Path("/kaare/state/argus/face_events.txt")
    if not face_path.exists():
        return t("cam_no_events", lang)
    try:
        raw_lines = [l for l in face_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception as e:
        return t("cam_events_read_error", lang, error=e)
    if not raw_lines:
        return t("cam_no_events", lang)
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
        local_tz = ZoneInfo(cfg["lokasjon"]["tidssone"])
    except Exception:
        local_tz = ZoneInfo("Europe/Oslo")
    cutoff = datetime.now(local_tz) - timedelta(hours=timer_tilbake)
    filtered = []
    for line in raw_lines:
        try:
            inner   = line[1: line.index("]")]
            ts_part = inner.split("→")[0].strip()
            dt      = datetime.strptime(ts_part, "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
            if dt < cutoff:
                continue
        except Exception:
            pass
        if navn_filter and navn_filter not in line.lower():
            continue
        filtered.append(line)
    if not filtered:
        label = f" for «{arguments['navn']}»" if arguments.get("navn") else ""
        return t("cam_no_events_filtered", lang, label=label, hours=timer_tilbake)
    count = len(filtered)
    suffix = "r" if count != 1 else ""
    header = t("cam_events_header", lang, hours=timer_tilbake, count=count, suffix=suffix)
    return header + "\n" + "\n".join(filtered)


async def _compat_liste_kameraer(arguments: Dict, lang: str = "nb") -> str:
    try:
        cams = await get_cameras()
    except Exception as e:
        return t("cam_list_error", lang, error=e)
    if not cams:
        return t("cam_no_frigate_events", lang)
    lines = [f"  {c['friendly_name']} → {c['api_name']}" for c in cams]
    return t("cam_list_header", lang, count=len(cams)) + "\n" + "\n".join(lines)
