# /kaare/kaare_core/routers/router_generate.py
"""
Router for /api/generate

Kåre drives entirely by tools — he decides what to do.
The old hardcoded pipeline is commented out at the bottom of the file
and kept until the tool pipeline is stable and tested.
"""
import asyncio
import base64
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
from kaare_core.config import get_local_tz as _get_local_tz

from typing import Dict, Any, Optional
from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.memory.long_term import get_ltm, USER_GLOBAL
from kaare_core.tools.timer_service import get_pending_notifications
from kaare_core.agents.mechanic.job_store import get_pending_mechanic_results, ack_mechanic_results
from kaare_core.tools.lister import huske_hent_påminnelser as get_login_reminders
from kaare_core.memory.semantic_memory import search_memory, format_for_context
from kaare_core.tools.executor import execute_tool
from adapters.llm_adapter import ask_llm_with_tools, get_model_size_b
from kaare_core.config import get_tools_for_role as _get_tools_for_role, filter_tools_by_model, get_llm_config, get_model
from kaare_core.users import store as _user_store
from kaare_core.llm_fallback import is_fallback_active
from kaare_core.tools.i18n import get_lang as _get_lang, t as _t_i18n
from kaare_core.context_builder import set_request_context as _set_request_context


def _hhmm() -> str:
    return datetime.now(tz=_get_local_tz()).strftime("%H:%M")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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

# Promise interceptor: detect when Kåre claims to use a tool but never calls it.
# Fires at most once per request. Patterns cover nb / en / de.
# Each entry: (expected_tool_names, i18n_correction_key, compiled_pattern)
_TOOL_PROMISES: list[tuple[set[str], str, re.Pattern]] = [
    (
        {"note"},
        "gen_promise_correction_note",
        re.compile(
            # nb
            r'\bjeg\s+(noterte|noterer|har\s+notert|skriver?\s+det\s+ned|har\s+skrevet'
            r'|husker\s+det|vil\s+huske|la\s+det\s+til|har\s+lagt\s+(det\s+)?til'
            r'|følger\s+opp|merker\s+meg|noterer\s+meg)\b'
            r'|\b(huskelisten\s+min|min\s+huskeliste|på\s+lista\s+mi'
            r'|lagt\s+til\s+på\s+(min|kåres?)\s+(liste|huskeliste))\b'
            # en
            r"|I'?ve?\s+(noted\s+that|jotted\s+that\s+down|written\s+(that\s+)?down"
            r'|added\s+(it\s+)?to\s+(my\s+)?(list|notes?))\b'
            r"|\bI('ll|'m going to)\s+(note|jot|write\s+(that\s+)?down)\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"timer"},
        "gen_promise_correction_timer",
        re.compile(
            # nb
            r'\bjeg\s+(setter?\s+(en\s+)?(timer|påminnelse|alarm)'
            r'|minner\s+deg\s+om|vil\s+minne\s+deg|kommer\s+til\s+å\s+minne'
            r'|har\s+satt\s+(en\s+)?(timer|påminnelse))\b'
            r'|\b(timer\s+er\s+satt|påminnelse\s+er\s+(satt|opprettet))\b'
            # en
            r"|I('ll|'ve|'m going to)\s+(set\s+(a\s+)?(timer|reminder|alarm)"
            r'|remind\s+you)\b'
            r"|\bI('ve|have)\s+set\s+(a\s+)?(timer|reminder)\b"
            # de
            r'|\bich\s+(stelle\s+(einen?\s+)?(timer|wecker|erinnerung)'
            r'|erinnere\s+dich|werde\s+(dich\s+)?erinnern'
            r'|habe\s+(einen?\s+)?(timer|erinnerung)\s+gestellt)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"user_profile"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r'\bjeg\s+(oppdaterer\s+(profilen\s+din|brukerinfo(en)?)'
            r'|lagrer\s+det\s+i\s+profilen|har\s+(oppdatert|lagret)\s+(profilen|det\s+i\s+profilen))\b'
            # en
            r"|I('ve|'ll)\s+(updated?|saved?|stored?)\s+(your\s+)?(profile|user\s+info|preferences)\b"
            # de
            r'|\bich\s+(habe\s+(dein\s+)?profil\s+aktualisiert'
            r'|aktualisiere\s+(dein\s+)?profil|speichere\s+(es\s+)?in\s+(deinem\s+)?profil)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"world"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r'\bjeg\s+(oppdaterer\s+verdensbildet|lagrer\s+det\s+i\s+verdensbildet'
            r'|har\s+oppdatert\s+verdensbildet)\b'
            # en
            r"|I('ve|'ll)\s+(updated?|saved?)\s+(the\s+)?(world\s+(model|view|knowledge))\b"
            # de
            r'|\bich\s+(habe\s+(das\s+)?weltbild\s+aktualisiert|aktualisiere\s+(das\s+)?weltbild)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"memory"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r'\bjeg\s+(lagrer?\s+det\s+i\s+minnet|har\s+lagret\s+det\s+i\s+minnet'
            r'|søker\s+i\s+minnet\s+etter)\b'
            # en
            r"|I('ve|'ll)\s+(saved?\s+(that\s+)?to\s+(my\s+)?memory|stored?\s+that\s+in\s+memory)\b"
            # de
            r'|\bich\s+(speichere\s+(es\s+)?im\s+(gedächtnis|speicher)'
            r'|habe\s+(es\s+)?im\s+gedächtnis\s+gespeichert)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"household"},
        "gen_promise_correction_household",
        re.compile(
            # nb (covers both "jeg setter X" and V2 word order "setter jeg X")
            r'\b(jeg\s+(setter|aktiverer|har\s+(satt|aktivert))|(setter|aktiverer|har\s+(satt|aktivert))\s+jeg)'
            r'\s+(husstanden|huset|dere)\s+(til\s+)?["\']?(borte\S*|bortreise\S*|hjemme\S*)\b'
            r'|\b(bortreisemodus|borte-?modus)\s+(er\s+)?(nå\s+)?(aktivert|satt|slått\s+på|skrudd\s+på)\b'
            r'|\bhusstanden\s+er\s+(nå\s+)?satt\s+til\s+(borte|hjemme)\b'
            # en
            r"|\bI('ve|'ll|'m going to)\s+(set|activate|switch)\s+(the\s+)?(household|house)\s+(to\s+)?(away|home)\b"
            r'|\baway\s+mode\s+is\s+(now\s+)?(activated|set|on)\b'
            # de
            r'|\bich\s+(setze|aktiviere)\s+den\s+(haushalt|abwesenheitsmodus)\b'
            r'|\bhabe\s+den\s+haushalt\s+auf\s+(abwesend|zuhause)\s+gesetzt\b'
            r'|\babwesenheitsmodus\s+ist\s+(jetzt\s+)?(aktiviert|eingeschaltet|gesetzt)\b',
            re.IGNORECASE,
        ),
    ),
]


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
    from kaare_core.users.profile_manager import check_unlock_phrase, check_unlock_pin
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
        from kaare_core.image_store import save_image
        import base64 as _b64
        for img in images:
            # Strip data URI prefix if present
            raw_b64 = img.split(",", 1)[-1] if "," in img else img
            try:
                save_image(raw_b64, user_id, "input")
            except Exception as e:
                print(f"[image_store] input save failed: {e}")
    except Exception as e:
        print(f"[image_store] _store_input_images error: {e}")


async def handle_generate(
    *,
    prompt: str,
    images: Optional[list[str]] = None,
    source: str = "",
    rid: str = "",
    user_id: str = USER_GLOBAL,
    memory: ShortTermMemory,
    miss_kare_addressed: bool = False,  # True when user starts message with "Miss Kåre"
    api_intent_to_ha=None,        # kept in signature for backwards compatibility
    api_exec_ha_direct=None,      # kept in signature for backwards compatibility
    api_ask_llm=None,             # kept in signature for backwards compatibility
    api_ask_vlm=None,
    api_ask_cloud=None,
    block_ha_write: bool = False,  # True for VPN users with vpn_access="ai_only"
    network_context: str = "local",  # "local" | "vpn" | "external"
    speaker_note: str = "",
    source_node: str = "",
) -> Dict[str, Any]:

    start_total = time.time()
    print("[ROUTER] === handle_generate start (tool-mode) ===")

    user_text = (prompt or "").strip()
    lang = _get_lang(user_id)
    _set_request_context(source_node, network_context)
    if not user_text:
        return {"text": _t_i18n("gen_empty_message", lang)}

    # =========================================================
    # VOICE NODE UNLOCK / LOCK DETECTION
    # Runs only for mic nodes (ESP32/Wyoming). Matches phrase or PIN
    # against users configured on the node in nodes.yaml.
    # =========================================================
    from kaare_core import app_state as _app_state

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
            if session_user and user_id == USER_GLOBAL:
                user_id = session_user
                lang = _get_lang(user_id)
        else:
            # Try to unlock via phrase or PIN
            matched_uid, method, remainder = _try_unlock(source_node, user_text)
            if matched_uid:
                _app_state.unlock_node(source_node, matched_uid, method)
                if user_id == USER_GLOBAL:
                    user_id = matched_uid
                    lang = _get_lang(user_id)
                print(f"[ROUTER] voice unlock: node={source_node} user={matched_uid} method={method}")
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
        asyncio.create_task(ltm.update_feedback(ltm._last_id.get(user_id), feedback=feedback_signal))
        print(f"[LTM] Feedback: {feedback_signal} på {ltm._last_id.get(user_id)}")

    _ltm_id = await ltm.log_interaction(prompt=user_text, user_id=user_id, source=source)

    # Check if we have enough unverified interactions to request verification
    _ask_verification   = False
    _ver_from_id        = 0
    _ver_to_id          = 0
    _ver_count          = 0
    if not _pending_ver:   # don't ask again if we're already waiting for a response
        _ver_count, _ver_from_id, _ver_to_id = ltm.count_unverified_since_last(user_id=user_id)
        if _ver_count >= 15:
            _ask_verification = True
            print(f"[LTM] Verifikasjonstrigger: {_ver_count} ubekreftede")

    # =========================================================
    # CLOUD-TRIGGER: "bruk online ..." → skip tools
    # =========================================================
    import re
    user_text_lower = user_text.lower()
    if any(kw in user_text_lower for kw in _CLOUD_TRIGGERS) and api_ask_cloud:
        pattern = "|".join(re.escape(kw) for kw in _CLOUD_TRIGGERS)
        clean_prompt = re.sub(pattern, "", user_text, flags=re.IGNORECASE).strip(" ,.")
        if not clean_prompt:
            clean_prompt = user_text

        print(f"[ROUTER] cloud trigger rid={rid} chars={len(clean_prompt)}")
        memory.add_dialog(role="user", text=user_text, user_id=user_id)
        try:
            cloud_res = await api_ask_cloud(clean_prompt)
            text_out  = (cloud_res.get("text") or "").strip() if isinstance(cloud_res, dict) else ""
        except Exception as e:
            print(f"[ROUTER] cloud exception: {e}")
            text_out = ""
        text_out = text_out or "Cloud-modellen svarte ikke. Prøv igjen."
        memory.add_dialog(role="assistant", text=text_out, user_id=user_id)
        asyncio.create_task(ltm.update_response(
            _ltm_id, response=text_out, outcome="success", model_used="cloud"
        ))
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
        f"\n\n[SYSTEM: Du har {_ver_count} interaksjoner uten brukerbekreftelse. "
        f"Her er de tidligste:{_ver_preview}\n"
        "Presenter 2–3 av dem naturlig i samtalen og spør om de stemte — bruk din egen stemme, ingen fast mal. "
        "Bruk hent_ubekreftede-verktøyet for å hente neste batch.]\n"
        if _ask_verification else ""
    )

    mk_note = (
        "\n\n[SYSTEM: Brukeren adresserer Miss Kåre direkte i dette innlegget. "
        "Miss Kåre leser meldingen selv og svarer i sitt eget panel — du trenger ikke videreformidle, "
        "introdusere henne, eller 'gi henne ordet'. "
        "Si gjerne noe kort fra din egen synsvinkel hvis du genuint har noe å tilføye, "
        "ellers hold deg i bakgrunnen.]\n"
        if miss_kare_addressed else ""
    )

    # Build static context (state, actions, daily summary) — WITHOUT dialog.
    # Dialog is injected below as real message objects to give the LLM the correct multi-turn structure.
    context_block = memory.build_prompt_context(
        user_text=user_text, user_id=user_id, include_dialog=False
    )

    # RAG: hent relevante episoder fra langtidsminnet
    ltm_hits = []
    try:
        ltm_hits = await search_memory(user_text, limit=3, user_id=user_id)
    except Exception as e:
        print(f"[RAG] search_memory feilet (ikke kritisk): {e}")

    ltm_block = format_for_context(ltm_hits)

    network_note = (
        "[Tilkobling: ekstern via VPN — brukeren er ikke hjemme akkurat nå.]"
        if network_context == "vpn"
        else "[Tilkobling: lokal — brukeren er hjemme.]"
    )

    rid_note = _t_i18n("rid_note", _get_lang(user_id), rid=rid) if rid else ""

    # Pending timer notifications — injiseres til brukeren kvitterer (timer: ack)
    pending_note = ""
    if user_id and user_id != USER_GLOBAL:
        try:
            pending = get_pending_notifications(user_id)
            if pending:
                lines = [
                    _t_i18n("timer_pending_context", lang,
                            user_id=user_id,
                            message=n["message"],
                            notif_id=n["id"])
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
                reminder_lines = [f"[{_t_i18n('list_remind_on_login', lang)}: {r}]" for r in reminders]
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
                    _t_i18n("mechanic_job_done", lang,
                            job_id=r["job_id"][:8] + "…",
                            summary=r["summary"])
                    for r in mechanic_results
                ]
                mechanic_note = "\n".join(lines)
                ack_mechanic_results(user_id)
        except Exception as e:
            print(f"[ROUTER] pending_mechanic_results error (non-critical): {e}")

    # Notes that apply only to this request (STT, verification, Miss Kåre, network, rid, reminders)
    current_content = "\n\n".join(
        p for p in [stt_note, speaker_note, ver_note, mk_note, network_note, rid_note,
                    pending_note, login_reminder_note, mechanic_note, user_text] if p
    ).strip()

    # Fetch the last 4 (user, kåre) pairs from STM as real message objects.
    # This gives the LLM the correct multi-turn structure: Kåre's question → user's reply are directly linked.
    recent_pairs = memory.get_dialog_pairs(user_id=user_id, n=4)

    if recent_pairs:
        # First message: static context + LTM + oldest user turn
        static_ctx = "\n\n".join(
            p for p in [context_block, ltm_block, recent_pairs[0][0]] if p
        ).strip()
        messages: list = [{"role": "user", "content": static_ctx}]
        messages.append({"role": "assistant", "content": recent_pairs[0][1]})
        for u_text, k_text in recent_pairs[1:]:
            messages.append({"role": "user",      "content": u_text})
            messages.append({"role": "assistant", "content": k_text})
        # Last message: current request (images here, not on the first message)
        current_msg: Dict[str, Any] = {"role": "user", "content": current_content}
        if isinstance(images, list) and images:
            current_msg["images"] = images
            asyncio.create_task(_store_input_images(images, user_id))
        messages.append(current_msg)
    else:
        # Ingen historikk — alt i én melding (same as before)
        first_parts = [
            p for p in [context_block, ltm_block, stt_note, ver_note,
                         mk_note, network_note, user_text] if p
        ]
        first_content = "\n\n".join(first_parts).strip()
        first_msg: Dict[str, Any] = {"role": "user", "content": first_content}
        if isinstance(images, list) and images:
            first_msg["images"] = images
            asyncio.create_task(_store_input_images(images, user_id))
        messages = [first_msg]

    memory.add_dialog(role="user", text=user_text, user_id=user_id)

    # Fetch the user's selected personality variant (dict lookup in adapter, no disk I/O)
    _user_rec = _user_store.get_user(user_id) if user_id != USER_GLOBAL else None
    _personality = (_user_rec or {}).get("personality", "standard") or "standard"
    _user_role = (_user_rec or {}).get("role", "admin")
    if _user_role == "child" and _personality == "standard":
        _personality = "barnevennlig"
    _tools = _get_tools_for_role(_user_role, user_id)

    _kare_cfg   = get_llm_config("default")
    _kare_model = get_model(_kare_cfg.get("model_role", "kare"))
    _model_size_b = await get_model_size_b(
        _kare_model,
        _kare_cfg.get("base_url", ""),
        _kare_cfg.get("provider", "ollama"),
    )
    _tools = filter_tools_by_model(_tools, _model_size_b)

    text_out      = ""
    used_tools    = []
    ha_actions    = []  # list of (entity_id, action, ok) for all styr_enhet calls
    tool_trace    = []  # visible thought process for frontend
    _used_fallback = False  # True if any round in this request used the 9B backup
    _generated_image_urls: list[str] = []  # /api/image/{id} URLs from kare_image calls
    _promise_retry_done = False  # fires at most once per request
    _original_text_out  = ""    # text saved when promise interceptor fires

    for round_num in range(_MAX_TOOL_ROUNDS):
        print(f"[ROUTER] tool round {round_num + 1}")

        try:
            result = await ask_llm_with_tools(
                messages=messages, tools=_tools, rid=rid, personality=_personality, user_id=user_id
            )
        except Exception as _llm_exc:
            print(f"[ROUTER] LLM call failed (round {round_num + 1}): {_llm_exc}")
            text_out = _t_i18n("gen_no_contact", lang)
            break

        # ── Fallback state transitions → STM markers ─────────────────────────
        _meta = result.get("meta", {})
        if _meta.get("instance") == "fallback_9b":
            _used_fallback = True

        if _meta.get("fallback_activated"):
            _ts = _hhmm()
            memory.add_dialog(
                role="system",
                text=f"[RESERVEMODUS aktivert kl. {_ts}: kjernemodellen svarte ikke. Svarer via 9B-backup-modell.]",
                user_id=user_id,
            )
            print(f"[ROUTER] fallback aktivert kl {_ts}")

        elif _meta.get("fallback_deactivated"):
            _info = _meta.get("fallback_info") or {}
            _ts   = _hhmm()
            _n    = _info.get("turn_count", 0)
            _t0_fb = _info.get("ts_start", "ukjent")
            memory.add_dialog(
                role="system",
                text=f"[GJENOPPRETTET kl. {_ts}: kjernemodellen er tilbake. Jeg svarte {_n} spørsmål i reservemodus (fra {_t0_fb}).]",
                user_id=user_id,
            )
            print(f"[ROUTER] fallback gjenopprettet etter {_n} turns")
            asyncio.create_task(ltm.log_fallback_session(
                ts_start=_t0_fb,
                ts_end=_utc_iso(),
                turn_count=_n,
            ))
        # ─────────────────────────────────────────────────────────────────────

        if not result.get("ok") and not result.get("tool_calls"):
            text_out = result.get("text") or "Jeg fikk ikke kontakt med systemene akkurat nå."
            break

        tool_calls = result.get("tool_calls")

        if not tool_calls:
            # Final answer — Kåre is done
            text_out = result.get("text", "").strip() or _t_i18n("gen_no_response", lang)

            # Promise interceptor: if Kåre claimed to use a tool but never called it,
            # force one correction round. Covers note/timer/user_profile/world/memory.
            if not _promise_retry_done and round_num < _MAX_TOOL_ROUNDS - 1:
                _used_set = set(used_tools)
                for _expected, _corr_key, _pattern in _TOOL_PROMISES:
                    if _expected & _used_set:
                        continue  # tool was already called this request
                    if _pattern.search(text_out):
                        _promise_retry_done = True
                        _original_text_out = text_out
                        print(f"[ROUTER] promise interceptor: promised {_expected} without calling — forcing retry")
                        messages.append(result["message"])
                        messages.append({"role": "user", "content": _t_i18n(_corr_key, lang)})
                        break

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
            print(f"[ROUTER] tool-kall: {name}({args})")
            try:
                res = await execute_tool(name, args)
            except Exception as _tc_exc:
                res = f"Feil ved utføring av verktøy {name}: {_tc_exc}"
                print(f"[ROUTER] execute_tool exception ({name}): {_tc_exc}")
            print(f"[ROUTER] tool-resultat: {res[:120]}")
            return name, args, res

        call_results = await asyncio.gather(*[_run_tc(tc) for tc in tool_calls])

        _round_vision: list[str] = []

        for fn_name, fn_args, tool_result in call_results:
            used_tools.append(fn_name)

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
                tool_result = "Bildet er lastet og sendt til deg som visjon-input."

            args_str = ", ".join(f"{k}={v}" for k, v in fn_args.items() if not k.startswith("_"))
            tool_trace.append({
                "round": round_num + 1,
                "tool":  fn_name,
                "args":  args_str[:80],
                "result": tool_result[:120],
            })

            messages.append({"role": "tool", "content": tool_result})

        if _round_vision:
            messages.append({
                "role": "user",
                "content": _t_i18n("gen_image_note", lang),
                "images": _round_vision,
            })

        # Promise interceptor: after the forced tool round, break immediately.
        # Return the original text + a small tag — no new LLM round needed.
        if _promise_retry_done:
            _tags = []
            for _fn, _fa, _tr in call_results:
                if _fn in ("note", "notat"):
                    _tags.append("_(notert ✓)_")
                else:
                    # Tool with real output — include truncated result
                    _tags.append(f"_({_tr[:120]})_")
            text_out = _original_text_out.rstrip()
            if _tags:
                text_out += "\n\n" + " · ".join(_tags)
            print("[ROUTER] promise interceptor: returning original text + tag")
            break

    else:
        # All 6 rounds used — one final call without tools, just to produce a response
        print("[ROUTER] round 7 (output-only): forcing response to user")
        messages.append({
            "role": "user",
            "content": _t_i18n("gen_tool_limit", lang)
        })
        try:
            _final = await ask_llm_with_tools(
                messages=messages, tools=[], rid=rid, personality=_personality, user_id=user_id
            )
            text_out = (_final.get("text") or "").strip()
        except Exception as e:
            print(f"[ROUTER] output-runde feilet: {e}")
        text_out = text_out or _t_i18n("gen_timeout", lang)

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
        tool_summary = "Mine verktøykall forrige svar: " + " → ".join(calls_desc)
        memory.add_dialog(role="system", text=tool_summary, user_id=user_id)

    outcome = "success" if text_out and not text_out.startswith("[") else "error"
    asyncio.create_task(ltm.update_response(
        _ltm_id,
        response=text_out,
        outcome=outcome,
        intent="tools",
        entity_id=final_entity,
        action=final_action,
        model_used="9b_fallback" if _used_fallback else "",
    ))

    # Open verification window if we asked this round
    if _ask_verification and _ver_from_id and _ver_to_id:
        try:
            ltm.open_verification(_ver_from_id, _ver_to_id, _ver_count, user_id=user_id)
            print(f"[LTM] Verifikasjonsvindu åpnet: {_ver_count} rader ({_ver_from_id}→{_ver_to_id})")
        except Exception as e:
            print(f"[LTM] open_verification feilet: {e}")

    elapsed = round(time.time() - start_total, 3)
    print("[ROUTER] === handle_generate end ===", elapsed, "s")
    if elapsed > 20:
        logger.warning(f"Slow LLM response: {elapsed}s for prompt: {prompt[:80]!r}")
    return {"text": text_out, "trace": tool_trace}

