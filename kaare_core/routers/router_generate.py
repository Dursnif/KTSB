# /kaare/kaare_core/routers/router_generate.py
"""
Router for /api/generate

Kåre styrer alt via tools. Han bestemmer selv hva han gjør.
Gammel hardkodet pipeline er kommentert bort nederst i filen —
beholdes til tool-pipelinen er stabil og testet.
"""
import asyncio
import base64
import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from kaare_core.config import get_local_tz as _get_local_tz

from typing import Dict, Any, Optional
from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.memory.long_term import get_ltm, USER_GLOBAL
from kaare_core.memory.semantic_memory import search_memory, format_for_context
from kaare_core.tools.executor import execute_tool
from adapters.llm_adapter import ask_llm_with_tools, get_model_size_b
from kaare_core.config import get_tools_for_role as _get_tools_for_role, filter_tools_by_model, get_llm_config, get_model
from kaare_core.users import store as _user_store
from kaare_core.llm_fallback import is_fallback_active


def _hhmm() -> str:
    return datetime.now(tz=_get_local_tz()).strftime("%H:%M")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Nøkkelord som trigger cloud-modellen direkte (bypass tools)
_CLOUD_TRIGGERS = [
    "bruk online",
    "spør online",
    "bruk cloud",
    "spør den store",
    "bruk stor modell",
    "spør stor modell",
]

_MAX_TOOL_ROUNDS = 6  # maks runder i tool-løkken (forhindrer uendelig loop)

# Promise interceptor: detect when Kåre claims to write/note something without a tool call.
# Only fires once per request, only if no "notat" tool was already called this session.
_PROMISE_RE = re.compile(
    r'\bjeg\s+(noterte|noterer|har\s+notert|skriver?\s+det\s+ned|'
    r'har\s+skrevet|husker\s+det|vil\s+huske|la\s+det\s+til|'
    r'har\s+lagt\s+(det\s+)?til|følger\s+opp|merker\s+meg|noterer\s+meg)\b'
    r'|'
    r'\b(huskelisten\s+min|min\s+huskeliste|på\s+lista\s+mi|lagt\s+til\s+på\s+(min|kåres?)\s+(liste|huskeliste))\b',
    re.IGNORECASE,
)


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
    miss_kare_addressed: bool = False,  # True når bruker starter melding med "Miss Kåre"
    api_intent_to_ha=None,        # beholdes i signatur for bakoverkompatibilitet
    api_exec_ha_direct=None,      # beholdes i signatur for bakoverkompatibilitet
    api_ask_llm=None,             # beholdes i signatur for bakoverkompatibilitet
    api_ask_vlm=None,
    api_ask_cloud=None,
    block_ha_write: bool = False,  # True for VPN users with vpn_access="ai_only"
    network_context: str = "local",  # "local" | "vpn" | "external"
) -> Dict[str, Any]:

    start_total = time.time()
    print("[ROUTER] === handle_generate start (tool-mode) ===")

    user_text = (prompt or "").strip()
    if not user_text:
        return {"text": "Jeg fikk en tom melding."}

    print(f"[ROUTER] user_id={user_id} source={source}")

    # =========================================================
    # LANGTIDSMINNE: feedback-sjekk + verifikasjonssvar + start logging
    # =========================================================
    ltm = get_ltm()

    # Sjekk om brukeren svarer på en åpen verifikasjonsforespørsel
    _pending_ver = ltm.get_pending_verification(user_id=user_id)
    if _pending_ver:
        ver_signal = ltm.detect_verification_response(user_text)
        if ver_signal:
            try:
                ltm.close_verification(_pending_ver["id"], ver_signal, user_text)
                print(f"[LTM] Verifikasjon lukket: {ver_signal} på vindu {_pending_ver['id']}")
            except Exception as e:
                print(f"[LTM] close_verification feilet: {e}")

    # Vanlig feedback-sjekk (knyttet til siste action)
    feedback_signal = ltm.detect_feedback_signal(user_text, user_id)
    if feedback_signal:
        asyncio.create_task(ltm.update_feedback(ltm._last_id.get(user_id), feedback=feedback_signal))
        print(f"[LTM] Feedback: {feedback_signal} på {ltm._last_id.get(user_id)}")

    _ltm_id = await ltm.log_interaction(prompt=user_text, user_id=user_id, source=source)

    # Sjekk om vi har nok ubekreftede til å be om verifikasjon
    _ask_verification   = False
    _ver_from_id        = 0
    _ver_to_id          = 0
    _ver_count          = 0
    if not _pending_ver:   # ikke spør igjen hvis vi allerede venter på svar
        _ver_count, _ver_from_id, _ver_to_id = ltm.count_unverified_since_last(user_id=user_id)
        if _ver_count >= 15:
            _ask_verification = True
            print(f"[LTM] Verifikasjonstrigger: {_ver_count} ubekreftede")

    # =========================================================
    # CLOUD-TRIGGER: "bruk online ..." → hopp over tools
    # =========================================================
    import re
    user_text_lower = user_text.lower()
    if any(kw in user_text_lower for kw in _CLOUD_TRIGGERS) and api_ask_cloud:
        pattern = "|".join(re.escape(kw) for kw in _CLOUD_TRIGGERS)
        clean_prompt = re.sub(pattern, "", user_text, flags=re.IGNORECASE).strip(" ,.")
        if not clean_prompt:
            clean_prompt = user_text

        print(f"[ROUTER] cloud trigger → '{clean_prompt}'")
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
    # TOOL-LØKKE: Kåre bestemmer alt
    # =========================================================
    print("[ROUTER] tool-løkke start")

    stt_note = "\nOBS: Dette er transkribert tale (STT). Ta hensyn til mulige talefeil, dialekt og feilstavinger.\n" if source == "stt" else ""

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

    # Bygg statisk kontekst (state, actions, daglig sammendrag) — UTEN dialog.
    # Dialog injiseres nedenfor som ekte meldingsobjekter for å gi LLM riktig flerturs-struktur.
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

    # Notater som gjelder kun dette kallet (STT, verifikasjon, Miss Kåre, nettverk)
    current_content = "\n\n".join(
        p for p in [stt_note, ver_note, mk_note, network_note, user_text] if p
    ).strip()

    # Hent siste 4 (bruker, kåre)-par fra STM som ekte meldingsobjekter.
    # Dette gir LLM korrekt flerturs-struktur: Kåres spørsmål → brukerens svar er direkte koblet.
    recent_pairs = memory.get_dialog_pairs(user_id=user_id, n=4)

    if recent_pairs:
        # Første melding: statisk kontekst + LTM + eldste bruker-turn
        static_ctx = "\n\n".join(
            p for p in [context_block, ltm_block, recent_pairs[0][0]] if p
        ).strip()
        messages: list = [{"role": "user", "content": static_ctx}]
        messages.append({"role": "assistant", "content": recent_pairs[0][1]})
        for u_text, k_text in recent_pairs[1:]:
            messages.append({"role": "user",      "content": u_text})
            messages.append({"role": "assistant", "content": k_text})
        # Siste melding: nåværende forespørsel (images her, ikke på første)
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

    # Hent brukerens valgte personlighetsvariant (dict-lookup i adapter, ingen disk-I/O)
    _user_rec = _user_store.get_user(user_id) if user_id != USER_GLOBAL else None
    _personality = (_user_rec or {}).get("personality", "standard") or "standard"
    _user_role = (_user_rec or {}).get("role", "admin")
    _tools = _get_tools_for_role(_user_role)

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
        print(f"[ROUTER] tool-runde {round_num + 1}")

        try:
            result = await ask_llm_with_tools(
                messages=messages, tools=_tools, rid=rid, personality=_personality, user_id=user_id
            )
        except Exception as _llm_exc:
            print(f"[ROUTER] LLM-kall feilet (runde {round_num + 1}): {_llm_exc}")
            text_out = "Jeg fikk ikke kontakt med systemene akkurat nå."
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
            # Endelig svar — Kåre er ferdig
            text_out = result.get("text", "").strip() or "Jeg fikk ikke noe fornuftig svar."

            # Promise interceptor: if Kåre claimed to note/remember something but
            # never called the "notat" tool, force one correction round.
            if (
                not _promise_retry_done
                and round_num < _MAX_TOOL_ROUNDS - 1
                and "notat" not in used_tools
                and _PROMISE_RE.search(text_out)
            ):
                _promise_retry_done = True
                _original_text_out = text_out
                print("[ROUTER] løftebryter: Kåre lovet skriving uten tool-kall — tvinger retry")
                messages.append(result["message"])
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM: Du sa at du ville notere eller huske noe, men du kalte ikke "
                        "notat-verktøyet. Gjør det nå: kall notat(action='skriv', liste='kare') "
                        "med det du lovet å huske. Kun tool-kall — ingen forklarende tekst.]"
                    ),
                })
                continue

            print(f"[ROUTER] endelig svar etter {round_num + 1} runde(r)")
            break

        # Kåre vil kalle tools — legg assistentens melding inn i historikken
        messages.append(result["message"])

        # Execute all tool calls in this round concurrently
        async def _run_tc(tc):
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = dict(fn.get("arguments", {}))
            args["_user_id"] = user_id
            args["_rid"] = rid
            args["_block_ha_write"] = block_ha_write
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

            if fn_name == "styr_enhet":
                ok = "OK:" in tool_result
                ha_actions.append((fn_args.get("entity_id"), fn_args.get("action"), ok))

            if fn_name in ("kare_image", "se_bilder", "kamera"):
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
                "content": "[SYSTEM: Her er bildet du nettopp lastet med se_bilder. Beskriv det i svaret ditt.]",
                "images": _round_vision,
            })

        # Promise interceptor: after the forced tool round, break immediately.
        # Return the original text + a small tag — no new LLM round needed.
        if _promise_retry_done:
            _tags = []
            for _fn, _fa, _tr in call_results:
                if _fn == "notat":
                    _tags.append("_(notert ✓)_")
                else:
                    # Tool with real output — include truncated result
                    _tags.append(f"_({_tr[:120]})_")
            text_out = _original_text_out.rstrip()
            if _tags:
                text_out += "\n\n" + " · ".join(_tags)
            print("[ROUTER] løftebryter: returnerer original tekst + tag")
            break

    else:
        # Alle 6 runder brukt — ett siste kall uten tools, kun for å svare brukeren
        print("[ROUTER] runde 7 (output-only): tvinger svar til bruker")
        messages.append({
            "role": "user",
            "content": "[SYSTEM: Du har nå brukt alle 6 tool-runder. Svar brukeren direkte med det du har funnet — ingen flere tool-kall er mulig.]"
        })
        try:
            _final = await ask_llm_with_tools(
                messages=messages, tools=[], rid=rid, personality=_personality, user_id=user_id
            )
            text_out = (_final.get("text") or "").strip()
        except Exception as e:
            print(f"[ROUTER] output-runde feilet: {e}")
        text_out = text_out or "Kåre kom ikke frem til et svar innen rimelig tid."

    # Append any generated image URLs that the LLM forgot to include in its reply
    for _img_url in _generated_image_urls:
        if _img_url not in text_out:
            text_out = text_out.rstrip() + f"\n{_img_url}"

    # =========================================================
    # Oppdater korttidsminne og langtidsminne
    # =========================================================

    # Logg alle HA-handlinger til STM (ikke bare den siste)
    for entity, action, ok in ha_actions:
        if entity and action:
            memory.record_action(action=action, entity_id=entity, ok=ok, user_id=user_id)
            if ok:
                memory.set_state(key=entity, value=action, source="ha")

    # LTM bruker siste vellykkede handling
    successful = [(e, a) for e, a, ok in ha_actions if ok]
    final_entity = successful[-1][0] if successful else None
    final_action = successful[-1][1] if successful else None

    memory.add_dialog(role="assistant", text=text_out, user_id=user_id)

    # Lagre tool-sammendrag i STM så Kåre vet hva han selv gjorde sist
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

    # Åpne verifikasjonsvindu hvis vi spurte denne runden
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

