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
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, get_service as _svc, is_agent_tool_enabled, get_ssh_nodes as _get_ssh_nodes
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
_MSG_WINDOW     = 10  # tool-meldinger som beholdes verbatim (eldre maskeres)

MEMORY_PATH   = Path("/kaare/state/mechanic_memory.md")
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def _fmt_ts_local(ts_raw: str) -> str:
    """Konverter UTC ISO-tidsstempel til lokal tid for visning."""
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

# ── Tool-definisjoner ─────────────────────────────────────────────────────────

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
        "Tillatt sudo per node: konfigurert i ssh_nodes.yaml (sudo_commands-listen). "
        if node_ids else ""
    )
    desc = (
        "Kjør en les-bare kommando lokalt på AI-pc eller på en nettverksnode via SSH. "
        "node='local': AI-pc (ingen sudo). "
        + (node_lines + " " if node_lines else "Ingen SSH-noder konfigurert ennå. ")
        + "Les-bare: cat, head, tail, grep, find, ls, ps, df, free, uptime, journalctl, "
        "systemctl status/list, dpkg, apt list, docker ps/logs, ip, ss, nvidia-smi, pihole status. "
        + sudo_note
        + "Ingen sudo på local."
    )
    node_desc = (
        "'local' = denne maskinen (ingen sudo)."
        + (" " + ", ".join(f"'{n}'" for n in node_ids) + " = SSH-noder." if node_ids else "")
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
                    "kommando": {"type": "string", "description": "Shell-kommando å kjøre."},
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
                "Utforsk /kaare-kodebasen. Tre operasjoner: "
                "action='les': les en fil (krever 'sti'). Uten fra_linje/til_linje: første 200 linjer. "
                "Med fra_linje og til_linje: eksakt blokk (maks 300 linjer). "
                "action='liste': list filer og undermapper (valgfri 'mappe', valgfri 'rekursiv'). "
                "action='søk': grep-søk etter mønster i .py/.yaml/.md/.json/.sh (krever 'mønster', valgfri 'mappe')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "liste", "søk"],
                        "description": (
                            "'les' = les fil (krever 'sti'). "
                            "'liste' = list filer/mapper (valgfri 'mappe', 'rekursiv'). "
                            "'søk' = grep-søk (krever 'mønster', valgfri 'mappe')."
                        ),
                    },
                    "sti": {"type": "string", "description": "Absolutt filsti under /kaare. Kun ved action='les'."},
                    "fra_linje": {"type": "integer", "description": "Første linje (1-basert). Kun ved action='les'."},
                    "til_linje": {"type": "integer", "description": "Siste linje (inklusiv). Kun ved action='les'."},
                    "mappe": {"type": "string", "description": "Absolutt mappe-sti under /kaare. Brukes ved 'liste' og 'søk'."},
                    "rekursiv": {"type": "boolean", "description": "List rekursivt (maks 200 filer). Kun ved action='liste'. Standard: false."},
                    "mønster": {"type": "string", "description": "Søketekst eller regex. Kun ved action='søk'."},
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
                "Inspiser systemstatus og logger. Fem operasjoner: "
                "action='logg': les eller søk i /kaare/logs/. Uten 'fil': oversikt over alle logger. "
                "Med 'fil': tail. Med 'mønster': grep-søk. Med fra_linje/til_linje: eksakt bulk. "
                "action='tjenester': systemd-status for Kåre-tjenester. "
                "Uten 'tjeneste': aktiv/inaktiv for alle. Med 'tjeneste': detaljer + journalctl. "
                "action='ressurser': sanntids CPU, RAM, disk og GPU VRAM. "
                "action='git_diff': ukommitterte endringer (valgfri 'sti'). "
                "action='git_log': commit-historikk (valgfri 'sti', 'antall')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["logg", "tjenester", "ressurser", "git_diff", "git_log"],
                        "description": (
                            "'logg' = les/søk loggfiler (valgfri 'fil', 'linjer', 'mønster', 'maks_treff', 'fra_linje', 'til_linje'). "
                            "'tjenester' = systemd-status (valgfri 'tjeneste', 'logglinjer'). "
                            "'ressurser' = CPU/RAM/disk/GPU sanntid. "
                            "'git_diff' = ukommitterte endringer (valgfri 'sti'). "
                            "'git_log' = commit-historikk (valgfri 'sti', 'antall')."
                        ),
                    },
                    "fil": {"type": "string", "description": "Loggfilnavn uten sti, f.eks. 'kaare_ha_gateway.log'. Kun ved action='logg'."},
                    "linjer": {"type": "integer", "description": "Antall linjer (tail). Standard 20, maks 200. Kun ved action='logg'."},
                    "mønster": {"type": "string", "description": "Søketekst/regex for grep. Kun ved action='logg'."},
                    "maks_treff": {"type": "integer", "description": "Maks grep-treff. Standard 50, maks 200. Kun ved action='logg'."},
                    "fra_linje": {"type": "integer", "description": "Første linje (1-basert). Kun ved action='logg'."},
                    "til_linje": {"type": "integer", "description": "Siste linje (inklusiv). Kun ved action='logg'."},
                    "tjeneste": {"type": "string", "description": "Tjenestenavn for detaljert visning, f.eks. 'kaare', 'kaare-agents'. Kun ved action='tjenester'."},
                    "logglinjer": {"type": "integer", "description": "Antall journalctl-linjer. Standard 20, maks 50. Kun ved action='tjenester'."},
                    "sti": {"type": "string", "description": "Absolutt filsti/mappe. Brukes ved action='git_diff'/'git_log'."},
                    "antall": {"type": "integer", "description": "Antall commits. Standard 10, maks 50. Kun ved action='git_log'."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nettsøk",
            "description": "Søk på nettet etter teknisk informasjon.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Søketekst. Norsk eller engelsk."}
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
                "Semantisk søk i systemloggen via Qdrant (BGE-M3, 1024-dim). "
                "Finner semantisk nærliggende hendelser fra alle loggkilder. "
                "Bruk for å se HA-handlinger, feil, treghet, stoppede forespørsler. "
                "Eksempel: søk_argus(query='HA feil siste dag')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Søketekst. Norsk eller engelsk."},
                    "grense": {"type": "integer", "description": "Maks resultater. Standard: 10, maks: 20."},
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
                "Mechanics personlige minne — ting han har lært om systemet og seg selv. "
                "Bruk 'skriv' for å lagre ny lærdom (datostemplet automatisk). "
                "Bruk 'les' for å hente alt du har lagret. "
                "Bruk 'slett_gammel' for å rydde ut oppføringer eldre enn N dager."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "skriv", "slett_gammel"],
                        "description": "les: hent hukommelse. skriv: legg til ny lærdom. slett_gammel: fjern gamle oppføringer.",
                    },
                    "tekst": {"type": "string", "description": "Tekst å lagre (kun ved action=skriv). Konkret og faktabasert."},
                    "dager": {"type": "integer", "description": "Fjern oppføringer eldre enn N dager (kun ved action=slett_gammel). Standard: 30."},
                },
                "required": ["action"],
            },
        },
    },
]


# ── Rolle-baserte verktøysett ─────────────────────────────────────────────────

def _tools_by_name(*names: str) -> list:
    return [t for t in MECHANIC_TOOLS if t["function"]["name"] in names]

UNDERSØKER_TOOLS = _tools_by_name(
    "utforsk", "inspiser", "søk_argus", "shell", "hukommelse",
)

KRITIKER_TOOLS = _tools_by_name("hukommelse")

ANALYTIKER_TOOLS = _tools_by_name("utforsk", "hukommelse")


# ── Tool-eksekvering ──────────────────────────────────────────────────────────

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
            return f"[Ukjent action for utforsk: {action}]"

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
            return f"[Ukjent action for inspiser: {action}]"

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
                        timeout=15.0,
                    )
                    r.raise_for_status()
                    data = r.json()
            except Exception as e:
                return f"[Argus utilgjengelig: {e}]"
            hits = data.get("result", {}).get("points", [])
            if not hits:
                return t("mech_no_log_results", get_lang("global"), query=query)
            lines = [f"Argus: {len(hits)} treff — '{query}'\n"]
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
                return content if content else "[Tom hukommelse]"

            elif action == "skriv":
                tekst = arguments.get("tekst", "").strip()
                if not tekst:
                    return t("mech_empty_memory", get_lang("global"))
                MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                ts = date.today().isoformat()
                entry = f"\n- [{ts}] {tekst}"
                with open(MEMORY_PATH, "a", encoding="utf-8") as f:
                    f.write(entry)
                return f"[Lagret: {tekst[:80]}]"

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

            return f"[Ukjent action for hukommelse: {action}]"

        return t("mech_unknown_tool", get_lang("global"), name=name)

    except Exception as e:
        log.error("Verktøyfeil %s: %s", name, e)
        return t("mech_tool_error", get_lang("global"), name=name, error=e)


# ── Tool-loop (kjøres av agents-server og dev-møtet) ─────────────────────────

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
    Kjører en tool-using samtale med Mechanic.
    messages: komplett meldingsliste inkl. system-melding.
    job_state: shared dict from _jobs[job_id] — checked between rounds for injected comments.
    Returnerer alltid en streng.
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

    # Faste meldinger (system + brukeroppgave) skilles fra tool-historikk
    _fixed = list(messages)
    _history: list[dict] = []
    _seen_calls: set[str] = set()  # loop-deteksjon

    def _build_messages() -> list[dict]:
        # Observation masking (ref. arxiv 2508.21433):
        # Behold siste _MSG_WINDOW meldinger verbatim, erstatt eldre tool-output med placeholder.
        # Assistant-meldinger (med tool_calls) beholdes alltid — modellen trenger å se sine egne valg.
        if len(_history) <= _MSG_WINDOW:
            return _fixed + _history
        cutoff = len(_history) - _MSG_WINDOW
        masked = []
        for i, msg in enumerate(_history):
            if i < cutoff and msg["role"] == "tool":
                masked.append({**msg, "content": f"[Output maskert — {len(msg['content'])} tegn]"})
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
            log.error("Mechanic LLM-kall feilet (runde %d): %s", round_num, e)
            return f"[Mechanic utilgjengelig: {e}]"

        msg        = resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls or round_num >= max_tool_rounds:
            content = msg.get("content", "").strip()
            return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip() or "[Ingen respons]"

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
                    "content": (
                        f"[Duplikat ignorert: {tool_name} ble allerede kalt med samme argumenter. "
                        "Prøv et annet verktøy eller endre argumentene.]"
                    ),
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

    return "[Ingen respons]"
