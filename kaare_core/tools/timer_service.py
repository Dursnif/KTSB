"""
Kåres timer-motor v3 (P40).

Støtter:
  - in_seconds: enkel forsinkelse
  - at_time:    norsk klokkeslett/dato — "07:30", "fredag 08:00", "2026-05-01 09:00"
  - repeat:     "hourly" | "daily" | "weekdays" | "weekend" | "weekly"

Leveringsmodell:
  action:     hva timeren gjør ved avfyring
              "tts_response" | "ha_action" | "llm_task" | "none"
  notify_via: liste over leveringskanaler — ["tts"] | ["chat"] | ["tts", "chat"]

Alle timere (inkl. engangstimere) persisteres til disk og gjenopprettes ved restart.
"""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from kaare_core.config import get_service as _svc, get_settings as _get_settings
from kaare_core.tools.i18n import t, get_lang
from kaare_core.users.profile_manager import get_profile_flag as _get_profile_flag
from kaare_core.users.store import get_user as _get_user

_timers: Dict[str, Dict[str, Any]] = {}
_TOOL_LOG          = Path("/kaare/logs/tool_calls.log")
_PERSIST_PATH      = Path("/kaare/state/timers.json")
_PENDING_DIR       = Path("/kaare/state/pending_notifications")
_ACTION_QUEUE_PATH = Path("/kaare/state/action_queue.json")
_API_BASE          = _svc("internal", "kaare_api")

VALID_REPEATS = {"hourly", "daily", "weekdays", "weekend", "weekly"}
VALID_ACTIONS = {"tts_response", "ha_action", "llm_task", "none"}
VALID_CHANNELS = {"tts", "chat"}

_REPEAT_LABELS = {
    "hourly":   "hver time",
    "daily":    "daglig",
    "weekdays": "hverdager",
    "weekend":  "helg",
    "weekly":   "ukentlig",
}

_DAYS_NO = {
    "mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3,
    "fredag": 4, "lørdag": 5, "søndag": 6,
}


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(event: str, **fields):
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with open(_TOOL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Tidsparsing ───────────────────────────────────────────────────────────────

def _parse_at_time(at_time: str) -> Optional[datetime]:
    now = datetime.now()
    s = at_time.strip().lower()

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        fire_dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if fire_dt <= now:
            fire_dt += timedelta(days=1)
        return fire_dt

    m = re.fullmatch(r"(\w+)\s+(\d{1,2}):(\d{2})", s)
    if m:
        day_name = m.group(1)
        if day_name not in _DAYS_NO:
            return None
        h, mi = int(m.group(2)), int(m.group(3))
        target_wd = _DAYS_NO[day_name]
        days_ahead = (target_wd - now.weekday()) % 7
        if days_ahead == 0:
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if candidate <= now:
                days_ahead = 7
            else:
                return candidate
        return (now + timedelta(days=days_ahead)).replace(hour=h, minute=mi, second=0, microsecond=0)

    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
            if m.group(2):
                d = d.replace(hour=int(m.group(2)), minute=int(m.group(3)))
            return d
        except ValueError:
            return None

    return None


def _next_occurrence(fires_at: datetime, repeat: str) -> datetime:
    if repeat == "hourly":
        return fires_at + timedelta(hours=1)
    if repeat == "daily":
        return fires_at + timedelta(days=1)
    if repeat == "weekly":
        return fires_at + timedelta(weeks=1)
    if repeat == "weekdays":
        nxt = fires_at + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if repeat == "weekend":
        nxt = fires_at + timedelta(days=1)
        while nxt.weekday() < 5:
            nxt += timedelta(days=1)
        return nxt
    return fires_at + timedelta(days=1)


def _fmt_delay(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}t {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ── Persistens ────────────────────────────────────────────────────────────────

def _persist_timers():
    """Skriv alle aktive timere til disk (uten asyncio Task-objekt)."""
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {k: v for k, v in info.items() if k != "task"}
            for info in _timers.values()
        ]
        _PERSIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# Beholdes for bakoverkompatibilitet med kode som kaller den direkte
_persist_repeating = _persist_timers


def _clear_persist(timer_id: str):
    _persist_timers()


# ── Pending notifications (chat-kanal) ────────────────────────────────────────

def _pending_path(user_id: str) -> Path:
    return _PENDING_DIR / f"{user_id}.json"


def _write_pending(user_id: str, notif: Dict[str, Any]) -> None:
    """Legg til en pending notification for brukeren."""
    try:
        path = _pending_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append(notif)
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        _log("pending_write_error", error=str(e))


def get_pending_notifications(user_id: str) -> List[Dict[str, Any]]:
    """Returnerer alle ukvitterte notifications for brukeren."""
    path = _pending_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [n for n in data if not n.get("acked", False)]
    except Exception:
        return []


def ack_notification(notif_id: str, user_id: str) -> bool:
    """Kvitter en pending notification. Returnerer True hvis funnet."""
    path = _pending_path(user_id)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        found = False
        for n in data:
            if n.get("id") == notif_id:
                n["acked"] = True
                found = True
        if found:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return found
    except Exception:
        return False


# ── Levering ──────────────────────────────────────────────────────────────────

async def _deliver_tts(timer_info: Dict[str, Any]) -> None:
    """Spill tts_text på target_node via voice bridge POST /speak."""
    try:
        import httpx
        tts_text = timer_info.get("tts_text", "")
        if not tts_text:
            return
        # target_node overstyrer source_node; fallback til "local" (aplay på AI-PC)
        target = timer_info.get("target_node") or timer_info.get("source_node") or "local"
        user_id = timer_info.get("user_id", "global")
        lang = get_lang(user_id)
        voice_url = _svc("internal", "voice_bridge")
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{voice_url}/speak", json={"text": tts_text, "target": target, "lang": lang})
        _log("tts_delivered", timer_id=timer_info.get("id"), target=target, status=r.status_code)
    except Exception as e:
        _log("tts_deliver_error", timer_id=timer_info.get("id"), error=str(e))


async def _deliver_ha(timer_info: Dict[str, Any]) -> None:
    """Kall HA gateway POST /api/ha_apply med ha_payload.
    ha_payload format: {"action": "turn_off", "entity_id": "light.workshop", "params": {}}
    """
    try:
        import httpx
        ha_payload = timer_info.get("ha_payload")
        if not ha_payload or not ha_payload.get("action") or not ha_payload.get("entity_id"):
            _log("ha_deliver_skip", timer_id=timer_info.get("id"), reason="missing action or entity_id")
            return
        ha_url = _svc("internal", "ha_gateway")
        body = {
            "action":    ha_payload["action"],
            "entity_id": ha_payload["entity_id"],
            "params":    ha_payload.get("params", {}),
            "source":    "timer",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{ha_url}/api/ha_apply", json=body)
        _log("ha_delivered", timer_id=timer_info.get("id"),
             action=body["action"], entity=body["entity_id"], status=r.status_code)
    except Exception as e:
        _log("ha_deliver_error", timer_id=timer_info.get("id"), error=str(e))


async def _deliver_chat(timer_info: Dict[str, Any], rid: str = "") -> None:
    """Skriv pending notification til brukerens kø."""
    user_id = timer_info.get("user_id", "global")
    message = timer_info.get("tts_text") or timer_info.get("prompt", "")
    notif = {
        "id":         f"notif_{uuid.uuid4().hex[:8]}",
        "timer_id":   timer_info.get("id"),
        "rid":        rid,
        "message":    message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "acked":      False,
    }
    _write_pending(user_id, notif)
    _log("chat_notify_queued", rid=rid, timer_id=timer_info.get("id"), user_id=user_id,
         notif_id=notif["id"])


async def _deliver_llm(timer_id: str, prompt: str, user_id: str) -> bool:
    """Kall /api/generate med prompt (llm_task). Returnerer True ved suksess."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_API_BASE}/api/generate",
                json={"prompt": prompt, "source": "self_timer", "user_id": user_id},
            )
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}")
            response_text = r.json().get("text", "")[:120]
        _log("llm_deliver_done", timer_id=timer_id, result_preview=response_text)
        return True
    except Exception as e:
        _log("llm_deliver_error", timer_id=timer_id, error=str(e))
        return False


# ── LLM action queue (Fase 4) ──────────────────────────────────────────────────

def _load_action_queue() -> List[Dict[str, Any]]:
    if not _ACTION_QUEUE_PATH.exists():
        return []
    try:
        return json.loads(_ACTION_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_action_queue(queue: List[Dict[str, Any]]) -> None:
    try:
        _ACTION_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ACTION_QUEUE_PATH.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        _log("action_queue_save_error", error=str(e))


def _enqueue_llm_task(timer_id: str, prompt: str, user_id: str) -> None:
    """Legg LLM-oppgave i retry-kø etter feilet direkteforsøk."""
    queue = _load_action_queue()
    # 30s grace period — worker poller hvert 30s, første retry skjer umiddelbart
    entry: Dict[str, Any] = {
        "id":          f"queue_{uuid.uuid4().hex[:8]}",
        "timer_id":    timer_id,
        "prompt":      prompt,
        "user_id":     user_id,
        "retries":     1,           # direkteforsøk regnes som forsøk 0
        "max_retries": 5,
        "next_retry":  datetime.now(timezone.utc).isoformat(),  # straks
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    queue.append(entry)
    _save_action_queue(queue)
    _log("action_queued", timer_id=timer_id, queue_id=entry["id"], retries_left=4)


async def _process_action_queue() -> None:
    """Behandle action-kø: ett forsøk per entry, eksponensiell backoff."""
    queue = _load_action_queue()
    if not queue:
        return

    now = datetime.now(timezone.utc)
    updated: List[Dict[str, Any]] = []

    for entry in queue:
        try:
            next_retry = datetime.fromisoformat(entry["next_retry"])
        except Exception:
            next_retry = now

        if next_retry > now:
            updated.append(entry)
            continue

        success = await _deliver_llm(entry["timer_id"], entry["prompt"], entry["user_id"])

        if success:
            _log("action_queue_success", queue_id=entry["id"],
                 timer_id=entry["timer_id"], retries=entry["retries"])
        else:
            entry["retries"] += 1
            if entry["retries"] >= entry["max_retries"]:
                # Maks forsøk nådd — varsle admin via _system-kø
                _log("action_queue_exhausted", queue_id=entry["id"],
                     timer_id=entry["timer_id"], retries=entry["retries"])
                _write_pending("_system", {
                    "id":         f"notif_{uuid.uuid4().hex[:8]}",
                    "timer_id":   entry["timer_id"],
                    "message":    t(
                        "timer_llm_task_failed", "nb",
                        max_retries=entry["max_retries"],
                        timer_id=entry["timer_id"],
                        prompt_preview=entry["prompt"][:80],
                    ),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "acked":      False,
                    "severity":   "error",
                })
            else:
                # Eksponensiell backoff: 60s, 120s, 240s, 480s ...
                backoff = 60 * (2 ** (entry["retries"] - 1))
                entry["next_retry"] = (now + timedelta(seconds=backoff)).isoformat()
                updated.append(entry)
                _log("action_queue_retry_scheduled", queue_id=entry["id"],
                     timer_id=entry["timer_id"], retries=entry["retries"],
                     backoff_seconds=backoff)

    _save_action_queue(updated)


async def start_action_queue_worker() -> None:
    """Starter bakgrunnsjobb som poller LLM action-kø hvert 30. sekund."""
    async def _worker():
        while True:
            try:
                await _process_action_queue()
            except Exception as e:
                _log("action_queue_worker_error", error=str(e))
            await asyncio.sleep(30)

    asyncio.create_task(_worker())
    _log("action_queue_worker_started")


# ── Avfyring ──────────────────────────────────────────────────────────────────

async def _fire(timer_id: str, timer_info: Dict[str, Any]):
    """
    Dispatcher for timer-avfyring.
    Fase 2: dispatcher basert på action + notify_via.
    Fase 1: fallback til llm_task for bakoverkompatibilitet med gamle timere.
    """
    action     = timer_info.get("action", "llm_task")
    notify_via = timer_info.get("notify_via")
    if notify_via is None:
        notify_via = ["tts"]
    repeat     = timer_info.get("repeat")
    fires_at   = datetime.fromtimestamp(timer_info.get("fires_at_ts", 0))
    user_id    = timer_info.get("user_id", "global")
    rid        = f"rid-timer-{int(time.time() * 1000)}"

    _log("timer_fired", rid=rid, timer_id=timer_id, action=action,
         notify_via=notify_via, user_id=user_id)

    try:
        if action == "tts_response":
            await _deliver_tts(timer_info)

        elif action == "ha_action":
            await _deliver_ha(timer_info)
            if "tts" in notify_via:
                await _deliver_tts(timer_info)

        elif action == "llm_task":
            prompt  = timer_info.get("prompt", "")
            success = await _deliver_llm(timer_id, prompt, user_id)
            if not success:
                _enqueue_llm_task(timer_id, prompt, user_id)

        # action == "none": ingen handling, bare levering

        # Chat-kanal: uavhengig av action
        if "chat" in notify_via:
            await _deliver_chat(timer_info, rid=rid)

        # STM-skriving: logg avfyringen i brukerens korttidshukommelse
        if user_id and user_id != "global":
            try:
                from kaare_core.app_state import get_stm
                message = timer_info.get("tts_text") or timer_info.get("prompt", "")
                fire_local = datetime.now().strftime("%H:%M")
                stm_text = f"[Timer avfyrt {fire_local} | rid: {rid}] Kåre leverte: «{message[:120]}»"
                get_stm(user_id).add_dialog(role="system", text=stm_text, user_id=user_id)
                _log("timer_stm_written", rid=rid, timer_id=timer_id, user_id=user_id)
            except Exception as e:
                _log("timer_stm_error", rid=rid, timer_id=timer_id, error=str(e))

    except Exception as e:
        _log("timer_fire_error", rid=rid, timer_id=timer_id, error=str(e))

    finally:
        if repeat and timer_id in _timers:
            next_dt    = _next_occurrence(fires_at, repeat)
            delay_secs = max(1.0, (next_dt - datetime.now()).total_seconds())

            updated = dict(timer_info)
            fires_at_ts_new = next_dt.timestamp()
            updated.update({
                "fires_at_ts": fires_at_ts_new,
                "fires_at":    datetime.fromtimestamp(fires_at_ts_new, tz=timezone.utc).isoformat(),
            })

            async def _next_delayed(tid=timer_id, info=updated, d=delay_secs):
                await asyncio.sleep(d)
                await _fire(tid, info)

            task = asyncio.create_task(_next_delayed())
            updated["task"] = task
            _timers[timer_id] = updated
            _persist_timers()
        else:
            _timers.pop(timer_id, None)
            _persist_timers()


# ── Offentlig API ─────────────────────────────────────────────────────────────

def set_timer(
    prompt: str = "",
    in_seconds: int = 0,
    notify: bool = True,
    repeat: Optional[str] = None,
    at_time: Optional[str] = None,
    lang: str = "nb",
    # P40: nye felter
    user_id: str = "global",
    source_node: Optional[str] = None,
    target_node: Optional[str] = None,
    action: str = "tts_response",
    notify_via: Optional[List[str]] = None,
    tts_text: str = "",
    ha_payload: Optional[Dict[str, Any]] = None,
    for_user_id: Optional[str] = None,  # foreldrestyrt timer for barn
) -> str:
    # Normalize notify_via: LLM passes it as a JSON string or plain string
    if isinstance(notify_via, str):
        stripped = notify_via.strip()
        if stripped.startswith("["):
            try:
                notify_via = json.loads(stripped)
            except Exception:
                notify_via = [stripped]
        elif stripped:
            notify_via = [ch.strip() for ch in stripped.split(",") if ch.strip()]
        else:
            notify_via = []

    # Normalize ha_payload: LLM passes it as a JSON string
    if isinstance(ha_payload, str):
        try:
            ha_payload = json.loads(ha_payload)
        except Exception:
            ha_payload = None

    # notify_via default per action type
    if notify_via is None:
        if tts_text or action == "tts_response":
            notify_via = ["tts"]
        elif action == "ha_action":
            notify_via = []  # silent — scheduled HA actions don't need a notification
        else:
            notify_via = ["chat"]

    # Bakoverkompatibilitet: prompt uten ny action → llm_task
    effective_action = action
    if not tts_text and not ha_payload and prompt.strip():
        if action == "tts_response":
            effective_action = "llm_task"

    # Effektiv mottaker
    effective_user = for_user_id or user_id

    # Validering
    if not tts_text.strip() and not prompt.strip() and effective_action not in ("ha_action", "none"):
        return t("timer_empty_prompt", lang)

    if repeat and repeat not in VALID_REPEATS:
        return t("timer_invalid_repeat", lang, repeat=repeat, valid=', '.join(VALID_REPEATS))

    if effective_action not in VALID_ACTIONS:
        return t("timer_invalid_action", lang, action=effective_action, valid=", ".join(VALID_ACTIONS))

    invalid_ch = set(notify_via) - VALID_CHANNELS
    if invalid_ch:
        return t("timer_invalid_channel", lang, channels=", ".join(invalid_ch))

    # Max timers per user
    max_per_user: int = (_get_settings().get("timers") or {}).get("max_per_user", 20)
    user_timer_count = sum(1 for v in _timers.values() if v.get("user_id") == effective_user)
    if user_timer_count >= max_per_user:
        return t("timer_max_reached", lang, max=max_per_user)

    # Parent permission check — setting a timer for another user (child/teen)
    if for_user_id and for_user_id != user_id:
        target = _get_user(for_user_id)
        if not target or target["role"] not in ("child", "teen"):
            return t("timer_target_not_child", lang, user_id=for_user_id)
        if not _get_profile_flag(user_id, "can_manage_child_timers"):
            return t("timer_child_permission_denied", lang)

    # Bestem tidspunkt
    if at_time:
        fire_dt = _parse_at_time(at_time)
        if fire_dt is None:
            return t("timer_parse_error", lang, at_time=at_time)
        delay_secs = max(1.0, (fire_dt - datetime.now()).total_seconds())
    else:
        if in_seconds < 5:
            return t("timer_min_seconds", lang)
        if in_seconds > 86400 * 365 and not repeat:
            return t("timer_max_one_year", lang)
        delay_secs = float(in_seconds)
        fire_dt    = datetime.now() + timedelta(seconds=delay_secs)

    timer_id    = str(uuid.uuid4())[:8]
    fires_at_ts = fire_dt.timestamp()

    timer_info: Dict[str, Any] = {
        "id":           timer_id,
        "user_id":      effective_user,
        "source_node":  source_node,
        "target_node":  target_node,
        "action":       effective_action,
        "notify_via":   list(notify_via),
        "tts_text":     tts_text.strip(),
        "ha_payload":   ha_payload,
        "prompt":       prompt.strip(),
        "fires_at_ts":  fires_at_ts,
        "fires_at":     datetime.fromtimestamp(fires_at_ts, tz=timezone.utc).isoformat(),
        "in_seconds":   int(delay_secs),
        "notify":       notify,
        "repeat":       repeat,
        "at_time":      at_time,
        "set_by":       user_id,  # hvem satte timeren (kan avvike fra user_id ved foreldrestyring)
    }

    async def _delayed(tid=timer_id, info=timer_info, d=delay_secs):
        await asyncio.sleep(d)
        await _fire(tid, info)

    task = asyncio.create_task(_delayed())
    timer_info["task"] = task
    _timers[timer_id] = timer_info

    _persist_timers()

    _log("timer_set", source="kare", tool="set_timer", timer_id=timer_id,
         user_id=effective_user, action=effective_action, notify_via=list(notify_via),
         in_seconds=int(delay_secs), prompt_preview=(prompt or tts_text)[:80],
         notify=notify, repeat=repeat, at_time=at_time,
         result_preview=f"Timer {timer_id} satt", duration_ms=0)

    local_str    = fire_dt.strftime("%d.%m.%Y %H:%M")
    delay_str    = _fmt_delay(delay_secs)
    repeat_label = t(f"timer_repeat_{repeat}", lang) if repeat else ""
    repeat_str   = t("timer_repeats", lang, label=repeat_label) if repeat_label else ""
    desc         = tts_text or prompt
    return t("timer_set", lang,
             timer_id=timer_id, delay=delay_str, local_time=local_str,
             repeat_str=repeat_str,
             prompt_preview=desc[:60] + ('…' if len(desc) > 60 else ''))


def cancel_timer(timer_id: str, lang: str = "nb") -> str:
    if timer_id not in _timers:
        return t("timer_not_found", lang, timer_id=timer_id)
    info = _timers.pop(timer_id)
    info["task"].cancel()
    _persist_timers()
    _log("timer_cancelled", source="kare", tool="cancel_timer", timer_id=timer_id,
         result_preview=f"Timer {timer_id} avbrutt", duration_ms=0)
    repeat_label = t(f"timer_repeat_{info['repeat']}", lang) if info.get("repeat") else ""
    repeat_str = t("timer_repeat_was", lang, label=repeat_label) if repeat_label else ""
    return t("timer_cancelled", lang, timer_id=timer_id, repeat_str=repeat_str)


def ack_timer(notif_id: str, user_id: str, lang: str = "nb") -> str:
    """Kvitter en pending chat-notification. Kåre kaller denne etter å ha levert påminnelsen."""
    if ack_notification(notif_id, user_id):
        _log("timer_acked", notif_id=notif_id, user_id=user_id)
        return t("timer_acked", lang, notif_id=notif_id)
    return t("timer_notif_not_found", lang, notif_id=notif_id, user_id=user_id)


def list_timers(lang: str = "nb", user_id: Optional[str] = None) -> str:
    """List timere. user_id=None → alle. user_id=str → kun denne brukeren."""
    visible = {
        k: v for k, v in _timers.items()
        if user_id is None or v.get("user_id") == user_id or v.get("user_id") is None
    }
    if not visible:
        return t("timer_none_active", lang)

    now = datetime.now(timezone.utc).timestamp()
    engang   = [(k, v) for k, v in visible.items() if not v.get("repeat")]
    gjentatt = [(k, v) for k, v in visible.items() if v.get("repeat")]

    lines = [t("timer_list_header", lang, count=len(visible))]
    if gjentatt:
        lines.append(t("timer_repeating_header", lang))
        for _, info in gjentatt:
            remaining = max(0, int(info["fires_at_ts"] - now))
            desc  = (info.get("tts_text") or info.get("prompt", ""))[:50]
            if len(desc) == 50:
                desc += "…"
            fire_local = datetime.fromtimestamp(info["fires_at_ts"]).strftime("%d.%m %H:%M")
            rep_label  = t(f"timer_repeat_{info['repeat']}", lang)
            uid_str    = f" [{info.get('user_id', 'global')}]" if user_id is None else ""
            lines.append(
                f"    [{info['id']}]{uid_str} {rep_label} — "
                f"neste om {_fmt_delay(remaining)} ({fire_local}): «{desc}»"
            )
    if engang:
        lines.append(t("timer_one_time_header", lang))
        for _, info in engang:
            remaining = max(0, int(info["fires_at_ts"] - now))
            desc = (info.get("tts_text") or info.get("prompt", ""))[:50]
            if len(desc) == 50:
                desc += "…"
            uid_str = f" [{info.get('user_id', 'global')}]" if user_id is None else ""
            lines.append(f"    [{info['id']}]{uid_str} om {_fmt_delay(remaining)}: «{desc}»")

    return "\n".join(lines)


def get_active_timers() -> List[Dict]:
    now = datetime.now(timezone.utc).timestamp()
    return [
        {
            "id":                info["id"],
            "user_id":           info.get("user_id", "global"),
            "action":            info.get("action", "llm_task"),
            "notify_via":        info.get("notify_via", ["tts"]),
            "tts_text":          info.get("tts_text", ""),
            "prompt":            info.get("prompt", ""),
            "source_node":       info.get("source_node"),
            "target_node":       info.get("target_node"),
            "fires_at":          info["fires_at"],
            "remaining_seconds": max(0, int(info["fires_at_ts"] - now)),
            "notify":            info.get("notify", True),
            "repeat":            info.get("repeat"),
            "at_time":           info.get("at_time"),
        }
        for info in _timers.values()
    ]


def get_timer_counts_by_user() -> Dict[str, int]:
    """Returnerer antall aktive timere per user_id — brukes av admin GUI."""
    counts: Dict[str, int] = {}
    for info in _timers.values():
        uid = info.get("user_id", "global")
        counts[uid] = counts.get(uid, 0) + 1
    return counts


# ── Gjenopprett ved oppstart ──────────────────────────────────────────────────

def restore_timers():
    """
    Kalles fra kaare_api.py ved oppstart.
    Laster alle timere fra disk og re-scheduler dem.
    Engangstimere som gikk av mens tjenesten var nede: avfyres umiddelbart (2s delay).
    """
    if not _PERSIST_PATH.exists():
        return
    try:
        data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    now = datetime.now()
    restored = 0

    for info in data:
        timer_id = info.get("id")
        prompt   = info.get("prompt", "")
        tts_text = info.get("tts_text", "")
        repeat   = info.get("repeat")

        if not timer_id:
            continue
        if not (prompt or tts_text or info.get("action") in ("ha_action", "none")):
            continue

        fires_at_ts = info.get("fires_at_ts", 0.0)
        fire_dt     = datetime.fromtimestamp(fires_at_ts)

        if repeat:
            # Finn neste gyldige tidspunkt
            while fire_dt <= now:
                fire_dt = _next_occurrence(fire_dt, repeat)
        else:
            # Engangstimer: avfyr umiddelbart hvis gikk av mens nede
            if fire_dt <= now:
                fire_dt = now  # vil gi delay_secs ≈ 0 → clamped til 2s

        delay_secs = max(2.0, (fire_dt - now).total_seconds())

        restored_info = dict(info)
        fires_at_ts_new = fire_dt.timestamp()
        restored_info.update({
            "fires_at_ts": fires_at_ts_new,
            "fires_at":    datetime.fromtimestamp(fires_at_ts_new, tz=timezone.utc).isoformat(),
            "in_seconds":  int(delay_secs),
        })

        async def _make_delayed(tid=timer_id, ri=restored_info, d=delay_secs):
            await asyncio.sleep(d)
            await _fire(tid, ri)

        task = asyncio.create_task(_make_delayed())
        restored_info["task"] = task
        _timers[timer_id] = restored_info
        restored += 1

    if restored:
        import logging as _logging
        _logging.getLogger("kaare_api").info(
            "Timer-gjenoppretting: %d timer(e) lastet fra disk.", restored
        )
