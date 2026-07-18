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

import json
import logging
import re
import subprocess
import sys
import yaml
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from kaare_core.model_lock import lock_11445, LockTimeout
from kaare_core.tools.i18n import t, get_lang
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, get_service as _svc, is_agent_tool_enabled, get_ssh_nodes as _get_ssh_nodes, get_qdrant_api_key as _qdrant_key
from kaare_core.tools.shared_tools import (
    read_file as _shared_read_file,
    list_files as _shared_list_files,
    search_code as _shared_search_code,
    read_log as _shared_read_log,
    check_services as _shared_check_services,
    check_resources as _shared_check_resources,
    git_diff as _shared_git_diff,
    git_log as _shared_git_log,
)

log = logging.getLogger("mechanic.tools")

MECHANIC_URL   = _llm("mechanic")["base_url"] + "/api/chat"
MECHANIC_MODEL = _cfg_model("miss_kare")

MAX_TOOL_ROUNDS = 10
TIMEOUT         = _llm("mechanic")["timeout"]
MAX_TOKENS      = _llm("mechanic")["options"]["num_predict"]
NUM_CTX         = _llm("mechanic")["options"]["num_ctx"]
_MSG_WINDOW     = 10  # keep last N tool messages verbatim; mask older ones

MEMORY_PATH   = Path("/kaare/state/mechanic_memory.md")
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def _fmt_ts_local(ts_raw: str) -> str:
    """Convert UTC ISO timestamp to local time for display."""
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text())
        loc = cfg.get("location") or cfg.get("lokasjon", {})
        return dt.astimezone(ZoneInfo(loc.get("timezone", "Europe/Oslo"))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")

# ── Tool definitions ──────────────────────────────────────────────────────────

def _build_shell_tool() -> dict:
    ssh_data  = _get_ssh_nodes()
    ssh_nodes = ssh_data.get("nodes", {})
    node_ids  = list(ssh_nodes.keys())
    node_enum = ["local"] + node_ids

    node_lines = " ".join(
        f"node='{n}': {ssh_nodes[n].get('label', n)} (SSH)."
        for n in node_ids
    )
    sudo_note = (
        "Sudo allowed per node: configured in ssh_nodes.yaml (sudo_commands list). "
        if node_ids else ""
    )
    desc = (
        "Run a read-only command locally on the AI-pc or on a network node via SSH. "
        "node='local': AI-pc (no sudo). "
        + (node_lines + " " if node_lines else "No SSH nodes configured yet. ")
        + "Read-only: cat, head, tail, grep, find, ls, ps, df, free, uptime, journalctl, "
        "systemctl status/list, dpkg, apt list, docker ps/logs, ip, ss, nvidia-smi, pihole status. "
        + sudo_note
        + "No sudo on local."
    )
    node_desc = (
        "'local' = this machine (no sudo)."
        + (" " + ", ".join(f"'{n}'" for n in node_ids) + " = SSH nodes." if node_ids else "")
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
                    "sti": {"type": "string", "description": "Absolute file path under /kaare. Only for action='les'."},
                    "fra_linje": {"type": "integer", "description": "First line (1-based). Only for action='les'."},
                    "til_linje": {"type": "integer", "description": "Last line (inclusive). Only for action='les'."},
                    "mappe": {"type": "string", "description": "Absolute directory path under /kaare. Used for 'liste' and 'søk'."},
                    "rekursiv": {"type": "boolean", "description": "List recursively (max 200 files). Only for action='liste'. Default: false."},
                    "mønster": {"type": "string", "description": "Search text or regex. Only for action='søk'."},
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
                "action='git_diff': uncommitted changes (optional 'sti'). "
                "action='git_log': commit history (optional 'sti', 'antall')."
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
                    "fil": {"type": "string", "description": "Log file name without path, e.g. 'kaare_ha_gateway.log'. Only for action='logg'."},
                    "linjer": {"type": "integer", "description": "Number of lines (tail). Default 20, max 200. Only for action='logg'."},
                    "mønster": {"type": "string", "description": "Search text/regex for grep. Only for action='logg'."},
                    "maks_treff": {"type": "integer", "description": "Max grep hits. Default 50, max 200. Only for action='logg'."},
                    "fra_linje": {"type": "integer", "description": "First line (1-based). Only for action='logg'."},
                    "til_linje": {"type": "integer", "description": "Last line (inclusive). Only for action='logg'."},
                    "tjeneste": {"type": "string", "description": "Service name for detailed view, e.g. 'kaare', 'kaare-agents'. Only for action='tjenester'."},
                    "logglinjer": {"type": "integer", "description": "Number of journalctl lines. Default 20, max 50. Only for action='tjenester'."},
                    "sti": {"type": "string", "description": "Absolute file/directory path. Used for action='git_diff'/'git_log'."},
                    "antall": {"type": "integer", "description": "Number of commits. Default 10, max 50. Only for action='git_log'."},
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
                "Example: søk_argus(query='HA error last day')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query. Any language."},
                    "grense": {"type": "integer", "description": "Max results. Default: 10, max: 20."},
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
                    "tekst": {"type": "string", "description": "Text to store (only for action=skriv). Concrete and fact-based."},
                    "dager": {"type": "integer", "description": "Remove entries older than N days (only for action=slett_gammel). Default: 30."},
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
    "utforsk", "inspiser", "søk_argus", "shell", "hukommelse",
)

KRITIKER_TOOLS = _tools_by_name("hukommelse")

ANALYTIKER_TOOLS = _tools_by_name("utforsk", "hukommelse")


# ── Tool execution ────────────────────────────────────────────────────────────

async def execute_tool(name: str, arguments: dict) -> str:
    try:
        if name == "utforsk":
            action = arguments.get("action", "")
            if action == "les":
                return _shared_read_file(arguments, default_chunk=200, max_chunk=300)
            elif action == "liste":
                return _shared_list_files(arguments)
            elif action == "søk":
                return _shared_search_code(arguments)
            return t("mech_unknown_action", get_lang("global"), tool="utforsk", action=action)

        elif name == "inspiser":
            action = arguments.get("action", "")
            if action == "logg":
                return _shared_read_log(arguments)
            elif action == "tjenester":
                return _shared_check_services(arguments)
            elif action == "ressurser":
                return _shared_check_resources(arguments)
            elif action == "git_diff":
                return _shared_git_diff(arguments)
            elif action == "git_log":
                return _shared_git_log(arguments)
            return t("mech_unknown_action", get_lang("global"), tool="inspiser", action=action)

        elif name == "nettsøk":
            query = arguments.get("query", "").strip()
            if not query:
                return t("mech_empty_search", get_lang("global"))
            sys.path.insert(0, "/kaare")
            from adapters.web_search_adapter import web_search
            return await web_search(query)

        elif name == "søk_argus":
            query  = arguments.get("query", "").strip()
            grense = max(1, min(int(arguments.get("grense", 10)), 20))
            if not query:
                return t("mech_empty_search", get_lang("global"))
            qdrant_url = _svc("storage", "qdrant")
            embed_url  = _svc("ollama", "embed") + "/api/embed"
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    emb_r = await client.post(embed_url, json={"model": "bge-m3", "input": query}, timeout=15.0)
                    emb_r.raise_for_status()
                    vector = emb_r.json()["embeddings"][0]
                    r = await client.post(
                        f"{qdrant_url}/collections/argus_events/points/query",
                        json={"query": vector, "limit": grense, "with_payload": True},
                        headers={"api-key": _qdrant_key(write=False) or ""},
                        timeout=15.0,
                    )
                    r.raise_for_status()
                    data = r.json()
            except Exception as e:
                return t("mech_argus_unavailable", get_lang("global"), error=e)
            hits = data.get("result", {}).get("points", [])
            if not hits:
                return t("mech_no_log_results", get_lang("global"), query=query)
            lines = [t("mech_argus_results", get_lang("global"), count=len(hits), query=query)]
            for h in hits:
                f     = h.get("payload", {})
                ts    = _fmt_ts_local(f.get("ts", ""))
                src   = f.get("source", "?")
                etype = f.get("event_type", "?")
                level = f.get("level", "info")
                msg   = f.get("message", "")
                tag   = "⚠" if level == "warning" else "✗" if level == "error" else " "
                lines.append(f"[{ts}]{tag} [{src}/{etype}] {msg}")
            return "\n".join(lines)

        elif name == "shell":
            # Shell commands require developer_tools: true in settings.yaml.
            # This mirrors Frigate's "protected mode" — off by default, user opts in knowingly.
            try:
                _dev_cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
                _dev_tools_enabled = bool(_dev_cfg.get("developer_tools", False))
            except Exception:
                _dev_tools_enabled = False
            if not _dev_tools_enabled:
                return (
                    "[Developer tools are disabled. Shell commands are not available. "
                    "An admin can enable this under Settings → Security in the GUI.]"
                )
            node     = arguments.get("node", "").strip()
            kommando = arguments.get("kommando", "").strip()
            if not kommando:
                return "[Empty command]"
            _ALLOWED_READ = (
                "cat ", "head ", "tail ", "grep ", "find ", "stat ", "file ", "wc ", "diff ",
                "ls", "pwd", "du ", "which ", "uptime", "hostname", "uname", "date",
                "whoami", "id", "who", "w ", "last", "env", "printenv", "echo ",
                "ps", "top -bn", "free", "df", "lsof", "lsblk", "lscpu", "lsusb", "lspci",
                "ip ", "ss", "netstat", "ping ", "ifconfig",
                "systemctl status", "systemctl list-units", "systemctl list-timers",
                "systemctl is-active", "systemctl is-enabled",
                "journalctl", "dmesg",
                "dpkg -l", "dpkg -s", "dpkg -L", "apt list", "apt-cache ",
                "docker ps", "docker logs", "docker stats", "docker inspect",
                "nvidia-smi", "pihole status", "pihole -v", "pihole -c",
            )
            _BLOCKED = ("rm ", "mv ", "chmod ", "chown ", "dd ", "mkfs",
                        ">", ">>", "tee ", "sed -i", "awk -i", "truncate",
                        "apt install", "apt remove", "apt-get install",
                        "pip install", "npm install",
                        "systemctl start ", "systemctl stop ", "systemctl restart ",
                        "systemctl enable ", "systemctl disable ")

            if node == "local":
                _BLOCKED_LOCAL = _BLOCKED + ("sudo", "reboot", "shutdown")
                if any(b in kommando for b in _BLOCKED_LOCAL):
                    return f"[Rejected: '{kommando[:60]}' contains sudo or a destructive operation. No sudo on local.]"
                if not any(kommando.startswith(a) for a in _ALLOWED_READ):
                    return f"[Rejected: '{kommando[:50]}' is not a recognised read-only command.]"
                result = subprocess.run(
                    kommando, shell=True, capture_output=True, text=True, timeout=30,
                )
                out = (result.stdout + result.stderr).strip()
                return out[:4000] if out else "[No output]"

            _ssh_cfg  = _get_ssh_nodes()
            _ssh_nodes = _ssh_cfg.get("nodes", {})
            _VALID_SSH = tuple(_ssh_nodes.keys())
            if node not in _VALID_SSH:
                return f"[Unknown node '{node}'. Configured nodes: {', '.join(_VALID_SSH) or 'none (add nodes in Settings → Tools)'}]"
            node_cfg = _ssh_nodes[node]
            if kommando.startswith("sudo "):
                allowed_sudo = tuple(c.strip() for c in node_cfg.get("sudo_commands", []) if c.strip())
                if not allowed_sudo:
                    return f"[Rejected: sudo not allowed on '{node}' — no sudo_commands configured in ssh_nodes.yaml.]"
                if not any(kommando.startswith(s) for s in allowed_sudo):
                    return (
                        f"[Rejected: sudo command '{kommando[:60]}' not in sudo_commands for '{node}'. "
                        f"Allowed: {', '.join(allowed_sudo[:5])}]"
                    )
            else:
                if any(b in kommando for b in _BLOCKED):
                    return f"[Rejected: '{kommando[:60]}' contains a write/destructive operation.]"
                if not any(kommando.startswith(a) for a in _ALLOWED_READ):
                    return f"[Rejected: '{kommando[:50]}' is not a recognised read-only command.]"
            _SLOW = ("sudo apt upgrade", "sudo apt dist-upgrade")
            ssh_timeout = 300 if any(kommando.startswith(s) for s in _SLOW) else 30
            is_ha_os  = node_cfg.get("node_type") == "ha_os"
            host      = node_cfg.get("host", node)
            user      = node_cfg.get("user", "root" if is_ha_os else "user")
            port      = int(node_cfg.get("port", 2222 if is_ha_os else 22))
            ssh_key   = str(node_cfg.get("ssh_key", "~/.ssh/id_ed25519")).replace("~", str(Path.home()))
            result = subprocess.run(
                [
                    "ssh",
                    "-i", ssh_key,
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=10",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-p", str(port),
                    f"{user}@{host}",
                    kommando,
                ],
                capture_output=True, text=True, timeout=ssh_timeout,
            )
            out = (result.stdout + result.stderr).strip()
            return out[:4000] if out else "[No output]"

        elif name == "hukommelse":
            action = arguments.get("action", "les")
            if action == "les":
                if not MEMORY_PATH.exists():
                    return t("mech_no_memory", get_lang("global"))
                content = MEMORY_PATH.read_text(encoding="utf-8").strip()
                return content if content else t("mech_no_memory", get_lang("global"))

            elif action == "skriv":
                tekst = arguments.get("tekst", "").strip()
                if not tekst:
                    return t("mech_empty_memory", get_lang("global"))
                MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                ts = date.today().isoformat()
                entry = f"\n- [{ts}] {tekst}"
                with open(MEMORY_PATH, "a", encoding="utf-8") as f:
                    f.write(entry)
                return t("mech_memory_saved", get_lang("global"), text=tekst[:80])

            elif action == "slett_gammel":
                dager = int(arguments.get("dager", 30))
                if not MEMORY_PATH.exists():
                    return t("mech_no_memory_del", get_lang("global"))
                cutoff = date.today() - timedelta(days=dager)
                lines = MEMORY_PATH.read_text(encoding="utf-8").splitlines()
                kept, removed = [], 0
                for line in lines:
                    m = re.search(r'\[(\d{4}-\d{2}-\d{2})\]', line)
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
                return t("mech_memory_deleted", get_lang("global"), count=removed, days=dager)

            return t("mech_unknown_action", get_lang("global"), tool="hukommelse", action=action)

        return t("mech_unknown_tool", get_lang("global"), name=name)

    except Exception as e:
        log.error("Tool error %s: %s", name, e)
        return t("mech_tool_error", get_lang("global"), name=name, error=e)


# ── Tool loop (used by agents-server and dev meeting) ─────────────────────────

async def ask_with_tools(
    messages: list[dict],
    url: str = MECHANIC_URL,
    model: str = MECHANIC_MODEL,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    timeout: float = TIMEOUT,
    max_tokens: int = MAX_TOKENS,
    job_state: dict | None = None,
    tools: list | None = None,
) -> str:
    """
    Run a tool-using conversation with Mechanic.
    messages: complete message list including system message.
    job_state: shared dict from _jobs[job_id] — checked between rounds for injected comments.
    Always returns a string.
    """
    if tools is not None:
        active_tools = tools
    else:
        active_tools = [
            t for t in MECHANIC_TOOLS
            if is_agent_tool_enabled("mechanic", t["function"]["name"], default=True)
        ]
    payload_base = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.4, "num_ctx": NUM_CTX, "num_predict": max_tokens},
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
                masked.append({**msg, "content": t("mech_output_masked", get_lang("global"), chars=len(msg["content"]))})
            else:
                masked.append(msg)
        return _fixed + masked

    for round_num in range(max_tool_rounds + 1):
        payload = {**payload_base, "messages": _build_messages()}
        try:
            async with lock_11445("mechanic", max_wait=300):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    r = await client.post(
                        url, json=payload,
                        headers={"x-kaare-source": "mechanic"},
                    )
                    r.raise_for_status()
                    resp = r.json()
        except LockTimeout as e:
            log.error("Mechanic: %s", e)
            return t("mech_model_busy", get_lang("global"))
        except Exception as e:
            log.error("Mechanic LLM call failed (round %d): %s", round_num, e)
            return t("mech_mechanic_unavailable", get_lang("global"), error=e)

        msg        = resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls or round_num >= max_tool_rounds:
            content = msg.get("content", "").strip()
            return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip() or t("mech_no_response", get_lang("global"))

        _history.append({
            "role": "assistant",
            "content": msg.get("content", ""),
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn        = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}

            # Loop-deteksjon: ignorer identiske gjentakelser
            sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
            if sig in _seen_calls:
                log.warning("[Mechanic loop] Duplikat ignorert: %s", sig[:80])
                _history.append({
                    "role": "tool",
                    "content": t("mech_loop_detected", get_lang("global"), tool=tool_name),
                    "name": tool_name,
                })
                continue
            _seen_calls.add(sig)

            log.info("[Mechanic tool] %s(%s)", tool_name, str(args)[:80])
            result = await execute_tool(tool_name, args)
            log.info("[Mechanic result] %s", result[:100])
            _history.append({"role": "tool", "content": result, "name": tool_name})

        # Check for injected user comment between rounds
        if job_state is not None:
            injected = job_state.pop("injected", None)
            if injected:
                log.info("[Mechanic] injecting user comment: %s", str(injected)[:60])
                _history.append({"role": "user", "content": f"[User comment mid-task]: {injected}"})

    return t("mech_no_response", get_lang("global"))
