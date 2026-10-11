"""
Kåre's timer engine v3 (P40).

Supports:
  - in_seconds: plain delay
  - at_time:    clock time/date — "07:30", "fredag 08:00", "2026-05-01 09:00"
  - repeat:     "hourly" | "daily" | "weekdays" | "weekend" | "weekly"

Delivery model:
  action:     what the timer does when it fires
              "tts_response" | "ha_action" | "llm_task" | "none"
  notify_via: list of delivery channels — ["tts"] | ["chat"] | ["tts", "chat"]

All timers (one-shot included) are persisted to disk and restored on restart.
"""

import asyncio
import json
import logging as _logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from kaare_core import log_buffer
from kaare_core.app_state import get_stm
from kaare_core.background import spawn
from kaare_core.config import (
    get_local_tz,
    local_from_ts,
    now_local,
)
from kaare_core.config import (
    get_service as _svc,
)
from kaare_core.config import (
    get_settings as _get_settings,
)
from kaare_core.service_auth import service_headers
from kaare_core.tools.i18n import get_lang, get_speech_lang, t
from kaare_core.users.profile_manager import get_profile_flag as _get_profile_flag
from kaare_core.users.store import get_user as _get_user

_timers: dict[str, dict[str, Any]] = {}
_TOOL_LOG = Path("/kaare/logs/tool_calls.log")
_PERSIST_PATH = Path("/kaare/state/timers.json")
_PENDING_DIR = Path("/kaare/state/pending_notifications")
_ACTION_QUEUE_PATH = Path("/kaare/state/action_queue.json")
_API_BASE = _svc("internal", "kaare_api")

VALID_REPEATS = {"hourly", "daily", "weekdays", "weekend", "weekly"}
VALID_ACTIONS = {"tts_response", "ha_action", "llm_task", "none"}
VALID_CHANNELS = {"tts", "chat"}

_REPEAT_LABELS = {
    "hourly": "hver time",
    "daily": "daglig",
    "weekdays": "hverdager",
    "weekend": "helg",
    "weekly": "ukentlig",
}

_DAYS_NO = {
    "mandag": 0,
    "tirsdag": 1,
    "onsdag": 2,
    "torsdag": 3,
    "fredag": 4,
    "lørdag": 5,
    "søndag": 6,
}


# ── Logging ───────────────────────────────────────────────────────────────────


def _log(event: str, **fields):
    try:
        rec = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        log_buffer.write(_TOOL_LOG, rec)
    except Exception:
        pass


# ── Time parsing ──────────────────────────────────────────────────────────────


def _parse_at_time(at_time: str) -> datetime | None:
    """Clock time in the household timezone (N16) — an aware datetime, never the machine clock."""
    now = now_local()
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
        return (now + timedelta(days=days_ahead)).replace(
            hour=h, minute=mi, second=0, microsecond=0
        )

    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=get_local_tz())
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
    m, s = divmod(rem, 60)
    if h:
        return f"{h}t {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ── Persistence ───────────────────────────────────────────────────────────────


def _persist_timers():
    """Write all active timers to disk (without the asyncio Task object)."""
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [{k: v for k, v in info.items() if k != "task"} for info in _timers.values()]
        _PERSIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# Kept for backward compatibility with code that calls it directly
_persist_repeating = _persist_timers


def _clear_persist(timer_id: str):
    _persist_timers()


# ── Pending notifications (chat channel) ──────────────────────────────────────


def _pending_path(user_id: str) -> Path:
    return _PENDING_DIR / f"{user_id}.json"


def queue_chat_notification(user_id: str, message: str, rid: str = "", source_id: str = "") -> None:
    """Public entry point for other subsystems (e.g. Mechanic's job_store) to proactively
    surface a chat message — reuses the same queue/poll/inject pipeline built for timer
    alerts (frontend polls every 30s, auto-injects as a 'kare' message, no new code needed
    on the frontend side). `source_id` is an opaque id (e.g. job_id) for logging/debugging,
    not interpreted here."""
    if not user_id or user_id == "global" or not message:
        return
    notif = {
        "id": f"notif_{uuid.uuid4().hex[:8]}",
        "source_id": source_id,
        "rid": rid,
        "message": message,
        "created_at": datetime.now(UTC).isoformat(),
        "acked": False,
    }
    _write_pending(user_id, notif)


def _write_pending(user_id: str, notif: dict[str, Any]) -> None:
    """Add a pending notification for the user."""
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


def get_pending_notifications(user_id: str) -> list[dict[str, Any]]:
    """Return all unacknowledged notifications for the user."""
    path = _pending_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [n for n in data if not n.get("acked", False)]
    except Exception:
        return []


def ack_notification(notif_id: str, user_id: str) -> bool:
    """Acknowledge a pending notification. Returns True if found."""
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


# ── Delivery ──────────────────────────────────────────────────────────────────


async def _deliver_tts(timer_info: dict[str, Any]) -> None:
    """Play tts_text on target_node via the voice bridge POST /speak."""
    try:
        tts_text = timer_info.get("tts_text", "")
        if not tts_text:
            return
        # target_node overrides source_node; falls back to "local" (aplay on the AI-PC)
        target = timer_info.get("target_node") or timer_info.get("source_node") or "local"
        user_id = timer_info.get("user_id", "global")
        lang = get_speech_lang(user_id)
        voice_url = _svc("internal", "voice_bridge")
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{voice_url}/speak",
                json={"text": tts_text, "target": target, "lang": lang},
                headers=service_headers(),
            )
        _log("tts_delivered", timer_id=timer_info.get("id"), target=target, status=r.status_code)
    except Exception as e:
        _log("tts_deliver_error", timer_id=timer_info.get("id"), error=str(e))


async def _deliver_ha(timer_info: dict[str, Any]) -> None:
    """Call the HA gateway POST /api/ha_apply with ha_payload.
    ha_payload format: {"action": "turn_off", "entity_id": "light.workshop", "params": {}}
    """
    try:
        ha_payload = timer_info.get("ha_payload")
        if not ha_payload or not ha_payload.get("action") or not ha_payload.get("entity_id"):
            _log(
                "ha_deliver_skip",
                timer_id=timer_info.get("id"),
                reason="missing action or entity_id",
            )
            return
        ha_url = _svc("internal", "ha_gateway")
        body = {
            "action": ha_payload["action"],
            "entity_id": ha_payload["entity_id"],
            "params": ha_payload.get("params", {}),
            "source": "timer",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{ha_url}/api/ha_apply", json=body, headers=service_headers())
        _log(
            "ha_delivered",
            timer_id=timer_info.get("id"),
            action=body["action"],
            entity=body["entity_id"],
            status=r.status_code,
        )
    except Exception as e:
        _log("ha_deliver_error", timer_id=timer_info.get("id"), error=str(e))


def _timer_message(timer_info: dict[str, Any], lang: str) -> str:
    """What a fired timer says in chat/STM. A scheduled HA action often has no text of its
    own — then describe the action instead of delivering an empty message."""
    message = timer_info.get("tts_text") or timer_info.get("prompt", "")
    if message:
        return message
    payload = timer_info.get("ha_payload") or {}
    if timer_info.get("action") == "ha_action" and payload:
        return t(
            "timer_ha_action_done",
            lang,
            action=payload.get("action", "?"),
            entity=payload.get("entity_id", "?"),
        )
    return t("timer_fired_no_text", lang)


async def _deliver_chat(timer_info: dict[str, Any], rid: str = "") -> None:
    """Write a pending notification to the user's queue."""
    user_id = timer_info.get("user_id", "global")
    message = _timer_message(timer_info, get_lang(user_id))
    notif = {
        "id": f"notif_{uuid.uuid4().hex[:8]}",
        "timer_id": timer_info.get("id"),
        "rid": rid,
        "message": message,
        "created_at": datetime.now(UTC).isoformat(),
        "acked": False,
    }
    _write_pending(user_id, notif)
    _log(
        "chat_notify_queued",
        rid=rid,
        timer_id=timer_info.get("id"),
        user_id=user_id,
        notif_id=notif["id"],
    )


async def _deliver_llm(timer_id: str, prompt: str, user_id: str) -> bool:
    """Call /api/generate with the prompt (llm_task). Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_API_BASE}/api/generate",
                json={"prompt": prompt, "source": "self_timer", "user_id": user_id},
                headers=service_headers(),  # the timer acts for its owner (P120)
            )
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}")
            response_text = r.json().get("text", "")[:120]
        _log("llm_deliver_done", timer_id=timer_id, result_preview=response_text)
        return True
    except Exception as e:
        _log("llm_deliver_error", timer_id=timer_id, error=str(e))
        return False


# ── LLM action queue (Phase 4) ────────────────────────────────────────────────


def _load_action_queue() -> list[dict[str, Any]]:
    if not _ACTION_QUEUE_PATH.exists():
        return []
    try:
        return json.loads(_ACTION_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_action_queue(queue: list[dict[str, Any]]) -> None:
    try:
        _ACTION_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ACTION_QUEUE_PATH.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        _log("action_queue_save_error", error=str(e))


def _enqueue_llm_task(timer_id: str, prompt: str, user_id: str) -> None:
    """Put an LLM task in the retry queue after a failed direct attempt."""
    queue = _load_action_queue()
    # 30 s grace period — the worker polls every 30 s, the first retry happens right away
    entry: dict[str, Any] = {
        "id": f"queue_{uuid.uuid4().hex[:8]}",
        "timer_id": timer_id,
        "prompt": prompt,
        "user_id": user_id,
        "retries": 1,  # the direct attempt counts as attempt 0
        "max_retries": 5,
        "next_retry": datetime.now(UTC).isoformat(),  # straks
        "created_at": datetime.now(UTC).isoformat(),
    }
    queue.append(entry)
    _save_action_queue(queue)
    _log("action_queued", timer_id=timer_id, queue_id=entry["id"], retries_left=4)


async def _process_action_queue() -> None:
    """Process the action queue: one attempt per entry, exponential backoff."""
    queue = _load_action_queue()
    if not queue:
        return

    now = datetime.now(UTC)
    updated: list[dict[str, Any]] = []

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
            _log(
                "action_queue_success",
                queue_id=entry["id"],
                timer_id=entry["timer_id"],
                retries=entry["retries"],
            )
        else:
            entry["retries"] += 1
            if entry["retries"] >= entry["max_retries"]:
                # Max attempts reached — notify admin via the _system queue
                _log(
                    "action_queue_exhausted",
                    queue_id=entry["id"],
                    timer_id=entry["timer_id"],
                    retries=entry["retries"],
                )
                _write_pending(
                    "_system",
                    {
                        "id": f"notif_{uuid.uuid4().hex[:8]}",
                        "timer_id": entry["timer_id"],
                        "message": t(
                            "timer_llm_task_failed",
                            "nb",
                            max_retries=entry["max_retries"],
                            timer_id=entry["timer_id"],
                            prompt_preview=entry["prompt"][:80],
                        ),
                        "created_at": datetime.now(UTC).isoformat(),
                        "acked": False,
                        "severity": "error",
                    },
                )
            else:
                # Exponential backoff: 60 s, 120 s, 240 s, 480 s ...
                backoff = 60 * (2 ** (entry["retries"] - 1))
                entry["next_retry"] = (now + timedelta(seconds=backoff)).isoformat()
                updated.append(entry)
                _log(
                    "action_queue_retry_scheduled",
                    queue_id=entry["id"],
                    timer_id=entry["timer_id"],
                    retries=entry["retries"],
                    backoff_seconds=backoff,
                )

    _save_action_queue(updated)


async def start_action_queue_worker() -> None:
    """Start the background job that polls the LLM action queue every 30 seconds."""

    async def _worker():
        while True:
            try:
                await _process_action_queue()
            except Exception as e:
                _log("action_queue_worker_error", error=str(e))
            await asyncio.sleep(30)

    spawn(_worker())
    _log("action_queue_worker_started")


# ── Firing ────────────────────────────────────────────────────────────────────


async def _fire(timer_id: str, timer_info: dict[str, Any]):
    """
    Dispatcher for a firing timer.
    Phase 2: dispatch on action + notify_via.
    Phase 1: falls back to llm_task for backward compatibility with old timers.
    """
    action = timer_info.get("action", "llm_task")
    notify_via = timer_info.get("notify_via")
    if notify_via is None:
        notify_via = ["tts"]
    repeat = timer_info.get("repeat")
    fires_at = local_from_ts(timer_info.get("fires_at_ts", 0))
    user_id = timer_info.get("user_id", "global")
    rid = f"rid-timer-{int(time.time() * 1000)}"

    _log(
        "timer_fired",
        rid=rid,
        timer_id=timer_id,
        action=action,
        notify_via=notify_via,
        user_id=user_id,
    )

    try:
        if action == "tts_response":
            await _deliver_tts(timer_info)

        elif action == "ha_action":
            await _deliver_ha(timer_info)
            if "tts" in notify_via:
                await _deliver_tts(timer_info)

        elif action == "llm_task":
            prompt = timer_info.get("prompt", "")
            success = await _deliver_llm(timer_id, prompt, user_id)
            if not success:
                _enqueue_llm_task(timer_id, prompt, user_id)

        # action == "none": no action, delivery only

        # Chat channel: independent of action
        if "chat" in notify_via:
            await _deliver_chat(timer_info, rid=rid)

        # STM write: record the firing in the user's short-term memory
        if user_id and user_id != "global":
            try:
                message = _timer_message(timer_info, get_lang(user_id))
                fire_local = now_local().strftime("%H:%M")
                stm_text = t(
                    "timer_stm_fired",
                    get_lang(user_id),
                    time=fire_local,
                    rid=rid,
                    message=message[:120],
                )
                _stm = get_stm(user_id)
                _stm.add_dialog(role="system", text=stm_text, user_id=user_id)  # history/audit only
                _stm.add_pending_system_note(
                    stm_text, user_id=user_id
                )  # actual delivery — see P105
                _log("timer_stm_written", rid=rid, timer_id=timer_id, user_id=user_id)
            except Exception as e:
                _log("timer_stm_error", rid=rid, timer_id=timer_id, error=str(e))

    except Exception as e:
        _log("timer_fire_error", rid=rid, timer_id=timer_id, error=str(e))

    finally:
        if repeat and timer_id in _timers:
            next_dt = _next_occurrence(fires_at, repeat)
            delay_secs = max(1.0, next_dt.timestamp() - time.time())

            updated = dict(timer_info)
            fires_at_ts_new = next_dt.timestamp()
            updated.update(
                {
                    "fires_at_ts": fires_at_ts_new,
                    "fires_at": datetime.fromtimestamp(fires_at_ts_new, tz=UTC).isoformat(),
                }
            )

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


# ── Public API ────────────────────────────────────────────────────────────────


def set_timer(
    prompt: str = "",
    in_seconds: int = 0,
    notify: bool = True,
    repeat: str | None = None,
    at_time: str | None = None,
    lang: str = "nb",
    # P40: new fields
    user_id: str = "global",
    source_node: str | None = None,
    target_node: str | None = None,
    action: str = "tts_response",
    notify_via: list[str] | None = None,
    tts_text: str = "",
    ha_payload: dict[str, Any] | None = None,
    for_user_id: str | None = None,  # foreldrestyrt timer for barn
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

    # Backward compatibility: prompt without a new action → llm_task
    effective_action = action
    if not tts_text and not ha_payload and prompt.strip() and action == "tts_response":
        effective_action = "llm_task"

    # Effective recipient
    effective_user = for_user_id or user_id

    # Validation
    if (
        not tts_text.strip()
        and not prompt.strip()
        and effective_action not in ("ha_action", "none")
    ):
        return t("timer_empty_prompt", lang)

    if repeat and repeat not in VALID_REPEATS:
        return t("timer_invalid_repeat", lang, repeat=repeat, valid=", ".join(VALID_REPEATS))

    if effective_action not in VALID_ACTIONS:
        return t(
            "timer_invalid_action", lang, action=effective_action, valid=", ".join(VALID_ACTIONS)
        )

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

    # Resolve the firing time
    if at_time:
        fire_dt = _parse_at_time(at_time)
        if fire_dt is None:
            return t("timer_parse_error", lang, at_time=at_time)
        delay_secs = max(1.0, fire_dt.timestamp() - time.time())
    else:
        if in_seconds < 5:
            return t("timer_min_seconds", lang)
        if in_seconds > 86400 * 365 and not repeat:
            return t("timer_max_one_year", lang)
        delay_secs = float(in_seconds)
        fire_dt = local_from_ts(time.time() + delay_secs)  # absolute: exact across a DST change

    timer_id = str(uuid.uuid4())[:8]
    fires_at_ts = fire_dt.timestamp()

    timer_info: dict[str, Any] = {
        "id": timer_id,
        "user_id": effective_user,
        "source_node": source_node,
        "target_node": target_node,
        "action": effective_action,
        "notify_via": list(notify_via),
        "tts_text": tts_text.strip(),
        "ha_payload": ha_payload,
        "prompt": prompt.strip(),
        "fires_at_ts": fires_at_ts,
        "fires_at": datetime.fromtimestamp(fires_at_ts, tz=UTC).isoformat(),
        "in_seconds": int(delay_secs),
        "notify": notify,
        "repeat": repeat,
        "at_time": at_time,
        "set_by": user_id,  # who set the timer (can differ from user_id under parental control)
    }

    async def _delayed(tid=timer_id, info=timer_info, d=delay_secs):
        await asyncio.sleep(d)
        await _fire(tid, info)

    task = asyncio.create_task(_delayed())
    timer_info["task"] = task
    _timers[timer_id] = timer_info

    _persist_timers()

    _log(
        "timer_set",
        source="kare",
        tool="set_timer",
        timer_id=timer_id,
        user_id=effective_user,
        action=effective_action,
        notify_via=list(notify_via),
        in_seconds=int(delay_secs),
        prompt_preview=(prompt or tts_text)[:80],
        notify=notify,
        repeat=repeat,
        at_time=at_time,
        result_preview=f"Timer {timer_id} satt",
        duration_ms=0,
    )

    local_str = fire_dt.strftime("%d.%m.%Y %H:%M")
    delay_str = _fmt_delay(delay_secs)
    repeat_label = t(f"timer_repeat_{repeat}", lang) if repeat else ""
    repeat_str = t("timer_repeats", lang, label=repeat_label) if repeat_label else ""
    desc = tts_text or prompt
    return t(
        "timer_set",
        lang,
        timer_id=timer_id,
        delay=delay_str,
        local_time=local_str,
        repeat_str=repeat_str,
        prompt_preview=desc[:60] + ("…" if len(desc) > 60 else ""),
    )


def cancel_timer(timer_id: str, lang: str = "nb") -> str:
    if timer_id not in _timers:
        return t("timer_not_found", lang, timer_id=timer_id)
    info = _timers.pop(timer_id)
    info["task"].cancel()
    _persist_timers()
    _log(
        "timer_cancelled",
        source="kare",
        tool="cancel_timer",
        timer_id=timer_id,
        result_preview=f"Timer {timer_id} avbrutt",
        duration_ms=0,
    )
    repeat_label = t(f"timer_repeat_{info['repeat']}", lang) if info.get("repeat") else ""
    repeat_str = t("timer_repeat_was", lang, label=repeat_label) if repeat_label else ""
    return t("timer_cancelled", lang, timer_id=timer_id, repeat_str=repeat_str)


def ack_timer(notif_id: str, user_id: str, lang: str = "nb") -> str:
    """Acknowledge a pending chat notification. Kåre calls this after delivering the reminder."""
    if ack_notification(notif_id, user_id):
        _log("timer_acked", notif_id=notif_id, user_id=user_id)
        return t("timer_acked", lang, notif_id=notif_id)
    return t("timer_notif_not_found", lang, notif_id=notif_id, user_id=user_id)


def list_timers(lang: str = "nb", user_id: str | None = None) -> str:
    """List timers. user_id=None → all. user_id=str → only that user."""
    visible = {
        k: v
        for k, v in _timers.items()
        if user_id is None or v.get("user_id") == user_id or v.get("user_id") is None
    }
    if not visible:
        return t("timer_none_active", lang)

    now = datetime.now(UTC).timestamp()
    engang = [(k, v) for k, v in visible.items() if not v.get("repeat")]
    gjentatt = [(k, v) for k, v in visible.items() if v.get("repeat")]

    lines = [t("timer_list_header", lang, count=len(visible))]
    if gjentatt:
        lines.append(t("timer_repeating_header", lang))
        for _, info in gjentatt:
            remaining = max(0, int(info["fires_at_ts"] - now))
            desc = (info.get("tts_text") or info.get("prompt", ""))[:50]
            if len(desc) == 50:
                desc += "…"
            fire_local = local_from_ts(info["fires_at_ts"]).strftime("%d.%m %H:%M")
            rep_label = t(f"timer_repeat_{info['repeat']}", lang)
            uid_str = f" [{info.get('user_id', 'global')}]" if user_id is None else ""
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


def get_active_timers() -> list[dict]:
    now = datetime.now(UTC).timestamp()
    return [
        {
            "id": info["id"],
            "user_id": info.get("user_id", "global"),
            "action": info.get("action", "llm_task"),
            "notify_via": info.get("notify_via", ["tts"]),
            "tts_text": info.get("tts_text", ""),
            "prompt": info.get("prompt", ""),
            "source_node": info.get("source_node"),
            "target_node": info.get("target_node"),
            "fires_at": info["fires_at"],
            "remaining_seconds": max(0, int(info["fires_at_ts"] - now)),
            "notify": info.get("notify", True),
            "repeat": info.get("repeat"),
            "at_time": info.get("at_time"),
        }
        for info in _timers.values()
    ]


def get_timer_counts_by_user() -> dict[str, int]:
    """Return the number of active timers per user_id — used by the admin GUI."""
    counts: dict[str, int] = {}
    for info in _timers.values():
        uid = info.get("user_id", "global")
        counts[uid] = counts.get(uid, 0) + 1
    return counts


# ── Restore at startup ────────────────────────────────────────────────────────


def restore_timers():
    """
    Called from kaare_api.py at startup.
    Loads every timer from disk and reschedules it.
    One-shot timers that came due while the service was down fire right away (2 s delay).
    """
    if not _PERSIST_PATH.exists():
        return
    try:
        data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    now = now_local()
    restored = 0

    for info in data:
        timer_id = info.get("id")
        prompt = info.get("prompt", "")
        tts_text = info.get("tts_text", "")
        repeat = info.get("repeat")

        if not timer_id:
            continue
        if not (prompt or tts_text or info.get("action") in ("ha_action", "none")):
            continue

        fires_at_ts = info.get("fires_at_ts", 0.0)
        fire_dt = local_from_ts(fires_at_ts)

        if repeat:
            # Find the next valid time
            while fire_dt <= now:
                fire_dt = _next_occurrence(fire_dt, repeat)
        else:
            # One-shot timer: fire right away if it came due while we were down
            if fire_dt <= now:
                fire_dt = now  # gives delay_secs ≈ 0 → clamped to 2 s

        delay_secs = max(2.0, fire_dt.timestamp() - time.time())

        restored_info = dict(info)
        fires_at_ts_new = fire_dt.timestamp()
        restored_info.update(
            {
                "fires_at_ts": fires_at_ts_new,
                "fires_at": datetime.fromtimestamp(fires_at_ts_new, tz=UTC).isoformat(),
                "in_seconds": int(delay_secs),
            }
        )

        async def _make_delayed(tid=timer_id, ri=restored_info, d=delay_secs):
            await asyncio.sleep(d)
            await _fire(tid, ri)

        task = asyncio.create_task(_make_delayed())
        restored_info["task"] = task
        _timers[timer_id] = restored_info
        restored += 1

    if restored:
        _logging.getLogger("kaare_api").info(
            "Timer-gjenoppretting: %d timer(e) lastet fra disk.", restored
        )
