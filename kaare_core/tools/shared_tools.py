"""
Shared tool implementations used by both executor.py (Kåre) and
mechanic/tools.py (Mechanic).

Each function takes an `arguments` dict matching the tool's JSON schema
and returns a plain-text string result.

Parameter names: Kåre sends English names (path, from_line, directory, etc.).
Mechanic sends Norwegian names (sti, fra_linje, mappe, etc.).
All functions accept both via fallback: arguments.get("english", arguments.get("norsk", default)).
"""

import subprocess as _sp
from pathlib import Path

from kaare_core.tools.i18n import t

_SENSITIVE_NAMES = {".env", "auth.env", "token", "secret", "password", "apikey", "api_key", "nvidia"}

_KAARE_ROOT = Path("/kaare").resolve()


def _validate_path(path: str, allowed_root: Path = _KAARE_ROOT) -> Path | None:
    """Resolve path and verify it stays inside allowed_root. Returns resolved Path or None."""
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(allowed_root)
        return resolved
    except (ValueError, RuntimeError):
        return None


def read_file(arguments: dict, default_chunk: int = 500, max_chunk: int = 500, lang: str = "nb") -> str:
    path = (arguments.get("path") or arguments.get("sti") or "").strip()
    resolved = _validate_path(path)
    if not path.startswith("/kaare/") or resolved is None:
        return t("sys_invalid_path", lang)
    if any(s in resolved.name.lower() for s in _SENSITIVE_NAMES):
        return t("sys_sensitive_file", lang)
    p = resolved
    if not p.exists():
        return t("sys_file_not_found", lang, path=path)
    all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(all_lines)
    start = max(1, int(arguments.get("from_line", arguments.get("fra_linje", 1)))) - 1
    end = min(total, int(arguments.get("to_line", arguments.get("til_linje", start + default_chunk))))
    end = min(end, start + max_chunk)
    chunk = all_lines[start:end]
    header = t("sys_file_header", lang, path=path, from_line=start+1, to_line=end, total=total)
    more = t("sys_more_lines", lang, count=total - end, next_line=end+1) if end < total else ""
    return header + "\n".join(chunk) + more


def list_files(arguments: dict, lang: str = "nb") -> str:
    directory = (arguments.get("directory") or arguments.get("mappe") or "/kaare/kaare_core").strip() or "/kaare/kaare_core"
    recursive = bool(arguments.get("recursive", arguments.get("rekursiv", False)))
    p = _validate_path(directory)
    if p is None:
        return t("sys_path_not_allowed", lang)
    if not p.exists():
        return t("sys_file_not_found", lang, path=directory)
    if recursive:
        items = sorted(p.rglob("*"), key=lambda x: str(x))[:200]
        lines = [f"{'[dir]  ' if i.is_dir() else '[file] '} {i.relative_to(p)}" for i in items]
        suffix = "\n[Max 200 files — use directory parameter to narrow the search]" if len(items) == 200 else ""
        return f"{directory}/\n" + "\n".join(lines) + suffix
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
    lines = [f"{'[dir]  ' if i.is_dir() else '[file] '} {i.name}" for i in items[:100]]
    return f"{p}/\n" + "\n".join(lines)


def search_code(arguments: dict, lang: str = "nb") -> str:
    pattern   = (arguments.get("pattern") or arguments.get("mønster") or "").strip()
    directory = (arguments.get("directory") or arguments.get("mappe") or "/kaare").strip() or "/kaare"
    if not directory.startswith("/kaare"):
        directory = "/kaare"
    if not pattern:
        return "[Empty search]"
    result = _sp.run(
        ["grep", "-rn",
         "--include=*.py", "--include=*.yaml", "--include=*.md",
         "--include=*.json", "--include=*.sh", "--include=*.toml",
         "-m", "50", pattern, directory],
        capture_output=True, text=True, timeout=15,
    )
    out = result.stdout.strip()
    return out[:3000] if out else t("sys_no_grep_results", lang, pattern=pattern, path=directory)


def read_log(arguments: dict, lang: str = "nb") -> str:
    """Read log files, with optional grep via pattern parameter."""
    log_file  = (arguments.get("file") or arguments.get("fil") or "").strip()
    n_lines   = max(20, min(int(arguments.get("lines", arguments.get("linjer", 20))), 200))
    pattern   = (arguments.get("pattern") or arguments.get("mønster") or "").strip()
    max_hits  = min(int(arguments.get("max_hits", arguments.get("maks_treff", 50))), 200)
    log_dir   = Path("/kaare/logs")
    log_files = sorted(f.name for f in log_dir.iterdir() if f.is_file()) if log_dir.exists() else []

    _lang_note = {
        "nb": "[Logginnhold er på engelsk — presenter funn og sammendrag på norsk]\n",
        "en": "",
        "de": "[Log-Inhalt ist auf Englisch — Ergebnisse und Zusammenfassung bitte auf Deutsch präsentieren]\n",
    }.get(lang, "[Log content is in English — present findings in the active language]\n")

    if pattern:
        if log_file:
            log_p = log_dir / log_file
            if not log_p.exists():
                return t("sys_log_not_found", lang, filename=log_file,
                         files="\n".join(f"  {f}" for f in log_files))
            grep_args = ["grep", "-n", "-m", str(max_hits), pattern, str(log_p)]
        else:
            grep_args = ["grep", "-rn", "-m", str(max_hits), "--include=*.log", pattern, str(log_dir)]
        result = _sp.run(grep_args, capture_output=True, text=True, timeout=15)
        out = result.stdout.strip()
        if not out:
            return t("sys_no_grep_results", lang, pattern=pattern, path=log_file or '/kaare/logs/')
        return _lang_note + out[:4000]

    if not log_file or "/" in log_file:
        _KEY_LOGS = ["kaare_ha_gateway.log", "llm_calls.log", "route_decisions.log", "tool_calls.log"]
        other_logs = [f for f in log_files if f not in _KEY_LOGS]
        parts = [_lang_note + f"[Log overview — {n_lines} lines per file. Other files: {', '.join(other_logs)}]\n"]
        for _fn in _KEY_LOGS:
            _p = log_dir / _fn
            if not _p.exists():
                continue
            _r = _sp.run(["tail", "-n", str(n_lines), str(_p)], capture_output=True, text=True)
            content = _r.stdout.strip()
            if content:
                parts.append(f"\n--- {_fn} ---\n{content}")
        return "\n".join(parts)[:6000]

    p = _validate_path(str(log_dir / log_file), log_dir.resolve())
    if p is None or not p.exists():
        return t("sys_log_not_found", lang, filename=log_file,
                 files="\n".join(f"  {f}" for f in log_files))
    from_line = arguments.get("from_line") or arguments.get("fra_linje")
    if from_line is not None:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)
        start = max(1, int(from_line)) - 1
        to_line = arguments.get("to_line") or arguments.get("til_linje")
        end = min(total, int(to_line if to_line is not None else start + n_lines))
        end = min(end, start + 300)
        chunk = all_lines[start:end]
        header = t("sys_file_header", lang, path=log_file, from_line=start+1, to_line=end, total=total)
        more = t("sys_more_lines", lang, count=total - end, next_line=end+1) if end < total else ""
        return _lang_note + header + "\n".join(chunk) + more
    result = _sp.run(["tail", "-n", str(n_lines), str(p)], capture_output=True, text=True)
    return (_lang_note + result.stdout.strip()) if result.stdout.strip() else t("sys_empty_log", lang)


_ALLOWED_SERVICES = {
    "kaare", "kaare_ha_gateway", "kaare-semantic-embed", "kaare-embedding",
    "kaare-qdrant", "kaare-argus", "kaare-voice-bridge",
    "kaare-frontend", "kaare-nightjob", "kaare-reflection", "kaare-dev-meeting",
    "kaare-nvidia-init", "kaare-backup",
}


def check_services(arguments: dict, lang: str = "nb") -> str:
    service = (arguments.get("service") or arguments.get("tjeneste") or "").strip()
    if service:
        if service not in _ALLOWED_SERVICES:
            return t("sys_unknown_service", lang, service=service,
                     allowed=', '.join(sorted(_ALLOWED_SERVICES)))
        log_lines = min(int(arguments.get("log_lines", arguments.get("logglinjer", 20))), 50)
        status_r = _sp.run(
            ["systemctl", "status", f"{service}.service", "--no-pager", "-l"],
            capture_output=True, text=True,
        )
        journal_r = _sp.run(
            ["journalctl", "-u", f"{service}.service",
             "-n", str(log_lines), "--no-pager", "--output=short"],
            capture_output=True, text=True,
        )
        return (
            f"=== {service}.service ===\n{status_r.stdout.strip()}\n\n"
            f"=== Last {log_lines} log lines ===\n{journal_r.stdout.strip()}"
        )[:5000]
    services_list = [
        "kaare", "kaare_ha_gateway", "kaare-semantic-embed",
        "kaare-qdrant", "kaare-argus", "kaare-voice-bridge", "kaare-frontend",
    ]
    rows = []
    for svc in services_list:
        r = _sp.run(
            ["systemctl", "is-active", f"{svc}.service"],
            capture_output=True, text=True,
        )
        rows.append(f"{svc}: {r.stdout.strip()}")
    return "\n".join(rows)


def check_resources(arguments: dict, lang: str = "nb") -> str:  # noqa: ARG001
    parts = []
    load_r = _sp.run(["cat", "/proc/loadavg"], capture_output=True, text=True)
    if load_r.returncode == 0:
        p = load_r.stdout.strip().split()
        parts.append(f"CPU load (1m/5m/15m): {p[0]} / {p[1]} / {p[2]}")
    free_r = _sp.run(["free", "-h"], capture_output=True, text=True)
    if free_r.returncode == 0:
        parts.append(f"\nRAM:\n{free_r.stdout.strip()}")
    df_r = _sp.run(["df", "-h", "/kaare"], capture_output=True, text=True)
    if df_r.returncode == 0:
        parts.append(f"\nDisk (/kaare):\n{df_r.stdout.strip()}")
    gpu_r = _sp.run(
        ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader"],
        capture_output=True, text=True, timeout=10,
    )
    if gpu_r.returncode == 0 and gpu_r.stdout.strip():
        parts.append(f"\nGPU:\n{gpu_r.stdout.strip()}")
    else:
        parts.append("\n" + t("sys_gpu_unavailable", lang))
    return "\n".join(parts)


def git_diff(arguments: dict, lang: str = "nb") -> str:
    path_arg = (arguments.get("path") or arguments.get("sti") or "").strip()
    cmd = ["git", "-C", "/kaare", "diff"]
    if path_arg and path_arg.startswith("/kaare/"):
        cmd.append(path_arg)
    result = _sp.run(cmd, capture_output=True, text=True, timeout=10)
    out = result.stdout.strip()
    return out[:4000] if out else t("sys_no_git_changes", lang)


def git_log(arguments: dict, lang: str = "nb") -> str:
    count = min(int(arguments.get("count", arguments.get("antall", 10))), 50)
    path_arg = (arguments.get("path") or arguments.get("sti") or "").strip()
    cmd = ["git", "-C", "/kaare", "log", f"-{count}",
           "--pretty=format:%h %as %s", "--no-color"]
    if path_arg and path_arg.startswith("/kaare/"):
        cmd += ["--", path_arg]
    result = _sp.run(cmd, capture_output=True, text=True, timeout=10)
    out = result.stdout.strip()
    return out[:3000] if out else t("sys_no_git_commits", lang)
