"""
Shared tool implementations used by both executor.py (Kåre) and
mechanic/tools.py (Mechanic).

Each function takes an `arguments` dict matching the tool's JSON schema
and returns a plain-text string result.

Parameter names: Kåre sends English names (path, from_line, directory, etc.).
Mechanic sends Norwegian names (sti, fra_linje, mappe, etc.).
All functions accept both via fallback: arguments.get("english", arguments.get("norsk", default)).
"""

import os
import re as _re
import shutil
import subprocess as _sp
from datetime import UTC, datetime
from pathlib import Path

import yaml

from kaare_core import log_buffer
from kaare_core.config import get_vllm_gpu_ids
from kaare_core.tools.i18n import t

_SENSITIVE_NAMES = {
    ".env",
    "auth.env",
    "token",
    "secret",
    "password",
    "apikey",
    "api_key",
    "nvidia",
}

_TOOL_CALLS_LOG = Path("/kaare/logs/tool_calls.log")


def log_tool_call(
    name: str, arguments: dict, result: str, duration_ms: int, source: str = "kare", rid: str = ""
) -> None:
    """Append one entry to tool_calls.log (rid-correlated, feeds trace_reader.py's
    per-request trace used by inspect_system(fetch_trace/trace_patterns) — Kåre's own
    self-improvement view). `rid` falls back to arguments["_rid"] if not given explicitly,
    matching executor.py's own convention for Kåre's tools."""
    try:
        safe_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "rid": rid or arguments.get("_rid", ""),
            "source": source,
            "tool": name,
            "args": safe_args,
            "result_preview": str(result)[:120],
            "duration_ms": duration_ms,
        }
        log_buffer.write(_TOOL_CALLS_LOG, rec)
    except Exception:
        pass


_KAARE_ROOT = Path("/kaare").resolve()


def _validate_path(path: str, allowed_root: Path = _KAARE_ROOT) -> Path | None:
    """Resolve path and verify it stays inside allowed_root. Returns resolved Path or None."""
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(allowed_root)
        return resolved
    except (ValueError, RuntimeError):
        return None


def read_file(
    arguments: dict, default_chunk: int = 500, max_chunk: int = 500, lang: str = "nb"
) -> str:
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
    end = min(
        total, int(arguments.get("to_line", arguments.get("til_linje", start + default_chunk)))
    )
    end = min(end, start + max_chunk)
    chunk = all_lines[start:end]
    header = t("sys_file_header", lang, path=path, from_line=start + 1, to_line=end, total=total)
    more = t("sys_more_lines", lang, count=total - end, next_line=end + 1) if end < total else ""
    return header + "\n".join(chunk) + more


def list_files(arguments: dict, lang: str = "nb") -> str:
    directory = (
        arguments.get("directory") or arguments.get("mappe") or "/kaare/kaare_core"
    ).strip() or "/kaare/kaare_core"
    recursive = bool(arguments.get("recursive", arguments.get("rekursiv", False)))
    p = _validate_path(directory)
    if p is None:
        return t("sys_path_not_allowed", lang)
    if not p.exists():
        return t("sys_file_not_found", lang, path=directory)
    if recursive:
        items = sorted(p.rglob("*"), key=lambda x: str(x))[:200]
        lines = [f"{'[dir]  ' if i.is_dir() else '[file] '} {i.relative_to(p)}" for i in items]
        suffix = (
            "\n[Max 200 files — use directory parameter to narrow the search]"
            if len(items) == 200
            else ""
        )
        return f"{directory}/\n" + "\n".join(lines) + suffix
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
    lines = [f"{'[dir]  ' if i.is_dir() else '[file] '} {i.name}" for i in items[:100]]
    return f"{p}/\n" + "\n".join(lines)


def search_code(arguments: dict, lang: str = "nb") -> str:
    pattern = (arguments.get("pattern") or arguments.get("mønster") or "").strip()
    directory = (
        arguments.get("directory") or arguments.get("mappe") or "/kaare"
    ).strip() or "/kaare"
    if not directory.startswith("/kaare"):
        directory = "/kaare"
    if not pattern:
        return "[Empty search]"
    result = _sp.run(
        [
            "grep",
            "-rn",
            "-E",
            "--include=*.py",
            "--include=*.yaml",
            "--include=*.md",
            "--include=*.json",
            "--include=*.sh",
            "--include=*.toml",
            "--exclude-dir=*venv*",
            "--exclude-dir=node_modules",
            "--exclude-dir=__pycache__",
            "--exclude-dir=.git",
            "-m",
            "50",
            pattern,
            directory,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    out = result.stdout.strip()
    return out[:3000] if out else t("sys_no_grep_results", lang, pattern=pattern, path=directory)


_TS_RE = _re.compile(r'"ts":\s*"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2})')
_MAX_TS_SCAN_LINES = 2_000_000  # hard cap so a deep hit in a multi-GB log (ha_events.log is
# 6.4GB/28M lines) can't turn a grep result into a long stall


def _nearest_ts_for_lines(filepath: Path, target_lines: set[int]) -> dict[int, str]:
    """Nearest preceding 'ts' JSON-field value for each target line number in filepath.
    Single streaming forward pass that stops at the highest target line — safe for
    multi-GB logs (e.g. ha_events.log, 6.4GB), never loads the whole file into memory.
    Targets past _MAX_TS_SCAN_LINES are left unresolved (caller shows '[date unknown]')
    rather than scanning unbounded."""
    if not target_lines:
        return {}
    max_target = min(max(target_lines), _MAX_TS_SCAN_LINES)
    result: dict[int, str] = {}
    current = ""
    try:
        with filepath.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                m = _TS_RE.search(line)
                if m:
                    current = m.group(1).replace("T", " ")
                if i in target_lines:
                    result[i] = current
                if i >= max_target:
                    break
    except Exception:
        pass
    return result


def _annotate_grep_hits(out: str, single_file: Path | None) -> str:
    """Prefix each grep hit with the nearest preceding timestamp found in its file.
    grep alone gives no date on a hit — a match can be silently weeks old and read as
    fresh (see BUG-log 2026-09-01, the actual mechanism behind a dev-meeting hallucination)."""
    hit_lines = out.splitlines()
    parsed: list[tuple[str, int] | None] = []
    targets: dict[str, set[int]] = {}

    for hit_line in hit_lines:
        if single_file is not None:
            ln_str, _, _ = hit_line.partition(":")
            fpath = str(single_file)
        else:
            fpath, _, remainder = hit_line.partition(":")
            ln_str, _, _ = remainder.partition(":")
        if not ln_str.isdigit():
            parsed.append(None)
            continue
        ln = int(ln_str)
        parsed.append((fpath, ln))
        targets.setdefault(fpath, set()).add(ln)

    labels_by_file = {fp: _nearest_ts_for_lines(Path(fp), lns) for fp, lns in targets.items()}

    annotated = []
    for entry, hit_line in zip(parsed, hit_lines, strict=False):
        if entry is None:
            annotated.append(hit_line)
            continue
        fpath, ln = entry
        label = labels_by_file.get(fpath, {}).get(ln, "")
        prefix = f"[~{label}] " if label else "[date unknown] "
        annotated.append(prefix + hit_line)
    return "\n".join(annotated)


def english_content_note(lang: str) -> str:
    """Instruction prefix telling the model that raw file/log/code content ahead is English
    and to present findings in the active language regardless — CLAUDE.md rule 8: any tool
    result dumping raw English content into the LLM's context needs this, not just logs."""
    return {
        "nb": "[Innholdet under er på engelsk — presenter funn og sammendrag på norsk]\n",
        "en": "",
        "de": "[Der folgende Inhalt ist auf Englisch — Ergebnisse und Zusammenfassung bitte auf Deutsch präsentieren]\n",
    }.get(lang, "[The content below is in English — present findings in the active language]\n")


def read_log(arguments: dict, lang: str = "nb") -> str:
    """Read log files, with optional grep via pattern parameter."""
    log_file = (arguments.get("file") or arguments.get("fil") or "").strip()
    n_lines = max(20, min(int(arguments.get("lines", arguments.get("linjer", 20))), 200))
    pattern = (arguments.get("pattern") or arguments.get("mønster") or "").strip()
    max_hits = min(int(arguments.get("max_hits", arguments.get("maks_treff", 50))), 200)
    log_dir = Path("/kaare/logs")
    log_files = sorted(f.name for f in log_dir.iterdir() if f.is_file()) if log_dir.exists() else []

    _lang_note = english_content_note(lang)

    if pattern:
        if log_file:
            log_p = log_dir / log_file
            if not log_p.exists():
                return t(
                    "sys_log_not_found",
                    lang,
                    filename=log_file,
                    files="\n".join(f"  {f}" for f in log_files),
                )
            grep_args = ["grep", "-n", "-m", str(max_hits), pattern, str(log_p)]
        else:
            grep_args = [
                "grep",
                "-rn",
                "-m",
                str(max_hits),
                "--include=*.log",
                pattern,
                str(log_dir),
            ]
        result = _sp.run(grep_args, capture_output=True, text=True, timeout=15)
        out = result.stdout.strip()
        if not out:
            return t("sys_no_grep_results", lang, pattern=pattern, path=log_file or "/kaare/logs/")
        out = _annotate_grep_hits(out, single_file=(log_dir / log_file) if log_file else None)
        return _lang_note + out[:4000]

    if not log_file or "/" in log_file:
        _KEY_LOGS = [
            "kaare_ha_gateway.log",
            "llm_calls.log",
            "route_decisions.log",
            "tool_calls.log",
        ]
        other_logs = [f for f in log_files if f not in _KEY_LOGS]
        parts = [
            _lang_note
            + f"[Log overview — {n_lines} lines per file. Other files: {', '.join(other_logs)}]\n"
        ]
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
        return t(
            "sys_log_not_found",
            lang,
            filename=log_file,
            files="\n".join(f"  {f}" for f in log_files),
        )
    from_line = arguments.get("from_line") or arguments.get("fra_linje")
    if from_line is not None:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)
        start = max(1, int(from_line)) - 1
        to_line = arguments.get("to_line") or arguments.get("til_linje")
        end = min(total, int(to_line if to_line is not None else start + n_lines))
        end = min(end, start + 300)
        chunk = all_lines[start:end]
        header = t(
            "sys_file_header", lang, path=log_file, from_line=start + 1, to_line=end, total=total
        )
        more = (
            t("sys_more_lines", lang, count=total - end, next_line=end + 1) if end < total else ""
        )
        return _lang_note + header + "\n".join(chunk) + more
    result = _sp.run(["tail", "-n", str(n_lines), str(p)], capture_output=True, text=True)
    return (
        (_lang_note + result.stdout.strip()) if result.stdout.strip() else t("sys_empty_log", lang)
    )


_ALLOWED_SERVICES = {
    "kaare",
    "kaare_ha_gateway",
    "kaare-semantic-embed",
    "kaare-embedding",
    "kaare-qdrant",
    "kaare-argus",
    "kaare-voice-bridge",
    "kaare-frontend",
    "kaare-nightjob",
    "kaare-reflection",
    "kaare-dev-meeting",
    "kaare-nvidia-init",
    "kaare-backup",
}

# Services run as Docker containers (docker-compose.yml), not systemd units —
# checked via `docker inspect`/`docker logs` instead of systemctl/journalctl.
_DOCKER_SERVICES = {"kaare-qdrant"}


def check_services(arguments: dict, lang: str = "nb") -> str:
    service = (arguments.get("service") or arguments.get("tjeneste") or "").strip()
    if service:
        if service not in _ALLOWED_SERVICES:
            return t(
                "sys_unknown_service",
                lang,
                service=service,
                allowed=", ".join(sorted(_ALLOWED_SERVICES)),
            )
        log_lines = min(int(arguments.get("log_lines", arguments.get("logglinjer", 20))), 50)
        if service in _DOCKER_SERVICES:
            status_r = _sp.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Status}} (started {{.State.StartedAt}})",
                    service,
                ],
                capture_output=True,
                text=True,
            )
            log_r = _sp.run(
                ["docker", "logs", "--tail", str(log_lines), service],
                capture_output=True,
                text=True,
            )
            return (
                f"=== {service} (docker) ===\n{status_r.stdout.strip()}\n\n"
                f"=== Last {log_lines} log lines ===\n{(log_r.stdout + log_r.stderr).strip()}"
            )[:5000]
        status_r = _sp.run(
            ["systemctl", "status", f"{service}.service", "--no-pager", "-l"],
            capture_output=True,
            text=True,
        )
        journal_r = _sp.run(
            [
                "journalctl",
                "-u",
                f"{service}.service",
                "-n",
                str(log_lines),
                "--no-pager",
                "--output=short",
            ],
            capture_output=True,
            text=True,
        )
        return (
            f"=== {service}.service ===\n{status_r.stdout.strip()}\n\n"
            f"=== Last {log_lines} log lines ===\n{journal_r.stdout.strip()}"
        )[:5000]
    services_list = [
        "kaare",
        "kaare_ha_gateway",
        "kaare-semantic-embed",
        "kaare-qdrant",
        "kaare-argus",
        "kaare-voice-bridge",
        "kaare-frontend",
    ]
    rows = []
    for svc in services_list:
        if svc in _DOCKER_SERVICES:
            r = _sp.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", svc],
                capture_output=True,
                text=True,
            )
            rows.append(f"{svc}: {r.stdout.strip() or 'not-found'}")
            continue
        r = _sp.run(
            ["systemctl", "is-active", f"{svc}.service"],
            capture_output=True,
            text=True,
        )
        rows.append(f"{svc}: {r.stdout.strip()}")
    return "\n".join(rows)


def _pct(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def check_resources(arguments: dict, lang: str = "nb") -> str:
    """CPU/RAM/disk/GPU as ready-computed, labelled lines in the caller's language — the 9B
    copies numbers well but subtracts them badly (it read «23 GB used of 16 GB»), so free
    memory and percentages are worked out here (P119 B9). Blocking: call via asyncio.to_thread."""
    parts = []
    try:
        l1, l5, l15 = os.getloadavg()
        parts.append(
            t(
                "sys_res_cpu",
                lang,
                l1=f"{l1:.2f}",
                l5=f"{l5:.2f}",
                l15=f"{l15:.2f}",
                cores=os.cpu_count() or "?",
            )
        )
    except OSError:
        pass
    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            meminfo[key] = int(rest.split()[0])  # kB
        total, free = meminfo["MemTotal"] / 1048576, meminfo["MemAvailable"] / 1048576
        parts.append(
            t(
                "sys_res_ram",
                lang,
                used=f"{total - free:.1f}",
                total=f"{total:.1f}",
                free=f"{free:.1f}",
                pct_free=_pct(free, total),
            )
        )
    except (OSError, KeyError, ValueError, IndexError):
        pass
    try:
        du = shutil.disk_usage("/kaare")
        gib = 1073741824
        parts.append(
            t(
                "sys_res_disk",
                lang,
                path="/kaare",
                used=f"{du.used / gib:.1f}",
                total=f"{du.total / gib:.1f}",
                free=f"{du.free / gib:.1f}",
                pct_free=_pct(du.free, du.total),
            )
        )
    except OSError:
        pass

    gpu_lines = []
    try:
        gpu_r = _sp.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if gpu_r.returncode == 0:
            vllm_gpus = get_vllm_gpu_ids()
            for row in gpu_r.stdout.strip().splitlines():
                cols = [c.strip() for c in row.split(",")]
                if len(cols) < 5:
                    continue
                index, name, used, total, util = (
                    cols[0],
                    cols[1],
                    int(cols[2]),
                    int(cols[3]),
                    cols[4],
                )
                gpu_lines.append(
                    t(
                        "sys_res_gpu",
                        lang,
                        index=index,
                        name=name,
                        used=used,
                        total=total,
                        free=total - used,
                        pct_free=_pct(total - used, total),
                        util=util,
                    )
                )
                if index.isdigit() and int(index) in vllm_gpus:
                    gpu_lines.append(t("sys_res_gpu_vllm", lang))
    except (OSError, _sp.SubprocessError, ValueError):
        pass
    parts.extend(gpu_lines or [t("sys_gpu_unavailable", lang)])
    return "\n".join(parts)


_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def _git_repo_path() -> Path:
    """Repo inspected by git_diff/git_log — configurable via settings.yaml (git_repo_path),
    defaults to the app root (a normal git working tree for a standard `git clone` install).
    /kaare itself has no local git tracking on Stian's own install (deliberately removed
    2026-08-29); his settings.yaml overrides this to point at the separate release repo."""
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text())
        path = (cfg or {}).get("git_repo_path") or "/kaare"
    except Exception:
        path = "/kaare"
    return Path(path)


def git_diff(arguments: dict, lang: str = "nb") -> str:
    repo = _git_repo_path()
    path_arg = (arguments.get("path") or arguments.get("sti") or "").strip()
    cmd = ["git", "-C", str(repo), "diff"]
    if path_arg and path_arg.startswith(str(repo) + "/"):
        cmd.append(path_arg)
    result = _sp.run(cmd, capture_output=True, text=True, timeout=10)
    out = result.stdout.strip()
    return out[:4000] if out else t("sys_no_git_changes", lang)


def git_log(arguments: dict, lang: str = "nb") -> str:
    repo = _git_repo_path()
    count = min(int(arguments.get("count", arguments.get("antall", 10))), 50)
    path_arg = (arguments.get("path") or arguments.get("sti") or "").strip()
    cmd = ["git", "-C", str(repo), "log", f"-{count}", "--pretty=format:%h %as %s", "--no-color"]
    if path_arg and path_arg.startswith(str(repo) + "/"):
        cmd += ["--", path_arg]
    result = _sp.run(cmd, capture_output=True, text=True, timeout=10)
    out = result.stdout.strip()
    return out[:3000] if out else t("sys_no_git_commits", lang)
