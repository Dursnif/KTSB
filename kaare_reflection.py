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
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
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
from kaare_core.think_strip import strip_think_block
from kaare_core.users.profile_manager import (
    load_profile,
    add_observation,
    get_recent_observations,
    write_vault_entry,
)
from kaare_core import session_keys as _session_keys
from kaare_core.config import get_service as _svc
from kaare_core.memory.long_term import load_recent_daily_summaries, _dec
from kaare_core.agents.miss_kare.evaluator import extract_facts_from_meeting
from kaare_core.tools.household_state import read_household_state
from kaare_core.tools.i18n import t, get_lang
from kaare_core.users import store as _store
from adapters.llm_adapter import (
    call_llm_chat,
    _build_role_age_block,
    _current_rid as _rid_ctx_refl,
    _current_source as _src_ctx_refl,
)
from adapters.web_search_adapter import web_search

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

_proxy_base    = _llm("default")["base_url"]
_mk_base       = _llm("miss_kare")["base_url"]
_KARE_PROVIDER = _llm("default").get("provider", "ollama")

_kare_chat_path = "/v1/chat/completions" if _KARE_PROVIDER == "vllm" else "/api/chat"
KARE_URL        = _proxy_base + _kare_chat_path
KARE_MODEL      = _cfg_model("kare")
LEDER_URL       = KARE_URL
LEDER_MODEL     = _cfg_model("kare")
MISS_KARE_URL   = _mk_base + "/api/chat"       # direct GPU → 5060 Ti (9b) — always Ollama
MISS_KARE_MODEL = _cfg_model("miss_kare")

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
    from kaare_core.crypto import read_user_text_file
    path = Path(f"/kaare/state/users/{user_id}/miss_kare_portrait.md")
    return read_user_text_file(path, user_id, allow_system_key=True).strip()[:3000]


def _load_user_knowledge(user_id: str) -> str:
    """Load concluded/settled knowledge about the user from user_knowledge.md."""
    path = Path(f"/kaare/state/users/{user_id}/user_knowledge.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _get_admin_comment_r() -> str:
    """Return and clear the most recent admin comment for the reflection meeting, if any.

    Admin comments live at state/meeting_comments/reflection/YYYY-MM-DD.txt
    (no username subdirectory — admin-scoped).
    User comments (per-user) are in state/meeting_comments/reflection/{username}/YYYY-MM-DD.txt.

    A comment is meant to be surfaced exactly once (the next meeting after it was
    written) — clearing the file after read prevents it from being re-injected into
    every meeting indefinitely.
    """
    comments_dir = Path("/kaare/state/meeting_comments/reflection")
    try:
        files = sorted(
            [f for f in comments_dir.glob("*.txt") if f.is_file()],
            reverse=True,
        )
        for f in files:
            text = f.read_text(encoding="utf-8").strip()
            if text:
                f.write_text("", encoding="utf-8")
                return text
    except Exception:
        pass
    return ""


def _get_user_role_r(user_id: str) -> str:
    """Fetch user role from users.db for reflection meeting context."""
    try:
        rec = _store.get_user(user_id)
        return (rec or {}).get("role", "adult")
    except Exception:
        return "adult"


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
    """Fetch the last N compressed episodes from LTM (SQLite).

    narrative may be ENC:-prefixed ciphertext (episodes are unencrypted today by omission —
    see P87 Phase 6 — but this call already passes allow_system_key=True so it keeps working
    once that lands). The reflection meeting runs unattended at night, so _dec() uses the
    system key rather than a live session key; if that ever fails to resolve (e.g. a user who
    has never logged in even once), it falls back to "[kryptert]" rather than handing
    ciphertext to the LLM. Episodes that decrypt to that placeholder carry zero signal and are
    dropped rather than shown as a fake "interaction."
    """
    try:
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
        dropped = 0
        for narrative, topics in reversed(rows):
            narrative = _dec(user_id or "global", narrative, allow_system_key=True)
            if narrative == "[kryptert]":
                dropped += 1
                continue
            lines.append(f"Interaction {len(lines) + 1} (topics: {topics or 'unknown'}):\n{narrative}")
        if dropped:
            log.info("Dropped %d encrypted episode(s) for %s (no session key).", dropped, user_id or "global")
        return "\n\n".join(lines) if lines else "No recorded interactions."
    except Exception as e:
        log.warning("Could not fetch interactions: %s", e)
        return "Interactions unavailable."


def _get_stm_daily_summaries(days: int = 3) -> str:
    """Fetch the last N daily STM summaries — compressed snapshots of recent days."""
    try:
        entries = load_recent_daily_summaries(days=days, allow_system_key=True)
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
    # allow_system_key=True (P87): without it, load_profile() silently drops the entire
    # private_encrypted section for every offline user — which is the normal case at
    # 04:00 — so the meeting only ever saw household_visible fields.
    profile = load_profile(user_id, allow_system_key=True)
    profile_text = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
    observations_text = get_recent_observations(user_id, days=14, allow_system_key=True)
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
            raw_content = r.json().get("message", {}).get("content", "").strip()
            content, think_content, think_closed = strip_think_block(raw_content)
            if think_content:
                try:
                    from kaare_core.tools.think_cache import log_think
                    log_think(think_text=think_content, response=content, role="miss_kare", model=model, recovered=False)
                except Exception:
                    pass
            return content or "[No response]"
    except Exception as e:
        log.error("Ollama call failed (%s): %s", url, e)
        return f"[Unavailable: {e}]"


# ── Meeting leader tools ──────────────────────────────────────────────────────
_LEDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_user_profile",
            "description": "Read the current user profile – what Kåre knows about the user",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hent_bruker_aktivitet",
            "description": "Fetch an overview of the user's recent activity and interactions with Kåre",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nettsøk",
            "description": "Search for information on the web – e.g. about the user's interests or needs",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search term"}
                },
                "required": ["query"],
            },
        },
    },
]


async def _execute_leder_tool(name: str, arguments: dict) -> str:
    try:
        if name in ("read_user_profile", "les_brukerprofil"):
            profile = load_profile(_active_user_id, allow_system_key=True)
            return f"User profile:\n{yaml.dump(profile, allow_unicode=True, default_flow_style=False)}"

        elif name == "hent_bruker_aktivitet":
            interactions = _get_recent_interactions(n=10, user_id=_active_user_id)
            observations = get_recent_observations(_active_user_id, days=7, allow_system_key=True)
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
            return await web_search(query)

        return t("meet_unknown_tool", get_lang("global"), name=name)
    except Exception as e:
        return t("meet_tool_error", get_lang("global"), name=name, error=e)


async def _ask_leder(messages: list[dict], with_tools: bool = False) -> str:
    """Call meeting leader with optional tool access. Provider-agnostic via call_llm_chat.

    On failure returns _LEDER_FAIL + translated error string.
    Callers detect errors with result.startswith(_LEDER_FAIL).
    """
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


def _get_prev_reflection_summaries(user_id: str, n: int = 3) -> str:
    """Return opening excerpts from the last n reflection reports for this user."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        files = sorted(
            [f for f in (REFLECTIONS_BASE / user_id).glob("*.md") if f.stem != today],
            reverse=True,
        )[:n]
    except Exception:
        return ""

    sections: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            # Match all 3 language variants of the report header — reports may have
            # been written under any past active language (mirrors kaare_dev_meeting.py).
            m = re.search(r"##\s*(?:Samtale|Conversation|Gespräch)\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
            excerpt = (m.group(1).strip()[:200] if m else text.strip()[:200])
            if excerpt:
                sections.append(f"**{f.stem}:**\n{excerpt}")
        except Exception:
            continue

    return "\n\n".join(sections)


def _get_user_comment(user_id: str, lang: str = "nb") -> str:
    """Return and clear the most recent user comment on a reflection meeting, if any.

    A comment is meant to be surfaced exactly once (the next meeting after it was
    written) — clearing the file after read prevents it from being re-injected into
    every meeting indefinitely.
    """
    comments_dir = Path("/kaare/state/meeting_comments/reflection") / user_id
    try:
        files = sorted(comments_dir.glob("*.txt"), reverse=True)
        for f in files:
            text = f.read_text(encoding="utf-8").strip()
            if text:
                f.write_text("", encoding="utf-8")
                return t("refl_user_comment_on_meeting", lang, date=f.stem, text=text)
    except Exception:
        pass
    return ""


def _get_user_topic(user_id: str) -> str:
    """Return and clear the user's topic suggestion for this meeting."""
    p = Path("/kaare/state/meeting_topics_user") / f"{user_id}.txt"
    try:
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                p.write_text("", encoding="utf-8")
                return text
    except Exception:
        pass
    return ""


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


def _build_leder_system(
    prev_block: str = "",
    admin_topic: str = "",
    user_topic: str = "",
    user_comment: str = "",
    role: str = "",
    admin_comment: str = "",
    user_display_name: str = "",
) -> str:
    hostname = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lang = _get_kare_language_r()
    mk_role = _get_miss_kare_meeting_role()
    mk_desc = _MK_ROLE_DESC.get(lang, _MK_ROLE_DESC["nb"]).get(mk_role, mk_role)
    preset_text = _load_leder_reflection_preset(lang)
    base = preset_text.format(mk_desc=mk_desc, hostname=hostname, time=now)

    sep = "\n\n---\n\n"
    blocks = [base]

    if role in ("child", "teen"):
        role_block = _build_role_age_block(role, lang)
        if role_block:
            blocks.append(role_block)

    if prev_block:
        blocks.append(t("refl_leder_sys_prev", lang, block=prev_block))
    if admin_comment:
        blocks.append(t("refl_leder_sys_admin_comment", lang, comment=admin_comment))
    name_label = t("refl_from_name", lang, name=user_display_name) if user_display_name else ""
    if user_comment:
        blocks.append(t("refl_leder_sys_user_comment", lang, name_label=name_label, comment=user_comment))
    if admin_topic:
        blocks.append(t("refl_leder_sys_admin_topic", lang, topic=admin_topic))
    if user_topic:
        blocks.append(t("refl_leder_sys_user_topic", lang, topic=user_topic))

    try:
        hs = read_household_state()
        if hs.get("mode") == "away":
            away_since = hs.get("away_since")
            days_away = 0
            if away_since:
                try:
                    delta = datetime.now(timezone.utc) - datetime.fromisoformat(away_since)
                    days_away = delta.days
                except Exception:
                    pass
            away_note = t("refl_away_note", lang)
            if hs.get("away_reason"):
                away_note += t("refl_away_reason", lang, reason=hs["away_reason"])
            if hs.get("expected_return"):
                away_note += t("refl_away_return", lang, date=hs["expected_return"])
            if days_away >= 2:
                away_note += t("refl_away_long", lang, days=days_away)
            blocks.append(away_note)
    except Exception:
        pass

    # Time goes last to preserve KV-cache on the static blocks above
    blocks.append(t("meet_leder_sys_time", lang, time=now))

    return sep.join(blocks)


# ── Opening sequence: topic proposals from participants ───────────────────────
async def _kare_temaforslag(kare_messages: list[dict], user_context: str) -> str:
    """Ask Kåre to propose one topic about the user to explore in the meeting."""
    lang = _get_kare_language_r()
    kare_messages.append({"role": "user", "content": t("refl_kare_topic_prompt", lang)})
    svar = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
    kare_messages.append({"role": "assistant", "content": svar})
    return svar


async def _miss_kare_temaforslag(miss_kare_messages: list[dict], kare_tema: str) -> str:
    """Ask Miss Kåre to propose one topic about the user to explore."""
    lang = _get_kare_language_r()
    miss_kare_messages.append({"role": "user", "content": t("refl_miss_kare_topic_prompt", lang, tema=kare_tema)})
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


async def _leder_presenter_admin_input(admin_input: str, leder_system: str = "") -> str:
    """Meeting leader presents admin input as the first item after the opening sequence."""
    lang = _get_kare_language_r()
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t("refl_leder_admin_input_user", lang, admin_input=admin_input)},
    ]
    result = await _ask_leder(messages, with_tools=False)
    if result.startswith(_LEDER_FAIL):
        return t("refl_admin_input_fallback", lang, admin_input=admin_input)
    return result


async def _leder_sett_agenda(kare_tema: str, miss_kare_tema: str, user_context: str, leder_system: str = "") -> str:
    """Meeting leader picks one topic from each participant and sets the agenda."""
    lang = _get_kare_language_r()
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t("refl_leder_agenda_user", lang, kare_tema=kare_tema, miss_kare_tema=miss_kare_tema)},
    ]
    result = await _ask_leder(messages, with_tools=True)
    if result.startswith(_LEDER_FAIL):
        return t("meet_leader_unavailable", lang, error=t("refl_err_using_suggested_topics", lang)) + f"\nKåre: {kare_tema}\nMiss Kåre: {miss_kare_tema}"
    return result


async def _leder_intro_gruppe2(prev_summary: str, leder_system: str = "") -> str:
    """Meeting leader sets agenda for group 2."""
    lang = _get_kare_language_r()
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t("refl_leder_group2_intro", lang, prev_summary=prev_summary)},
    ]
    result = await _ask_leder(messages, with_tools=False)
    if result.startswith(_LEDER_FAIL):
        return t("meet_leader_unavailable", lang, error=t("refl_err_group2_no_intro", lang))
    return result


async def _leder_runde_sjekk(conversation_tail: str, global_round: int, leder_system: str = "") -> tuple[str, str]:
    """Brief check after a round. Returns (action, value)."""
    lang = _get_kare_language_r()
    decision_instructions = t("refl_leder_decision_instructions", lang)
    system_content = (
        f"{leder_system}\n\n---\n\n{decision_instructions}"
        if leder_system else
        t("refl_leder_fallback_system", lang, instructions=decision_instructions)
    )
    messages = [
        {"role": "system", "content": system_content},
        {
            "role": "user",
            "content": t("refl_leder_round_check_user", lang, round_num=global_round, conv_tail=conversation_tail),
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
    lang = _get_kare_language_r()
    if action == "fortsett":
        return

    if action == "hent_aktivitet":
        exchanges.append(("Møteleder", t("refl_leder_checking_activity", lang)))
        result = await _execute_leder_tool("hent_bruker_aktivitet", {})
        exchanges.append(("Kåre [brukeraktivitet]", result))
        inject = t("refl_leder_activity_fetched", lang, result=result)
        log.info("[user activity fetched] %s…", result[:80])

    elif action == "nettsøk":
        exchanges.append(("Møteleder", t("refl_leder_searching", lang, value=value)))
        result = await _execute_leder_tool("nettsøk", {"query": value})
        exchanges.append(("Kåre [nettsøk]", result))
        inject = t("refl_leder_search_result", lang, value=value, result=result)
        log.info("[web search '%s'] %s…", value, result[:80])

    elif action == "innspill":
        exchanges.append(("Møteleder", value))
        inject = t("refl_leder_colon", lang, value=value)
        log.info("[meeting leader input] %s", value[:80])

    else:
        return

    kare_messages.append({"role": "user", "content": inject})
    miss_kare_messages.append({"role": "user", "content": inject})


async def _leder_vurder(conversation: str, group: int, max_groups: int, leder_system: str = "") -> tuple[bool, str]:
    """Meeting leader evaluates whether to continue. Returns (should_continue, reason)."""
    lang = _get_kare_language_r()
    if group >= max_groups:
        return False, t("meet_max_groups", lang)

    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t("refl_leder_vurder_user", lang, group=group, max_groups=max_groups, conv_tail=conversation[-2500:])},
    ]
    svar = await _ask_leder(messages, with_tools=False)
    log.info("[meeting leader evaluation] %s", svar[:100])

    if svar.startswith(_LEDER_FAIL):
        log.warning("Meeting leader unavailable — continuing automatically")
        return True, t("meet_leader_unavailable", lang, error=t("refl_err_continuing_to_group", lang, group=group + 1))

    fortsett = svar.upper().startswith("FORTSETT")
    return fortsett, svar


async def _leder_vurder_closing(conversation_tail: str, closing_round: int) -> bool:
    """Meeting leader checks whether the free closing round is still adding anything
    new, or just repeating earlier points. Returns should_continue."""
    lang = _get_kare_language_r()
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": t("refl_leder_closing_check_user", lang, closing_round=closing_round, conv_tail=conversation_tail[-2000:])},
    ]
    svar = await _ask_leder(messages, with_tools=False)
    log.info("[meeting leader closing check %d] %s", closing_round, svar[:100])

    if svar.startswith(_LEDER_FAIL):
        log.warning("Meeting leader unavailable during closing check — continuing automatically")
        return True

    return svar.upper().startswith("FORTSETT")


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


async def _save_meeting_insights(user_id: str, kare_closing: str) -> None:
    """
    Parse Kåre's closing statement, write the narrative observation line, and route any
    concrete biographical fact into the real profile_template.yaml schema.

    Previously also wrote LIKER/BEKYMRING/GLEDE into ad-hoc `preferences`/`concerns`/
    `delights` profile fields that don't exist in the schema — replaced (P86 Phase 5)
    with extract_facts_from_meeting(), which targets the same 19-section schema and
    novelty gate used by Miss Kåre's live fact-capture (P86 Phase 4), so the meeting
    stops restating the same soft-preference bullets night after night without ever
    adding anything structured.
    """
    try:
        obs_match      = re.search(r"OBSERVASJON:\s*(.+?)(?=LIKER:|BEKYMRING:|GLEDE:|$)",      kare_closing, re.DOTALL | re.IGNORECASE)
        liker_match    = re.search(r"LIKER:\s*(.+?)(?=OBSERVASJON:|BEKYMRING:|GLEDE:|$)",       kare_closing, re.DOTALL | re.IGNORECASE)
        bekymring_match = re.search(r"BEKYMRING:\s*(.+?)(?=OBSERVASJON:|LIKER:|GLEDE:|$)",     kare_closing, re.DOTALL | re.IGNORECASE)
        glede_match    = re.search(r"GLEDE:\s*(.+?)(?=OBSERVASJON:|LIKER:|BEKYMRING:|$)",      kare_closing, re.DOTALL | re.IGNORECASE)

        obs_lines = []
        if obs_match:
            obs_lines.append(obs_match.group(1).strip())
        if liker_match and liker_match.group(1).strip().lower() not in ("ingenting nytt", "ingenting", "ingen"):
            obs_lines.append(f"Likes: {liker_match.group(1).strip()}")
        if bekymring_match and bekymring_match.group(1).strip().lower() not in ("ingen", "ingenting"):
            obs_lines.append(f"Concern: {bekymring_match.group(1).strip()}")
        if glede_match and glede_match.group(1).strip().lower() not in ("ingen", "ingenting"):
            obs_lines.append(f"Delight: {glede_match.group(1).strip()}")

        observation_text = "\n".join(obs_lines) if obs_lines else f"Meeting held.\n{kare_closing[:300]}"
        _vault_or_write_observation(user_id, observation_text)

        await extract_facts_from_meeting(user_id, kare_closing)
        log.info("Meeting insights saved to profile for %s", user_id)

    except Exception as e:
        log.error("Failed to save meeting insights: %s", e)


async def _update_user_knowledge(
    user_id: str,
    exchanges: list[tuple[str, str]],
    existing: str,
    leder_system: str = "",
    lang: str = "nb",
) -> None:
    """Distil concluded knowledge from the meeting and write user_knowledge.md."""
    transcript = "\n".join(f"{who}: {text[:300]}" for who, text in exchanges[-20:])
    existing_block = t("refl_existing_knowledge_block", lang, existing=existing) if existing else ""
    prompt = t("refl_update_knowledge_prompt", lang, existing_block=existing_block, transcript=transcript)
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": prompt},
    ]
    try:
        result = await _ask_kare(messages)
        if result and result.strip() and result.strip() != "[No response]":
            path = Path(f"/kaare/state/users/{user_id}/user_knowledge.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            content = t("refl_knowledge_file_header", lang, date=date_str) + f"{result.strip()}\n"
            path.write_text(content, encoding="utf-8")
            log.info("user_knowledge.md updated for %s", user_id)
    except Exception as e:
        log.error("Failed to update user_knowledge for %s: %s", user_id, e)


# ── Write reflection file (atomic) ────────────────────────────────────────────
def _write_reflection(user_id: str, date_str: str, exchanges: list[tuple[str, str]], lang: str = "nb") -> Path:
    out_dir = REFLECTIONS_BASE / user_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}.md"

    participants = sorted(set(agent for agent, _ in exchanges))
    lines = [
        t("refl_report_title", lang, date=date_str),
        "",
        t("meet_report_participants", lang),
        *[f"- {p}" for p in participants],
        "",
        t("meet_report_conversation", lang),
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
        _topics = json.loads(_topics_file.read_text(encoding="utf-8"))
        _admin_topic = _topics.get("reflection", "").strip()
        if _admin_topic:
            _topics["reflection"] = ""
            _topics_file.write_text(json.dumps(_topics, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Admin topic read and cleared: %s", _admin_topic[:80])
    except Exception:
        pass

    # ── Møteleder kontekst-blokker ────────────────────────────────────────────
    _lang_r = _get_kare_language_r()
    log.info("=== Building Møteleder context ===")
    _prev_block    = _get_prev_reflection_summaries(user_id)
    _user_comment  = _get_user_comment(user_id, lang=_lang_r)
    _user_topic    = _get_user_topic(user_id)
    _admin_comment = _get_admin_comment_r()
    _user_role_r   = _get_user_role_r(user_id)
    try:
        _rec_r = _store.get_user(user_id)
        _display_name_r = (_rec_r or {}).get("display_name", "") if _rec_r else ""
    except Exception:
        _display_name_r = ""
    leder_system   = _build_leder_system(
        prev_block=_prev_block,
        admin_topic=_admin_topic,
        user_topic=_user_topic,
        user_comment=_user_comment,
        role=_user_role_r,
        admin_comment=_admin_comment,
        user_display_name=_display_name_r,
    )
    log.info("[Møteleder system] %d chars", len(leder_system))

    profile_text, observations_text, interactions_text, stm_text = _get_user_context(user_id)
    portrait = _load_miss_kare_portrait(user_id)
    user_knowledge = _load_user_knowledge(user_id)

    _stm_section = t("refl_stm_section", _lang_r, stm=stm_text) if stm_text else ""
    _knowledge_section = (
        t("refl_knowledge_section", _lang_r, knowledge=user_knowledge)
        if user_knowledge else ""
    )
    user_context_summary = (
        t("refl_user_profile_header", _lang_r, profile=profile_text) + "\n\n"
        + t("refl_recent_observations_header", _lang_r, obs=observations_text) + "\n\n"
        + t("refl_recent_interactions_header", _lang_r, interactions=interactions_text)
        + _stm_section
        + _knowledge_section
    )

    _topic_line = t("refl_admin_topic_line", _lang_r, topic=_admin_topic) if _admin_topic else ""

    total_info = t(
        "refl_meeting_info", _lang_r,
        max_rounds=MAX_ROUNDS, max_groups=max_groups, rounds_per_group=ROUNDS_PER_GROUP,
        topic_line=_topic_line,
    )

    kare_system = (
        f"{KARE_CORE}\n\n{KARE_BEHAVIOR}\n\n"
        f"{t('refl_kare_system_header', _lang_r)}\n"
        f"{total_info}\n\n"
        f"{t('meet_lang_respond', _lang_r)}\n\n"
        f"{t('refl_kare_system_body', _lang_r)}\n\n"
        f"{user_context_summary}"
    )

    _miss_kare_pers = _load_miss_kare_meeting_pers()
    miss_kare_system = (
        f"{_miss_kare_pers}\n\n"
        "---\n\n"
        f"{t('meet_lang_respond', _lang_r)}\n\n"
        f"{t('refl_miss_kare_system_body', _lang_r)}\n\n"
        + t("refl_user_profile_header", _lang_r, profile=profile_text) + "\n\n"
        + t("refl_recent_observations_header_short", _lang_r, obs=observations_text)
        + (t("refl_stm_section", _lang_r, stm=stm_text) if stm_text else "")
        + (t("refl_own_observations_header", _lang_r, portrait=portrait) if portrait else "")
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

    agenda = await _leder_sett_agenda(kare_tema, miss_kare_tema, user_context_summary, leder_system=leder_system)
    exchanges.append(("Møteleder", agenda))
    log.info("[meeting leader agenda] %s", agenda[:120])

    agenda_msg = t("refl_agenda_msg", _lang_r, agenda=agenda)
    kare_messages.append({"role": "user", "content": agenda_msg})
    miss_kare_messages.append({"role": "user", "content": agenda_msg})

    # ── Admin input round (one extra round if admin submitted a topic) ────────
    if _admin_topic:
        log.info("=== Admin input round ===")
        admin_intro = await _leder_presenter_admin_input(_admin_topic, leder_system=leder_system)
        exchanges.append(("Møteleder", admin_intro))
        log.info("[meeting leader admin intro] %s", admin_intro[:120])

        admin_msg = t("meet_leder_says", _lang_r, text=admin_intro)
        kare_messages.append({"role": "user", "content": admin_msg})
        miss_kare_messages.append({"role": "user", "content": admin_msg})

        kare_messages.append({"role": "user", "content": t("refl_admin_react_prompt", _lang_r)})
        kare_reply = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
        kare_messages.append({"role": "assistant", "content": kare_reply})
        exchanges.append(("Kåre", kare_reply))
        log.info("[Kåre admin round] %s", kare_reply[:120])

        miss_kare_messages.append({"role": "user", "content": t("refl_kare_said_admin_react_mk", _lang_r, text=kare_reply)})
        async with lock_11445("miss_kare_refleksjon"):
            miss_reply = await _ask_ollama(
                MISS_KARE_URL, MISS_KARE_MODEL,
                _trim(miss_kare_messages, MISS_KARE_WINDOW),
                temperature=0.5, timeout=MISS_KARE_TIMEOUT,
                max_tokens=MISS_KARE_MAX_TOKENS, num_thread=12, num_ctx=_MISS_KARE_NUM_CTX,
            )
        miss_reply = re.sub(r"^Miss\s+Kåre\s+sa:\s*", "", miss_reply, flags=re.IGNORECASE).strip()
        miss_kare_messages.append({"role": "assistant", "content": miss_reply})
        kare_messages.append({"role": "user", "content": t("refl_miss_kare_said", _lang_r, text=miss_reply)})
        exchanges.append(("Miss Kåre", miss_reply))
        log.info("[Miss Kåre admin round] %s", miss_reply[:120])

    # ── Group rounds ──────────────────────────────────────────────────────────
    global_round = 0
    prev_summary = ""

    for group in range(1, max_groups + 1):
        log.info("=== Group %d of %d ===", group, max_groups)

        if group > 1:
            intro = await _leder_intro_gruppe2(prev_summary, leder_system=leder_system)
            exchanges.append(("Møteleder", intro))
            log.info("[meeting leader group 2] %s", intro[:120])
            intro_msg = t("meet_leder_says", _lang_r, text=intro)
            kare_messages.append({"role": "user", "content": intro_msg})
            miss_kare_messages.append({"role": "user", "content": intro_msg})

        for local_round in range(ROUNDS_PER_GROUP):
            global_round += 1
            log.info("--- Round %d/%d (group %d, local %d) ---",
                     global_round, MAX_ROUNDS, group, local_round + 1)

            kare_prompt = t("refl_your_turn", _lang_r)
            kare_messages.append({"role": "user", "content": kare_prompt})
            kare_reply = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
            kare_messages.append({"role": "assistant", "content": kare_reply})
            exchanges.append(("Kåre", kare_reply))
            log.info("[Kåre] %s", kare_reply[:120])

            miss_kare_messages.append({"role": "user", "content": t("refl_kare_said_your_turn", _lang_r, text=kare_reply)})
            async with lock_11445("miss_kare_refleksjon"):
                miss_kare_reply = await _ask_ollama(
                    MISS_KARE_URL, MISS_KARE_MODEL,
                    _trim(miss_kare_messages, MISS_KARE_WINDOW),
                    temperature=0.5, timeout=MISS_KARE_TIMEOUT,
                    max_tokens=MISS_KARE_MAX_TOKENS, num_thread=12, num_ctx=_MISS_KARE_NUM_CTX,
                )
            miss_kare_reply = re.sub(r"^Miss\s+Kåre\s+sa:\s*", "", miss_kare_reply, flags=re.IGNORECASE).strip()
            miss_kare_messages.append({"role": "assistant", "content": miss_kare_reply})
            kare_messages.append({"role": "user", "content": t("refl_miss_kare_said", _lang_r, text=miss_kare_reply)})
            exchanges.append(("Miss Kåre", miss_kare_reply))
            log.info("[Miss Kåre] %s", miss_kare_reply[:120])

            if local_round < ROUNDS_PER_GROUP - 1:
                conv_tail = "\n\n".join(f"{a}: {t}" for a, t in exchanges[-6:])
                action, value = await _leder_runde_sjekk(conv_tail, global_round, leder_system=leder_system)
                await _handle_leder_action(action, value, kare_messages, miss_kare_messages, exchanges)

        conversation_text = "\n\n".join(f"{a}: {t}" for a, t in exchanges)
        fortsett, begrunnelse = await _leder_vurder(conversation_text, group, max_groups, leder_system=leder_system)
        exchanges.append(("Møteleder", begrunnelse))
        prev_summary = begrunnelse

        if not fortsett:
            log.info("Meeting leader ends after group %d: %s", group, begrunnelse)
            break

    # ── Free closing rounds (max 4) ───────────────────────────────────────────
    log.info("--- Free closing rounds ---")
    CLOSING_PROMPT = t("refl_closing_prompt", _lang_r)
    for closing_round in range(1, 5):
        log.info("--- Closing round %d/4 ---", closing_round)
        noen_svarte = False

        kare_messages.append({"role": "user", "content": CLOSING_PROMPT})
        kare_free = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
        kare_messages.append({"role": "assistant", "content": kare_free})
        if kare_free.strip().upper() != "INGENTING":
            exchanges.append(("Kåre", kare_free))
            miss_kare_messages.append({"role": "user", "content": t("refl_kare_says", _lang_r, text=kare_free) + f"\n\n{CLOSING_PROMPT}"})
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
            kare_messages.append({"role": "user", "content": t("refl_miss_kare_said", _lang_r, text=miss_kare_free)})
            log.info("[Miss Kåre free] %s", miss_kare_free[:120])
            noen_svarte = True
        else:
            log.info("[Miss Kåre free] INGENTING")

        if not noen_svarte:
            log.info("No one had more to say — ending free rounds after round %d", closing_round)
            break

        if closing_round < 4:
            conv_tail = "\n\n".join(f"{a}: {txt}" for a, txt in exchanges[-4:])
            should_continue = await _leder_vurder_closing(conv_tail, closing_round)
            if not should_continue:
                log.info("Meeting leader ended free rounds early after round %d — repetition", closing_round)
                break

    # ── Kåre's closing — saved to user profile ────────────────────────────────
    log.info("--- Kåre closing statement ---")
    kare_messages.append({"role": "user", "content": t("refl_kare_closing_prompt", _lang_r)})
    kare_closing = await _ask_kare(_trim(kare_messages, KARE_WINDOW))
    exchanges.append(("Kåre", kare_closing))
    log.info("[Kåre closing] %s", kare_closing[:120])

    _write_reflection(user_id, date_str, exchanges, lang=_lang_r)
    await _save_meeting_insights(user_id, kare_closing)
    await _update_user_knowledge(user_id, exchanges, user_knowledge, leder_system=leder_system, lang=_lang_r)
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
