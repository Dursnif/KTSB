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

# ── Konfigurasjon ─────────────────────────────────────────────────────────────
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


# ── Hjelpefunksjoner ──────────────────────────────────────────────────────────
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


def _get_memory_context() -> str:
    """
    Fetch recent STM daily summaries + LTM episodes for meeting context.
    Returns a formatted block, or empty string if nothing is available.
    """
    parts = []
    try:
        from kaare_core.memory.long_term import load_recent_daily_summaries
        entries = load_recent_daily_summaries(days=3)
        if entries:
            lines = []
            for e in reversed(entries):
                lines.append(f"[{e['date']}] ({e['count']} interaksjoner):\n{e['summary']}")
            parts.append("Daglige STM-sammendrag (siste 3 dager):\n" + "\n\n".join(lines))
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
                lines.append(f"Episode {i} (temaer: {topics or 'ukjent'}):\n{narrative}")
            parts.append("Siste komprimerte episoder (LTM):\n" + "\n\n".join(lines))
    except Exception as e:
        log.warning("Could not load LTM episodes: %s", e)

    try:
        if LATEST_PATH.exists():
            prev = LATEST_PATH.read_text(encoding="utf-8")
            lines = prev.splitlines()
            # Extract the proposals + last conversation turns (last 40 lines)
            excerpt = "\n".join(lines[-40:])
            parts.append(f"Gårsdagens utviklingsmøte (avslutning):\n{excerpt}")
    except Exception as e:
        log.warning("Could not load previous dev meeting: %s", e)

    if not parts:
        return ""
    return "--- MINNE OG INTERAKSJONSHISTORIKK ---\n" + "\n\n".join(parts)


async def _run_health_check() -> str:
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
        return t("meet_system_timeout", get_lang("global"))
    except Exception as e:
        return f"[Systemsjekk feilet: {e}]"

    lines = [f"SYSTEMSJEKK VED MØTESTART ({data['timestamp']}):"]

    # Services
    svc_results = data.get("services", {}).get("results", [])
    svc_up   = data.get("services", {}).get("passed", 0)
    svc_fail = data.get("services", {}).get("failed", 0)
    svc_down = [s for s in svc_results if not s["ok"]]
    if svc_down:
        lines.append(f"  ⚠ TJENESTER NEDE ({svc_fail} av {svc_up + svc_fail}):")
        for s in svc_down:
            lines.append(f"    – {s['name']}: {s['detail']}")
    else:
        lines.append(f"  ✓ Tjenester: alle {svc_up} oppe og svarer")

    # Imports
    imp_errors = data.get("imports", {}).get("errors", [])
    imp_ok     = data.get("imports", {}).get("passed", 0)
    imp_skip   = data.get("imports", {}).get("skipped", 0)
    if imp_errors:
        lines.append(f"  ⚠ IMPORT-FEIL ({len(imp_errors)}):")
        for e in imp_errors:
            lines.append(f"    – {e['name']}: {e['detail']}")
    else:
        skip_note = (
            f" ({imp_skip} hoppet over — voice-venv-moduler som chromecast/airplay/dlna "
            f"krever services/voice/venv/, ikke en feil)"
            if imp_skip else ""
        )
        lines.append(f"  ✓ Importer: {imp_ok} OK{skip_note}")

    # Configs
    cfg_errors = data.get("configs", {}).get("errors", [])
    cfg_ok     = data.get("configs", {}).get("passed", 0)
    if cfg_errors:
        lines.append(f"  ⚠ KONFIG-FEIL ({len(cfg_errors)}):")
        for e in cfg_errors:
            lines.append(f"    – {e['name']}: {e['detail']}")
    else:
        lines.append(f"  ✓ Konfig: {cfg_ok} filer OK")

    total_errors = data.get("total_errors", 0)
    if total_errors == 0:
        lines.append(t("meet_system_ok", get_lang("global")))
    else:
        lines.append(t("meet_system_errors", get_lang("global"), count=total_errors))
    return "\n".join(lines)


# ── Kåres verktøy i møtet ─────────────────────────────────────────────────────
# All Mechanic tools (except sandkasse) + Kåre's own domain tools.

_KARE_MEETING_TOOLS = _build_kare_dev_tools()


# ── Kåre-kall (møte – med verktøystøtte) ─────────────────────────────────────
async def _kare_investigate(system_prompt: str) -> str:
    """Kåre undersøker med sine verktøy og returnerer et sammendrag av funn."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            "Undersøk systemet nå. Bruk alle verktøyene dine for å finne reelle problemer "
            "og mønstre fra siste 24 timer. Start bredt: les logg-filer, søk i argus, "
            "sjekk minnet. Zoom inn på det viktigste. Bruk verktøyene – ikke gjett."
        )},
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
            log.warning("[Kåre investigate] Tom respons (thinking brukte alle tokens) — retry uten thinking")
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
                    r = await client.post(
                        KARE_URL,
                        json=_kare_payload(messages, KARE_INVEST_TOKENS, thinking=False),
                        headers={"x-kaare-source": "dev_meeting"},
                    )
                    r.raise_for_status()
                    content, _ = _parse_kare_resp(r.json())
                    return _strip_think(content) or "[Ingen funn]"
            except Exception as e:
                log.error("Kåre investigate retry feilet: %s", e)
                return "[Ingen funn]"

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
    messages.append({"role": "user", "content": "Oppsummer funnene dine. Hva er de viktigste problemene?"})
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
            r = await client.post(
                KARE_URL,
                json=_kare_payload(messages, KARE_INVEST_TOKENS, thinking=False),
                headers={"x-kaare-source": "dev_meeting"},
            )
            r.raise_for_status()
            content, _ = _parse_kare_resp(r.json())
            return _strip_think(content) or "[Ingen funn]"
    except httpx.HTTPStatusError as e:
        log.error("Kåre investigate-oppsummering feilet: %s\nBody: %s", e, e.response.text[:1000])
        return t("meet_kare_failed", get_lang("global"), error=e)
    except Exception as e:
        return t("meet_kare_failed", get_lang("global"), error=e)


async def _ask_kare(messages: list[dict]) -> str:
    """Kåre i diskusjonsfasen – med verktøystøtte (maks 2 tool-runder)."""
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
            log.warning("[Kåre discuss] Tom respons (thinking brukte alle tokens) — retry uten thinking")
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
                    r = await client.post(
                        KARE_URL,
                        json=_kare_payload(current, KARE_MAX_TOKENS, thinking=False),
                        headers={"x-kaare-source": "dev_meeting"},
                    )
                    r.raise_for_status()
                    content, _ = _parse_kare_resp(r.json())
                    return _strip_think(content) or "[Ingen respons]"
            except Exception as e:
                log.error("Kåre discuss retry feilet: %s", e)
                return "[Ingen respons]"

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
            return _strip_think(content) or "[Ingen respons]"
    except httpx.HTTPStatusError as e:
        log.error("Kåre diskusjon-kall feilet: %s\nBody: %s", e, e.response.text[:1000])
        return f"[Kåre utilgjengelig: {e}]"
    except Exception as e:
        log.error("Kåre diskusjon-kall feilet: %s", e)
        return f"[Kåre utilgjengelig: {e}]"


# ── Mechanic ───────────────────────────────────────────────────────────────
async def _mechanic_investigate(system_prompt: str) -> str:
    """Mechanic i undersøker-modus — graver i systemet med fokuserte verktøy."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            "Undersøk systemet. Bruk søk_argus, les_logg, git_log, sjekk_tjenester "
            "og sjekk_ressurser for å finne reelle problemer fra siste 24 timer. "
            "Start bredt – logger, tjenestestatus, siste kodeendringer. "
            "Kall også inspiser_system(action='trace_mønstre', antall=100, source='all') og inkluder i rapporten: "
            "antall traces per source (user/refl/meet), gjennomsnittlig latency, hyppigste tools, "
            "andel 9B-fallback per source (korrelerer de med bestemt tidspunkt?), "
            "og traces med unormalt høy latency eller recovered=True (tomme svar). "
            "Oppsummer funnene konkret til slutt. Ikke gjett. "
            "Avslutt med hukommelse(action='skriv') for å lagre 1–2 viktige tekniske observasjoner."
        )},
    ]
    return await _mechanic_ask(
        messages=messages,
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=UNDERSØKER_TOOLS,
    )


async def _mechanic_kritiker(kare_funn: str, mechanic_memory: str) -> str:
    """Mechanic i kritiker-modus — stiller kritiske spørsmål til Kåres funn."""
    pers = _load_mechanic_pers("kritiker")
    mem_block = f"\n\n--- DIN HUKOMMELSE ---\n{mechanic_memory}" if mechanic_memory else ""
    messages = [
        {"role": "system", "content": f"/no_think\n{pers}{mem_block}"},
        {"role": "user", "content": (
            f"Kåre har rapportert følgende fra sin undersøkelse:\n\n{kare_funn}\n\n"
            "Still 3–5 kritiske, konkrete spørsmål. "
            "Hva ble ikke sjekket? Hva er antatt, ikke bekreftet? Hva mangler tall? "
            "Gi ingen svar eller løsninger — bare spørsmål."
        )},
    ]
    return await _mechanic_ask(
        messages=messages,
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=KRITIKER_TOOLS,
    )


async def _ask_mechanic(messages: list[dict]) -> str:
    """Mechanic i diskusjonsfasen – kan bruke undersøkelsesverktøy."""
    return await _mechanic_ask(
        messages=_trim(messages, MECHANIC_WINDOW),
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=UNDERSØKER_TOOLS,
    )


# ── Møteleder ─────────────────────────────────────────────────────────────────
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


def _build_leder_system() -> str:
    hostname = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lang = _get_kare_language()
    ps_role = _get_mechanic_meeting_role()
    ps_desc = _PS_ROLE_DESC.get(lang, _PS_ROLE_DESC["nb"]).get(ps_role, ps_role)

    ps_perms = _get_tool_perms().get("agent_tools", {}).get("mechanic", {})
    disabled_ps = [t for t, on in ps_perms.items() if on is False]
    tool_note = _TOOL_NOTE_TMPL.get(lang, _TOOL_NOTE_TMPL["nb"]).format(tools=", ".join(disabled_ps)) if disabled_ps else ""

    preset_text = _load_leder_dev_preset(lang)
    return preset_text.format(ps_desc=ps_desc, hostname=hostname, time=now, tool_note=tool_note)


_LEDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "nettsøk",
            "description": "Søk etter teknisk informasjon på nettet.",
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
                "Kjør en komplett systemsjekk: Python-importer, konfig-filer og live tjenestestatus. "
                "Bruk dette hvis du mistenker at en tjeneste har krasjet siden møtestart, "
                "eller for å bekrefte at en tjeneste er oppe igjen etter feilsøking."
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
            return _strip_think(content) or "[Ingen respons]"

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

    return "[Ingen respons]"


async def _leder_sett_agenda(
    kare_funn: str,
    mechanic_funn: str,
    group: int,
    prev_summary: str = "",
    mechanic_kritikk: str = "",
    health_summary: str = "",
) -> str:
    """Møteleder leser begge rapporter + kritikk og setter konkret agenda for diskusjonsrunden."""
    kontekst = ""
    if prev_summary:
        kontekst = f"Forrige gruppe oppsummert: {prev_summary}\n\n"

    kritikk_block = (
        f"**Mechanics kritiske spørsmål til Kåres funn:**\n{mechanic_kritikk[:800]}\n\n"
        if mechanic_kritikk else ""
    )

    health_block = (
        f"**Systemstatus ved møtestart:**\n{health_summary}\n\n"
        if health_summary else ""
    )

    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"{health_block}"
            f"{kontekst}"
            f"Undersøkelsesfasen er ferdig. Her er funnene:\n\n"
            f"**Kåres funn (interaksjoner og systemhendelser):**\n{kare_funn[:1500]}\n\n"
            f"**Mechanics funn (kode, logger, tjenester):**\n{mechanic_funn[:1500]}\n\n"
            f"{kritikk_block}"
            f"Åpne gruppe {group} av møtet. Velg 1-2 konkrete temaer å fokusere på basert på funnene. "
            f"Vær spesifikk: pek på filer, tjenester eller mønstre de skal gå dypere inn i. "
            f"Be Mechanic starte undersøkelsen."
        )},
    ]
    return await _ask_leder(messages, with_tools=False)


async def _leder_styr(conversation_tail: str, round_num: int) -> str:
    """Møteleder styrer diskusjonen aktivt mellom runder."""
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Runde {round_num} er ferdig. Siste del av diskusjonen:\n{conversation_tail}\n\n"
            "Er de på rett spor? Styr dem: be om mer dybde, bytt fokus, eller stopp et sidespor. "
            "Maks 2 setninger. Gi klar retning for neste runde."
        )},
    ]
    return await _ask_leder(messages, with_tools=False)


async def _leder_presenter_admin_input(admin_input: str, kare_funn: str, mechanic_funn: str) -> str:
    """Møteleder løfter admin-innspill som første sak etter undersøkelsesfasen."""
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Undersøkelsesfasen er ferdig. Funnene er:\n\n"
            f"Kåre: {kare_funn[:800]}\n\nMechanic: {mechanic_funn[:800]}\n\n"
            f"Admin har sendt inn dette innspillet til møtet:\n\"{admin_input}\"\n\n"
            "Åpne møtet med å presentere admin sitt innspill. Koble det gjerne til funnene hvis relevant. "
            "Be Mechanic starte med sin vurdering. Maks 3 setninger."
        )},
    ]
    return await _ask_leder(messages, with_tools=False)


async def _leder_vurder(conv_text: str, group: int, max_groups: int) -> tuple[bool, str]:
    """Møteleder avgjør om vi fortsetter til neste gruppe."""
    if group >= max_groups:
        return False, t("meet_max_groups", get_lang("global"))
    messages = [
        {"role": "system", "content": _build_leder_system()},
        {"role": "user", "content": (
            f"Gruppe {group} av {max_groups} er ferdig.\n\n"
            f"Samtalen:\n{conv_text[-2000:]}\n\n"
            "Svar KUN med: 'FORTSETT: <begrunnelse>' eller 'AVSLUTT: <begrunnelse>'."
        )},
    ]
    svar = await _ask_leder(messages)
    log.info("[Møteleder vurdering] %s", svar[:100])
    return svar.upper().startswith("FORTSETT"), svar


# ── Cloud ─────────────────────────────────────────────────────────────────────
async def _ask_cloud(conversation: str, is_final: bool) -> str:
    env     = _load_env("/kaare/configs/nvidia.env")
    api_key = env.get("NVIDIA_API_KEY", "")
    if not api_key:
        return t("meet_no_api_key", get_lang("global"))

    instruction = (
        "Gi en avsluttende vurdering av forslagene. Hvilke er mest verdifulle? Er det noe de gikk glipp av? Maks 5 setninger."
        if is_final else
        "Gi ett konkret teknisk innspill – noe de har oversett eller en bedre løsning. Maks 3 setninger."
    )
    system = (
        "Du er en ekstern teknisk ekspert som kommenterer et utviklingsmøte mellom "
        f"Kåre (hjemme-AI) og Mechanic (teknisk problemløser). {instruction} "
        "Vær direkte. Ikke presenter deg selv."
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
            return r.json()["choices"][0]["message"]["content"].strip() or "[Cloud svarte ikke]"
    except Exception as e:
        log.error("Cloud-kall feilet: %s", e)
        return f"[Cloud utilgjengelig: {e}]"


# ── Rapport ───────────────────────────────────────────────────────────────────
def _write_report(
    date_str: str,
    kare_funn: str,
    mechanic_funn: str,
    exchanges: list[tuple[str, str]],
) -> Path:
    DEV_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEV_DIR / f"{date_str}.md"

    forslag = []
    for _, text in exchanges:
        for linje in text.splitlines():
            if linje.upper().startswith("FORSLAG:"):
                forslag.append(linje.strip())

    lines = [
        f"# Utviklingsmøte – {date_str}",
        "",
        "## Deltakere",
        "- Kåre",
        "- Mechanic",
        "- Møteleder",
        "- Online",
        "",
        "## Undersøkelsesfunn",
        "",
        "### Kåres funn",
        kare_funn,
        "",
        "### Mechanics funn",
        mechanic_funn,
        "",
    ]
    if forslag:
        lines += ["## Forslag til forbedringer", ""]
        for f in forslag:
            lines.append(f"- {f}")
        lines.append("")

    lines += ["## Samtale", ""]
    for agent, text in exchanges:
        lines.append(f"**[{agent}]**")
        lines.append(text)
        lines.append("")

    content = "\n".join(lines)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out_path)
    LATEST_PATH.write_text(content, encoding="utf-8")
    log.info("Utviklingsrapport skrevet: %s", out_path)
    return out_path


# ── Hovedflyt ─────────────────────────────────────────────────────────────────
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

    _topic_block = f"\nADMIN HAR FORESLÅTT TEMA FOR DETTE MØTET: {_admin_topic}\nDette skal prioriteres i undersøkelses- og diskusjonsfasen.\n" if _admin_topic else ""

    _time_anchor = (
        f"NÅVÆRENDE TIDSPUNKT: {now_str}.\n"
        "Fokuser KUN på hendelser fra de siste 24 timene. "
        "Ignorer feil og hendelser som er eldre enn det – de er ikke relevante for dette møtet.\n"
        f"{_topic_block}"
    )

    # ── Minne og interaksjonshistorikk ────────────────────────────────────────
    _memory_ctx = _get_memory_context()
    _memory_block = f"\n\n{_memory_ctx}\n" if _memory_ctx else ""

    # Mechanics personlige minne
    _mechanic_mem = _load_mechanic_memory()
    _mechanic_mem_block = f"\n\n--- MECHANICS HUKOMMELSE ---\n{_mechanic_mem}\n" if _mechanic_mem else ""

    # ── Systemsjekk ───────────────────────────────────────────────────────────
    log.info("=== Systemsjekk ===")
    _health_summary = await _run_health_check()
    log.info("[Systemsjekk] %s", _health_summary.replace("\n", " ")[:200])
    _health_block = f"\n\n--- {_health_summary}\n"

    # ── System-prompts ────────────────────────────────────────────────────────
    kare_investigate_system = (
        f"{KARE_CORE}\n\n{KARE_BEHAVIOR}\n\n"
        "--- UNDERSØKELSESFASE: UTVIKLINGSMØTE ---\n"
        f"{_time_anchor}"
        f"{_health_block}"
        f"{_memory_block}"
        "Din oppgave: finn reelle problemer i systemet ved å søke i argus og minne.\n"
        "Se etter: feil, mønstre som gjentar seg, ting du ikke klarer å svare på, "
        "interaksjoner med dårlig utfall. Bruk verktøyene – ikke gjett.\n"
        "Oppsummer funnene konkret og ærlig."
    )

    kare_discuss_system = (
        f"{KARE_CORE}\n\n{KARE_BEHAVIOR}\n\n"
        "--- DISKUSJONSFASE: UTVIKLINGSMØTE ---\n"
        f"{_time_anchor}"
        f"{_health_block}"
        f"{_memory_block}"
        "Du er i et teknisk møte med Mechanic. Evaluer forslagene kritisk fra ditt perspektiv.\n"
        "Du kjenner systemet fra innsiden – hva stemmer, hva mangler, hva er viktigst?\n"
        "Vær direkte. Svar alltid på norsk. Maks 5 setninger per innlegg."
    )

    mechanic_investigate_system = (
        f"{_load_mechanic_pers('undersøker')}\n\n"
        "--- UNDERSØKELSESFASE: UTVIKLINGSMØTE ---\n"
        f"{_time_anchor}"
        f"{_health_block}"
        f"{_memory_block}"
        f"{_mechanic_mem_block}"
        "Din oppgave: kartlegg systemets nåværende tilstand med verktøyene dine.\n"
        "Bruk søk_argus, les_logg, git_log, sjekk_tjenester og sjekk_ressurser.\n"
        "Let etter: feil i logger, ustabile tjenester, siste kodeendringer, ressurspress.\n"
        "Bruk verktøyene – ikke gjett. Oppsummer funnene konkret."
    )

    mechanic_discuss_system = (
        f"{_load_mechanic_pers('standard')}\n\n"
        "--- DISKUSJONSFASE: UTVIKLINGSMØTE ---\n"
        f"{_time_anchor}"
        f"{_memory_block}"
        f"{_mechanic_mem_block}"
        "Du er i teknisk møte med Kåre. Agenda er satt av Møteleder basert på reelle funn.\n"
        "Bruk verktøyene dine hvis du trenger mer data. Merk forslag med 'FORSLAG: ...'.\n"
        "Maks 5 setninger per innlegg. Svar alltid på norsk."
    )

    # ── Fase 1: Undersøkelse (parallelt) ─────────────────────────────────────
    log.info("=== Fase 1: Undersøkelse ===")

    mechanic_funn, kare_funn = await asyncio.gather(
        _mechanic_investigate(mechanic_investigate_system),
        _kare_investigate(kare_investigate_system),
    )
    log.info("[Mechanic funn] %s", mechanic_funn[:150])
    log.info("[Kåre funn] %s", kare_funn[:150])

    # ── Fase 1b: Kritiker-runde ───────────────────────────────────────────────
    log.info("=== Fase 1b: Mechanic kritiker ===")
    mechanic_kritikk = await _mechanic_kritiker(kare_funn, _mechanic_mem)
    log.info("[Mechanic kritikk] %s", mechanic_kritikk[:150])
    exchanges.append(("Mechanic", mechanic_kritikk))

    # ── Fase 2: Admin-innspill (én ekstra runde hvis admin har sendt noe) ────────
    kare_messages       = [{"role": "system", "content": kare_discuss_system}]
    mechanic_messages = [{"role": "system", "content": mechanic_discuss_system}]

    if _admin_topic:
        log.info("=== Admin-innspillsrunde ===")
        admin_intro = await _leder_presenter_admin_input(_admin_topic, kare_funn, mechanic_funn)
        exchanges.append(("Møteleder", admin_intro))
        log.info("[Møteleder admin-intro] %s", admin_intro[:120])

        admin_msg = f"Møteleder sier: {admin_intro}"
        mechanic_messages.append({"role": "user", "content": admin_msg})
        kare_messages.append({"role": "user", "content": admin_msg})

        p_reply = await _ask_mechanic(_trim(mechanic_messages, MECHANIC_WINDOW))
        mechanic_messages.append({"role": "assistant", "content": p_reply})
        exchanges.append(("Mechanic", p_reply))
        log.info("[Mechanic admin-runde] %s", p_reply[:120])

        kare_messages.append({"role": "user", "content": f"Mechanic sier:\n{p_reply}\n\nHva tenker du?"})
        k_reply = await _ask_kare(kare_messages)
        kare_messages.append({"role": "assistant", "content": k_reply})
        mechanic_messages.append({"role": "user", "content": f"Kåre svarer:\n{k_reply}"})
        exchanges.append(("Kåre", k_reply))
        log.info("[Kåre admin-runde] %s", k_reply[:120])

    # ── Fase 3: Diskusjonsrunder ──────────────────────────────────────────────
    global_round = 0
    prev_summary = ""

    for group in range(1, max_groups + 1):
        log.info("=== Gruppe %d av %d ===", group, max_groups)

        # Møteleder setter agenda basert på undersøkelsesfunnene
        agenda = await _leder_sett_agenda(kare_funn, mechanic_funn, group, prev_summary, mechanic_kritikk, _health_summary)
        exchanges.append(("Møteleder", agenda))
        log.info("[Møteleder agenda] %s", agenda[:120])

        agenda_msg = f"Møteleder sier: {agenda}"
        kare_messages.append({"role": "user", "content": agenda_msg})
        mechanic_messages.append({"role": "user", "content": agenda_msg})

        for local_round in range(ROUNDS_PER_GROUP):
            global_round += 1
            log.info("--- Runde %d/%d ---", global_round, MAX_ROUNDS)

            # Mechanic – kan bruke verktøy
            p_prompt = (
                "Din tur – undersøk det Møteleder pekte på med verktøyene dine."
                if global_round == 1 else
                "Din tur – grav dypere eller kom med konkrete forslag. Bruk verktøy ved behov."
            )
            mechanic_messages.append({"role": "user", "content": p_prompt})
            p_reply = await _ask_mechanic(_trim(mechanic_messages, MECHANIC_WINDOW))
            mechanic_messages.append({"role": "assistant", "content": p_reply})
            exchanges.append(("Mechanic", p_reply))
            log.info("[Mechanic] %s", p_reply[:120])

            # Kåre – evaluerer fra sitt perspektiv
            kare_messages.append({"role": "user", "content": f"Mechanic sier:\n{p_reply}\n\nEvaluer dette fra ditt perspektiv."})
            k_reply = await _ask_kare(kare_messages)
            kare_messages.append({"role": "assistant", "content": k_reply})
            mechanic_messages.append({"role": "user", "content": f"Kåre svarer:\n{k_reply}"})
            exchanges.append(("Kåre", k_reply))
            log.info("[Kåre] %s", k_reply[:120])

            # Møteleder styrer mellom runder (ikke etter siste runde i gruppen)
            if local_round < ROUNDS_PER_GROUP - 1:
                conv_tail = "\n\n".join(f"{a}: {t}" for a, t in exchanges[-4:])
                styring = await _leder_styr(conv_tail, global_round)
                exchanges.append(("Møteleder", styring))
                log.info("[Møteleder styring] %s", styring[:120])
                styring_msg = f"Møteleder sier: {styring}"
                kare_messages.append({"role": "user", "content": styring_msg})
                mechanic_messages.append({"role": "user", "content": styring_msg})

        # Online etter gruppen
        is_final  = (group == max_groups)
        conv_text = "\n\n".join(f"{a}: {t}" for a, t in exchanges)
        cloud_reply = await _ask_cloud(conv_text, is_final=is_final)
        exchanges.append(("Online", cloud_reply))
        log.info("[Online] %s", cloud_reply[:120])

        online_msg = f"Online sier: {cloud_reply}"
        kare_messages.append({"role": "user", "content": online_msg})
        mechanic_messages.append({"role": "user", "content": online_msg})

        fortsett, begrunnelse = await _leder_vurder(conv_text, group, max_groups)
        exchanges.append(("Møteleder", begrunnelse))
        prev_summary = begrunnelse

        if not fortsett:
            log.info("Møteleder avslutter: %s", begrunnelse)
            break

    # ── Oppsummering ──────────────────────────────────────────────────────────
    log.info("--- Mechanics oppsummering ---")
    mechanic_messages.append({"role": "user", "content": (
        "Møtet er over. Gi en punktliste med alle dine konkrete forslag – ett per linje, "
        "hvert merket med 'FORSLAG: ...'. Inkluder kun forslag du faktisk har undersøkt og bevist."
    )})
    p_closing = await _ask_mechanic(_trim(mechanic_messages, MECHANIC_WINDOW))
    exchanges.append(("Mechanic", p_closing))
    log.info("[Mechanic avslutning] %s", p_closing[:120])

    log.info("--- Kåres oppsummering ---")
    kare_messages.append({"role": "user", "content": (
        "Møtet er over. Prioriter Mechanics forslag – hva er viktigst å ta videre til brukeren? "
        "Maks 5 setninger."
    )})
    k_closing = await _ask_kare(kare_messages)
    exchanges.append(("Kåre", k_closing))
    log.info("[Kåre avslutning] %s", k_closing[:120])

    _write_report(date_str, kare_funn, mechanic_funn, exchanges)
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
