#!/usr/bin/env python3
"""
Kåre utviklingsmøte – kjøres kl. 05:30 via systemd timer.

Deltakere:
  - Mechanic (9B, 5060 Ti port 11445) – teknisk graving med verktøy
  - Kåre (27B, Blackwell GPU via proxy port 11441) – systemperspektiv med verktøy
  - Møteleder (27B, deler GPU-proxy port 11441) – regissør og prioriterer
  - Online (405B cloud) – ekstern vurdering

Flyt:
  1. Undersøkelsesfase: Mechanic + Kåre graver uavhengig med sine verktøy
  2. Møteleder leser begge rapporter og setter konkret agenda
  3. Diskusjonsrunder: Mechanic → Kåre → Møteleder styrer
  4. Online + oppsummering

Utviklingsmøtet handler IKKE om refleksjonsmøtet – det er et eget møte med
eget råmateriale: systemlogger, kodebase, tjenestestatus, interaksjonsmønstre.

Output: /kaare/state/memory/dev_meetings/YYYY-MM-DD.md
"""

import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, "/kaare")
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, get_tool_permissions as _get_tool_perms
from kaare_core.tools.i18n import t, get_lang
from kaare_core.agents.mechanic.tools import (
    ask_with_tools as _mechanic_ask,
    MECHANIC_URL,
    MECHANIC_MODEL,
    MECHANIC_TOOLS,
    UNDERSØKER_TOOLS,
    KRITIKER_TOOLS,
    MEMORY_PATH as _MECHANIC_MEMORY_PATH,
    execute_tool as _mechanic_execute_tool,
)
from kaare_core.tools.executor import execute_tool as _kaare_execute_tool
from kaare_core.tools.definitions import KAARE_TOOLS
from adapters.llm_adapter import _parse_ollama_tool_calls, _normalise_messages_for_vllm


def _build_kare_dev_tools() -> list[dict]:
    """
    Kåre's tool set for the dev meeting:
    All Mechanic tools except sandkasse, merged with Kåre-specific tools
    that aren't already covered (by name) in Mechanic's set.
    """
    mechanic_without_sandbox = [
        t for t in MECHANIC_TOOLS
        if t["function"]["name"] != "sandkasse"
    ]
    mechanic_names = {t["function"]["name"] for t in mechanic_without_sandbox}

    # Kåre-specific tools that don't overlap with Mechanic's names
    kare_only = [
        t for t in KAARE_TOOLS
        if t["function"]["name"] not in mechanic_names
    ]
    return mechanic_without_sandbox + kare_only


async def _kare_dev_execute_tool(name: str, args: dict) -> str:
    """
    Hybrid executor: Mechanic's executor handles investigation tools,
    Kåre's executor handles his own domain tools.
    """
    mechanic_names = {t["function"]["name"] for t in MECHANIC_TOOLS}
    if name in mechanic_names:
        return await _mechanic_execute_tool(name, args)
    return await _kaare_execute_tool(name, args)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dev_meeting")

# ── Configuration ─────────────────────────────────────────────────────────────
DEV_DIR      = Path("/kaare/state/memory/dev_meetings")
LATEST_PATH  = Path("/kaare/state/memory/dev_meeting_latest.md")

from kaare_core.config import get_service as _svc

_default_cfg  = _llm("default")
_cloud_base   = _llm("cloud")["base_url"]

_KARE_PROVIDER = _default_cfg.get("provider", "ollama")   # "vllm" or "ollama"
_KARE_BASE_URL = _default_cfg["base_url"]

# URL per provider — everything read from llm.yaml, nothing hardcoded
KARE_URL   = (_KARE_BASE_URL + "/v1/chat/completions") if _KARE_PROVIDER == "vllm" else (_KARE_BASE_URL + "/api/chat")
LEDER_URL  = KARE_URL
KARE_MODEL  = _cfg_model("kare")
LEDER_MODEL = _cfg_model("kare")
CLOUD_URL   = _cloud_base + "/chat/completions"
CLOUD_MODEL = _cfg_model("cloud")

TIMEOUT_SECS      = 300
LEDER_TIMEOUT     = 120
KARE_WINDOW       = 8
MECHANIC_WINDOW = 6

_CURRENT_MEET_RID: str = ""

_SETTINGS_PATH      = Path("/kaare/configs/settings.yaml")
_LEDER_PRESET_DIR   = Path("/kaare/configs/meeting_leder")
_LEDER_CUSTOM_PATH  = Path("/kaare/configs/meeting_leder/dev_egendefinert.md")
_VALID_LEDER_PRESETS = ("standard", "streng", "utforskende", "egendefinert")

def _load_dev_meeting_cfg() -> dict:
    try:
        return yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")).get("dev_meeting", {})
    except Exception:
        return {}

def _dev_cfg() -> dict:
    return _load_dev_meeting_cfg()

MAX_ROUNDS        = int(_dev_cfg().get("max_rounds", 6))
ROUNDS_PER_GROUP  = MAX_ROUNDS // 2
MAX_INVEST_ROUNDS = int(_dev_cfg().get("max_invest_rounds", 5))
KARE_MAX_TOKENS   = int(_dev_cfg().get("kare_max_tokens", 2500))
KARE_INVEST_TOKENS = int(_dev_cfg().get("kare_invest_tokens", 1000))

_kare_opts    = _default_cfg["options"]
_KARE_NUM_CTX = _kare_opts.get("num_ctx") or _kare_opts.get("max_tokens", 8192)


def _kare_payload(
    messages: list[dict],
    max_tokens: int,
    tools: list | None = None,
    thinking: bool = True,
    rid: str = "",
) -> dict:
    """Build LLM payload for Kåre/Møteleder, adapted to provider from llm.yaml.

    thinking=False disables chain-of-thought for short coordination calls (møteleder).
    """
    if _KARE_PROVIDER == "vllm":
        p: dict = {
            "model": KARE_MODEL,
            "messages": _normalise_messages_for_vllm(messages),
            "stream": False,
            "temperature": 0.4,
            "max_tokens": max_tokens,
        }
        if not thinking:
            p["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        p = {
            "model": KARE_MODEL,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.4, "num_ctx": _KARE_NUM_CTX, "num_predict": max_tokens},
        }
    if tools:  # Only add non-empty tool lists — empty list + tool_choice causes vLLM 400
        p["tools"] = tools
        if _KARE_PROVIDER == "vllm":
            p["tool_choice"] = "auto"
    effective_rid = rid or _CURRENT_MEET_RID
    if effective_rid:
        p["rid"] = effective_rid
    return p


def _parse_kare_resp(resp: dict) -> tuple[str, list]:
    """Extract (content, tool_calls) from LLM response, handling vLLM and Ollama formats."""
    if _KARE_PROVIDER == "vllm":
        msg = resp.get("choices", [{}])[0].get("message", {})
    else:
        msg = resp.get("message", {})
    content    = msg.get("content", "") or ""
    tool_calls = msg.get("tool_calls") or []
    # Fallback: model put tool calls in content as XML instead of structured tool_calls
    if not tool_calls and "<tool_call>" in content:
        parsed, content = _parse_ollama_tool_calls(content)
        if parsed:
            tool_calls = parsed
    # vLLM reasoning-parser puts thinking in reasoning_content; use as fallback when content is empty
    if not content and not tool_calls:
        content = msg.get("reasoning_content", "") or ""
    return content, tool_calls


# ── Helper functions ──────────────────────────────────────────────────────────
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
    system = [m for m in messages if m.get("role") == "system"]
    rest   = [m for m in messages if m.get("role") != "system"]
    return system + rest[-window:]

def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

KARE_CORE     = _load("/kaare/configs/personality_core.md")
KARE_BEHAVIOR = _load("/kaare/configs/personality_behavior.md")

_MECHANIC_DIR = Path(__file__).parent / "kaare_core" / "agents" / "mechanic"

def _load_mechanic_pers(role: str = "standard") -> str:
    if role == "egendefinert":
        custom = Path("/kaare/configs/meeting_role_mechanic_custom.md")
        if custom.exists():
            return custom.read_text(encoding="utf-8")
    if role and role not in ("standard", "egendefinert"):
        p = _MECHANIC_DIR / f"personlighet_{role}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    p = _MECHANIC_DIR / "personlighet.md"
    return p.read_text(encoding="utf-8") if p.exists() else "Du er Mechanic – praktisk problemløser."


def _get_mechanic_meeting_role() -> str:
    try:
        data = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text(encoding="utf-8")) or {}
        return data.get("meeting_roles", {}).get("mechanic", "undersøker")
    except Exception:
        return "undersøker"


MECHANIC_PERS = _load_mechanic_pers(_get_mechanic_meeting_role())


def _load_mechanic_memory() -> str:
    try:
        content = _MECHANIC_MEMORY_PATH.read_text(encoding="utf-8").strip()
        return content if content else ""
    except Exception:
        return ""


def _get_memory_context(lang: str = "nb") -> str:
    """Fetch recent STM daily summaries + LTM episodes for meeting context."""
    parts = []
    try:
        from kaare_core.memory.long_term import load_recent_daily_summaries
        entries = load_recent_daily_summaries(days=3)
        if entries:
            lines = []
            for e in reversed(entries):
                lines.append(t("meet_mem_daily_item", lang, date=e["date"], count=e["count"], summary=e["summary"]))
            parts.append(t("meet_mem_daily_header", lang) + "\n" + "\n\n".join(lines))
    except Exception as e:
        log.warning("Could not load STM daily summaries: %s", e)

    try:
        import sqlite3
        conn = sqlite3.connect("/kaare/state/memory/interactions.db")
        rows = conn.execute(
            "SELECT narrative, topics FROM episodes ORDER BY id DESC LIMIT 5"
        ).fetchall()
        conn.close()
        if rows:
            lines = []
            for i, (narrative, topics) in enumerate(reversed(rows), 1):
                lines.append(t("meet_mem_episode_item", lang, n=i, topics=topics or t("meet_mem_topic_unknown", lang), narrative=narrative))
            parts.append(t("meet_mem_episodes_header", lang) + "\n" + "\n\n".join(lines))
    except Exception as e:
        log.warning("Could not load LTM episodes: %s", e)

    try:
        if LATEST_PATH.exists():
            prev = LATEST_PATH.read_text(encoding="utf-8")
            lines = prev.splitlines()
            # Extract the proposals + last conversation turns (last 40 lines)
            excerpt = "\n".join(lines[-40:])
            parts.append(t("meet_mem_prev_meeting", lang, excerpt=excerpt))
    except Exception as e:
        log.warning("Could not load previous dev meeting: %s", e)

    if not parts:
        return ""
    return t("meet_mem_header", lang) + "\n" + "\n\n".join(parts)


async def _run_health_check(lang: str = "nb") -> str:
    """Run scripts/health_check.py --json and return a formatted text summary for the meeting."""
    try:
        env = {**os.environ, "PYTHONPATH": "/kaare"}
        proc = await asyncio.create_subprocess_exec(
            "/kaare/venv/bin/python", "/kaare/scripts/health_check.py", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=35)
        data = json.loads(stdout.decode())
    except asyncio.TimeoutError:
        return t("meet_system_timeout", lang)
    except Exception as e:
        return t("meet_health_failed", lang, error=e)

    lines = [t("meet_health_header", lang, timestamp=data["timestamp"])]

    # Services
    svc_results = data.get("services", {}).get("results", [])
    svc_up   = data.get("services", {}).get("passed", 0)
    svc_fail = data.get("services", {}).get("failed", 0)
    svc_down = [s for s in svc_results if not s["ok"]]
    if svc_down:
        lines.append(t("meet_health_services_down", lang, failed=svc_fail, total=svc_up + svc_fail))
        for s in svc_down:
            lines.append(f"    – {s['name']}: {s['detail']}")
    else:
        lines.append(t("meet_health_services_ok", lang, count=svc_up))

    # Imports
    imp_errors = data.get("imports", {}).get("errors", [])
    imp_ok     = data.get("imports", {}).get("passed", 0)
    imp_skip   = data.get("imports", {}).get("skipped", 0)
    if imp_errors:
        lines.append(t("meet_health_import_errors", lang, count=len(imp_errors)))
        for e in imp_errors:
            lines.append(f"    – {e['name']}: {e['detail']}")
    else:
        skip_note = t("meet_health_import_skip", lang, count=imp_skip) if imp_skip else ""
        lines.append(t("meet_health_imports_ok", lang, count=imp_ok, skip=skip_note))

    # Configs
    cfg_errors = data.get("configs", {}).get("errors", [])
    cfg_ok     = data.get("configs", {}).get("passed", 0)
    if cfg_errors:
        lines.append(t("meet_health_config_errors", lang, count=len(cfg_errors)))
        for e in cfg_errors:
            lines.append(f"    – {e['name']}: {e['detail']}")
    else:
        lines.append(t("meet_health_config_ok", lang, count=cfg_ok))

    total_errors = data.get("total_errors", 0)
    if total_errors == 0:
        lines.append(t("meet_system_ok", lang))
    else:
        lines.append(t("meet_system_errors", lang, count=total_errors))
    return "\n".join(lines)


# ── Kåre's tools in the meeting ───────────────────────────────────────────────
# All Mechanic tools (except sandkasse) + Kåre's own domain tools.

_KARE_MEETING_TOOLS = _build_kare_dev_tools()


# ── Kåre calls (meeting – with tool support) ──────────────────────────────────
async def _kare_investigate(system_prompt: str, lang: str = "nb") -> str:
    """Kåre investigates with his tools and returns a summary of findings."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": t("meet_kare_investigate_user", lang)},
    ]

    for _ in range(MAX_INVEST_ROUNDS):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
                r = await client.post(
                    KARE_URL,
                    json=_kare_payload(messages, KARE_INVEST_TOKENS, _KARE_MEETING_TOOLS),
                    headers={"x-kaare-source": "dev_meeting"},
                )
                r.raise_for_status()
                resp = r.json()
        except httpx.HTTPStatusError as e:
            log.error("Kåre investigate-kall feilet: %s\nBody: %s", e, e.response.text[:1000])
            return t("meet_kare_unavailable", get_lang("global"), error=e)
        except Exception as e:
            log.error("Kåre investigate-kall feilet: %s", e)
            return t("meet_kare_unavailable", get_lang("global"), error=e)

        content, tool_calls = _parse_kare_resp(resp)

        if not tool_calls:
            stripped = _strip_think(content)
            if stripped:
                return stripped
            # Think-block used all tokens — retry without thinking
            log.warning("[Kåre investigate] Empty response (think used all tokens) — retry without thinking")
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
                    r = await client.post(
                        KARE_URL,
                        json=_kare_payload(messages, KARE_INVEST_TOKENS, thinking=False),
                        headers={"x-kaare-source": "dev_meeting"},
                    )
                    r.raise_for_status()
                    content, _ = _parse_kare_resp(r.json())
                    return _strip_think(content) or "[No findings]"
            except Exception as e:
                log.error("Kåre investigate retry failed: %s", e)
                return "[No findings]"

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn   = tc.get("function", {})
            name = fn.get("name", "")
            try:
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}
            log.info("[Kåre tool] %s(%s)", name, list(args.keys()))
            args["_user_id"] = "kare"
            result = await _kare_dev_execute_tool(name, args)
            if len(result) > 3000:
                result = result[:3000] + "\n" + t("meet_truncated", get_lang("global"))
            messages.append({"role": "tool", "content": result, "name": name})

    # Force a summary after max rounds — always without thinking to avoid empty responses
    messages.append({"role": "user", "content": t("meet_kare_summarize_user", lang)})
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
            r = await client.post(
                KARE_URL,
                json=_kare_payload(messages, KARE_INVEST_TOKENS, thinking=False),
                headers={"x-kaare-source": "dev_meeting"},
            )
            r.raise_for_status()
            content, _ = _parse_kare_resp(r.json())
            return _strip_think(content) or "[No findings]"
    except httpx.HTTPStatusError as e:
        log.error("Kåre investigate-oppsummering feilet: %s\nBody: %s", e, e.response.text[:1000])
        return t("meet_kare_failed", get_lang("global"), error=e)
    except Exception as e:
        return t("meet_kare_failed", get_lang("global"), error=e)


async def _ask_kare(messages: list[dict]) -> str:
    """Kåre in the discussion phase – with tool support (max 2 tool rounds)."""
    current = list(_trim(messages, KARE_WINDOW))

    for _ in range(2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
                r = await client.post(
                    KARE_URL,
                    json=_kare_payload(current, KARE_MAX_TOKENS, _KARE_MEETING_TOOLS),
                    headers={"x-kaare-source": "dev_meeting"},
                )
                r.raise_for_status()
                resp = r.json()
        except httpx.HTTPStatusError as e:
            log.error("Kåre diskusjon-kall feilet: %s\nBody: %s", e, e.response.text[:1000])
            return t("meet_kare_unavailable", get_lang("global"), error=e)
        except Exception as e:
            log.error("Kåre diskusjon-kall feilet: %s", e)
            return t("meet_kare_unavailable", get_lang("global"), error=e)

        content, tool_calls = _parse_kare_resp(resp)

        if not tool_calls:
            stripped = _strip_think(content)
            if stripped:
                return stripped
            # Think-block used all tokens — retry without thinking to get an actual response
            log.warning("[Kåre discuss] Empty response (think used all tokens) — retry without thinking")
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
                    r = await client.post(
                        KARE_URL,
                        json=_kare_payload(current, KARE_MAX_TOKENS, thinking=False),
                        headers={"x-kaare-source": "dev_meeting"},
                    )
                    r.raise_for_status()
                    content, _ = _parse_kare_resp(r.json())
                    return _strip_think(content) or "[No response]"
            except Exception as e:
                log.error("Kåre discuss retry failed: %s", e)
                return "[No response]"

        current.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn   = tc.get("function", {})
            name = fn.get("name", "")
            try:
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}
            log.info("[Kåre discuss tool] %s(%s)", name, list(args.keys()))
            args["_user_id"] = "kare"
            result = await _kare_dev_execute_tool(name, args)
            if len(result) > 3000:
                result = result[:3000] + "\n" + t("meet_truncated", get_lang("global"))
            current.append({"role": "tool", "content": result, "name": name})

    # Force reply after max tool rounds — always without thinking to avoid empty responses
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
            r = await client.post(
                KARE_URL,
                json=_kare_payload(current, KARE_MAX_TOKENS, thinking=False),
                headers={"x-kaare-source": "dev_meeting"},
            )
            r.raise_for_status()
            content, _ = _parse_kare_resp(r.json())
            return _strip_think(content) or "[No response]"
    except httpx.HTTPStatusError as e:
        log.error("Kåre discuss force-reply failed: %s\nBody: %s", e, e.response.text[:1000])
        return t("meet_kare_unavailable", get_lang("global"), error=e)
    except Exception as e:
        log.error("Kåre discuss force-reply failed: %s", e)
        return t("meet_kare_unavailable", get_lang("global"), error=e)


# ── Mechanic ──────────────────────────────────────────────────────────────────
async def _mechanic_investigate(system_prompt: str, lang: str = "nb") -> str:
    """Mechanic in investigator mode — digs into the system with focused tools."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": t("meet_mechanic_investigate_user", lang)},
    ]
    return await _mechanic_ask(
        messages=messages,
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=UNDERSØKER_TOOLS,
    )


async def _mechanic_kritiker(kare_funn: str, mechanic_memory: str, lang: str = "nb") -> str:
    """Mechanic in critic mode — asks critical questions about Kåre's findings."""
    pers = _load_mechanic_pers("kritiker")
    mem_block = f"\n\n{t('meet_mechanic_your_mem_header', lang)}\n{mechanic_memory}" if mechanic_memory else ""
    messages = [
        {"role": "system", "content": f"/no_think\n{pers}{mem_block}"},
        {"role": "user", "content": t("meet_mechanic_kritiker_user", lang, kare_funn=kare_funn)},
    ]
    return await _mechanic_ask(
        messages=messages,
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=KRITIKER_TOOLS,
    )


async def _ask_mechanic(messages: list[dict]) -> str:
    """Mechanic in the discussion phase – can use investigation tools."""
    return await _mechanic_ask(
        messages=_trim(messages, MECHANIC_WINDOW),
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=UNDERSØKER_TOOLS,
    )


# ── Meeting leader ────────────────────────────────────────────────────────────
def _get_kare_language() -> str:
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return data.get("kare_language") or data.get("language", "nb")
    except Exception:
        return "nb"


_PS_ROLE_DESC: dict[str, dict[str, str]] = {
    "nb": {
        "undersøker":   "teknisk graver",
        "kritiker":     "kritisk spørrer",
        "analytiker":   "rapport-analytiker",
        "standard":     "allsidig problemløser",
        "egendefinert": "tilpasset rolle",
    },
    "en": {
        "undersøker":   "technical investigator",
        "kritiker":     "critical questioner",
        "analytiker":   "report analyst",
        "standard":     "versatile problem-solver",
        "egendefinert": "custom role",
    },
    "de": {
        "undersøker":   "technischer Ermittler",
        "kritiker":     "kritischer Fragesteller",
        "analytiker":   "Berichtsanalyst",
        "standard":     "vielseitiger Problemlöser",
        "egendefinert": "benutzerdefinierte Rolle",
    },
}

_TOOL_NOTE_TMPL: dict[str, str] = {
    "nb": " Deaktivert i GUI: {tools}.",
    "en": " Disabled in GUI: {tools}.",
    "de": " In der GUI deaktiviert: {tools}.",
}

_ADMIN_COMMENT_TMPL: dict[str, str] = {
    "nb": (
        "**Admin-kommentar til møterapporten:**\n{comment}\n\n"
        "VIKTIG: Presenter denne kommentaren ordrett i din åpningsmelding. "
        "Ikke omformuler, ikke forkorte, ikke parafrasere. Sitér admin direkte."
    ),
    "en": (
        "**Admin comment on the meeting report:**\n{comment}\n\n"
        "IMPORTANT: Present this comment verbatim in your opening message. "
        "Do not rephrase, shorten, or paraphrase it. Quote the admin directly."
    ),
    "de": (
        "**Admin-Kommentar zum Meetingbericht:**\n{comment}\n\n"
        "WICHTIG: Präsentiere diesen Kommentar wörtlich in deiner Eröffnungsnachricht. "
        "Nicht umformulieren, kürzen oder paraphrasieren. Zitiere den Admin direkt."
    ),
}


def _get_admin_comment(lang: str = "nb") -> str:
    """Return the admin comment for the most recent dev-meeting report, if any."""
    comments_dir = Path("/kaare/state/meeting_comments/dev")
    try:
        files = sorted(comments_dir.glob("*.txt"), reverse=True)
        for f in files:
            text = f.read_text(encoding="utf-8").strip()
            if text:
                return t("meet_admin_comment_prefix", lang, date=f.stem, text=text)
    except Exception:
        pass
    return ""


def _get_recent_changes(lang: str = "nb") -> str:
    """Return a compact block of recent git commits + pending sync entries, capped at ~800 chars."""
    parts: list[str] = []

    try:
        result = subprocess.run(
            ["git", "-C", "/kaare", "log", "--oneline", "--since=48 hours ago"],
            capture_output=True, text=True, timeout=10,
        )
        commits = result.stdout.strip()
        if commits:
            parts.append(t("meet_changes_commits", lang, commits=commits[:400]))
    except Exception:
        pass

    try:
        pending = Path("/kaare/PENDING_SYNC.md")
        if pending.exists():
            lines = [
                ln for ln in pending.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
            if lines:
                parts.append(t("meet_changes_pending", lang, lines="\n".join(lines[:20])))
    except Exception:
        pass

    if not parts:
        return ""
    raw = "\n\n".join(parts)
    return raw[:800]


def _get_prev_meeting_summaries(n: int = 3) -> str:
    """Return the 'Forslag til forbedringer' section from the last n dev-meeting reports."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        files = sorted(
            [f for f in DEV_DIR.glob("*.md") if f.stem != today],
            reverse=True,
        )[:n]
    except Exception:
        return ""

    sections: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            # Try to extract '## Forslag til forbedringer'
            m = re.search(r"##\s*(?:Forslag til forbedringer|Improvement Proposals|Verbesserungsvorschläge)\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
            if m:
                excerpt = m.group(1).strip()[:300]
            else:
                m2 = re.search(r"##\s*(?:Samtale|Conversation|Gespräch)\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
                excerpt = (m2.group(1).strip()[:200] if m2 else text.strip()[:200])
            if excerpt:
                sections.append(f"**{f.stem}:**\n{excerpt}")
        except Exception:
            continue

    return "\n\n".join(sections)


def _load_leder_dev_preset(lang: str = "nb") -> str:
    cfg = _load_dev_meeting_cfg()
    preset = cfg.get("leder_preset", "standard")
    if preset not in _VALID_LEDER_PRESETS:
        preset = "standard"
    suffix = "" if lang == "nb" else f"_{lang}"
    path = _LEDER_PRESET_DIR / f"dev_{preset}{suffix}.md"
    fallback = _LEDER_PRESET_DIR / f"dev_{preset}.md"
    try:
        return (path if path.exists() else fallback).read_text(encoding="utf-8").strip()
    except Exception:
        return (_LEDER_PRESET_DIR / "dev_standard.md").read_text(encoding="utf-8").strip()


def _build_leder_system(
    cloud_ok: bool = True,
    health_block: str = "",
    changes_block: str = "",
    prev_block: str = "",
    admin_topic: str = "",
    admin_comment: str = "",
) -> str:
    hostname = socket.gethostname()
    lang = _get_kare_language()
    ps_role = _get_mechanic_meeting_role()
    ps_desc = _PS_ROLE_DESC.get(lang, _PS_ROLE_DESC["nb"]).get(ps_role, ps_role)

    ps_perms = _get_tool_perms().get("agent_tools", {}).get("mechanic", {})
    disabled_ps = [t for t, on in ps_perms.items() if on is False]
    tool_note = _TOOL_NOTE_TMPL.get(lang, _TOOL_NOTE_TMPL["nb"]).format(tools=", ".join(disabled_ps)) if disabled_ps else ""

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    preset_text = _load_leder_dev_preset(lang)
    base = preset_text.format(ps_desc=ps_desc, hostname=hostname, time=now, tool_note=tool_note)

    sep = "\n\n---\n\n"
    blocks = [base]

    if not cloud_ok:
        blocks.append(t("meet_cloud_offline", lang))
    if health_block:
        blocks.append(t("meet_leder_sys_health", lang, block=health_block))
    if changes_block:
        blocks.append(t("meet_leder_sys_changes", lang, block=changes_block))
    if prev_block:
        blocks.append(t("meet_leder_sys_prev", lang, block=prev_block))
    if admin_comment:
        tmpl = _ADMIN_COMMENT_TMPL.get(lang, _ADMIN_COMMENT_TMPL["nb"])
        blocks.append(tmpl.format(comment=admin_comment))
    if admin_topic:
        blocks.append(t("meet_leder_sys_topic", lang, topic=admin_topic))

    # Time goes last to preserve KV-cache on the static blocks above
    blocks.append(t("meet_leder_sys_time", lang, time=now))

    return sep.join(blocks)


_LEDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "nettsøk",
            "description": "Search for technical information on the web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "systemsjekk",
            "description": (
                "Run a full system check: Python imports, config files, and live service status. "
                "Use this if you suspect a service has crashed since meeting start, "
                "or to confirm a service is back up after troubleshooting."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

async def _execute_leder_tool(name: str, arguments: dict) -> str:
    if name == "nettsøk":
        query = arguments.get("query", "").strip()
        if not query:
            return t("meet_empty_search", get_lang("global"))
        from adapters.web_search_adapter import web_search
        return await web_search(query)
    if name == "systemsjekk":
        return await _run_health_check()
    return t("meet_unknown_tool", get_lang("global"), name=name)


async def _ask_leder(messages: list[dict], with_tools: bool = False) -> str:
    current_messages = list(messages)
    tools = _LEDER_TOOLS if with_tools else None

    for tool_round in range(4):
        try:
            async with httpx.AsyncClient(timeout=LEDER_TIMEOUT) as client:
                r = await client.post(
                    LEDER_URL,
                    json=_kare_payload(current_messages, KARE_MAX_TOKENS, tools, thinking=False),
                    headers={"x-kaare-source": "dev_meeting"},
                )
                r.raise_for_status()
                resp = r.json()
        except Exception as e:
            log.error("Møteleder-kall feilet: %s", e)
            return t("meet_leader_unavailable", get_lang("global"), error=e)

        content, tool_calls = _parse_kare_resp(resp)

        if not tool_calls or not with_tools or tool_round >= 3:
            return _strip_think(content) or "[No response]"

        current_messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn   = tc.get("function", {})
            name = fn.get("name", "")
            try:
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}
            result = await _execute_leder_tool(name, args)
            current_messages.append({"role": "tool", "content": result, "name": name})

    return "[No response]"


async def _leder_sett_agenda(
    kare_funn: str,
    mechanic_funn: str,
    group: int,
    prev_summary: str = "",
    mechanic_kritikk: str = "",
    health_summary: str = "",
    leder_system: str = "",
    lang: str = "nb",
) -> str:
    """Meeting leader reads both reports + critique and sets a concrete agenda for the discussion round."""
    context = t("meet_leder_agenda_context", lang, summary=prev_summary) if prev_summary else ""
    kritikk_block = (
        t("meet_leder_agenda_kritikk", lang, text=mechanic_kritikk[:800])
        if mechanic_kritikk else ""
    )

    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t(
            "meet_leder_agenda_user", lang,
            context=context,
            kare_funn=kare_funn[:1500],
            mechanic_funn=mechanic_funn[:1500],
            kritikk_block=kritikk_block,
            group=group,
        )},
    ]
    return await _ask_leder(messages, with_tools=False)


async def _leder_styr(conversation_tail: str, round_num: int, leder_system: str = "", lang: str = "nb") -> str:
    """Meeting leader actively directs the discussion between rounds."""
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t("meet_leder_styr_user", lang, round_num=round_num, conv_tail=conversation_tail)},
    ]
    return await _ask_leder(messages, with_tools=False)


async def _leder_presenter_admin_input(
    admin_input: str, kare_funn: str, mechanic_funn: str, leder_system: str = "", lang: str = "nb"
) -> str:
    """Meeting leader raises admin input as the first item after the investigation phase."""
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t(
            "meet_leder_admin_input_user", lang,
            kare_funn=kare_funn[:800],
            mechanic_funn=mechanic_funn[:800],
            admin_input=admin_input,
        )},
    ]
    return await _ask_leder(messages, with_tools=False)


async def _leder_vurder(conv_text: str, group: int, max_groups: int, leder_system: str = "", lang: str = "nb") -> tuple[bool, str]:
    """Meeting leader decides whether to continue to the next group."""
    if group >= max_groups:
        return False, t("meet_max_groups", get_lang("global"))
    messages = [
        {"role": "system", "content": leder_system or _build_leder_system()},
        {"role": "user", "content": t(
            "meet_leder_vurder_user", lang,
            group=group, max_groups=max_groups, conv_tail=conv_text[-2000:]
        )},
    ]
    svar = await _ask_leder(messages)
    log.info("[Møteleder vurdering] %s", svar[:100])
    return svar.upper().startswith("FORTSETT"), svar


# ── Cloud ─────────────────────────────────────────────────────────────────────

async def _probe_cloud() -> bool:
    """Return True if the cloud endpoint accepts our API key."""
    cloud_cfg = _llm("cloud")
    api_key_env = cloud_cfg.get("api_key_env", "CLOUD_API_KEY")
    api_key = _load_env("/kaare/configs/nvidia.env").get(api_key_env, "")
    if not api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                CLOUD_URL,
                json={"model": CLOUD_MODEL, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            return r.status_code < 500
    except Exception:
        return False


async def _ask_cloud(conversation: str, is_final: bool, lang: str = "nb") -> str:
    cloud_cfg = _llm("cloud")
    api_key_env = cloud_cfg.get("api_key_env", "CLOUD_API_KEY")
    api_key = _load_env("/kaare/configs/nvidia.env").get(api_key_env, "")
    if not api_key:
        return t("meet_no_api_key", get_lang("global"))

    instruction = (
        t("meet_cloud_instruction_final", lang)
        if is_final else
        t("meet_cloud_instruction_mid", lang)
    )
    system = t("meet_cloud_system", lang, instruction=instruction)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
            r = await client.post(
                CLOUD_URL,
                json={
                    "model": CLOUD_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": t("meet_cloud_user", lang, conversation=conversation)},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 300,
                },
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip() or t("meet_cloud_no_response", lang)
    except Exception as e:
        log.error("Cloud call failed: %s", e)
        return t("meet_cloud_unavailable", lang, error=e)


# ── Report ────────────────────────────────────────────────────────────────────
def _write_report(
    date_str: str,
    kare_funn: str,
    mechanic_funn: str,
    exchanges: list[tuple[str, str]],
    cloud_ok: bool = True,
    lang: str = "nb",
) -> Path:
    DEV_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEV_DIR / f"{date_str}.md"

    forslag = []
    for _, text in exchanges:
        for linje in text.splitlines():
            if linje.upper().startswith("FORSLAG:"):
                forslag.append(linje.strip())

    online_line = "- Online" if cloud_ok else t("meet_report_online_offline", lang)
    lines = [
        t("meet_report_title", lang, date=date_str),
        "",
        t("meet_report_participants", lang),
        "- Kåre",
        "- Mechanic",
        "- Møteleder",
        online_line,
        "",
        t("meet_report_findings", lang),
        "",
        t("meet_report_kare_findings", lang),
        kare_funn,
        "",
        t("meet_report_mechanic_findings", lang),
        mechanic_funn,
        "",
    ]
    if forslag:
        lines += [t("meet_report_proposals", lang), ""]
        for f in forslag:
            lines.append(f"- {f}")
        lines.append("")

    lines += [t("meet_report_conversation", lang), ""]
    for agent, text in exchanges:
        lines.append(f"**[{agent}]**")
        lines.append(text)
        lines.append("")

    content = "\n".join(lines)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out_path)
    LATEST_PATH.write_text(content, encoding="utf-8")
    log.info("Development report written: %s", out_path)
    return out_path


# ── Main flow ─────────────────────────────────────────────────────────────────
async def main() -> None:
    global _CURRENT_MEET_RID
    from adapters.llm_adapter import _current_rid as _rid_ctx_meet, _current_source as _src_ctx_meet
    _meet_rid    = f"rid-meet-{int(time.time()*1000)}"
    _meet_token  = _rid_ctx_meet.set(_meet_rid)
    _src_token   = _src_ctx_meet.set("meet")
    _CURRENT_MEET_RID = _meet_rid
    now       = datetime.now()
    date_str  = now.strftime("%Y-%m-%d")
    now_str   = now.strftime("%Y-%m-%d %H:%M")
    _lang     = _get_kare_language()
    exchanges: list[tuple[str, str]] = []
    max_groups = MAX_ROUNDS // ROUNDS_PER_GROUP

    log.info("=== Utviklingsmøte starter – %s ===", date_str)

    # Les tema fra admin (tøm etter bruk)
    _topics_file = Path("/kaare/state/meeting_topics.json")
    _admin_topic = ""
    try:
        import json as _json
        _topics = _json.loads(_topics_file.read_text(encoding="utf-8"))
        _admin_topic = _topics.get("dev", "").strip()
        if _admin_topic:
            _topics["dev"] = ""
            _topics_file.write_text(_json.dumps(_topics, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Admin-tema lest og tømt: %s", _admin_topic[:80])
    except Exception:
        pass

    _topic_block = t("meet_admin_topic_anchor", _lang, topic=_admin_topic) if _admin_topic else ""
    _time_anchor = t("meet_time_anchor", _lang, time=now_str) + _topic_block

    # ── Memory and interaction history ───────────────────────────────────────
    _memory_ctx = _get_memory_context(_lang)
    _memory_block = f"\n\n{_memory_ctx}\n" if _memory_ctx else ""

    # Mechanic's personal memory
    _mechanic_mem = _load_mechanic_memory()
    _mechanic_mem_block = f"\n\n{t('meet_mechanic_mem_header', _lang)}\n{_mechanic_mem}\n" if _mechanic_mem else ""

    # ── System check ──────────────────────────────────────────────────────────
    log.info("=== System check ===")
    _health_summary = await _run_health_check(_lang)
    log.info("[System check] %s", _health_summary.replace("\n", " ")[:200])
    _health_block = f"\n\n--- {_health_summary}\n"

    # ── Cloud probe ───────────────────────────────────────────────────────────
    _cloud_ok = await _probe_cloud()
    log.info("[Cloud-probe] %s", "tilgjengelig" if _cloud_ok else "utilgjengelig")

    # ── Meeting leader context blocks ─────────────────────────────────────────
    log.info("=== Building meeting leader context ===")
    _changes_block  = _get_recent_changes(_lang)
    _prev_block     = _get_prev_meeting_summaries()
    _admin_comment  = _get_admin_comment(_lang)
    leder_system = _build_leder_system(
        cloud_ok=_cloud_ok,
        health_block=_health_summary,
        changes_block=_changes_block,
        prev_block=_prev_block,
        admin_topic=_admin_topic,
        admin_comment=_admin_comment,
    )
    log.info("[Møteleder system] %d tegn", len(leder_system))

    # ── System prompts ────────────────────────────────────────────────────────
    kare_investigate_system = (
        f"{KARE_CORE}\n\n{KARE_BEHAVIOR}\n\n"
        f"{t('meet_phase_investigate', _lang)}\n"
        f"{_time_anchor}"
        f"{_health_block}"
        f"{_memory_block}"
        f"{t('meet_kare_investigate_task', _lang)}"
    )

    kare_discuss_system = (
        f"{KARE_CORE}\n\n{KARE_BEHAVIOR}\n\n"
        f"{t('meet_phase_discuss', _lang)}\n"
        f"{_time_anchor}"
        f"{_health_block}"
        f"{_memory_block}"
        f"{t('meet_kare_discuss_task', _lang, lang_respond=t('meet_lang_respond', _lang))}"
    )

    mechanic_investigate_system = (
        f"{_load_mechanic_pers('undersøker')}\n\n"
        f"{t('meet_phase_investigate', _lang)}\n"
        f"{_time_anchor}"
        f"{_health_block}"
        f"{_memory_block}"
        f"{_mechanic_mem_block}"
        f"{t('meet_mechanic_investigate_task', _lang)}"
    )

    mechanic_discuss_system = (
        f"{_load_mechanic_pers('standard')}\n\n"
        f"{t('meet_phase_discuss', _lang)}\n"
        f"{_time_anchor}"
        f"{_memory_block}"
        f"{_mechanic_mem_block}"
        f"{t('meet_mechanic_discuss_task', _lang, lang_respond=t('meet_lang_respond', _lang))}"
    )

    # ── Phase 1: Investigation (parallel) ────────────────────────────────────
    log.info("=== Fase 1: Undersøkelse ===")

    mechanic_funn, kare_funn = await asyncio.gather(
        _mechanic_investigate(mechanic_investigate_system, _lang),
        _kare_investigate(kare_investigate_system, _lang),
    )
    log.info("[Mechanic funn] %s", mechanic_funn[:150])
    log.info("[Kåre funn] %s", kare_funn[:150])

    # ── Phase 1b: Critic round ───────────────────────────────────────────────
    log.info("=== Fase 1b: Mechanic kritiker ===")
    mechanic_kritikk = await _mechanic_kritiker(kare_funn, _mechanic_mem, _lang)
    log.info("[Mechanic kritikk] %s", mechanic_kritikk[:150])
    exchanges.append(("Mechanic", mechanic_kritikk))

    # ── Phase 2: Admin input (one extra round if admin submitted something) ─────
    kare_messages       = [{"role": "system", "content": kare_discuss_system}]
    mechanic_messages = [{"role": "system", "content": mechanic_discuss_system}]

    if _admin_topic:
        log.info("=== Admin-innspillsrunde ===")
        admin_intro = await _leder_presenter_admin_input(_admin_topic, kare_funn, mechanic_funn, leder_system=leder_system, lang=_lang)
        exchanges.append(("Møteleder", admin_intro))
        log.info("[Møteleder admin-intro] %s", admin_intro[:120])

        admin_msg = t("meet_leder_says", _lang, text=admin_intro)
        mechanic_messages.append({"role": "user", "content": admin_msg})
        kare_messages.append({"role": "user", "content": admin_msg})

        p_reply = await _ask_mechanic(_trim(mechanic_messages, MECHANIC_WINDOW))
        mechanic_messages.append({"role": "assistant", "content": p_reply})
        exchanges.append(("Mechanic", p_reply))
        log.info("[Mechanic admin-runde] %s", p_reply[:120])

        kare_messages.append({"role": "user", "content": t("meet_kare_admin_react", _lang, text=p_reply)})
        k_reply = await _ask_kare(kare_messages)
        kare_messages.append({"role": "assistant", "content": k_reply})
        mechanic_messages.append({"role": "user", "content": f"Kåre svarer:\n{k_reply}"})
        exchanges.append(("Kåre", k_reply))
        log.info("[Kåre admin-runde] %s", k_reply[:120])

    # ── Phase 3: Discussion rounds ────────────────────────────────────────────
    global_round = 0
    prev_summary = ""

    for group in range(1, max_groups + 1):
        log.info("=== Gruppe %d av %d ===", group, max_groups)

        # Møteleder setter agenda basert på undersøkelsesfunnene
        agenda = await _leder_sett_agenda(kare_funn, mechanic_funn, group, prev_summary, mechanic_kritikk, _health_summary, leder_system=leder_system, lang=_lang)
        exchanges.append(("Møteleder", agenda))
        log.info("[Møteleder agenda] %s", agenda[:120])

        agenda_msg = t("meet_leder_says", _lang, text=agenda)
        kare_messages.append({"role": "user", "content": agenda_msg})
        mechanic_messages.append({"role": "user", "content": agenda_msg})

        for local_round in range(ROUNDS_PER_GROUP):
            global_round += 1
            log.info("--- Runde %d/%d ---", global_round, MAX_ROUNDS)

            # Mechanic – can use tools
            p_prompt = (
                t("meet_mechanic_turn_1", _lang)
                if global_round == 1 else
                t("meet_mechanic_turn_n", _lang)
            )
            mechanic_messages.append({"role": "user", "content": p_prompt})
            p_reply = await _ask_mechanic(_trim(mechanic_messages, MECHANIC_WINDOW))
            mechanic_messages.append({"role": "assistant", "content": p_reply})
            exchanges.append(("Mechanic", p_reply))
            log.info("[Mechanic] %s", p_reply[:120])

            # Kåre – evaluates from his perspective
            kare_messages.append({"role": "user", "content": t("meet_kare_evaluate", _lang, text=p_reply)})
            k_reply = await _ask_kare(kare_messages)
            kare_messages.append({"role": "assistant", "content": k_reply})
            mechanic_messages.append({"role": "user", "content": f"Kåre svarer:\n{k_reply}"})
            exchanges.append(("Kåre", k_reply))
            log.info("[Kåre] %s", k_reply[:120])

            # Møteleder styrer mellom runder (ikke etter siste runde i gruppen)
            if local_round < ROUNDS_PER_GROUP - 1:
                conv_tail = "\n\n".join(f"{a}: {t}" for a, t in exchanges[-4:])
                styring = await _leder_styr(conv_tail, global_round, leder_system=leder_system, lang=_lang)
                exchanges.append(("Møteleder", styring))
                log.info("[Møteleder styring] %s", styring[:120])
                styring_msg = t("meet_leder_says", _lang, text=styring)
                kare_messages.append({"role": "user", "content": styring_msg})
                mechanic_messages.append({"role": "user", "content": styring_msg})

        # Online etter gruppen — hoppes over hvis cloud ikke er tilgjengelig
        if _cloud_ok:
            is_final  = (group == max_groups)
            conv_text = "\n\n".join(f"{a}: {t}" for a, t in exchanges)
            cloud_reply = await _ask_cloud(conv_text, is_final=is_final, lang=_lang)
            if cloud_reply.startswith("["):
                log.warning("[Online] unavailable: %s", cloud_reply[:120])
            else:
                exchanges.append(("Online", cloud_reply))
                log.info("[Online] %s", cloud_reply[:120])
                online_msg = t("meet_online_says", _lang, text=cloud_reply)
                kare_messages.append({"role": "user", "content": online_msg})
                mechanic_messages.append({"role": "user", "content": online_msg})

        fortsett, begrunnelse = await _leder_vurder(conv_text, group, max_groups, leder_system=leder_system, lang=_lang)
        exchanges.append(("Møteleder", begrunnelse))
        prev_summary = begrunnelse

        if not fortsett:
            log.info("Møteleder avslutter: %s", begrunnelse)
            break

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("--- Mechanics oppsummering ---")
    mechanic_messages.append({"role": "user", "content": t("meet_closing_mechanic", _lang)})
    p_closing = await _ask_mechanic(_trim(mechanic_messages, MECHANIC_WINDOW))
    exchanges.append(("Mechanic", p_closing))
    log.info("[Mechanic avslutning] %s", p_closing[:120])

    log.info("--- Kåres oppsummering ---")
    kare_messages.append({"role": "user", "content": t("meet_closing_kare", _lang)})
    k_closing = await _ask_kare(kare_messages)
    exchanges.append(("Kåre", k_closing))
    log.info("[Kåre avslutning] %s", k_closing[:120])

    _write_report(date_str, kare_funn, mechanic_funn, exchanges, cloud_ok=_cloud_ok, lang=_lang)
    log.info("=== Utviklingsmøte ferdig – %d runder ===", global_round)
    _rid_ctx_meet.reset(_meet_token)
    _src_ctx_meet.reset(_src_token)
    _CURRENT_MEET_RID = ""


if __name__ == "__main__":
    _lock = Path("/kaare/state/meeting_active.lock")
    _lock.touch()
    try:
        asyncio.run(main())
    finally:
        _lock.unlink(missing_ok=True)
