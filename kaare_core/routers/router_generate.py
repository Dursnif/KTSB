# /kaare/kaare_core/routers/router_generate.py
"""
Router for /api/generate

Kåre drives entirely by tools — he decides what to do.
The old hardcoded pipeline is commented out at the bottom of the file
and kept until the tool pipeline is stable and tested.
"""

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from adapters.llm_adapter import ask_llm_with_tools, get_model_size_b
from adapters.llm_adapter import is_stranger as _is_stranger
from adapters.llm_adapter import time_block as _time_block
from kaare_core import app_state as _app_state
from kaare_core.agents.mechanic.job_store import ack_mechanic_results, get_pending_mechanic_results
from kaare_core.background import spawn
from kaare_core.config import filter_tools_by_model, get_llm_config, get_model, get_settings
from kaare_core.config import get_local_tz as _get_local_tz
from kaare_core.config import get_tools_for_role as _get_tools_for_role
from kaare_core.context_builder import set_request_context as _set_request_context
from kaare_core.image_store import save_image
from kaare_core.memory.long_term import USER_GLOBAL, get_ltm
from kaare_core.memory.semantic_memory import format_for_context, search_memory
from kaare_core.memory.short_term import _SESSION_GAP_MINUTES, ShortTermMemory
from kaare_core.model_enforcement import ModelEnforcer, strip_language_tag, tool_usage_tags
from kaare_core.tools import work_round
from kaare_core.tools.executor import execute_tool
from kaare_core.tools.i18n import get_lang as _get_lang
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.tools.lister import reminder_login_reminders as get_login_reminders
from kaare_core.tools.shared_tools import log_tool_call as _log_tool_call
from kaare_core.tools.timer_service import get_pending_notifications
from kaare_core.users import store as _user_store
from kaare_core.users.profile_manager import check_unlock_phrase, check_unlock_pin

logger = logging.getLogger(__name__)


def _hhmm() -> str:
    return datetime.now(tz=_get_local_tz()).strftime("%H:%M")


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── P105 Phase 6: replayed STM turns carry time (BUG-log 2026-09-28) ─────────────────────
# Replayed dialog pairs used to reach the model as bare text, so turns from days ago read
# as the ongoing conversation (Kåre claimed the weather was "already checked today" from a
# 5-day-old turn). Only USER turns are stamped: stamping assistant turns would teach the
# model to start its own replies with a timestamp.

_DEFAULT_GAP_NOTE_HOURS = 6.0


def _parse_utc(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _local_date_time(dt: datetime) -> tuple[str, str]:
    """(date, time) in the configured local timezone; year added only when not this year."""
    local = dt.astimezone(_get_local_tz())
    now_year = datetime.now(tz=_get_local_tz()).year
    date = local.strftime("%d.%m") if local.year == now_year else local.strftime("%d.%m.%Y")
    return date, local.strftime("%H:%M")


def _replay_stamp(ts: str, lang: str) -> str:
    dt = _parse_utc(ts)
    if dt is None:
        return ""
    date, hhmm = _local_date_time(dt)
    return _t_i18n("stm_replay_stamp", lang, date=date, time=hhmm)


def _gap_note_hours() -> float:
    try:
        return float(
            (get_settings().get("stm") or {}).get("replay_gap_note_hours", _DEFAULT_GAP_NOTE_HOURS)
        )
    except (TypeError, ValueError):
        return _DEFAULT_GAP_NOTE_HOURS


def _gap_note(previous: datetime | None, lang: str, now: datetime | None = None) -> str:
    """Explicit note when the user's previous activity is older than the configured gap."""
    if previous is None:
        return ""
    now = now or datetime.now(UTC)
    hours = (now - previous).total_seconds() / 3600
    if hours < _gap_note_hours():
        return ""
    ago = (
        _t_i18n("gen_gap_days", lang, n=int(hours // 24))
        if hours >= 48
        else _t_i18n("gen_gap_hours", lang, n=int(hours))
    )
    date, hhmm = _local_date_time(previous)
    when = _t_i18n("stm_replay_stamp", lang, date=date, time=hhmm).strip("[]")
    return _t_i18n("gen_gap_note", lang, when=when, ago=ago)


# ── P105 Phase 7: context budget — replay window, tool-result stubs, token guard ─────────
# Stress test 2026-09-28 reached 39,770 of 43,008 input tokens in round 6 with nothing but the
# 6-round cap in the way. A tool result is read in the round after it arrives; from then on a
# large one is replaced by a stub naming the exact call (Stian's rule), and under budget
# pressure remaining results are stubbed oldest-first.

_BUDGET_DEFAULTS = {
    "chars_per_token": 3.6,
    "safety_margin_tokens": 2000,
    "replay_token_budget": 4000,
    "replay_max_pairs": 20,
    "replay_prior_session_pairs": 2,
    "stub_threshold_tokens": 1500,
    "image_tokens_estimate": 1200,
    "system_prompt_tokens_estimate": 6000,
}
_MIN_STUB_SAVING_TOKENS = 200  # stubbing smaller results saves too little to be worth losing them


def _budget_cfg() -> dict:
    cfg = dict(_BUDGET_DEFAULTS)
    try:
        cfg.update(
            {k: v for k, v in (get_settings().get("context_budget") or {}).items() if v is not None}
        )
    except Exception:
        pass
    return cfg


def _input_cap(instance: str) -> int:
    """Largest prompt the backend accepts: context window minus the tokens reserved for output."""
    if instance == "fallback_9b":
        opts = get_llm_config("fallback").get("options") or {}
        return int(opts.get("num_ctx", 32768)) - int(opts.get("num_predict", 1024))
    cfg = get_llm_config("default")
    opts = cfg.get("options") or {}
    if cfg.get("provider", "vllm") != "vllm":
        # Ollama: the window is num_ctx and the output reserve num_predict — vllm_docker is
        # only the vLLM upgrade path and says nothing about this backend (DT03 D7)
        return int(opts.get("num_ctx", 32768)) - int(opts.get("num_predict", 1024))
    window = int((cfg.get("vllm_docker") or {}).get("max_model_len", 32768))
    return window - int(opts.get("max_tokens", 2048))


def _msg_tokens(msg: dict, cfg: dict) -> float:
    content = msg.get("content") or ""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    tokens = len(text) / max(float(cfg["chars_per_token"]), 0.1)
    if msg.get("tool_calls"):
        tokens += len(json.dumps(msg["tool_calls"], ensure_ascii=False)) / max(
            float(cfg["chars_per_token"]), 0.1
        )
    tokens += len(msg.get("images") or []) * float(cfg["image_tokens_estimate"])
    return tokens


def _stub_message(msg: dict, meta: dict, lang: str, cfg: dict) -> float:
    """Replace a tool-result or image message in place with a short stub. Returns tokens saved."""
    before = meta["tokens"]
    if meta["kind"] == "image":
        msg.pop("images", None)
        msg["content"] = _t_i18n("tool_image_stub", lang, round=meta["round"])
    else:
        msg["content"] = _t_i18n(
            "tool_result_stub",
            lang,
            tool=meta["tool"],
            args=meta["args"],
            round=meta["round"],
            tokens=int(before),
        )
    meta["stubbed"] = True
    return max(0.0, before - len(msg["content"]) / max(float(cfg["chars_per_token"]), 0.1))


def _stub_after_one_round(tracked: list, current_round: int, cfg: dict, lang: str) -> float:
    """Stian's rule: large results and all images are dropped once Kåre has read them one round.

    Rounds are 1-based. A result produced by round r's tool calls is read by the model in round
    r+1, so it is stubbed from round r+2 on."""
    saved = 0.0
    for msg, meta in tracked:
        if meta["stubbed"] or meta["round"] > current_round - 2:
            continue
        if meta["kind"] == "image" or meta["tokens"] > float(cfg["stub_threshold_tokens"]):
            saved += _stub_message(msg, meta, lang, cfg)
    return saved


def _stub_under_pressure(
    tracked: list, estimate: float, limit: float, lang: str, cfg: dict
) -> float:
    """Budget guard: stub remaining results oldest-first until the estimate fits under limit."""
    saved = 0.0
    for msg, meta in tracked:
        if estimate - saved <= limit:
            break
        if meta["stubbed"] or meta["tokens"] < _MIN_STUB_SAVING_TOKENS:
            continue
        saved += _stub_message(msg, meta, lang, cfg)
    return saved


def _count_today(pairs: list) -> int:
    today = datetime.now(tz=_get_local_tz()).date()
    count = 0
    for pair in pairs:
        dt = _parse_utc(pair[2]) if len(pair) > 2 else None
        if dt is not None and dt.astimezone(_get_local_tz()).date() == today:
            count += 1
    return count


def _stamp_pairs(recent_pairs: list, lang: str) -> list:
    """(user, assistant[, user_ts]) → (stamped user, assistant). Assistant text is never touched."""
    out = []
    for pair in recent_pairs:
        u_text, k_text = pair[0], pair[1]
        stamp = _replay_stamp(pair[2], lang) if len(pair) > 2 and pair[2] else ""
        out.append((f"{stamp} {u_text}" if stamp else u_text, k_text))
    return out


# Keywords that trigger the cloud model directly (bypass tools)
_CLOUD_TRIGGERS = [
    "bruk online",
    "spør online",
    "bruk cloud",
    "spør den store",
    "bruk stor modell",
    "spør stor modell",
]

_MAX_TOOL_ROUNDS = 6  # max rounds in the tool loop (prevents infinite loop)

# Model-enforcement detectors (tool-promise-without-call, truncated-think, etc.)
# live in kaare_core/model_enforcement.py — see ModelEnforcer usage in handle_generate().

_NODES_PATH = Path("/kaare/configs/nodes.yaml")

# Lock commands in all three supported languages
_LOCK_COMMANDS = frozenset({"lås", "las", "lock", "lock session", "sperr", "sperr sitzung"})


def _node_users(node_id: str) -> list[str]:
    """Return the list of user_ids configured on a node (from default_user field)."""
    try:
        data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
        node = data.get("nodes", {}).get(node_id, {})
        raw = node.get("default_user", "") or ""
        return [u.strip() for u in raw.split(",") if u.strip()]
    except Exception:
        return []


def _is_mic_node(node_id: str) -> bool:
    """Return True if the node has a microphone (ESP32 or Wyoming type)."""
    if not node_id:
        return False
    try:
        data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
        node = data.get("nodes", {}).get(node_id, {})
        ntype = node.get("type", "")
        return ntype in ("esp32", "wyoming") or bool(node.get("mic_enabled", False))
    except Exception:
        return False


def _try_unlock(node_id: str, text: str) -> tuple[str | None, str, str]:
    """
    Try to match text against unlock phrase or PIN for any user on the node.
    Returns (user_id, method, remainder_text) or (None, "", text) if no match.
    """

    for uid in _node_users(node_id):
        matched, remainder = check_unlock_phrase(uid, text)
        if matched:
            return uid, "phrase", remainder
        if check_unlock_pin(uid, text):
            return uid, "pin", ""
    return None, "", text


async def _store_input_images(images: list[str], user_id: str) -> None:
    """Save user-sent images to state/images/{user_id}/input/ in the background."""
    try:
        for img in images:
            # Strip data URI prefix if present
            raw_b64 = img.split(",", 1)[-1] if "," in img else img
            try:
                save_image(raw_b64, user_id, "input")
            except Exception as e:
                print(f"[image_store] input save failed: {e}")
    except Exception as e:
        print(f"[image_store] _store_input_images error: {e}")


def _assemble_messages(
    *,
    context_block: str,
    ltm_block: str,
    recent_pairs: list,
    current_content: str,
    images: list | None,
    user_id: str,
) -> list:
    """
    Build the `messages` list sent to the LLM from already-computed context pieces.

    Extracted from handle_generate() (P105 Phase 1) so it is a unit-testable function in
    its own right, not logic buried inline in a large request handler — this is the exact
    code that decides what actually reaches the model, which is what
    tests/test_stm_system_notes.py exercises directly.

    `current_content` already folds together every per-request note (STT, speaker,
    verification, Miss Kåre, network, rid, pending timer/login/mechanic/system notices,
    user_text) — both branches below use it as-is for the current turn's content. Fixed
    2026-09-06 (P105): the no-history branch used to rebuild a smaller, inconsistent
    subset of these pieces by hand, silently dropping several of them (including the
    system notices this function exists to deliver) whenever a conversation had no prior
    turns yet — e.g. the very first message, or right after an STM restart.

    `recent_pairs` may be (user, assistant) or (user, assistant, user_ts); with a ts the
    user turn gets a local-time stamp (P105 Phase 6).
    """
    recent_pairs = _stamp_pairs(recent_pairs, _get_lang(user_id))
    if recent_pairs:
        # First message: static context + LTM + oldest user turn
        static_ctx = "\n\n".join(
            p for p in [context_block, ltm_block, recent_pairs[0][0]] if p
        ).strip()
        messages: list = [{"role": "user", "content": static_ctx}]
        messages.append({"role": "assistant", "content": recent_pairs[0][1]})
        for u_text, k_text in recent_pairs[1:]:
            messages.append({"role": "user", "content": u_text})
            messages.append({"role": "assistant", "content": k_text})
        # Last message: current request (images here, not on the first message)
        current_msg: dict[str, Any] = {"role": "user", "content": current_content}
        if isinstance(images, list) and images:
            current_msg["images"] = images
            spawn(_store_input_images(images, user_id))
        messages.append(current_msg)
    else:
        # Ingen historikk — alt i én melding
        first_content = "\n\n".join(
            p for p in [context_block, ltm_block, current_content] if p
        ).strip()
        first_msg: dict[str, Any] = {"role": "user", "content": first_content}
        if isinstance(images, list) and images:
            first_msg["images"] = images
            spawn(_store_input_images(images, user_id))
        messages = [first_msg]

    return messages


def offered_tool_actions(tools: list) -> dict[str, set[str] | None]:
    """Tool name → allowed action values (None = the tool has no action enum) for exactly the
    tools offered in this request. The role's tool list is enforced from this, not left to
    the model (P120 Phase 1)."""
    offered: dict[str, set[str] | None] = {}
    for tool in tools or []:
        fn = tool.get("function", {})
        action = fn.get("parameters", {}).get("properties", {}).get("action", {})
        offered[fn.get("name", "")] = set(action["enum"]) if "enum" in action else None
    return offered


def refuse_unoffered_call(name: str, args: dict, offered: dict, lang: str) -> str | None:
    """Refusal text when a tool call is outside what was offered (name, or action enum), else
    None. Fails closed: an unknown name is refused."""
    if name not in offered:
        return _t_i18n("gen_tool_not_offered", lang, name=name or "?")
    allowed = offered[name]
    action = args.get("action")
    if allowed is not None and action is not None and str(action) not in allowed:
        return _t_i18n(
            "gen_tool_action_not_offered",
            lang,
            name=name,
            action=action,
            valid=", ".join(sorted(allowed)),
        )
    return None


async def handle_generate(
    *,
    prompt: str,
    images: list[str] | None = None,
    source: str = "",
    rid: str = "",
    user_id: str = USER_GLOBAL,
    memory: ShortTermMemory,
    miss_kare_addressed: bool = False,  # True when user starts message with "Miss Kåre"
    api_intent_to_ha=None,  # kept in signature for backwards compatibility
    api_exec_ha_direct=None,  # kept in signature for backwards compatibility
    api_ask_llm=None,  # kept in signature for backwards compatibility
    api_ask_vlm=None,
    api_ask_cloud=None,
    block_ha_write: bool = False,  # True for VPN users with vpn_access="ai_only"
    network_context: str = "local",  # "local" | "vpn" | "external"
    speaker_note: str = "",
    source_node: str = "",
) -> dict[str, Any]:

    start_total = time.time()
    print("[ROUTER] === handle_generate start (tool-mode) ===")

    user_text = (prompt or "").strip()
    lang = _get_lang(user_id)
    _set_request_context(source_node, network_context)
    if not user_text:
        return {"text": _t_i18n("gen_empty_message", lang)}

    # Always-visible day/session anchors — survive get_dialog_pairs()'s n=4 replay window,
    # unlike plain dialog turns. See BUG-log 2026-09-06 / P105.
    # The previous activity time is captured here, before mark_activity() overwrites it,
    # so the gap note below can say how old the replayed turns are (P105 Phase 6).
    _previous_activity = memory.mark_activity()

    # =========================================================
    # VOICE NODE UNLOCK / LOCK DETECTION
    # Runs only for mic nodes (ESP32/Wyoming). Matches phrase or PIN
    # against users configured on the node in nodes.yaml.
    # =========================================================

    _node_locked = False
    if source_node and _is_mic_node(source_node):
        text_lower = user_text.strip().lower()

        # Lock command — works even when already locked
        if text_lower in _LOCK_COMMANDS:
            _app_state.lock_node(source_node)
            return {"text": _t_i18n("voice_lock_ok", lang)}

        # Check if already unlocked — touch the rolling timer
        if _app_state.is_unlocked(source_node):
            _app_state.touch_session(source_node)
            session_user = _app_state.get_session_user(source_node)
            if session_user and user_id in _user_store.ANONYMOUS_IDS:
                user_id = session_user
                lang = _get_lang(user_id)
        else:
            # Try to unlock via phrase or PIN
            matched_uid, method, remainder = _try_unlock(source_node, user_text)
            if matched_uid:
                _app_state.unlock_node(source_node, matched_uid, method)
                if user_id in _user_store.ANONYMOUS_IDS:
                    user_id = matched_uid
                    lang = _get_lang(user_id)
                print(
                    f"[ROUTER] voice unlock: node={source_node} user={matched_uid} method={method}"
                )
                if not remainder:
                    return {"text": _t_i18n("voice_unlock_ok", lang)}
                # Remainder after phrase: continue with the stripped command
                user_text = remainder
            else:
                # No unlock — mark node as locked for tool gating below
                _node_locked = True

    print(f"[ROUTER] user_id={user_id} source={source}")

    # =========================================================
    # LONG-TERM MEMORY: feedback check + verification response + start logging
    # =========================================================
    ltm = get_ltm()

    # Check if the user is responding to an open verification request
    _pending_ver = ltm.get_pending_verification(user_id=user_id)
    if _pending_ver:
        ver_signal = ltm.detect_verification_response(user_text)
        if ver_signal:
            try:
                ltm.close_verification(_pending_ver["id"], ver_signal, user_text)
                print(f"[LTM] Verifikasjon lukket: {ver_signal} på vindu {_pending_ver['id']}")
            except Exception as e:
                print(f"[LTM] close_verification feilet: {e}")

    # Regular feedback check (tied to the last action)
    feedback_signal = ltm.detect_feedback_signal(user_text, user_id)
    if feedback_signal:
        spawn(ltm.update_feedback(ltm._last_id.get(user_id), feedback=feedback_signal))
        print(f"[LTM] Feedback: {feedback_signal} på {ltm._last_id.get(user_id)}")

    _ltm_id = await ltm.log_interaction(prompt=user_text, user_id=user_id, source=source, rid=rid)

    # Check if we have enough unverified interactions to request verification
    _ask_verification = False
    _ver_from_id = 0
    _ver_to_id = 0
    _ver_count = 0
    if not _pending_ver:  # don't ask again if we're already waiting for a response
        _ver_count, _ver_from_id, _ver_to_id = ltm.count_unverified_since_last(user_id=user_id)
        if _ver_count >= 15:
            _ask_verification = True
            print(f"[LTM] Verifikasjonstrigger: {_ver_count} ubekreftede")

    # =========================================================
    # CLOUD-TRIGGER: "bruk online ..." → skip tools
    # =========================================================
    user_text_lower = user_text.lower()
    if any(kw in user_text_lower for kw in _CLOUD_TRIGGERS) and api_ask_cloud:
        pattern = "|".join(re.escape(kw) for kw in _CLOUD_TRIGGERS)
        clean_prompt = re.sub(pattern, "", user_text, flags=re.IGNORECASE).strip(" ,.")
        if not clean_prompt:
            clean_prompt = user_text

        print(f"[ROUTER] cloud trigger rid={rid} chars={len(clean_prompt)}")
        memory.add_dialog(role="user", text=work_round.stm_text(user_text, source), user_id=user_id)
        try:
            cloud_res = await api_ask_cloud(clean_prompt)
            text_out = (cloud_res.get("text") or "").strip() if isinstance(cloud_res, dict) else ""
        except Exception as e:
            print(f"[ROUTER] cloud exception: {e}")
            text_out = ""
        text_out = text_out or _t_i18n("gen_cloud_no_answer", lang)
        memory.add_dialog(role="assistant", text=text_out, user_id=user_id)
        spawn(
            ltm.update_response(_ltm_id, response=text_out, outcome="success", model_used="cloud")
        )
        return {"text": text_out}

    # =========================================================
    # TOOL LOOP: Kåre decides everything
    # =========================================================
    print("[ROUTER] tool loop start")

    stt_note = ("\n" + _t_i18n("gen_stt_note", lang) + "\n") if source == "stt" else ""

    _ver_preview = ""
    if _ask_verification:
        try:
            _preview_rows = ltm.get_unverified_interactions(user_id=user_id, limit=3)
            if _preview_rows:
                preview_lines = []
                for r in _preview_rows:
                    ts = r["ts"][:10]
                    p = r["prompt"][:80].replace("\n", " ")
                    resp = r["response"][:120].replace("\n", " ")
                    preview_lines.append(f"  [ID {r['id']} | {ts}] Du: {p} → Kåre: {resp}")
                _ver_preview = "\n" + "\n".join(preview_lines)
        except Exception:
            pass

    ver_note = (
        _t_i18n("gen_ver_note", lang, count=_ver_count, preview=_ver_preview)
        if _ask_verification
        else ""
    )

    mk_note = _t_i18n("gen_mk_note", lang) if miss_kare_addressed else ""

    # Build static context (state, actions, daily summary) — WITHOUT dialog.
    # Dialog is injected below as real message objects to give the LLM the correct multi-turn structure.
    # A stranger gets no STM context block: it carries the daily summary, recent actions and
    # last-known device states (P120 knowledge boundary).
    _stranger = _is_stranger(user_id)
    context_block = (
        ""
        if _stranger
        else memory.build_prompt_context(
            user_text=user_text, user_id=user_id, include_dialog=False, lang=lang
        )
    )

    # RAG: hent relevante episoder fra langtidsminnet
    ltm_hits = []
    try:
        # Shared «global» episodes come back for every user — never for a stranger (P120)
        ltm_hits = [] if _stranger else await search_memory(user_text, limit=3, user_id=user_id)
    except Exception as e:
        print(f"[RAG] search_memory failed (non-critical): {e}")

    ltm_block = format_for_context(ltm_hits, lang=lang)

    network_note = (
        _t_i18n("gen_network_vpn", lang)
        if network_context == "vpn"
        else _t_i18n("gen_network_local", lang)
    )

    rid_note = _t_i18n("rid_note", _get_lang(user_id), rid=rid) if rid else ""

    # Pending timer notifications — injiseres til brukeren kvitterer (timer: ack)
    pending_note = ""
    if user_id and user_id != USER_GLOBAL:
        try:
            pending = get_pending_notifications(user_id)
            if pending:
                lines = [
                    _t_i18n(
                        "timer_pending_context",
                        lang,
                        user_id=user_id,
                        message=n["message"],
                        notif_id=n["id"],
                    )
                    for n in pending
                ]
                pending_note = "\n".join(lines)
        except Exception as e:
            print(f"[ROUTER] pending_notifications feil (ikke kritisk): {e}")

    # Login reminders (påminn_ved_login=True) — wired into the request flow
    login_reminder_note = ""
    if user_id and user_id != USER_GLOBAL:
        try:
            reminders = get_login_reminders(user_id)
            if reminders:
                reminder_lines = [
                    f"[{_t_i18n('list_remind_on_login', lang)}: {r}]" for r in reminders
                ]
                login_reminder_note = "\n".join(reminder_lines)
        except Exception as e:
            print(f"[ROUTER] login_reminders feil (ikke kritisk): {e}")

    # Pending Mechanic results — injected once, then cleared
    mechanic_note = ""
    if user_id and user_id != USER_GLOBAL:
        try:
            mechanic_results = get_pending_mechanic_results(user_id)
            if mechanic_results:
                lines = [
                    _t_i18n(
                        "mechanic_job_done",
                        lang,
                        job_id=r["job_id"][:8] + "…",
                        summary=r["summary"],
                    )
                    for r in mechanic_results
                ]
                mechanic_note = "\n".join(lines)
                ack_mechanic_results(user_id)
        except Exception as e:
            print(f"[ROUTER] pending_mechanic_results error (non-critical): {e}")

    # Pending one-shot system notices (fallback state change, timer fired, tool summary)
    # — delivered once, then cleared. Not gated on user_id != USER_GLOBAL like the notes
    # below: these describe system-wide events, not per-user reminders/jobs, and the
    # original 2026-05-05 intent was for Kåre to notice them regardless of session.
    # get_dialog_pairs() never surfaces role="system" dialog turns to the model — this is
    # the actual delivery path. See P105.
    system_notes_note = ""
    try:
        pending_system_notes = memory.get_pending_system_notes(user_id)
        if pending_system_notes:
            system_notes_note = "\n".join(pending_system_notes)
            memory.ack_pending_system_notes(user_id)
    except Exception as e:
        print(f"[ROUTER] pending_system_notes feil (ikke kritisk): {e}")

    # Replayed earlier turns: dynamic window instead of a fixed n=4 — the current session
    # first, within a token budget, plus a couple of pairs from before (P105 Phase 7 A).
    # with_ts: user turns are stamped with their local time in _assemble_messages() (Phase 6).
    _budget = _budget_cfg()
    _omitted_pairs: list = []
    try:
        recent_pairs, _omitted_pairs = memory.get_dialog_pairs_window(
            user_id,
            token_budget=int(_budget["replay_token_budget"]),
            max_pairs=int(_budget["replay_max_pairs"]),
            # a stranger only gets the conversation in progress, nothing from earlier sessions
            prior_session_pairs=0 if _stranger else int(_budget["replay_prior_session_pairs"]),
            session_gap_minutes=_SESSION_GAP_MINUTES,
            chars_per_token=float(_budget["chars_per_token"]),
        )
    except Exception as e:
        print(
            f"[ROUTER] replay window failed, falling back to the last 4 pairs (non-critical): {e}"
        )
        recent_pairs = memory.get_dialog_pairs(user_id=user_id, n=4, with_ts=True)

    # Current time, directly before the new message — after the replayed turns, not in the
    # system prompt (P105 Phase 6b). A long gap since the user's previous message (Phase 6) and
    # earlier turns from today that are not replayed (Phase 7 A) are stated in the same block.
    time_note = ""
    try:
        _omitted_today = _count_today(_omitted_pairs)
        time_note = "\n".join(
            p
            for p in [
                _time_block(lang),
                _gap_note(_previous_activity, lang),
                _t_i18n("gen_older_turns_omitted", lang, n=_omitted_today)
                if _omitted_today
                else "",
            ]
            if p
        )
    except Exception as e:
        print(f"[ROUTER] time_note failed (non-critical): {e}")

    # Notes that apply only to this request (STT, verification, Miss Kåre, network, rid, reminders)
    current_content = "\n\n".join(
        p
        for p in [
            stt_note,
            speaker_note,
            ver_note,
            mk_note,
            network_note,
            rid_note,
            pending_note,
            login_reminder_note,
            mechanic_note,
            system_notes_note,
            time_note,
            user_text,
        ]
        if p
    ).strip()

    # recent_pairs (fetched above) become real message objects: Kåre's question → user's
    # reply stay directly linked in the multi-turn structure.
    messages = _assemble_messages(
        context_block=context_block,
        ltm_block=ltm_block,
        recent_pairs=recent_pairs,
        current_content=current_content,
        images=images,
        user_id=user_id,
    )

    # A work-round turn's prompt carries the raw search result — STM keeps only its marker
    # line (P122, Stian: «jeg vil ikke ha det inn i STM»).
    memory.add_dialog(role="user", text=work_round.stm_text(user_text, source), user_id=user_id)

    # Fetch the user's selected personality variant (dict lookup in adapter, no disk I/O)
    _user_rec = _user_store.get_user(user_id)
    _personality = (_user_rec or {}).get("personality", "standard") or "standard"
    # No record → the most restricted role, never admin (P120; was «admin»)
    _user_role = (_user_rec or {}).get("role", "stranger")
    if _user_role == "child" and _personality == "standard":
        _personality = "barnevennlig"
    _tools = _get_tools_for_role(_user_role, user_id)

    _kare_cfg = get_llm_config("default")
    _kare_model = get_model(_kare_cfg.get("model_role", "kare"))
    _model_size_b = await get_model_size_b(
        _kare_model,
        _kare_cfg.get("base_url", ""),
        _kare_cfg.get("provider", "ollama"),
    )
    _tools = filter_tools_by_model(_tools, _model_size_b)
    _offered = offered_tool_actions(_tools)

    text_out = ""
    used_tools = []
    ha_actions = []  # list of (entity_id, action, ok) for all styr_enhet calls
    tool_trace = []  # visible thought process for frontend
    _used_fallback = False  # True if any round in this request used the 9B backup
    _generated_image_urls: list[str] = []  # /api/image/{id} URLs from kare_image calls
    enforcer = ModelEnforcer(
        lang, rid=rid
    )  # forces at most one correction round per request — see kaare_core/model_enforcement.py
    # Set after an enforcement retry round that made tool calls: one more round without tools, so
    # the model answers with the real tool results in view (N24 A2).
    _answer_only = False

    # P105 Phase 7 B — tool results / images tracked per round so they can be stubbed.
    _tracked: list = []  # (message dict, meta) in arrival order
    _last_prompt_tokens: int | None = None
    _last_instance = "kare"
    _msgs_at_last_call = 0
    _cpt = max(float(_budget["chars_per_token"]), 0.1)
    _tools_tokens = len(json.dumps(_tools, ensure_ascii=False)) / _cpt

    def _token_guard(round_1based: int, tools_for_call: list) -> None:
        """Stub large results read last round; then, if the next prompt would not fit, stub
        remaining results oldest-first. Estimate = last round's real prompt size + what was
        added since (or a full estimate when no usage was reported, e.g. the 9B fallback)."""
        try:
            saved = _stub_after_one_round(_tracked, round_1based, _budget, lang)
            if _last_prompt_tokens:
                estimate = (
                    _last_prompt_tokens
                    - saved
                    + sum(_msg_tokens(m, _budget) for m in messages[_msgs_at_last_call:])
                )
                if not tools_for_call:
                    estimate -= _tools_tokens
            else:
                estimate = (
                    float(_budget["system_prompt_tokens_estimate"])
                    + sum(_msg_tokens(m, _budget) for m in messages)
                    + (_tools_tokens if tools_for_call else 0.0)
                )
            limit = _input_cap(_last_instance) - float(_budget["safety_margin_tokens"])
            if estimate > limit:
                freed = _stub_under_pressure(_tracked, estimate, limit, lang, _budget)
                print(
                    f"[ROUTER] token guard round {round_1based}: estimate {int(estimate)} > "
                    f"limit {int(limit)} — stubbed ~{int(freed)} tokens"
                )
        except Exception as e:
            print(f"[ROUTER] token guard failed (non-critical): {e}")

    for round_num in range(_MAX_TOOL_ROUNDS):
        print(f"[ROUTER] tool round {round_num + 1}")
        _round_tools = [] if _answer_only else _tools
        _token_guard(round_num + 1, _round_tools)

        try:
            result = await ask_llm_with_tools(
                messages=messages,
                tools=_round_tools,
                rid=rid,
                personality=_personality,
                user_id=user_id,
                source=source or "user",
                time_in_messages=bool(time_note),
            )
        except Exception as _llm_exc:
            print(f"[ROUTER] LLM call failed (round {round_num + 1}): {_llm_exc}")
            text_out = _t_i18n("gen_no_contact", lang)
            break

        # ── Fallback state transitions → STM markers ─────────────────────────
        _meta = result.get("meta", {})
        _last_prompt_tokens = _meta.get("prompt_tokens")
        _last_instance = _meta.get("instance") or "kare"
        _msgs_at_last_call = len(messages)
        if _meta.get("instance") == "fallback_9b":
            _used_fallback = True

        if _meta.get("fallback_activated"):
            _ts = _hhmm()
            _fb_text = _t_i18n("gen_fallback_activated", lang, ts=_ts)
            memory.add_dialog(role="system", text=_fb_text, user_id=user_id)  # history/audit only
            memory.add_pending_system_note(_fb_text, user_id=user_id)  # actual delivery — see P105
            print(f"[ROUTER] fallback aktivert kl {_ts}")

        elif _meta.get("fallback_deactivated"):
            _info = _meta.get("fallback_info") or {}
            _ts = _hhmm()
            _n = _info.get("turn_count", 0)
            _t0_fb = _info.get("ts_start", "ukjent")
            _fb_text = _t_i18n("gen_fallback_recovered", lang, ts=_ts, n=_n, t0=_t0_fb)
            memory.add_dialog(role="system", text=_fb_text, user_id=user_id)  # history/audit only
            memory.add_pending_system_note(_fb_text, user_id=user_id)  # actual delivery — see P105
            print(f"[ROUTER] fallback gjenopprettet etter {_n} turns")
            spawn(
                ltm.log_fallback_session(
                    ts_start=_t0_fb,
                    ts_end=_utc_iso(),
                    turn_count=_n,
                )
            )
        # ─────────────────────────────────────────────────────────────────────

        if not result.get("ok") and not result.get("tool_calls"):
            text_out = result.get("text") or _t_i18n("gen_no_contact", lang)
            break

        tool_calls = None if _answer_only else result.get("tool_calls")

        if not tool_calls:
            # Final answer — Kåre is done
            _raw_text = result.get("text", "").strip()

            action = enforcer.check(text_out=_raw_text, used_tools=used_tools, meta=_meta)
            if action:
                messages.append(result["message"])
                if round_num < _MAX_TOOL_ROUNDS - 1:
                    messages.append({"role": "user", "content": action.correction_message})
                    continue  # send the correction to the model — do NOT break here
                # Last tool round (N24 A6): long answers after many tools end here, and no tool
                # round is left for a correction that demands a call. Ask for an honest answer
                # instead, in one call without tools; resolve() below then uses that answer.
                messages.append(
                    {"role": "user", "content": _t_i18n("gen_correction_no_tools_left", lang)}
                )
                _token_guard(_MAX_TOOL_ROUNDS + 1, [])
                try:
                    _honest = await ask_llm_with_tools(
                        messages=messages,
                        tools=[],
                        rid=rid,
                        personality=_personality,
                        user_id=user_id,
                        source=source or "user",
                        time_in_messages=bool(time_note),
                    )
                    _raw_text = (_honest.get("text") or "").strip()
                except Exception as _hon_exc:
                    print(f"[ROUTER] last-round honesty call failed: {_hon_exc}")
                    _raw_text = ""

            text_out = _raw_text or _t_i18n("gen_no_response", lang)
            if enforcer.pending:
                text_out = enforcer.resolve(text_out, None)

            print(f"[ROUTER] final answer after {round_num + 1} round(s)")
            break

        # Kåre wants to call tools — add the assistant message to the history
        messages.append(result["message"])

        # Execute all tool calls in this round concurrently
        async def _run_tc(tc):
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = dict(fn.get("arguments", {}))
            args["_user_id"] = user_id
            args["_rid"] = rid
            args["_block_ha_write"] = block_ha_write
            args["_source_node"] = source_node
            args["_node_locked"] = _node_locked
            args["_source"] = source or "user"  # e.g. «work_round» turns search directly (P122)
            print(f"[ROUTER] tool-kall: {name}({args})")
            # Fourth value = the tool actually ran. Refused, corrected and crashed calls do not
            # count as used, so they cannot cover a claim in the reply (N24 A3).
            refusal = refuse_unoffered_call(name, args, _offered, lang)
            if refusal:
                print(f"[ROUTER] tool call refused (not offered for role {_user_role}): {name}")
                _log_tool_call(name, args, refusal, 0, source="kare", rid=rid)
                return name, args, refusal, False
            correction = enforcer.check_tool_call(name, args)  # e.g. web_search without near_home
            if correction:
                _log_tool_call(name, args, correction, 0, source="kare", rid=rid)
                return name, args, correction, False
            try:
                res = await execute_tool(name, args)
            except Exception as _tc_exc:
                res = _t_i18n("gen_tool_exec_error", lang, name=name, error=_tc_exc)
                print(f"[ROUTER] execute_tool exception ({name}): {_tc_exc}")
                return name, args, res, False
            print(f"[ROUTER] tool-resultat: {res[:120]}")
            return name, args, res, True

        _runs = await asyncio.gather(*[_run_tc(tc) for tc in tool_calls])
        call_results = [(n, a, r) for n, a, r, _ran in _runs]
        for n, a, _r, _ran in _runs:
            if _ran:
                used_tools.extend(tool_usage_tags(n, a))

        _round_vision: list[str] = []

        for fn_name, fn_args, tool_result in call_results:
            if fn_name in ("ha_control", "styr_enhet"):
                ok = "OK:" in tool_result
                ha_actions.append((fn_args.get("entity_id"), fn_args.get("action"), ok))

            if fn_name in ("kare_image", "view_images", "camera", "se_bilder", "kamera"):
                for _m in re.finditer(r"/api/image/[a-zA-Z0-9_-]+", tool_result):
                    _url = _m.group(0)
                    if _url not in _generated_image_urls:
                        _generated_image_urls.append(_url)

            if tool_result.startswith("[VISION:") and tool_result.endswith("]"):
                _round_vision.append(tool_result[8:-1])
                tool_result = _t_i18n("tool_vision_loaded", lang)

            args_str = ", ".join(f"{k}={v}" for k, v in fn_args.items() if not k.startswith("_"))
            tool_trace.append(
                {
                    "round": round_num + 1,
                    "tool": fn_name,
                    "args": args_str[:80],
                    "result": tool_result[:120],
                }
            )

            _tool_msg = {"role": "tool", "content": tool_result}
            messages.append(_tool_msg)
            _tracked.append(
                (
                    _tool_msg,
                    {
                        "kind": "tool",
                        "round": round_num + 1,
                        "tool": fn_name,
                        "args": args_str[:80],
                        "tokens": _msg_tokens(_tool_msg, _budget),
                        "stubbed": False,
                    },
                )
            )

        if _round_vision:
            _img_msg = {
                "role": "user",
                "content": _t_i18n("gen_image_note", lang),
                "images": _round_vision,
            }
            messages.append(_img_msg)
            _tracked.append(
                (
                    _img_msg,
                    {
                        "kind": "image",
                        "round": round_num + 1,
                        "tool": "",
                        "args": "",
                        "tokens": _msg_tokens(_img_msg, _budget),
                        "stubbed": False,
                    },
                )
            )

        # The enforcement retry round made tool calls: give the model one more round without
        # tools to answer from the real results (N24 A2) — never the original claim plus a «✓»
        # the code cannot verify. That round ends in the final-answer branch above, which
        # calls enforcer.resolve().
        if enforcer.pending:
            _answer_only = True

    else:
        # All 6 rounds used — one final call without tools, just to produce a response
        print("[ROUTER] round 7 (output-only): forcing response to user")
        messages.append({"role": "user", "content": _t_i18n("gen_tool_limit", lang)})
        _token_guard(_MAX_TOOL_ROUNDS + 1, [])
        try:
            _final = await ask_llm_with_tools(
                messages=messages,
                tools=[],
                rid=rid,
                personality=_personality,
                user_id=user_id,
                source=source or "user",
                time_in_messages=bool(time_note),
            )
            text_out = (_final.get("text") or "").strip()
        except Exception as e:
            print(f"[ROUTER] output-runde feilet: {e}")
        text_out = text_out or _t_i18n("gen_timeout", lang)

    # Strip the [LANG:xx] self-report tag (see model_enforcement.py) — internal signal
    # only, never shown to the user. Every exit path above converges here first.
    text_out = strip_language_tag(text_out)

    # Append any generated image URLs that the LLM forgot to include in its reply
    for _img_url in _generated_image_urls:
        if _img_url not in text_out:
            text_out = text_out.rstrip() + f"\n{_img_url}"

    # =========================================================
    # Update short-term and long-term memory
    # =========================================================

    # Log all HA actions to STM (not just the last one)
    for entity, action, ok in ha_actions:
        if entity and action:
            memory.record_action(action=action, entity_id=entity, ok=ok, user_id=user_id)
            if ok:
                memory.set_state(key=entity, value=action, source="ha")

    # LTM uses the last successful action
    successful = [(e, a) for e, a, ok in ha_actions if ok]
    final_entity = successful[-1][0] if successful else None
    final_action = successful[-1][1] if successful else None

    memory.add_dialog(role="assistant", text=text_out, user_id=user_id)

    # Save tool summary to STM so Kåre knows what he did in the last response
    if tool_trace:
        calls_desc = []
        for tc in tool_trace:
            entry = tc["tool"]
            if tc.get("args"):
                entry += f"({tc['args'][:60]})"
            calls_desc.append(entry)
        tool_summary = _t_i18n("gen_tool_summary", lang, calls=" → ".join(calls_desc))
        memory.add_dialog(role="system", text=tool_summary, user_id=user_id)  # history/audit only
        memory.add_pending_system_note(tool_summary, user_id=user_id)  # actual delivery — see P105

    outcome = "success" if text_out and not text_out.startswith("[") else "error"
    spawn(
        ltm.update_response(
            _ltm_id,
            response=text_out,
            outcome=outcome,
            intent="tools",
            entity_id=final_entity,
            action=final_action,
            model_used="9b_fallback" if _used_fallback else "",
        )
    )

    # Open verification window if we asked this round
    if _ask_verification and _ver_from_id and _ver_to_id:
        try:
            ltm.open_verification(_ver_from_id, _ver_to_id, _ver_count, user_id=user_id)
            print(
                f"[LTM] Verifikasjonsvindu åpnet: {_ver_count} rader ({_ver_from_id}→{_ver_to_id})"
            )
        except Exception as e:
            print(f"[LTM] open_verification feilet: {e}")

    elapsed = round(time.time() - start_total, 3)
    print("[ROUTER] === handle_generate end ===", elapsed, "s")
    if elapsed > 20:
        logger.warning(f"Slow LLM response: {elapsed}s for prompt: {prompt[:80]!r}")
    return {"text": text_out, "trace": tool_trace}
