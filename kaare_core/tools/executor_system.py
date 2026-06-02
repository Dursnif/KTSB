"""
System executor module — file exploration, log reading, SSH, local shell, git inspect.
Exported: SYSTEM_TOOLS, dispatch()
"""

import subprocess as _sp
import yaml
from pathlib import Path
from typing import Dict

from kaare_core.tools.i18n import t, get_lang
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

SYSTEM_TOOLS = {
    "utforsk_kode",
    "inspiser_system",
    "read_file",
    "list_files",
    "search_code",
    "read_log",
    "check_services",
    "check_resources",
    "git_inspect",
    "ssh_kommando",
    "local_kommando",
}


async def dispatch(name: str, arguments: Dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "utforsk_kode":
        action = arguments.get("action", "")
        if action == "read":
            return _shared_read_file(arguments, lang=lang)
        if action == "list":
            return _shared_list_files(arguments, lang=lang)
        if action == "search":
            return _shared_search_code(arguments, lang=lang)
        return f"Unknown action for utforsk_kode: '{action}'. Valid: read, list, search."

    if name == "inspiser_system":
        action = arguments.get("action", "")
        if action == "log":
            return _shared_read_log(arguments, lang=lang)
        if action == "services":
            return _shared_check_services(arguments, lang=lang)
        if action == "resources":
            return _shared_check_resources(arguments, lang=lang)
        if action == "git_diff":
            return _shared_git_diff(arguments, lang=lang)
        if action == "git_log":
            return _shared_git_log(arguments, lang=lang)
        if action == "fetch_trace":
            from kaare_core.tools.trace_reader import get_trace, format_trace_for_kare
            rid_arg = arguments.get("rid", "").strip()
            if not rid_arg:
                return t("inspiser_system_hent_trace_mangler_rid", lang)
            trace = get_trace(rid_arg)
            if not trace.get("stages") and not trace.get("llm_calls") and not trace.get("tool_calls"):
                return t("inspiser_system_hent_trace_ikke_funnet", lang, rid=rid_arg)
            return format_trace_for_kare(trace, lang=lang)
        if action == "trace_patterns":
            from kaare_core.tools.trace_reader import get_recent_traces, format_patterns_for_kare
            n      = min(int(arguments.get("count", arguments.get("antall", 50))), 200)
            source = arguments.get("source", "all")
            if source not in ("user", "refl", "meet", "all"):
                source = "all"
            traces = get_recent_traces(n, source=source)
            if not traces:
                return t("inspiser_system_trace_mønstre_ingen", lang)
            return format_patterns_for_kare(traces, lang=lang)
        return f"Unknown action for inspiser_system: '{action}'. Valid: log, services, resources, git_diff, git_log, fetch_trace, trace_patterns."

    if name == "read_file":
        return _shared_read_file(arguments, lang=lang)

    if name == "list_files":
        return _shared_list_files(arguments, lang=lang)

    if name == "search_code":
        return _shared_search_code(arguments, lang=lang)

    if name == "read_log":
        return _shared_read_log(arguments, lang=lang)

    if name == "check_services":
        return _shared_check_services(arguments, lang=lang)

    if name == "check_resources":
        return _shared_check_resources(arguments, lang=lang)

    if name == "ssh_kommando":
        from kaare_core.config import get_ssh_nodes
        node = arguments.get("node", "").strip()
        kommando = arguments.get("kommando", "").strip()

        ssh_cfg = get_ssh_nodes()
        nodes = ssh_cfg.get("nodes", {})

        if not nodes:
            return "[No SSH nodes configured. Add nodes in Settings → Tools → SSH Nodes.]"
        if node not in nodes:
            return f"[Unknown node '{node}'. Configured nodes: {', '.join(nodes.keys())}]"
        if not kommando:
            return t("sys_empty_command", lang)

        node_cfg   = nodes[node]
        node_type  = node_cfg.get("node_type", "linux")
        is_ha_os   = node_type == "ha_os"
        sudo_ok    = node_cfg.get("sudo_enabled", False) and not is_ha_os
        sudo_cmds  = [c.strip() for c in node_cfg.get("sudo_commands", []) if c.strip()]

        host    = node_cfg.get("host", node)
        user    = node_cfg.get("user", "root" if is_ha_os else "user")
        port    = int(node_cfg.get("port", 2222 if is_ha_os else 22))
        ssh_key = str(node_cfg.get("ssh_key", "~/.ssh/id_ed25519")).replace("~", str(Path.home()))

        _HA_PRIVILEGED = (
            "ha core restart", "ha core update",
            "ha supervisor restart", "ha supervisor update",
            "ha os update",
            "ha addon restart", "ha addon start", "ha addon stop",
        )

        if kommando.startswith("sudo "):
            if is_ha_os:
                return "[Rejected: HA OS node runs as root — use 'ha ...' commands directly, no sudo needed.]"
            if not sudo_ok:
                return f"[Rejected: sudo is not enabled for node '{node}'.]"
            if not any(kommando.startswith(s) for s in sudo_cmds):
                allowed_str = ", ".join(sudo_cmds) if sudo_cmds else "none"
                return (
                    f"[Rejected: sudo command '{kommando[:60]}' not in allowlist for {node}. "
                    f"Allowed: {allowed_str}]"
                )
        elif is_ha_os and any(kommando.startswith(h) for h in _HA_PRIVILEGED):
            pass  # HA CLI privileged commands allowed on ha_os nodes
        else:
            _ALLOWED = (
                "cat ", "head ", "tail ", "grep ", "find ", "stat ", "file ", "wc ", "diff ",
                "ls", "pwd", "du ", "which ",
                "uptime", "hostname", "uname", "date", "whoami", "id", "who", "w ", "last",
                "env", "printenv", "echo ",
                "ps", "top -bn", "free", "df", "lsof", "lsblk", "lscpu", "lsusb", "lspci",
                "ip ", "ss", "netstat", "ping ", "ifconfig",
                "systemctl status", "systemctl list-units", "systemctl list-timers",
                "systemctl is-active", "systemctl is-enabled",
                "journalctl", "dmesg",
                "dpkg -l", "dpkg -s", "dpkg -L", "apt list", "apt-cache ",
                "docker ps", "docker logs", "docker stats", "docker inspect",
                "nvidia-smi",
                "pihole status", "pihole -v", "pihole -c",
                "ha core info", "ha core logs", "ha core check",
                "ha supervisor info", "ha supervisor logs",
                "ha os info", "ha network info",
                "ha addon info", "ha addon list", "ha addon logs",
                "ha host info", "ha hardware info",
            )
            _BLOCKED = ("rm ", "mv ", "chmod ", "chown ", "dd ", "mkfs", "fdisk",
                        ">", ">>", "tee ", "sed -i", "awk -i", "truncate",
                        "apt install", "apt remove", "apt-get install",
                        "pip install", "npm install",
                        "systemctl start ", "systemctl stop ", "systemctl restart ",
                        "systemctl enable ", "systemctl disable ")
            if any(b in kommando for b in _BLOCKED):
                return f"[Rejected: '{kommando[:60]}' contains a write/destructive operation.]"
            if not any(kommando.startswith(a) for a in _ALLOWED):
                return (
                    f"[Rejected: '{kommando[:50]}' is not a recognised read-only command on {node}. "
                    f"Allowed: cat, head, tail, grep, find, ls, ps, df, free, uptime, journalctl, "
                    f"systemctl status/list, dpkg, pihole status/version, docker ps/logs, ip, ss, "
                    f"ha core/supervisor/os/addon info/logs, ...]"
                )

        ssh_cmd = [
            "ssh",
            "-i", ssh_key,
            "-F", "/kaare/.ssh/config",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-p", str(port),
            f"{user}@{host}",
            kommando,
        ]
        result = _sp.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        out = (result.stdout + result.stderr).strip()
        return out[:4000] if out else t("sys_no_output", lang)

    if name == "local_kommando":
        # Shell commands require developer_tools: true in settings.yaml.
        try:
            _dev_cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
            _dev_tools_enabled = bool(_dev_cfg.get("developer_tools", False))
        except Exception:
            _dev_tools_enabled = False
        if not _dev_tools_enabled:
            return t("sys_dev_tools_disabled", lang)
        kommando = arguments.get("kommando", "").strip()
        if not kommando:
            return t("sys_empty_command", lang)
        _ALLOWED = (
            # File reading
            "cat ", "head ", "tail ", "grep ", "find ", "stat ", "file ", "wc ", "diff ",
            "ls", "pwd", "du ", "which ",
            # System info
            "uptime", "hostname", "uname", "date", "whoami", "id", "who", "w ", "last",
            "env", "printenv", "echo ",
            # Processes & resources
            "ps", "top -bn", "free", "df", "lsof", "lsblk", "lscpu", "lsusb", "lspci",
            # Network
            "ip ", "ss", "netstat", "ping ", "ifconfig",
            # Systemd (read only)
            "systemctl status", "systemctl list-units", "systemctl list-timers",
            "systemctl is-active", "systemctl is-enabled",
            "journalctl", "dmesg",
            # Packages (read)
            "dpkg -l", "dpkg -s", "dpkg -L", "apt list", "apt-cache ",
            # Docker (read)
            "docker ps", "docker logs", "docker stats", "docker inspect",
            # Hardware / GPU
            "nvidia-smi",
        )
        _BLOCKED = ("sudo", "rm ", "mv ", "chmod ", "chown ", "dd ", "mkfs",
                    ">", ">>", "tee ", "sed -i", "awk -i", "truncate",
                    "apt install", "apt remove", "apt upgrade", "apt-get",
                    "pip install", "npm install",
                    "systemctl start ", "systemctl stop ", "systemctl restart ",
                    "systemctl enable ", "systemctl disable ", "reboot", "shutdown")
        if any(b in kommando for b in _BLOCKED):
            return f"[Rejected: '{kommando[:60]}' contains sudo or a write/destructive operation. No sudo on AI-pc.]"
        if not any(kommando.startswith(a) for a in _ALLOWED):
            return (
                f"[Rejected: '{kommando[:50]}' is not a recognised read-only command. "
                f"Allowed: cat, head, tail, grep, find, ls, ps, df, free, uptime, "
                f"journalctl, dmesg, systemctl status/list, dpkg, docker ps/logs, "
                f"ip, ss, netstat, lsblk, lscpu, nvidia-smi, ...]"
            )
        result = _sp.run(
            kommando, shell=True, capture_output=True, text=True, timeout=30,
        )
        out = (result.stdout + result.stderr).strip()
        return out[:4000] if out else t("sys_no_output", lang)

    if name == "git_inspect":
        action = arguments.get("action", "log")
        if action == "diff":
            return _shared_git_diff(arguments, lang=lang)
        return _shared_git_log(arguments, lang=lang)

    return f"[executor_system] Unknown tool: '{name}'"
