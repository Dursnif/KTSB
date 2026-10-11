"""
Read-only guard for Mechanic's shell tool (P116 B33).

Stian, 2026-10-01: Mechanic may only READ — never edit, write, delete or change anything,
locally or on a node. The old check only looked at how a command started plus a deny-list,
under shell=True, so chaining (`ls; unlink x`), substitution (`$(...)`), deleting flags of
allowed programs (`find -delete`, `journalctl --vacuum-size`) and state-changing
subcommands (`ip link set`, `date -s`, `env rm x`) all got through.

Rules here:
  - no chaining, substitution or redirection at all: ; & ` $( > < and newlines are rejected;
    a pipe | is allowed, and every segment of the pipe is checked on its own
  - every segment must start with a program on the allow-list
  - per program: forbidden flags, allowed subcommands, or "no arguments at all"
  - `sudo` is accepted only in front of a command that itself passes these rules — except a
    sudo command the user added for that node on the Tools page (a deliberate opt-in, future
    nerd mode; Stian 2026-10-01: «pr default så får ktsb kun lese»), checked by the caller
  - follow modes (journalctl -f, tail -f, docker logs -f) would never end — they are turned
    into a bounded "last 100 lines" read instead of failing on the timeout
"""

import os
import re
import shlex

_META = re.compile(r"[;&`<>\n\r]|\$\(|\$\{")

# Programs that only read. Anything else (rm, mv, cp, sed, awk, tee, python, sh, xargs …) is
# rejected by not being here — awk/sed are out because they can write or run commands.
_PROGRAMS = {
    "cat",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "zgrep",
    "zcat",
    "find",
    "stat",
    "file",
    "wc",
    "diff",
    "sort",
    "uniq",
    "cut",
    "tr",
    "ls",
    "pwd",
    "du",
    "which",
    "uptime",
    "hostname",
    "uname",
    "date",
    "whoami",
    "id",
    "who",
    "w",
    "last",
    "env",
    "printenv",
    "echo",
    "ps",
    "top",
    "free",
    "df",
    "lsof",
    "lsblk",
    "lscpu",
    "lsusb",
    "lspci",
    "ip",
    "ss",
    "netstat",
    "ping",
    "ifconfig",
    "systemctl",
    "journalctl",
    "dmesg",
    "dpkg",
    "apt",
    "apt-cache",
    "docker",
    "nvidia-smi",
    "pihole",
    "ha",
}

# Programs whose (non-flag) arguments would change state, so they run bare or with flags only.
_NO_POSITIONAL = {
    "hostname",
    "env",
    "printenv",
    "ifconfig",
    "date",
    "uptime",
    "free",
    "lscpu",
    "whoami",
    "pwd",
}

# Flags that delete, write or change state.
_FORBIDDEN_FLAGS = {
    "find": {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",
    },
    "journalctl": {
        "--vacuum-size",
        "--vacuum-time",
        "--vacuum-files",
        "--rotate",
        "--flush",
        "--sync",
        "--relinquish-var",
        "--smart-relinquish-var",
        "--setup-keys",
        "--update-catalog",
    },
    "dmesg": {
        "-c",
        "-C",
        "--clear",
        "--read-clear",
        "-D",
        "--console-off",
        "-E",
        "--console-on",
        "-n",
        "--console-level",
    },
    "date": {"-s", "--set"},
    "hostname": {"-F", "--file", "-b", "--boot"},
    "sort": {"-o", "--output"},
    "nvidia-smi": {
        "-pm",
        "--persistence-mode",
        "-pl",
        "--power-limit",
        "-r",
        "--gpu-reset",
        "-ac",
        "--applications-clocks",
        "-rac",
        "--reset-applications-clocks",
        "-e",
        "--ecc-config",
        "-c",
        "--compute-mode",
        "-lgc",
        "--lock-gpu-clocks",
        "-rgc",
        "--reset-gpu-clocks",
        "-lmc",
        "--lock-memory-clocks",
        "-rmc",
        "--reset-memory-clocks",
        "-mig",
        "--multi-instance-gpu",
    },
    "top": {"-w"},
}

# Programs where the first non-flag word is a subcommand that must be one of these.
_SUBCOMMANDS = {
    "systemctl": {
        "status",
        "list-units",
        "list-timers",
        "list-unit-files",
        "is-active",
        "is-enabled",
        "is-failed",
        "show",
        "cat",
    },
    "docker": {"ps", "logs", "stats", "inspect", "images", "version", "info"},
    "apt": {"list", "show", "policy"},
    "apt-cache": {"show", "policy", "search", "depends", "rdepends", "showpkg"},
    "pihole": {"status", "version", "-v", "-c", "-q"},
    "ha": {"info"},  # HA CLI: only `ha <thing> info` — checked below
    "ip": {
        "a",
        "addr",
        "address",
        "r",
        "route",
        "l",
        "link",
        "n",
        "neigh",
        "neighbour",
        "-br",
        "-4",
        "-6",
        "-s",
        "-d",
        "-c",
        "-j",
    },
}
_IP_FORBIDDEN_WORDS = {
    "set",
    "add",
    "del",
    "delete",
    "flush",
    "change",
    "replace",
    "append",
    "save",
    "restore",
    "up",
    "down",
}
_DPKG_ALLOWED = {
    "-l",
    "--list",
    "-s",
    "--status",
    "-L",
    "--listfiles",
    "-S",
    "--search",
    "-p",
    "--print-avail",
}

_FOLLOW_FLAGS = {"-f", "--follow", "-F"}
_FOLLOW_PROGRAMS = {"journalctl", "tail", "docker"}
FOLLOW_LINES = 100


def _flag_name(arg: str) -> str:
    return arg.split("=", 1)[0]


def bound_follow(command: str) -> tuple[str, bool]:
    """Turn follow-mode reads into a bounded read. Returns (command, changed)."""
    if _META.search(command):
        return command, False
    out, changed = [], False
    for seg in command.split("|"):
        try:
            argv = shlex.split(seg)
        except ValueError:
            return command, False
        if not argv:
            out.append(seg)
            continue
        prog = os.path.basename(argv[1] if argv[0] == "sudo" and len(argv) > 1 else argv[0])
        if prog in _FOLLOW_PROGRAMS and any(a in _FOLLOW_FLAGS for a in argv):
            argv = [a for a in argv if a not in _FOLLOW_FLAGS]
            if prog == "docker" and "--tail" not in argv and "-n" not in argv:
                argv += ["--tail", str(FOLLOW_LINES)]
            elif prog != "docker" and not any(
                a in ("-n", "--lines") or a.startswith(("-n", "--lines=")) for a in argv[1:]
            ):
                argv += ["-n", str(FOLLOW_LINES)]
            changed = True
        out.append(shlex.join(argv))
    return " | ".join(s.strip() for s in out), changed


def has_shell_meta(command: str) -> bool:
    """True if the command chains, substitutes or redirects — never allowed, not even for a
    user-configured sudo command."""
    return bool(_META.search(command)) or "||" in command


def readonly_violation(command: str) -> str | None:
    """None if the command only reads; otherwise a short English reason (shown to the model)."""
    if not command.strip():
        return "empty command"
    if _META.search(command):
        return "chaining, substitution or redirection (; & ` $( ${ > < newline) is not allowed"
    if "||" in command:
        return "'||' chaining is not allowed"
    for seg in command.split("|"):
        try:
            argv = shlex.split(seg)
        except ValueError:
            return "unbalanced quotes"
        if not argv:
            return "empty pipe segment"
        if argv[0] == "sudo":
            argv = argv[1:]
            if not argv or argv[0].startswith("-"):
                return "sudo is only allowed directly in front of a read-only command"
        prog = os.path.basename(argv[0])
        args = argv[1:]
        if prog not in _PROGRAMS:
            return f"'{prog}' is not an allowed read-only program"
        if prog in _FOLLOW_PROGRAMS and any(a in _FOLLOW_FLAGS for a in args):
            return "follow mode never ends — read the last lines instead (-n 100)"
        bad = _FORBIDDEN_FLAGS.get(prog, set())
        for a in args:
            if _flag_name(a) in bad:
                return f"'{prog} {a}' changes or deletes something"
        positional = [a for a in args if not a.startswith("-")]
        if prog in _NO_POSITIONAL and positional:
            return f"'{prog}' with arguments can change state — run it bare"
        if prog == "dpkg" and (not args or args[0] not in _DPKG_ALLOWED):
            return "dpkg: only -l/-s/-L/-S/-p (read) are allowed"
        if prog in _SUBCOMMANDS:
            words = (
                [a for a in args if not a.startswith("-")] if prog not in ("ip", "pihole") else args
            )
            if not words:
                if prog in ("ip",):
                    return "ip: give a read subcommand (addr, route, link, neigh)"
                continue
            if prog == "ha":
                if words[-1] != "info" or len(words) != 2:
                    return "ha: only '<subsystem> info' (e.g. 'ha core info') is allowed"
                continue
            if prog == "ip":
                if any(w in _IP_FORBIDDEN_WORDS for w in args):
                    return "ip: only showing addresses/routes/links is allowed"
                if args[0] not in _SUBCOMMANDS["ip"]:
                    return f"ip: '{args[0]}' is not a read subcommand"
                continue
            if words[0] not in _SUBCOMMANDS[prog]:
                return f"'{prog} {words[0]}' is not a read subcommand"
    return None
