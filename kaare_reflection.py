#!/usr/bin/env python3
"""
Kåre reflection job — runs at 04:00 via systemd timer.

Purpose: Kåre, Miss Kåre and Meeting Leader reflect on THE USER — who they are,
         what they like, what they need, what might worry or delight them.
         Insights are saved to per-user profile (state/users/{user_id}/).

Opening sequence:
  1. Meeting leader opens and invites topic proposals
  2. Kåre proposes one topic about the user to explore
  3. Miss Kåre proposes one topic about the user to explore
  4. Meeting leader picks one topic from each → sets agenda

Structure:
  - Meeting leader (27B, GPU-proxy) controls flow and decides whether to continue.
  - Kåre (27B, GPU-proxy) and Miss Kåre (9B, 5060 Ti) are participants.
  - Groups of 3 local rounds → online check → meeting leader evaluation.
  - Max 6 rounds total (2 groups).

Output: /kaare/state/memory/reflections/YYYY-MM-DD.md
        /kaare/state/memory/reflection_latest.md
        /kaare/state/users/{user_id}/profile.yaml  (updated)
        /kaare/state/users/{user_id}/observations.md  (new entry)
"""

import asyncio
import json
import logging
import os
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _bootstrap_env(path: str) -> None:
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_bootstrap_env("/kaare/configs/kare_llm.env")

import httpx
import yaml

sys.path.insert(0, "/kaare")
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm
from kaare_core.model_lock import lock_11445
from kaare_core.users.profile_manager import (
    load_profile,
    add_observation,
    update_profile_field,
    get_recent_observations,
    write_vault_entry,
)
from kaare_core import session_keys as _session_keys
from kaare_core.tools.i18n import t, get_lang

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reflection")

# ── Configuration ─────────────────────────────────────────────────────────────
REFLECTIONS_BASE = Path("/kaare/state/memory/reflections")

USER_ID         = os.getenv("REFLECTION_USER_ID", "admin")   # set by runner or env
_active_user_id = USER_ID   # updated by main() — used by tool functions

from kaare_core.config import get_service as _svc

_proxy_base    = _llm("default")["base_url"]
_mk_base       = _llm("miss_kare")["base_url"]
_cloud_base    = _llm("cloud")["base_url"]
_KARE_PROVIDER = _llm("default").get("provider", "ollama")

_kare_chat_path = "/v1/chat/completions" if _KARE_PROVIDER == "vllm" else "/api/chat"
KARE_URL        = _proxy_base + _kare_chat_path
KARE_MODEL      = _cfg_model("kare")
LEDER_URL       = KARE_URL
LEDER_MODEL     = _cfg_model("kare")
MISS_KARE_URL   = _mk_base + "/api/chat"       # direct GPU → 5060 Ti (9b) — always Ollama
MISS_KARE_MODEL = _cfg_model("miss_kare")
CLOUD_URL       = _cloud_base + "/chat/completions"
CLOUD_MODEL     = _cfg_model("cloud")

KARE_API_URL    = _svc("internal", "kaare_api")

TIMEOUT_SECS         = 300
LEDER_TIMEOUT        = 120
LEDER_TOOL_ROUNDS    = 3
MISS_KARE_TIMEOUT    = int(_llm("miss_kare")["timeout"])
KARE_WINDOW          = 8
MISS_KARE_WINDOW     = 6

_SETTINGS_PATH_R     = Path("/kaare/configs/settings.yaml")
_LEDER_PRESET_DIR_R  = Path("/kaare/configs/meeting_leder")
_LEDER_CUSTOM_PATH_R = Path("/kaare/configs/meeting_leder/reflection_egendefinert.md")
_VALID_LEDER_PRESETS_R = ("standard", "analytisk", "utfordrende", "egendefinert")

# Sentinel prefix prepended to all _ask_leder() error returns.
# Callers use result.startswith(_LEDER_FAIL) — language-independent.
_LEDER_FAIL = "\x00leder_fail"


def _load_reflection_meeting_cfg() -> dict:
    try:
        return yaml.safe_load(_SETTINGS_PATH_R.read_text(encoding="utf-8")).get("kare_reflection", {})
    except Exception:
        return {}

def _ref_cfg() -> dict:
    return _load_reflection_meeting_cfg()

MAX_ROUNDS           = int(_ref_cfg().get("max_rounds", 6))
ROUNDS_PER_GROUP     = MAX_ROUNDS // 2
KARE_MAX_TOKENS      = int(_ref_cfg().get("kare_max_tokens", 1000))
MISS_KARE_MAX_TOKENS = int(_ref_cfg().get("miss_kare_max_tokens", 500))

# For Ollama: must match warmup's num_ctx or model reloads (~3.5 min)
# For vLLM: num_ctx is unused — use as fallback only
_KARE_NUM_CTX      = _llm("default")["options"].get("num_ctx", 8192)
_MISS_KARE_NUM_CTX = _llm("miss_kare")["options"]["num_ctx"]


# ── Helpers ───────────────────────────────────────────────────────────────────

_MK_AGENT_DIR = Path(__file__).parent / "kaare_core" / "agents" / "miss_kare"


def _get_miss_kare_meeting_role() -> str:
    try:
        data = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text(encoding="utf-8")) or {}
        return data.get("meeting_roles", {}).get("miss_kare", "empatisk")
    except Exception:
        return "empatisk"


def _load_miss_kare_meeting_pers() -> str:
    """Load Miss Kåre's personality for the reflection meeting based on configured role."""
    try:
        data = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text(encoding="utf-8")) or {}
        role = data.get("meeting_roles", {}).get("miss_kare", "empatisk")
    except Exception:
        role = "empatisk"
    log.info("[reflection] Miss Kåre role: %s", role)

    if role == "egendefinert":
        custom = Path("/kaare/configs/meeting_role_miss_kare_custom.md")
        if custom.exists():
            return custom.read_text(encoding="utf-8")
    elif role not in ("empatisk",):
        variant = _MK_AGENT_DIR / f"personlighet_{role}.md"
        if variant.exists():
            return variant.read_text(encoding="utf-8")

    base = _MK_AGENT_DIR / "personlighet.md"
    return base.read_text(encoding="utf-8") if base.exists() else "Du er Miss Kåre – varm, moderlig, jordnær."


def _load_miss_kare_portrait(user_id: str) -> str:
    """Load Miss Kåre's accumulated observations about this user (portrait file)."""
    path = Path(f"/kaare/state/users/{user_id}/miss_kare_portrait.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()[:3000]


def _load_user_knowledge(user_id: str) -> str:
    """Load concluded/settled knowledge about the user from user_knowledge.md."""
    path = Path(f"/kaare/state/users/{user_id}/user_knowledge.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _load(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _load_env(path: str) -> dict:
    result = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def _trim(messages: list[dict], window: int) -> list[dict]:
    """Keep system prompt + last `window` messages."""
    system = [m for m in messages if m.get("role") == "system"]
    rest   = [m for m in messages if m.get("role") != "system"]
    return system + rest[-window:]


KARE_CORE     = _load("/kaare/configs/personality_core.md")
KARE_BEHAVIOR = _load("/kaare/configs/personality_behavior.md")


# ── User context ──────────────────────────────────────────────────────────────
def _get_recent_interactions(n: int = 5, user_id: str | None = None) -> str:
    """Fetch the last N compressed episodes from LTM (SQLite)."""
    try:
        import sqlite3
        conn = sqlite3.connect("/kaare/state/memory/interactions.db")
        if user_id:
            cur = conn.execute(
                "SELECT narrative, topics FROM episodes WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, n),
            )
        else:
            cur = conn.execute(
                "SELECT narrative, topics FROM episodes ORDER BY id DESC LIMIT ?", (n,)
            )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return "No recorded interactions."
        lines = []
        for i, (narrative, topics) in enumerate(reversed(rows), 1):
            lines.append(f"Interaction {i} (topics: {topics or 'unknown'}):\n{narrative}")
        return "\n\n".join(lines)
    except Exception as e:
        log.warning("Could not fetch interactions: %s", e)
        return "Interactions unavailable."


def _get_stm_daily_summaries(days: int = 3) -> str:
    """Fetch the last N daily STM summaries — compressed snapshots of recent days."""
    try:
        from kaare_core.memory.long_term import load_recent_daily_summaries
        entries = load_recent_daily_summaries(days=days)
        if not entries:
            return ""
        lines = []
        for e in reversed(entries):
            lines.append(f"[{e['date']}] ({e['count']} interactions):\n{e['summary']}")
        return "\n\n".join(lines)
    except Exception as e:
        log.warning("Could not load STM daily summaries: %s", e)
        return ""


def _get_user_context(user_id: str) -> tuple[str, str, str, str]:
    """Returns (profile_text, observations_14d, recent_episodes, stm_daily_summaries)."""
    profile = load_profile(user_id)
    profile_text = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
    observations_text = get_recent_observations(user_id, days=14)
    interactions_text = _get_recent_interactions(n=5, user_id=user_id)
    stm_text = _get_stm_daily_summaries(days=3)
    return profile_text, observations_text, interactions_text, stm_text


# ── LLM calls (provider-agnostic for Kåre, direct Ollama for Miss Kåre) ──────
async def _ask_kare(
    messages: list[dict],
    temperature: float = 0.4,
    timeout: float = TIMEOUT_SECS,
    max_tokens: int = KARE_MAX_TOKENS,
) -> str:
    """Call the default role (Kåre). Provider (vLLM/Ollama) is read from llm.yaml."""
    from adapters.llm_adapter import call_llm_chat
    options = {"num_predict": max_tokens, "temperature": temperature, "num_ctx": _KARE_NUM_CTX}
    result = await call_llm_chat("default", messages, options=options, timeout=timeout)
    return result.get("text") or "[No response]"


async def _ask_ollama(
    url: str, model: str, messages: list[dict],
    temperature: float = 0.4, timeout: float = TIMEOUT_SECS,
    max_tokens: int = KARE_MAX_TOKENS, num_thread: int | None = None,
    num_ctx: int = _MISS_KARE_NUM_CTX,
) -> str:
    """Call Ollama /api/chat directly — only for Miss Kåre (always Ollama)."""
    options: dict = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": max_tokens}
    if num_thread is not None:
        options["num_thread"] = num_thread
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options,
        "think": False,
    }
    headers = {"x-kaare-source": "reflection"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "").strip()
            return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip() or "[No response]"
    except Exception as e:
        log.error("Ollama call failed (%s): %s", url, e)
        return f"[Unavailable: {e}]"


# ── Online LLM call ───────────────────────────────────────────────────────────
async def _ask_cloud(conversation: str, is_final: bool) -> str:
    env = _load_env("/kaare/configs/nvidia.env")
    api_key = env.get("NVIDIA_API_KEY", "")
    if not api_key:
        return t("meet_no_api_key", get_lang("global"))

    if is_final:
        instruction = (
            "Gi en avsluttende vurdering av hva møtet lærte om brukeren i dag. "
            "Oppsummer de viktigste innsiktene og gi ett konkret forslag til hvordan "
            "Kåre kan støtte brukeren bedre. Maks 5 setninger."
        )
    else:
        instruction = (
            "Gi ett kort innspill om brukeren – noe de kan ha oversett, "
            "et mønster eller en menneskelig vinkel de ikke har nevnt. "
            "Maks 3 setninger."
        )

    system = (
        "Du er en ekstern stemme i et internt møte mellom to AI-agenter: Kåre (hjemme-AI) "
        "og Miss Kåre (varm analytiker). Møtet handler om brukeren de betjener. "
        "Du har lest samtalen så langt. "
        f"{instruction} Vær direkte. Ikke presenter deg selv."
    )

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
            r = await client.post(
                CLOUD_URL,
                json={
                    "model": CLOUD_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"Møtet så langt:\n\n{conversation}\n\nDitt innspill:"},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 300,
                },
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip() or "[Cloud did not respond]"
    except Exception as e:
        log.error("Cloud call failed: %s", e)
        return f"[Cloud unavailable: {e}]"


# ── Meeting leader tools ──────────────────────────────────────────────────────
_LEDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_user_profile",
            "description": "Les nåværende brukerprofil – hva Kåre vet om brukeren",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hent_bruker_aktivitet",
            "description": "Hent oversikt over brukerens siste aktivitet og interaksjoner med Kåre",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nettsøk",
            "description": "Søk etter informasjon på nettet – f.eks. om brukerens interesser eller behov",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Søketermen"}
                },
                "required": ["query"],
            },
        },
    },
]


async def _execute_leder_tool(name: str, arguments: dict) -> str:
    try:
        if name in ("read_user_profile", "les_brukerprofil"):
            profile = load_profile(_active_user_id)
            return f"User profile:\n{yaml.dump(profile, allow_unicode=True, default_flow_style=False)}"

        elif name == "hent_bruker_aktivitet":
            interactions = _get_recent_interactions(n=10, user_id=_active_user_id)
            observations = get_recent_observations(_active_user_id, days=7)
            stm_summaries = _get_stm_daily_summaries(days=3)
            stm_section = f"\n\nDaily STM summaries (last 3 days):\n{stm_summaries}" if stm_summaries else ""
            return (
                f"Recent interactions (compressed episodes):\n{interactions}\n\n"
                f"Recent observations (7 days):\n{observations}"
                f"{stm_section}"
            )

        elif name == "nettsøk":
            query = arguments.get("query", "").strip()
            if not query:
                return t("meet_empty_search", get_lang("global"))
            from adapters.web_search_adapter import web_search
            return await web_search(query)

        return t("meet_unknown_tool", get_lang("global"), name=name)
    except Exception as e:
        return t("meet_tool_error", get_lang("global"), name=name, error=e)


async def _ask_leder(messages: list[dict], with_tools: bool = False) -> str:
    """Call meeting leader with optional tool access. Provider-agnostic via call_llm_chat.

    On failure returns _LEDER_FAIL + translated error string.
    Callers detect errors with result.startswith(_LEDER_FAIL).
    """
    from adapters.llm_adapter import call_llm_chat
    options = {"temperature": 0.3, "num_predict": KARE_MAX_TOKENS, "num_ctx": _KARE_NUM_CTX}
    tools = _LEDER_TOOLS if with_tools else None
    current_messages = list(messages)

    for tool_round in range(LEDER_TOOL_ROUNDS + 1):
        result = await call_llm_chat(
            "default", current_messages,
            tools=tools, options=options, timeout=LEDER_TIMEOUT,
            disable_thinking=True,
        )
        if not result.get("ok"):
            log.error("Meeting leader call failed: %s", result.get("error", "unknown"))
            return _LEDER_FAIL + t("meet_leader_unavailable", get_lang("global"), error=result.get("error", "ukjent feil"))

        tool_calls = result.get("tool_calls")

        if not tool_calls or not with_tools or tool_round >= LEDER_TOOL_ROUNDS:
            return result.get("text") or "[No response]"

        current_messages.append(
            result.get("message") or {"role": "assistant", "content": result.get("text", ""), "tool_calls": tool_calls}
        )
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}
            log.info("[meeting leader tool] %s(%s)", tool_name, args)
            tool_result = await _execute_leder_tool(tool_name, args)
            current_messages.append({"role": "tool", "content": tool_result, "name": tool_name})

    return "[No response]"


def _get_kare_language_r() -> str:
    try:
        data = yaml.safe_load(_SETTINGS_PATH_R.read_text(encoding="utf-8")) or {}
        return data.get("kare_language") or data.get("language", "nb")
    except Exception:
        return "nb"


_MK_ROLE_DESC: dict[str, dict[str, str]] = {
    "nb": {
        "empatisk":     "varm og empatisk",
        "analytiker":   "analytisk og strukturert",
        "utfordrende":  "kritisk og utfordrende",
        "egendefinert": "tilpasset rolle",
    },
    "en": {
        "empatisk":     "warm and empathetic",
        "analytiker":   "analytical and structured",
        "utfordrende":  "critical and challenging",
        "egendefinert": "custom role",
    },
    "de": {
        "empatisk":     "warm und einfühlsam",
        "analytiker":   "analytisch und strukturiert",
        "utfordrende":  "kritisch und herausfordernd",
        "egendefinert": "benutzerdefinierte Rolle",
    },
}


def _load_leder_reflection_preset(lang: str = "nb") -> str:
    cfg = _load_reflection_meeting_cfg()
    preset = cfg.get("leder_preset", "standard")
    if preset not in _VALID_LEDER_PRESETS_R:
        preset = "standard"
    suffix = "" if lang == "nb" else f"_{lang}"
    path = _LEDER_PRESET_DIR_R / f"reflection_{preset}{suffix}.md"
    fallback = _LEDER_PRESET_DIR_R / f"reflection_{preset}.md"
    try:
        return (path if path.exists() else fallback).read_text(encoding="utf-8").strip()
    except Exception:
        return (_LEDER_PRESET_DIR_R / "reflection_standard.md").read_text(encoding="utf-8").strip()


def _build_leder_system() -> str:
    hostname = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lang = _get_kare_language_r()
    mk_role = _get_miss_kare_meeting_role()
    mk_desc = _MK_ROLE_DESC.get(lang, _MK_ROLE_DESC["nb"]).get(mk_role, mk_role)
    preset_text = _load_leder_reflection_preset(lang)
    return preset_text.format(mk_desc=mk_desc, hostname=hostname, time=now)


# ── Opening sequence: topic proposals from participants ───────────────────────
async def _kare_temaforslag(kare_messages: list[dict], user_context: str) -> str:
    """Ask Kåre to propose one topic about the user to explore in the meeting."""
    kare_messages.append({"role": "user", "content": (
        "Møtet starter. Vi skal snakke om brukeren i dag – hvem de er, hva de liker, "
        "hva de trenger. Foreslå ÉTT konkret tema om brukeren du ønsker å utforske. "
        "Vær spesifikk. Maks 2 setninger."
    )})
    svar = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
    kare_messages.append({"role": "assistant", "content": svar})
    return svar


async def _miss_kare_temaforslag(miss_kare_messages: list[dict], kare_tema: str) -> str:
    """Ask Miss Kåre to propose one topic about the user to explore."""
    miss_kare_messages.append({"role": "user", "content": (
        f"Kåre ønsker å utforske dette i møtet: «{kare_tema}»\n\n"
        "Foreslå DU ÉTT annet konkret tema om brukeren – noe du synes er viktig å belyse. "
        "Det kan gjerne utfylle Kåres tema. Maks 2 setninger."
    )})
    async with lock_11445("miss_kare_refleksjon"):
        svar = await _ask_ollama(
            MISS_KARE_URL, MISS_KARE_MODEL,
            _trim(miss_kare_messages, MISS_KARE_WINDOW),
            temperature=0.5, timeout=MISS_KARE_TIMEOUT,
            max_tokens=MISS_KARE_MAX_TOKENS, num_thread=12, num_ctx=_MISS_KARE_NUM_CTX,
        )
    svar = re.sub(r"^Miss\s+Kåre\s+sa:\s*", "", svar, flags=re.IGNORECASE).strip()
    miss_kare_messages.append({"role": "assistant", "content": svar})
    return svar


async def _leder_presenter_admin_input(admin_input: str) -> str:
    """Meeting leader presents admin input as the first item after the opening sequence."""
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Admin har sendt inn dette innspillet til møtet:\n\"{admin_input}\"\n\n"
            "Introduser dette innspillet for Kåre og Miss Kåre. "
            "Koble det til hvem brukeren er hvis relevant. "
            "Be Kåre starte med sin refleksjon. Maks 3 setninger."
        )},
    ]
    result = await _ask_leder(messages, with_tools=False)
    if result.startswith(_LEDER_FAIL):
        return f"Admin har sendt inn et tema: «{admin_input}» – la oss se på dette."
    return result


async def _leder_sett_agenda(kare_tema: str, miss_kare_tema: str, user_context: str) -> str:
    """Meeting leader picks one topic from each participant and sets the agenda."""
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Kåre vil utforske: «{kare_tema}»\n"
            f"Miss Kåre vil utforske: «{miss_kare_tema}»\n\n"
            "Bruk gjerne verktøyene for å lese brukerprofil eller hente aktivitetsdata. "
            "Ta deretter med ÉTT tema fra Kåre og ÉTT tema fra Miss Kåre inn i agendaen. "
            "Sett en kort, konkret agenda for gruppe 1 (2-4 setninger). "
            "Vær tydelig på hva vi vil finne ut om brukeren i dag."
        )},
    ]
    result = await _ask_leder(messages, with_tools=True)
    if result.startswith(_LEDER_FAIL):
        return t("meet_leader_unavailable", get_lang("global"), error="bruker foreslåtte temaer direkte") + f"\nKåre: {kare_tema}\nMiss Kåre: {miss_kare_tema}"
    return result


async def _leder_intro_gruppe2(prev_summary: str) -> str:
    """Meeting leader sets agenda for group 2."""
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Gruppe 2 starter. Oppsummering fra gruppe 1:\n\n{prev_summary}\n\n"
            "Gi en kort intro (2-4 setninger) med ny fokusert agenda. "
            "Gå dypere – ta tak i noe som ikke ble fullt utforsket i gruppe 1."
        )},
    ]
    result = await _ask_leder(messages, with_tools=False)
    if result.startswith(_LEDER_FAIL):
        return t("meet_leader_unavailable", get_lang("global"), error="gruppe 2 starter uten intro")
    return result


async def _leder_runde_sjekk(conversation_tail: str, global_round: int) -> tuple[str, str]:
    """Brief check after a round. Returns (action, value)."""
    messages = [
        {
            "role": "system",
            "content": (
                "Du er møteleder i et møte om brukeren. Avgjør hva som skjer etter denne runden.\n"
                "Svar med NØYAKTIG én linje – ingen forklaring:\n"
                "  FORTSETT\n"
                "  HENT_AKTIVITET\n"
                "  NETTSØK: <søketerm>\n"
                "  INNSPILL: <maks 2 setninger>\n"
                "Svar på norsk."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Runde {global_round} er ferdig. Siste del av samtalen:\n\n"
                f"{conversation_tail}\n\nHva gjør du?"
            ),
        },
    ]
    svar = await _ask_leder(messages, with_tools=False)
    svar = svar.strip()
    log.info("[meeting leader round %d] %s", global_round, svar[:100])

    if svar.upper().startswith("HENT_AKTIVITET"):
        return "hent_aktivitet", ""
    m = re.match(r"^NETTSØK[:\s]+(.+)", svar, re.IGNORECASE)
    if m:
        return "nettsøk", m.group(1).strip()
    m = re.match(r"^INNSPILL[:\s]+(.+)", svar, re.IGNORECASE | re.DOTALL)
    if m:
        return "innspill", m.group(1).strip()
    return "fortsett", ""


async def _handle_leder_action(
    action: str, value: str,
    kare_messages: list, miss_kare_messages: list,
    exchanges: list,
) -> None:
    """Execute meeting leader's requested action and inject result into the conversation."""
    if action == "fortsett":
        return

    if action == "hent_aktivitet":
        exchanges.append(("Møteleder", "La oss se på brukerens siste aktivitet."))
        result = await _execute_leder_tool("hent_bruker_aktivitet", {})
        exchanges.append(("Kåre [brukeraktivitet]", result))
        inject = f"Møteleder hentet brukeraktivitet:\n{result}"
        log.info("[user activity fetched] %s…", result[:80])

    elif action == "nettsøk":
        exchanges.append(("Møteleder", f"La meg søke etter: {value}"))
        result = await _execute_leder_tool("nettsøk", {"query": value})
        exchanges.append(("Kåre [nettsøk]", result))
        inject = f"Møteleder søkte etter «{value}». Resultat:\n{result}"
        log.info("[web search '%s'] %s…", value, result[:80])

    elif action == "innspill":
        exchanges.append(("Møteleder", value))
        inject = f"Møteleder: {value}"
        log.info("[meeting leader input] %s", value[:80])

    else:
        return

    kare_messages.append({"role": "user", "content": inject})
    miss_kare_messages.append({"role": "user", "content": inject})


async def _leder_vurder(conversation: str, group: int, max_groups: int) -> tuple[bool, str]:
    """Meeting leader evaluates whether to continue. Returns (should_continue, reason)."""
    if group >= max_groups:
        return False, t("meet_max_groups", get_lang("global"))

    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Gruppe {group} av {max_groups} er ferdig.\n\n"
            f"Samtalen:\n{conversation[-2500:]}\n\n"
            "Svar KUN med én linje: 'FORTSETT: <begrunnelse>' eller 'AVSLUTT: <begrunnelse>'."
        )},
    ]
    svar = await _ask_leder(messages, with_tools=False)
    log.info("[meeting leader evaluation] %s", svar[:100])

    if svar.startswith(_LEDER_FAIL):
        log.warning("Meeting leader unavailable — continuing automatically")
        return True, t("meet_leader_unavailable", get_lang("global"), error=f"fortsetter til gruppe {group + 1}")

    fortsett = svar.upper().startswith("FORTSETT")
    return fortsett, svar


# ── Save meeting insights to user profile ─────────────────────────────────────

def _user_is_online(user_id: str) -> bool:
    """True if the user has an active session key in RAM."""
    return _session_keys.get_session_key_sync(user_id) is not None


def _vault_or_write_observation(user_id: str, text: str) -> None:
    """Write observation to observations.md, or vault it if user is offline."""
    if _user_is_online(user_id):
        add_observation(user_id, text)
    elif not write_vault_entry(user_id, {"type": "observation", "text": text}):
        add_observation(user_id, text)  # fallback: plaintext if no public key yet


def _vault_or_update_profile(user_id: str, field: str, value: Any, reason: str) -> None:
    """Update profile field, or vault it if user is offline."""
    if _user_is_online(user_id):
        update_profile_field(user_id, field, value, reason)
    elif not write_vault_entry(user_id, {"type": "profile_field", "field": field, "value": value, "reason": reason}):
        update_profile_field(user_id, field, value, reason)  # fallback


def _save_meeting_insights(user_id: str, kare_closing: str) -> None:
    """Parse Kåre's closing statement and save insights to user profile."""
    try:
        obs_match      = re.search(r"OBSERVASJON:\s*(.+?)(?=LIKER:|BEKYMRING:|GLEDE:|$)",      kare_closing, re.DOTALL | re.IGNORECASE)
        liker_match    = re.search(r"LIKER:\s*(.+?)(?=OBSERVASJON:|BEKYMRING:|GLEDE:|$)",       kare_closing, re.DOTALL | re.IGNORECASE)
        bekymring_match = re.search(r"BEKYMRING:\s*(.+?)(?=OBSERVASJON:|LIKER:|GLEDE:|$)",     kare_closing, re.DOTALL | re.IGNORECASE)
        glede_match    = re.search(r"GLEDE:\s*(.+?)(?=OBSERVASJON:|LIKER:|BEKYMRING:|$)",      kare_closing, re.DOTALL | re.IGNORECASE)

        date_str = datetime.now().strftime("%Y-%m-%d")
        obs_lines = []

        if obs_match:
            obs_lines.append(obs_match.group(1).strip())

        if liker_match:
            liker_text = liker_match.group(1).strip()
            if liker_text.lower() not in ("ingenting nytt", "ingenting", "ingen"):
                obs_lines.append(f"Likes: {liker_text}")
                profile = load_profile(user_id)
                prefs = profile.get("preferences", {})
                prefs[f"observed_{date_str}"] = liker_text
                _vault_or_update_profile(user_id, "preferences", prefs, "From reflection meeting")

        if bekymring_match:
            bekymring_text = bekymring_match.group(1).strip()
            if bekymring_text.lower() not in ("ingen", "ingenting"):
                obs_lines.append(f"Concern: {bekymring_text}")
                profile = load_profile(user_id)
                concerns = profile.get("concerns", [])
                concerns.append({"date": date_str, "text": bekymring_text})
                _vault_or_update_profile(user_id, "concerns", concerns[-10:], "From reflection meeting")

        if glede_match:
            glede_text = glede_match.group(1).strip()
            if glede_text.lower() not in ("ingen", "ingenting"):
                obs_lines.append(f"Delight: {glede_text}")
                profile = load_profile(user_id)
                delights = profile.get("delights", [])
                delights.append({"date": date_str, "text": glede_text})
                _vault_or_update_profile(user_id, "delights", delights[-20:], "From reflection meeting")

        observation_text = "\n".join(obs_lines) if obs_lines else f"Meeting held.\n{kare_closing[:300]}"
        _vault_or_write_observation(user_id, observation_text)
        log.info("Meeting insights saved to profile for %s", user_id)

    except Exception as e:
        log.error("Failed to save meeting insights: %s", e)


async def _update_user_knowledge(
    user_id: str,
    exchanges: list[tuple[str, str]],
    existing: str,
) -> None:
    """Distil concluded knowledge from the meeting and write user_knowledge.md."""
    transcript = "\n".join(f"{who}: {text[:300]}" for who, text in exchanges[-20:])
    existing_block = f"Eksisterende avklart kunnskap:\n{existing}\n\n" if existing else ""
    prompt = (
        f"{existing_block}"
        "Her er utdrag fra refleksjonsmøtet om brukeren:\n"
        f"{transcript}\n\n"
        "Oppdater user_knowledge.md basert på møtets konklusjoner. "
        "Behold eksisterende punkter som fortsatt er gyldige. Fjern det som er utdatert. "
        "Skriv KUN på formen:\n"
        "- Brukeren liker/foretrekker/reagerer/...\n\n"
        "Maks 10 punkter. Kun punkter som representerer konkludert, stabil kunnskap."
    )
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": prompt},
    ]
    try:
        result = await _ask_kare(messages)
        if result and result.strip() and result.strip() != "[No response]":
            path = Path(f"/kaare/state/users/{user_id}/user_knowledge.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            content = f"# Konkludert kunnskap om brukeren\n_Sist oppdatert: {date_str}_\n\n{result.strip()}\n"
            path.write_text(content, encoding="utf-8")
            log.info("user_knowledge.md updated for %s", user_id)
    except Exception as e:
        log.error("Failed to update user_knowledge for %s: %s", user_id, e)


# ── Write reflection file (atomic) ────────────────────────────────────────────
def _write_reflection(user_id: str, date_str: str, exchanges: list[tuple[str, str]]) -> Path:
    out_dir = REFLECTIONS_BASE / user_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}.md"

    participants = sorted(set(agent for agent, _ in exchanges))
    lines = [
        f"# Reflection – {date_str}",
        "",
        "## Participants",
        *[f"- {p}" for p in participants],
        "",
        "## Conversation",
        "",
    ]
    for agent, text in exchanges:
        lines.append(f"**[{agent}]**")
        lines.append(text)
        lines.append("")

    content = "\n".join(lines)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out_path)
    (out_dir / "latest.md").write_text(content, encoding="utf-8")
    log.info("Reflection written: %s", out_path)
    return out_path


# ── Main flow ──────────────────────────────────────────────────────────────────
async def main(user_id: str | None = None) -> None:
    global _active_user_id
    from adapters.llm_adapter import _current_rid as _rid_ctx_refl, _current_source as _src_ctx_refl
    _refl_rid    = f"rid-refl-{int(time.time()*1000)}"
    _refl_token  = _rid_ctx_refl.set(_refl_rid)
    _src_token   = _src_ctx_refl.set("refl")
    user_id         = user_id or USER_ID
    _active_user_id = user_id
    date_str        = datetime.now().strftime("%Y-%m-%d")
    exchanges: list[tuple[str, str]] = []
    max_groups = MAX_ROUNDS // ROUNDS_PER_GROUP

    log.info("=== Reflection meeting starting — %s (user: %s) ===", date_str, user_id)
    log.info("Max %d rounds, %d groups, meeting leader + Kåre share %s on GPU", MAX_ROUNDS, max_groups, LEDER_MODEL)

    # Read admin topic (clear after use — only first user gets it)
    _topics_file = Path("/kaare/state/meeting_topics.json")
    _admin_topic = ""
    try:
        import json as _json
        _topics = _json.loads(_topics_file.read_text(encoding="utf-8"))
        _admin_topic = _topics.get("reflection", "").strip()
        if _admin_topic:
            _topics["reflection"] = ""
            _topics_file.write_text(_json.dumps(_topics, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Admin topic read and cleared: %s", _admin_topic[:80])
    except Exception:
        pass

    profile_text, observations_text, interactions_text, stm_text = _get_user_context(user_id)
    portrait = _load_miss_kare_portrait(user_id)
    user_knowledge = _load_user_knowledge(user_id)

    _stm_section = f"\n\nDaily STM summaries (last 3 days):\n{stm_text}" if stm_text else ""
    _knowledge_section = (
        f"\n\nSettled knowledge (do NOT re-derive in this meeting — focus on new):\n{user_knowledge}"
        if user_knowledge else ""
    )
    user_context_summary = (
        f"User profile:\n{profile_text}\n\n"
        f"Recent observations (14 days):\n{observations_text}\n\n"
        f"Recent interactions (compressed episodes):\n{interactions_text}"
        f"{_stm_section}"
        f"{_knowledge_section}"
    )

    _topic_line = f"\nADMIN HAR FORESLÅTT TEMA: {_admin_topic}\nDette skal prioriteres i møtet.\n" if _admin_topic else ""

    total_info = (
        f"Dette møtet har maks {MAX_ROUNDS} lokale runder ({max_groups} grupper à {ROUNDS_PER_GROUP}). "
        f"Møteleder introduserer hver gruppe og beslutter om vi fortsetter.{_topic_line}"
    )

    kare_system = (
        f"{KARE_CORE}\n\n{KARE_BEHAVIOR}\n\n"
        "--- REFLEKSJONSMØTE OM BRUKEREN ---\n"
        f"{total_info}\n\n"
        "VIKTIG: Svar alltid på norsk, uansett hva andre sier.\n\n"
        "Du er i et internt møte med Miss Kåre. Møtet handler om brukeren du betjener. "
        "Diskuter hvem brukeren er, hva de liker, hva de trenger, hva som kan bekymre og glede dem. "
        "Vær konkret og ærlig – bruk det du faktisk har observert.\n\n"
        f"{user_context_summary}"
    )

    _miss_kare_pers = _load_miss_kare_meeting_pers()
    miss_kare_system = (
        f"{_miss_kare_pers}\n\n"
        "---\n\n"
        "VIKTIG: Svar alltid på norsk.\n\n"
        "Du er med i et internt refleksjonsmøte om brukeren. "
        "Din oppgave er å bidra med empati og menneskelig innsikt – se brukeren som et helt menneske. "
        "Del dine observasjoner, legg merke til mønstre, og bidra med omsorgsfull analyse.\n\n"
        "Regler:\n"
        "- Maks 4 setninger per innlegg\n"
        "- Vær konkret og ærlig – ikke bare bekreftende\n"
        "- Knytt observasjoner til det du faktisk vet om brukeren\n"
        "- Naturlig, varm tale – ingen lister eller agendanummer\n\n"
        f"Brukerprofil:\n{profile_text}\n\n"
        f"Siste observasjoner:\n{observations_text}"
        + (f"\n\nDaglige STM-sammendrag (siste 3 dager):\n{stm_text}" if stm_text else "")
        + (f"\n\n## Dine egne observasjoner om brukeren\n{portrait}" if portrait else "")
    )

    kare_messages      = [{"role": "system", "content": kare_system}]
    miss_kare_messages = [{"role": "system", "content": miss_kare_system}]

    # ── Opening sequence: topic proposals ────────────────────────────────────
    log.info("=== Opening sequence — topic proposals ===")

    kare_tema = await _kare_temaforslag(kare_messages, user_context_summary)
    exchanges.append(("Kåre", kare_tema))
    log.info("[Kåre topic] %s", kare_tema[:120])

    miss_kare_tema = await _miss_kare_temaforslag(miss_kare_messages, kare_tema)
    exchanges.append(("Miss Kåre", miss_kare_tema))
    log.info("[Miss Kåre topic] %s", miss_kare_tema[:120])

    agenda = await _leder_sett_agenda(kare_tema, miss_kare_tema, user_context_summary)
    exchanges.append(("Møteleder", agenda))
    log.info("[meeting leader agenda] %s", agenda[:120])

    agenda_msg = f"Møteleder setter agenda: {agenda}"
    kare_messages.append({"role": "user", "content": agenda_msg})
    miss_kare_messages.append({"role": "user", "content": agenda_msg})

    # ── Admin input round (one extra round if admin submitted a topic) ────────
    if _admin_topic:
        log.info("=== Admin input round ===")
        admin_intro = await _leder_presenter_admin_input(_admin_topic)
        exchanges.append(("Møteleder", admin_intro))
        log.info("[meeting leader admin intro] %s", admin_intro[:120])

        admin_msg = f"Møteleder sier: {admin_intro}"
        kare_messages.append({"role": "user", "content": admin_msg})
        miss_kare_messages.append({"role": "user", "content": admin_msg})

        kare_messages.append({"role": "user", "content": "Din tur – reflekter over admin sitt innspill."})
        kare_reply = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
        kare_messages.append({"role": "assistant", "content": kare_reply})
        exchanges.append(("Kåre", kare_reply))
        log.info("[Kåre admin round] %s", kare_reply[:120])

        miss_kare_messages.append({"role": "user", "content": f"Kåre sa:\n{kare_reply}\n\nDin tur – hva tenker du om admin sitt innspill?"})
        async with lock_11445("miss_kare_refleksjon"):
            miss_reply = await _ask_ollama(
                MISS_KARE_URL, MISS_KARE_MODEL,
                _trim(miss_kare_messages, MISS_KARE_WINDOW),
                temperature=0.5, timeout=MISS_KARE_TIMEOUT,
                max_tokens=MISS_KARE_MAX_TOKENS, num_thread=12, num_ctx=_MISS_KARE_NUM_CTX,
            )
        miss_reply = re.sub(r"^Miss\s+Kåre\s+sa:\s*", "", miss_reply, flags=re.IGNORECASE).strip()
        miss_kare_messages.append({"role": "assistant", "content": miss_reply})
        kare_messages.append({"role": "user", "content": f"Miss Kåre sier: {miss_reply}"})
        exchanges.append(("Miss Kåre", miss_reply))
        log.info("[Miss Kåre admin round] %s", miss_reply[:120])

    # ── Group rounds ──────────────────────────────────────────────────────────
    global_round = 0
    prev_summary = ""

    for group in range(1, max_groups + 1):
        log.info("=== Group %d of %d ===", group, max_groups)

        if group > 1:
            intro = await _leder_intro_gruppe2(prev_summary)
            exchanges.append(("Møteleder", intro))
            log.info("[meeting leader group 2] %s", intro[:120])
            kare_messages.append({"role": "user", "content": f"Møteleder sier: {intro}"})
            miss_kare_messages.append({"role": "user", "content": f"Møteleder sier: {intro}"})

        for local_round in range(ROUNDS_PER_GROUP):
            global_round += 1
            log.info("--- Round %d/%d (group %d, local %d) ---",
                     global_round, MAX_ROUNDS, group, local_round + 1)

            kare_prompt = "Din tur."
            kare_messages.append({"role": "user", "content": kare_prompt})
            kare_reply = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
            kare_messages.append({"role": "assistant", "content": kare_reply})
            exchanges.append(("Kåre", kare_reply))
            log.info("[Kåre] %s", kare_reply[:120])

            miss_kare_messages.append({"role": "user", "content": f"Kåre sa:\n{kare_reply}\n\nDin tur."})
            async with lock_11445("miss_kare_refleksjon"):
                miss_kare_reply = await _ask_ollama(
                    MISS_KARE_URL, MISS_KARE_MODEL,
                    _trim(miss_kare_messages, MISS_KARE_WINDOW),
                    temperature=0.5, timeout=MISS_KARE_TIMEOUT,
                    max_tokens=MISS_KARE_MAX_TOKENS, num_thread=12, num_ctx=_MISS_KARE_NUM_CTX,
                )
            miss_kare_reply = re.sub(r"^Miss\s+Kåre\s+sa:\s*", "", miss_kare_reply, flags=re.IGNORECASE).strip()
            miss_kare_messages.append({"role": "assistant", "content": miss_kare_reply})
            kare_messages.append({"role": "user", "content": f"Miss Kåre sier: {miss_kare_reply}"})
            exchanges.append(("Miss Kåre", miss_kare_reply))
            log.info("[Miss Kåre] %s", miss_kare_reply[:120])

            if local_round < ROUNDS_PER_GROUP - 1:
                conv_tail = "\n\n".join(f"{a}: {t}" for a, t in exchanges[-6:])
                action, value = await _leder_runde_sjekk(conv_tail, global_round)
                await _handle_leder_action(action, value, kare_messages, miss_kare_messages, exchanges)

        is_final_group = group == max_groups
        log.info("--- Online (group %d) ---", group)
        conversation_text = "\n\n".join(f"{a}: {t}" for a, t in exchanges)
        cloud_reply = await _ask_cloud(conversation_text, is_final=is_final_group)
        exchanges.append(("Online", cloud_reply))
        log.info("[Online] %s", cloud_reply[:120])

        online_msg = f"Online sier: {cloud_reply}"
        kare_messages.append({"role": "user", "content": online_msg})
        miss_kare_messages.append({"role": "user", "content": online_msg})

        fortsett, begrunnelse = await _leder_vurder(conversation_text, group, max_groups)
        exchanges.append(("Møteleder", begrunnelse))
        prev_summary = begrunnelse

        if not fortsett:
            log.info("Meeting leader ends after group %d: %s", group, begrunnelse)
            break

    # ── Free closing rounds (max 4) ───────────────────────────────────────────
    log.info("--- Free closing rounds ---")
    CLOSING_PROMPT = (
        "Møtet nærmer seg slutten. Har du noe mer å si om brukeren – "
        "noe du ikke fikk sagt, eller en tanke du synes er viktig? "
        "Hvis du ikke har noe relevant å tilføye, svar KUN med ordet INGENTING."
    )
    for closing_round in range(1, 5):
        log.info("--- Closing round %d/4 ---", closing_round)
        noen_svarte = False

        kare_messages.append({"role": "user", "content": CLOSING_PROMPT})
        kare_free = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
        kare_messages.append({"role": "assistant", "content": kare_free})
        if kare_free.strip().upper() != "INGENTING":
            exchanges.append(("Kåre", kare_free))
            miss_kare_messages.append({"role": "user", "content": f"Kåre sier: {kare_free}\n\n{CLOSING_PROMPT}"})
            log.info("[Kåre free] %s", kare_free[:120])
            noen_svarte = True
        else:
            miss_kare_messages.append({"role": "user", "content": CLOSING_PROMPT})
            log.info("[Kåre free] INGENTING")

        async with lock_11445("miss_kare_refleksjon"):
            miss_kare_free = await _ask_ollama(
                MISS_KARE_URL, MISS_KARE_MODEL,
                _trim(miss_kare_messages, MISS_KARE_WINDOW),
                temperature=0.5, timeout=MISS_KARE_TIMEOUT,
                max_tokens=MISS_KARE_MAX_TOKENS, num_thread=12, num_ctx=_MISS_KARE_NUM_CTX,
            )
        miss_kare_free = re.sub(r"^Miss\s+Kåre\s+sa:\s*", "", miss_kare_free, flags=re.IGNORECASE).strip()
        miss_kare_messages.append({"role": "assistant", "content": miss_kare_free})
        if miss_kare_free.strip().upper() != "INGENTING":
            exchanges.append(("Miss Kåre", miss_kare_free))
            kare_messages.append({"role": "user", "content": f"Miss Kåre sier: {miss_kare_free}"})
            log.info("[Miss Kåre free] %s", miss_kare_free[:120])
            noen_svarte = True
        else:
            log.info("[Miss Kåre free] INGENTING")

        if not noen_svarte:
            log.info("No one had more to say — ending free rounds after round %d", closing_round)
            break

    # ── Kåre's closing — saved to user profile ────────────────────────────────
    log.info("--- Kåre closing statement ---")
    kare_messages.append({"role": "user", "content": (
        "Møtet er over. Skriv en strukturert avslutning med disse taggene:\n"
        "OBSERVASJON: <1-3 setninger om hva dere lærte om brukeren i dag>\n"
        "LIKER: <noe spesifikt brukeren liker, eller 'ingenting nytt'>\n"
        "BEKYMRING: <noe å følge med på, eller 'ingen'>\n"
        "GLEDE: <noe konkret som kan glede brukeren, eller 'ingen'>"
    )})
    kare_closing = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
    exchanges.append(("Kåre", kare_closing))
    log.info("[Kåre closing] %s", kare_closing[:120])

    _write_reflection(user_id, date_str, exchanges)
    _save_meeting_insights(user_id, kare_closing)
    await _update_user_knowledge(user_id, exchanges, user_knowledge)
    log.info("=== Reflection meeting done — %d local rounds ===", global_round)
    _rid_ctx_refl.reset(_refl_token)
    _src_ctx_refl.reset(_src_token)


if __name__ == "__main__":
    _lock = Path("/kaare/state/meeting_active.lock")
    _lock.touch()
    try:
        asyncio.run(main())
    finally:
        _lock.unlink(missing_ok=True)
