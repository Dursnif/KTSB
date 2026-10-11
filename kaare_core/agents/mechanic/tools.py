"""
Mechanic tool engine — shared between kaare_dev_meeting and executor_agents.

Tools (LLM-facing names — do not rename, these are sent by the Mechanic LLM):
  utforsk          – read/list/search files under /kaare
  inspiser         – log reading, service status, resources, git diff/log
  nettsøk          – web search via web_search_adapter
  søk_argus        – semantic search in system log (Qdrant BGE-M3)
  shell            – read-only commands on configured SSH nodes or local
  hukommelse       – Mechanic's personal memory (les/skriv/slett_gammel)

Role-based tool sets:
  MECHANIC_TOOLS     – all tools (default / technical assistant)
  UNDERSØKER_TOOLS   – investigation focus: logs, services, code, SSH
  KRITIKER_TOOLS     – memory only (no investigation tools)
  ANALYTIKER_TOOLS   – file reading + memory (report synthesis)
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
import time as _time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

from adapters.web_search_adapter import web_search
from kaare_core.agents.mechanic import shell_guard
from kaare_core.aio_files import append_text
from kaare_core.argus_query import build_argus_filter
from kaare_core.audit import audit_log as _audit
from kaare_core.config import get_llm_config as _llm
from kaare_core.config import get_local_tz, is_agent_tool_enabled, now_local
from kaare_core.config import get_model as _cfg_model
from kaare_core.config import get_qdrant_api_key as _qdrant_key
from kaare_core.config import get_service as _svc
from kaare_core.config import get_ssh_nodes as _get_ssh_nodes
from kaare_core.config import pool_call_options as _pool_opts
from kaare_core.model_lock import LockTimeout, lock_agent_pool, pool_order, role_url
from kaare_core.ollama_tool_rescue import post_chat
from kaare_core.think_strip import strip_think_block
from kaare_core.tools.i18n import get_lang, t
from kaare_core.tools.shared_tools import (
    check_resources as _shared_check_resources,
)
from kaare_core.tools.shared_tools import (
    check_services as _shared_check_services,
)
from kaare_core.tools.shared_tools import (
    git_diff as _shared_git_diff,
)
from kaare_core.tools.shared_tools import (
    git_log as _shared_git_log,
)
from kaare_core.tools.shared_tools import (
    list_files as _shared_list_files,
)
from kaare_core.tools.shared_tools import (
    log_tool_call as _log_tool_call,
)
from kaare_core.tools.shared_tools import (
    read_file as _shared_read_file,
)
from kaare_core.tools.shared_tools import (
    read_log as _shared_read_log,
)
from kaare_core.tools.shared_tools import (
    search_code as _shared_search_code,
)
from kaare_core.tools.think_cache import log_think

log = logging.getLogger("mechanic.tools")

MECHANIC_URL = _llm("mechanic")["base_url"] + "/api/chat"
MECHANIC_MODEL = _cfg_model("miss_kare")

MAX_TOOL_ROUNDS = 10
TIMEOUT = _llm("mechanic")["timeout"]
MAX_TOKENS = _llm("mechanic")["options"]["num_predict"]
_MSG_WINDOW = 10  # keep last N tool messages verbatim; mask older ones
_ROUND_RETRIES = 1  # extra attempts for one round after an HTTP 5xx (e.g. Ollama failing to parse malformed tool-call XML)

MEMORY_PATH = Path("/kaare/state/mechanic_memory.md")
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def _fmt_ts_local(ts_raw: str) -> str:
    """Convert UTC ISO timestamp to local time for display."""
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(get_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")


# ── Tool definitions ──────────────────────────────────────────────────────────


def _build_shell_tool() -> dict:
    ssh_data = _get_ssh_nodes()
    ssh_nodes = ssh_data.get("nodes", {})
    node_ids = list(ssh_nodes.keys())
    node_enum = ["local", *node_ids]

    node_lines = " ".join(f"node='{n}': {ssh_nodes[n].get('label', n)} (SSH)." for n in node_ids)
    sudo_note = (
        "Sudo allowed per node: configured in ssh_nodes.yaml (sudo_commands list). "
        if node_ids
        else ""
    )
    desc = (
        "Run a READ-ONLY command locally or on a network node via SSH — it can never change, "
        "write or delete anything. node='local': this machine, where KTSB's own services run "
        "(kaare, kaare-argus, …; their logs: journalctl -u <service> -n 100). "
        + (node_lines + " " if node_lines else "No SSH nodes configured yet. ")
        + "Allowed: cat, head, tail, grep, find, ls, ps, df, free, uptime, journalctl, dmesg, "
        "systemctl status/show/is-active, dpkg -l/-s, apt list, docker ps/logs/inspect, ip addr/route, "
        "ss, nvidia-smi, pihole status, ha <x> info. One pipe '|' is fine; ; && $( > < are rejected. "
        "Follow mode (-f) is turned into the last 100 lines. " + sudo_note + "No sudo on local."
    )
    node_desc = "'local' = this machine — KTSB's own services run here (no sudo)." + (
        " " + ", ".join(f"'{n}'" for n in node_ids) + " = SSH nodes." if node_ids else ""
    )
    return {
        "type": "function",
        "function": {
            "name": "shell",
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "enum": node_enum,
                        "description": node_desc,
                    },
                    "kommando": {"type": "string", "description": "Shell command to run."},
                },
                "required": ["node", "kommando"],
            },
        },
    }


MECHANIC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "utforsk",
            "description": (
                "Explore the /kaare codebase. Three operations: "
                "action='les': read a file (requires 'sti'). Without fra_linje/til_linje: first 200 lines. "
                "With fra_linje and til_linje: exact block (max 300 lines). "
                "action='liste': list files and subdirectories (optional 'mappe', optional 'rekursiv'). "
                "action='søk': grep search for pattern in .py/.yaml/.md/.json/.sh (requires 'mønster', optional 'mappe')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "liste", "søk"],
                        "description": (
                            "'les' = read file (requires 'sti'). "
                            "'liste' = list files/dirs (optional 'mappe', 'rekursiv'). "
                            "'søk' = grep search (requires 'mønster', optional 'mappe')."
                        ),
                    },
                    "sti": {
                        "type": "string",
                        "description": "Absolute file path under /kaare. Only for action='les'.",
                    },
                    "fra_linje": {
                        "type": "integer",
                        "description": "First line (1-based). Only for action='les'.",
                    },
                    "til_linje": {
                        "type": "integer",
                        "description": "Last line (inclusive). Only for action='les'.",
                    },
                    "mappe": {
                        "type": "string",
                        "description": "Absolute directory path under /kaare. Used for 'liste' and 'søk'.",
                    },
                    "rekursiv": {
                        "type": "boolean",
                        "description": "List recursively (max 200 files). Only for action='liste'. Default: false.",
                    },
                    "mønster": {
                        "type": "string",
                        "description": "Search text or regex. Only for action='søk'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspiser",
            "description": (
                "Inspect system status and logs. Five operations: "
                "action='logg': read or search /kaare/logs/. Without 'fil': overview of all logs. "
                "With 'fil': tail. With 'mønster': grep search. With fra_linje/til_linje: exact block. "
                "action='tjenester': systemd status for Kåre services. "
                "Without 'tjeneste': active/inactive for all. With 'tjeneste': details + journalctl. "
                "action='ressurser': real-time CPU, RAM, disk and GPU VRAM. "
                "action='git_diff': uncommitted changes in the app's git repository "
                "(configurable via settings.yaml git_repo_path, optional 'sti'). "
                "action='git_log': commit history in that repository (optional 'sti', 'antall')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["logg", "tjenester", "ressurser", "git_diff", "git_log"],
                        "description": (
                            "'logg' = read/search log files (optional 'fil', 'linjer', 'mønster', 'maks_treff', 'fra_linje', 'til_linje'). "
                            "'tjenester' = systemd status (optional 'tjeneste', 'logglinjer'). "
                            "'ressurser' = CPU/RAM/disk/GPU real-time. "
                            "'git_diff' = uncommitted changes (optional 'sti'). "
                            "'git_log' = commit history (optional 'sti', 'antall')."
                        ),
                    },
                    "fil": {
                        "type": "string",
                        "description": "Log file name without path, e.g. 'kaare_ha_gateway.log'. Only for action='logg'.",
                    },
                    "linjer": {
                        "type": "integer",
                        "description": "Number of lines (tail). Default 20, max 200. Only for action='logg'.",
                    },
                    "mønster": {
                        "type": "string",
                        "description": "Search text/regex for grep. Only for action='logg'.",
                    },
                    "maks_treff": {
                        "type": "integer",
                        "description": "Max grep hits. Default 50, max 200. Only for action='logg'.",
                    },
                    "fra_linje": {
                        "type": "integer",
                        "description": "First line (1-based). Only for action='logg'.",
                    },
                    "til_linje": {
                        "type": "integer",
                        "description": "Last line (inclusive). Only for action='logg'.",
                    },
                    "tjeneste": {
                        "type": "string",
                        "description": "Service name for detailed view, e.g. 'kaare', 'kaare-argus'. Only for action='tjenester'.",
                    },
                    "logglinjer": {
                        "type": "integer",
                        "description": "Number of journalctl lines. Default 20, max 50. Only for action='tjenester'.",
                    },
                    "sti": {
                        "type": "string",
                        "description": "Absolute file/directory path. Used for action='git_diff'/'git_log'.",
                    },
                    "antall": {
                        "type": "integer",
                        "description": "Number of commits. Default 10, max 50. Only for action='git_log'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nettsøk",
            "description": "Search the web for technical information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query. Any language."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "søk_argus",
            "description": (
                "Semantic search in the system log via Qdrant (BGE-M3, 1024-dim). "
                "Finds semantically related events from all log sources. "
                "Use to find HA actions, errors, slowness, stalled requests. "
                "Always semantic (query required); add entity_id/since/until to precisely "
                "answer 'did X happen at time Y' instead of just 'something related to X'. "
                "Example: søk_argus(query='HA error last day')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query. Any language."},
                    "grense": {
                        "type": "integer",
                        "description": "Max results. Default: 10, max: 20.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Optional: restrict to one specific HA entity_id (e.g. 'binary_sensor.bevegelse_stue'), not a friendly name.",
                    },
                    "since": {
                        "type": "string",
                        "description": "Optional: only events from this time onward (ISO 8601, e.g. '2026-08-28T19:30:00+02:00').",
                    },
                    "until": {
                        "type": "string",
                        "description": "Optional: only events up to this time (ISO 8601).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    _build_shell_tool(),
    {
        "type": "function",
        "function": {
            "name": "hukommelse",
            "description": (
                "Mechanic's personal memory — things he has learned about the system and himself. "
                "Use 'skriv' to store new knowledge (auto-dated). "
                "Use 'les' to retrieve everything stored. "
                "Use 'slett_gammel' to clean out entries older than N days."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "skriv", "slett_gammel"],
                        "description": "les: retrieve memory. skriv: add new knowledge. slett_gammel: remove old entries.",
                    },
                    "tekst": {
                        "type": "string",
                        "description": "Text to store (only for action=skriv). Concrete and fact-based.",
                    },
                    "dager": {
                        "type": "integer",
                        "description": "Remove entries older than N days (only for action=slett_gammel). Default: 30.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


# ── Role-based tool sets ──────────────────────────────────────────────────────


def _tools_by_name(*names: str) -> list:
    return [t for t in MECHANIC_TOOLS if t["function"]["name"] in names]


UNDERSØKER_TOOLS = _tools_by_name(
    "utforsk",
    "inspiser",
    "søk_argus",
    "shell",
    "hukommelse",
)

KRITIKER_TOOLS = _tools_by_name("hukommelse")

ANALYTIKER_TOOLS = _tools_by_name("utforsk", "hukommelse")


# action enum per tool, read from the schemas above (one source — never a second hand-kept list)
_ACTION_ENUMS: dict[str, list[str]] = {
    tool["function"]["name"]: tool["function"]["parameters"]["properties"]["action"]["enum"]
    for tool in MECHANIC_TOOLS
    if "enum" in tool["function"].get("parameters", {}).get("properties", {}).get("action", {})
}
# Stray characters the 9B leaves at the end of an enum value (`søk}`, `logg>`, `les"`) — the XML
# is valid, so Ollama passes them on and the tool would answer «unknown action» (P119 B8).
_ACTION_TRAILING_JUNK = " \t\r\n>}])\"'"


def normalize_action(tool: str, value) -> str:
    """Return the tool's enum value the model meant, or the cleaned value if none matches.
    Shared by execute_tool and the dev slot's read-only pre-check so both agree."""
    cleaned = str(value or "").strip().strip("\"'").rstrip(_ACTION_TRAILING_JUNK).strip()
    enum = _ACTION_ENUMS.get(tool)
    if not enum or cleaned in enum:
        return cleaned
    for option in enum:
        if option.lower() == cleaned.lower():
            return option
    return cleaned


def _unknown_action(tool: str, action: str, lang: str) -> str:
    return t(
        "mech_unknown_action",
        lang,
        tool=tool,
        action=action,
        valid=", ".join(_ACTION_ENUMS.get(tool, [])),
    )


# ── Tool execution ────────────────────────────────────────────────────────────


async def execute_tool(name: str, arguments: dict, lang: str | None = None) -> str:
    # Tool results go back into the model's context — in the caller's language (rule 8);
    # callers without a user (the dev meeting) fall back to the household language.
    lang = lang or get_lang("global")
    if isinstance(arguments, dict) and "action" in arguments:
        arguments = {**arguments, "action": normalize_action(name, arguments["action"])}
    try:
        if name == "utforsk":
            action = arguments.get("action", "")
            if action == "les":
                return _shared_read_file(arguments, default_chunk=200, max_chunk=300)
            elif action == "liste":
                return _shared_list_files(arguments)
            elif action == "søk":
                return _shared_search_code(arguments)
            return _unknown_action("utforsk", action, lang)

        elif name == "inspiser":
            action = arguments.get("action", "")
            if action == "logg":
                return _shared_read_log(arguments)
            elif action == "tjenester":
                return _shared_check_services(arguments)
            elif action == "ressurser":
                return await asyncio.to_thread(_shared_check_resources, arguments, lang)
            elif action == "git_diff":
                return _shared_git_diff(arguments)
            elif action == "git_log":
                return _shared_git_log(arguments)
            return _unknown_action("inspiser", action, lang)

        elif name == "nettsøk":
            query = arguments.get("query", "").strip()
            if not query:
                return t("mech_empty_search", lang)
            sys.path.insert(0, "/kaare")

            return await web_search(query)

        elif name == "søk_argus":
            query = arguments.get("query", "").strip()
            grense = max(1, min(int(arguments.get("grense", 10)), 20))
            if not query:
                return t("mech_empty_search", lang)
            qdrant_url = _svc("storage", "qdrant")
            embed_url = _svc("ollama", "embed") + "/api/embed"
            payload_filter = build_argus_filter(
                arguments.get("entity_id", ""),
                arguments.get("since", ""),
                arguments.get("until", ""),
            )
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    emb_r = await client.post(
                        embed_url, json={"model": "bge-m3", "input": query}, timeout=15.0
                    )
                    emb_r.raise_for_status()
                    vector = emb_r.json()["embeddings"][0]
                    body: dict = {"query": vector, "limit": grense, "with_payload": True}
                    if payload_filter:
                        body["filter"] = payload_filter
                    r = await client.post(
                        f"{qdrant_url}/collections/argus_events/points/query",
                        json=body,
                        headers={"api-key": _qdrant_key(write=False) or ""},
                        timeout=15.0,
                    )
                    r.raise_for_status()
                    data = r.json()
            except Exception as e:
                return t("mech_argus_unavailable", lang, error=e)
            hits = data.get("result", {}).get("points", [])
            if not hits:
                return t("mech_no_log_results", lang, query=query)
            lines = [t("mech_argus_results", lang, count=len(hits), query=query)]
            for h in hits:
                f = h.get("payload", {})
                ts = _fmt_ts_local(f.get("ts", ""))
                src = f.get("source", "?")
                etype = f.get("event_type", "?")
                level = f.get("level", "info")
                msg = f.get("message", "")
                tag = "⚠" if level == "warning" else "✗" if level == "error" else " "
                lines.append(f"[{ts}]{tag} [{src}/{etype}] {msg}")
            return "\n".join(lines)

        elif name == "shell":
            # One switch: the shell toggle in Agent settings (backend-enforced here, off unless
            # enabled — also for callers that pass their own tool list, like the dev meeting).
            # Replaces the old settings.yaml-only `developer_tools` gate, which had no GUI
            # control at all (P116 B28). Mechanic may only READ (Stian 2026-10-01) — every
            # command goes through shell_guard, locally and on nodes (P116 B33).
            if not is_agent_tool_enabled("mechanic", "shell", default=False):
                return t("mech_shell_disabled", lang)
            node = arguments.get("node", "").strip()
            kommando = arguments.get("kommando", "").strip()
            if not kommando:
                return t("mech_shell_empty", lang)
            kommando, bounded = shell_guard.bound_follow(kommando)
            _ssh_nodes = (_get_ssh_nodes() or {}).get("nodes", {})
            node_cfg = _ssh_nodes.get(node, {}) if node != "local" else {}
            # Default is read-only. The one exception: a sudo command the user added for this
            # node on the Tools page — a deliberate opt-in — still without chaining/redirection.
            user_sudo = tuple(c.strip() for c in node_cfg.get("sudo_commands", []) if c.strip())
            opted_in = (
                kommando.startswith("sudo ")
                and any(kommando.startswith(s) for s in user_sudo)
                and not shell_guard.has_shell_meta(kommando)
            )
            reason = None if opted_in else shell_guard.readonly_violation(kommando)
            if reason:
                _audit("shell_rejected", "mechanic", f"{node or '?'}: {kommando[:200]} — {reason}")
                return t("mech_shell_rejected", lang, command=kommando[:80], reason=reason)
            note = (
                t("mech_shell_follow_bounded", lang, lines=shell_guard.FOLLOW_LINES) + "\n"
                if bounded
                else ""
            )

            if node == "local":
                if kommando.startswith("sudo"):
                    return t("mech_shell_no_sudo_local", lang)
                _audit("shell_command", "mechanic", f"local: {kommando[:200]}")
                result = await asyncio.to_thread(
                    subprocess.run,
                    kommando,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                out = (result.stdout + result.stderr).strip()
                return note + (out[:4000] if out else t("mech_shell_no_output", lang))

            if node not in _ssh_nodes:
                return t(
                    "mech_shell_unknown_node", lang, node=node, nodes=", ".join(_ssh_nodes) or "—"
                )
            if (
                kommando.startswith("sudo ")
                and not opted_in
                and not any(kommando.startswith(s) for s in user_sudo)
            ):
                return t(
                    "mech_shell_sudo_not_allowed",
                    lang,
                    node=node,
                    allowed=", ".join(user_sudo[:5]) or "—",
                )
            is_ha_os = node_cfg.get("node_type") == "ha_os"
            host = node_cfg.get("host", node)
            user = node_cfg.get("user", "root" if is_ha_os else "user")
            port = int(node_cfg.get("port", 2222 if is_ha_os else 22))
            ssh_key = str(node_cfg.get("ssh_key", "~/.ssh/id_ed25519")).replace(
                "~", str(Path.home())
            )
            _audit("shell_command", "mechanic", f"{node}: {kommando[:200]}")
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "ssh",
                    "-i",
                    ssh_key,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-p",
                    str(port),
                    f"{user}@{host}",
                    kommando,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = (result.stdout + result.stderr).strip()
            return note + (out[:4000] if out else t("mech_shell_no_output", lang))

        elif name == "hukommelse":
            action = arguments.get("action", "les")
            if action == "les":
                if not MEMORY_PATH.exists():
                    return t("mech_no_memory", lang)
                content = MEMORY_PATH.read_text(encoding="utf-8").strip()
                return content if content else t("mech_no_memory", lang)

            elif action == "skriv":
                tekst = arguments.get("tekst", "").strip()
                if not tekst:
                    return t("mech_empty_memory", lang)
                MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                ts = now_local().date().isoformat()
                entry = f"\n- [{ts}] {tekst}"
                await append_text(MEMORY_PATH, entry)
                return t("mech_memory_saved", lang, text=tekst[:80])

            elif action == "slett_gammel":
                dager = int(arguments.get("dager", 30))
                if not MEMORY_PATH.exists():
                    return t("mech_no_memory_del", lang)
                cutoff = now_local().date() - timedelta(days=dager)
                lines = MEMORY_PATH.read_text(encoding="utf-8").splitlines()
                kept, removed = [], 0
                for line in lines:
                    m = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", line)
                    if m:
                        try:
                            dt = date.fromisoformat(m.group(1))
                            if dt >= cutoff:
                                kept.append(line)
                            else:
                                removed += 1
                        except ValueError:
                            kept.append(line)
                    else:
                        kept.append(line)
                MEMORY_PATH.write_text("\n".join(kept), encoding="utf-8")
                return t("mech_memory_deleted", lang, count=removed, days=dager)

            return _unknown_action("hukommelse", action, lang)

        return t("mech_unknown_tool", lang, name=name)

    except Exception as e:
        log.error("Tool error %s: %s", name, e)
        return t("mech_tool_error", lang, name=name, error=e)


# ── Tool loop (used by agents-server and dev meeting) ─────────────────────────


def _partial_or_unavailable(history: list[dict], error: Exception | str, lang: str) -> str:
    """When a round fails for good, return the tool results already gathered instead of
    discarding them — the caller still gets the real data, labelled as unfinished."""
    gathered = [
        f"[{m.get('name', '?')}]\n{m['content']}"
        for m in history
        if m.get("role") == "tool" and m.get("content")
    ]
    if not gathered:
        return t("mech_mechanic_unavailable", lang, error=error)
    return (
        t("mech_partial_after_error", lang, error=error, rounds=len(gathered))
        + "\n\n"
        + "\n\n".join(gathered)
    )


async def ask_with_tools(
    messages: list[dict],
    url: str = MECHANIC_URL,
    model: str = MECHANIC_MODEL,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    timeout: float = TIMEOUT,
    max_tokens: int = MAX_TOKENS,
    job_state: dict | None = None,
    tools: list | None = None,
    rid: str = "",
    lang: str | None = None,
) -> str:
    """
    Run a tool-using conversation with Mechanic.
    messages: complete message list including system message.
    job_state: shared dict from _jobs[job_id] — checked between rounds for injected comments.
    rid: the originating request's trace id (if any) — threaded through so Mechanic's own
    tool calls land in tool_calls.log under the same rid as the Kåre request that triggered
    them, feeding trace_reader.py's per-request trace (Kåre's self-improvement view). Without
    it Mechanic's internal tool use is invisible to that trace even though the delegate/search
    call itself is logged.
    Always returns a string.
    """
    lang = lang or get_lang(
        "global"
    )  # user's language from chat callers; household language otherwise
    _url_path = urlparse(
        url
    ).path  # overridden host:port comes from whichever pool instance wins each round

    if tools is not None:
        active_tools = tools
    else:
        active_tools = [
            t
            for t in MECHANIC_TOOLS
            if is_agent_tool_enabled("mechanic", t["function"]["name"], default=True)
        ]
    offered_names = {tool["function"]["name"] for tool in active_tools}
    payload_base = {
        "model": model,
        "stream": False,
        "think": False,
        "options": _pool_opts(_llm("mechanic"), {"num_predict": max_tokens}),
        "tools": active_tools,
    }

    # Fixed messages (system + user task) are kept separate from tool history
    _fixed = list(messages)
    _history: list[dict] = []
    _seen_calls: set[str] = set()  # loop detection

    def _build_messages() -> list[dict]:
        # Observation masking (ref. arxiv 2508.21433):
        # Keep last _MSG_WINDOW messages verbatim; replace older tool output with placeholder.
        # Assistant messages (with tool_calls) are always kept — model needs to see its own choices.
        if len(_history) <= _MSG_WINDOW:
            return _fixed + _history
        cutoff = len(_history) - _MSG_WINDOW
        masked = []
        for i, msg in enumerate(_history):
            if i < cutoff and msg["role"] == "tool":
                masked.append(
                    {**msg, "content": t("mech_output_masked", lang, chars=len(msg["content"]))}
                )
            else:
                masked.append(msg)
        return _fixed + masked

    for round_num in range(max_tool_rounds + 1):
        payload = {**payload_base, "messages": _build_messages()}
        resp = None
        for attempt in range(_ROUND_RETRIES + 1):
            try:
                async with (
                    lock_agent_pool(
                        pool_order(role_url("mechanic")), "mechanic", max_wait=300
                    ) as inst_url,
                    httpx.AsyncClient(timeout=timeout) as client,
                ):
                    resp = await post_chat(
                        client,
                        inst_url + _url_path,
                        payload,
                        headers={"x-kaare-source": "mechanic"},
                        rid=rid,
                        source="mechanic",
                    )
                break
            except LockTimeout as e:
                log.error("Mechanic: %s", e)
                return t("mech_model_busy", lang)
            except httpx.HTTPStatusError as e:
                # A 5xx is often one bad sample (e.g. malformed tool-call XML Ollama can't
                # parse) — generation is not deterministic, so the same round usually
                # succeeds on a new attempt.
                if e.response.status_code >= 500 and attempt < _ROUND_RETRIES:
                    log.warning(
                        "Mechanic LLM call failed (round %d, attempt %d), retrying: %s",
                        round_num,
                        attempt + 1,
                        e,
                    )
                    continue
                log.error("Mechanic LLM call failed (round %d): %s", round_num, e)
                return _partial_or_unavailable(_history, f"HTTP {e.response.status_code}", lang)
            except Exception as e:
                log.error("Mechanic LLM call failed (round %d): %s", round_num, e)
                return _partial_or_unavailable(_history, e, lang)

        msg = resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls or round_num >= max_tool_rounds:
            raw_content = msg.get("content", "").strip()
            content, think_content, _think_closed = strip_think_block(raw_content)
            if think_content:
                try:
                    log_think(
                        think_text=think_content,
                        response=content,
                        role="mechanic",
                        model=model,
                        recovered=False,
                    )
                except Exception:
                    pass
            return content or t("mech_no_response", lang)

        _history.append(
            {
                "role": "assistant",
                "content": msg.get("content", ""),
                "tool_calls": tool_calls,
            }
        )

        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}

            # Only tools offered in this call run — a role's tool list and the admin's
            # per-tool toggles are enforced here, not left to the model (P119 B7).
            if tool_name not in offered_names:
                refusal = t(
                    "mech_tool_not_offered",
                    lang,
                    tool=tool_name or "?",
                    available=", ".join(sorted(offered_names)),
                )
                log.warning("[Mechanic] tool not offered, refused: %s", tool_name)
                _log_tool_call(tool_name, args, refusal, 0, source="mechanic", rid=rid)
                _history.append({"role": "tool", "content": refusal, "name": tool_name})
                continue

            # Loop detection: ignore identical repeated calls
            sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
            if sig in _seen_calls:
                log.warning("[Mechanic loop] Duplicate ignored: %s", sig[:80])
                _history.append(
                    {
                        "role": "tool",
                        "content": t("mech_loop_detected", lang, tool=tool_name),
                        "name": tool_name,
                    }
                )
                continue
            _seen_calls.add(sig)

            log.info("[Mechanic tool] %s(%s)", tool_name, str(args)[:80])
            _t0 = _time.monotonic()
            result = await execute_tool(tool_name, args, lang)
            _duration_ms = int((_time.monotonic() - _t0) * 1000)
            log.info("[Mechanic result] %s", result[:100])
            _log_tool_call(tool_name, args, result, _duration_ms, source="mechanic", rid=rid)
            _history.append({"role": "tool", "content": result, "name": tool_name})

        # Check for injected user comment between rounds
        if job_state is not None:
            injected = job_state.pop("injected", None)
            if injected:
                log.info("[Mechanic] injecting user comment: %s", str(injected)[:60])
                _history.append({"role": "user", "content": f"[User comment mid-task]: {injected}"})

    return t("mech_no_response", lang)
