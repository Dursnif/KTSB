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
    les_fil as _shared_les_fil,
    liste_filer as _shared_liste_filer,
    søk_kode as _shared_søk_kode,
    les_logg as _shared_les_logg,
    sjekk_tjenester as _shared_sjekk_tjenester,
    sjekk_ressurser as _shared_sjekk_ressurser,
    git_diff as _shared_git_diff,
    git_log as _shared_git_log,
)

SYSTEM_TOOLS = {
    "utforsk_kode",
    "inspiser_system",
    "les_fil",
    "liste_filer",
    "søk_kode",
    "les_logg",
    "sjekk_tjenester",
    "sjekk_ressurser",
    "git_inspect",
    "ssh_kommando",
    "local_kommando",
}


async def dispatch(name: str, arguments: Dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "utforsk_kode":
        action = arguments.get("action", "")
        if action == "les":
            return _shared_les_fil(arguments, lang=lang)
        if action == "liste":
            return _shared_liste_filer(arguments, lang=lang)
        if action == "søk":
            return _shared_søk_kode(arguments, lang=lang)
        return f"Unknown action for utforsk_kode: '{action}'. Valid: les, liste, søk."

    if name == "inspiser_system":
        action = arguments.get("action", "")
        if action == "logg":
            return _shared_les_logg(arguments, lang=lang)
        if action == "tjenester":
            return _shared_sjekk_tjenester(arguments, lang=lang)
        if action == "ressurser":
            return _shared_sjekk_ressurser(arguments, lang=lang)
        if action == "git_diff":
            return _shared_git_diff(arguments, lang=lang)
        if action == "git_log":
            return _shared_git_log(arguments, lang=lang)
        return f"Unknown action for inspiser_system: '{action}'. Valid: logg, tjenester, ressurser, git_diff, git_log."

    if name == "les_fil":
        return _shared_les_fil(arguments, lang=lang)

    if name == "liste_filer":
        return _shared_liste_filer(arguments, lang=lang)

    if name == "søk_kode":
        return _shared_søk_kode(arguments, lang=lang)

    if name == "les_logg":
        return _shared_les_logg(arguments, lang=lang)

    if name == "sjekk_tjenester":
        return _shared_sjekk_tjenester(arguments, lang=lang)

    if name == "sjekk_ressurser":
        return _shared_sjekk_ressurser(arguments, lang=lang)

    if name == "ssh_kommando":
        node = arguments.get("node", "").strip()
        kommando = arguments.get("kommando", "").strip()
        _VALID_NODES = ("ainuc", "dnspi", "proxypi", "hapi")
        if node not in _VALID_NODES:
            return f"[Ukjent node '{node}'. Tillatte: {', '.join(_VALID_NODES)}]"
        if not kommando:
            return t("sys_empty_command", lang)
        _SUDO_ALL = (
            "sudo apt update",
            "sudo apt upgrade",
            "sudo reboot now",
        )
        _SUDO_DNSPI = (
            "sudo pihole -up",
            "sudo pihole -g",
        )
        _HA_PRIVILEGED = (
            "ha core restart",
            "ha core update",
            "ha supervisor restart",
            "ha supervisor update",
            "ha os update",
            "ha addon restart",
            "ha addon start",
            "ha addon stop",
        )
        if kommando.startswith("sudo "):
            if node == "hapi":
                return "[Rejected: hapi runs as root — use 'ha ...' commands directly, no sudo needed.]"
            allowed_sudo = _SUDO_ALL + (_SUDO_DNSPI if node == "dnspi" else ())
            if not any(kommando.startswith(s) for s in allowed_sudo):
                node_extra = " + sudo pihole -up/-g" if node == "dnspi" else ""
                return (
                    f"[Rejected: sudo command '{kommando[:60]}' not allowed on {node}. "
                    f"Allowed sudo: apt update, apt upgrade, reboot now{node_extra}]"
                )
        elif node == "hapi" and any(kommando.startswith(h) for h in _HA_PRIVILEGED):
            pass  # HA CLI privileged commands allowed on hapi
        else:
            _ALLOWED = (
                # File reading — any path
                "cat ", "head ", "tail ", "grep ", "find ", "stat ", "file ", "wc ", "diff ",
                "ls", "pwd", "du ", "which ",
                # System info
                "uptime", "hostname", "uname", "date", "whoami", "id", "who", "w ", "last",
                "env", "printenv", "echo ",
                # Processes & resources
                "ps", "top -bn", "free", "df", "lsof", "lsblk", "lscpu", "lsusb", "lspci",
                # Network
                "ip ", "ss", "netstat", "ping ", "ifconfig",
                # Systemd
                "systemctl status", "systemctl list-units", "systemctl list-timers",
                "systemctl is-active", "systemctl is-enabled",
                "journalctl", "dmesg",
                # Packages (read-only)
                "dpkg -l", "dpkg -s", "dpkg -L", "apt list", "apt-cache ",
                # Docker
                "docker ps", "docker logs", "docker stats", "docker inspect",
                # Hardware / GPU
                "nvidia-smi",
                # Pi-hole (read)
                "pihole status", "pihole -v", "pihole -c",
                # Home Assistant CLI (read-only)
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
        result = _sp.run(
            [
                "ssh",
                "-i", "/kaare/.ssh/id_ed25519",
                "-F", "/kaare/.ssh/config",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new",
                node,
                kommando,
            ],
            capture_output=True, text=True, timeout=30,
        )
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
