"""
Agents executor — Mechanic, Argus search, Docker restart.

All Mechanic calls are handled in-process via job_store.py (asyncio background tasks)
and mechanic/tools.py (direct tool loop with model_lock). No HTTP to port 11450.
Exported: AGENT_TOOLS, dispatch()
"""

import asyncio
import json as _json
import re
import subprocess as _sp
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

import httpx

from kaare_core.agents.mechanic import job_store
from kaare_core.agents.mechanic.tools import (
    ask_with_tools,
    MECHANIC_URL, MECHANIC_MODEL, MAX_TOOL_ROUNDS, TIMEOUT, MAX_TOKENS,
    MECHANIC_TOOLS, UNDERSØKER_TOOLS, KRITIKER_TOOLS, ANALYTIKER_TOOLS,
    execute_tool as _mechanic_execute_tool,
)
from kaare_core.config import get_service as _svc, get_llm_config as _llm_cfg
from kaare_core.memory.long_term import get_ltm
from kaare_core.model_lock import lock_11445, LockTimeout
from kaare_core.tools.i18n import t, get_lang

_SETTINGS_PATH  = Path("/kaare/configs/settings.yaml")
_MECHANIC_DIR   = Path(__file__).parent.parent / "agents" / "mechanic"
_MAX_SEARCH_CHARS = 12000
_ALLOWED_BASE   = "/kaare"

AGENT_TOOLS = {
    "mechanic",
    "søk_i_argus",
    "spør_mechanic",
    "deleger_til_mechanic",
    "hent_mechanic_svar",
    "avbryt_mechanic",
    "kommenter_mechanic",
    "restart_docker_container",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _local_tz() -> ZoneInfo:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text())
        loc = cfg.get("location") or cfg.get("lokasjon", {})
        return ZoneInfo(loc.get("timezone", "Europe/Oslo"))
    except Exception:
        return ZoneInfo("Europe/Oslo")


def _fmt_ts_local(ts_raw: str) -> str:
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")


def _load_mechanic_personality(role: str = "standard") -> str:
    if role and role != "standard":
        path = _MECHANIC_DIR / f"personlighet_{role}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    path = _MECHANIC_DIR / "personlighet.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Du er Mechanic – en allsidig teknisk assistent."


def _load_mechanic_memory() -> str:
    try:
        content = Path("/kaare/state/mechanic_memory.md").read_text(encoding="utf-8").strip()
        return content if content else ""
    except Exception:
        return ""


def _tools_for_role(role: str) -> list:
    return {
        "undersøker": UNDERSØKER_TOOLS,
        "kritiker":   KRITIKER_TOOLS,
        "analytiker": ANALYTIKER_TOOLS,
    }.get(role, MECHANIC_TOOLS)


# ── Argus semantic search ─────────────────────────────────────────────────────

async def _search_argus(query: str, grense: int = 8, lang: str = "nb") -> str:
    if not query.strip():
        return t("agent_empty_query", lang)
    grense = max(1, min(grense, 20))
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
        return t("agent_argus_unavailable", lang, error=e)
    hits = data.get("result", {}).get("points", [])
    if not hits:
        return t("agent_no_argus_events", lang, query=query)
    lines = [t("agent_argus_results", lang, count=len(hits)) + "\n"]
    for h in hits:
        f = h.get("payload", {})
        ts    = _fmt_ts_local(f.get("ts", ""))
        msg   = f.get("message", "")
        level = f.get("level", "info")
        prefix = "⚠️ " if level == "warning" else "❌ " if level == "error" else ""
        lines.append(f"[{ts}] {prefix}{msg}")
    return "\n".join(lines)


# ── Mechanic search (file/grep/log fetch + one-shot summary) ─────────────────

async def _fetch_search_content(
    search_type: str,
    files: list,
    from_line: int | None,
    to_line: int | None,
    pattern: str,
    directory: str,
    service: str,
    log_file: str,
    lines: int,
    log_filter: str,
    lang: str,
) -> str:
    if search_type in ("files", "filer"):
        if not files:
            return ""
        parts = []
        per_file = _MAX_SEARCH_CHARS // max(len(files), 1)
        for path_str in files[:5]:
            p = Path(path_str)
            if not p.is_absolute() or not str(p).startswith(_ALLOWED_BASE):
                parts.append(f"### {path_str}\n[Rejected: only /kaare paths allowed]")
                continue
            try:
                all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                if from_line and to_line:
                    chunk = all_lines[from_line - 1 : to_line]
                elif from_line:
                    chunk = all_lines[from_line - 1 : from_line + 299]
                else:
                    chunk = all_lines
                text = "\n".join(f"{i+1}: {l}" for i, l in enumerate(
                    chunk, start=(from_line or 1) - 1
                ))
                parts.append(f"### {path_str}\n{text[:per_file]}")
            except Exception as e:
                parts.append(f"### {path_str}\n[Read error: {e}]")
        return "\n\n".join(parts)

    if search_type == "grep":
        if not pattern:
            return t("svc_no_grep_pattern", lang)
        search_dir = directory if directory.startswith(_ALLOWED_BASE) else _ALLOWED_BASE
        try:
            result = _sp.run(
                ["grep", "-rn", "-E",
                 "--include=*.py", "--include=*.yaml", "--include=*.md",
                 "--include=*.json", "--include=*.sh", "--include=*.toml",
                 pattern, search_dir],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            out = result.stdout.strip()
            return out[:_MAX_SEARCH_CHARS] if out else t("svc_no_grep_results", lang, pattern=pattern, path=search_dir)
        except Exception as e:
            return f"[Grep failed: {e}]"

    if search_type in ("log", "logg"):
        n = min(max(lines, 10), 500)
        if service:
            try:
                result = _sp.run(
                    ["journalctl", "-u", service, "-n", str(n), "--no-pager"],
                    capture_output=True, text=True, timeout=15,
                )
                out = result.stdout.strip()
                if log_filter and out:
                    out = "\n".join(l for l in out.splitlines() if log_filter.lower() in l.lower())
                return out[:_MAX_SEARCH_CHARS] if out else f"[Empty log for {service}]"
            except Exception as e:
                return f"[journalctl failed: {e}]"
        if log_file:
            log_path = Path("/kaare/logs") / Path(log_file).name
            if not log_path.exists():
                return f"[Log file not found: {log_file}]"
            try:
                result = _sp.run(
                    ["tail", "-n", str(n), str(log_path)],
                    capture_output=True, text=True, timeout=10,
                )
                out = result.stdout.strip()
                if log_filter and out:
                    out = "\n".join(l for l in out.splitlines() if log_filter.lower() in l.lower())
                return out[:_MAX_SEARCH_CHARS] if out else f"[Empty log: {log_file}]"
            except Exception as e:
                return f"[Log read failed: {e}]"
        return "[Error: specify 'service' or 'log_file' for type=log]"

    return "[Unknown search_type]"


async def _mechanic_search(arguments: Dict, lang: str) -> str:
    """File/grep/log fetch followed by one-shot Mechanic summary."""
    search_type = arguments.get("search_type", "files")
    question    = arguments.get("question", "Summarize the content.")

    if not _llm_cfg("mechanic").get("enabled", True):
        return t("agent_mechanic_disabled", lang)

    raw_files = arguments.get("files", [])
    if isinstance(raw_files, str):
        try:
            raw_files = _json.loads(raw_files)
        except Exception:
            raw_files = []

    content = await _fetch_search_content(
        search_type=search_type,
        files=raw_files,
        from_line=arguments.get("from_line"),
        to_line=arguments.get("to_line"),
        pattern=arguments.get("pattern", ""),
        directory=arguments.get("directory", "/kaare"),
        service=arguments.get("service", ""),
        log_file=arguments.get("log_file", ""),
        lines=arguments.get("lines", 100),
        log_filter=arguments.get("filter", ""),
        lang=lang,
    )
    if not content:
        return t("svc_no_content", lang)

    system = _load_mechanic_personality()
    memory = _load_mechanic_memory()
    if memory:
        system = f"{system}\n\n## Your memory\n{memory}"

    user_msg = f"Content:\n{content}\n\nTask: {question}"
    payload = {
        "model": MECHANIC_MODEL,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": 800},
        "messages": [
            {"role": "system", "content": f"/no_think\n{system}"},
            {"role": "user",   "content": user_msg},
        ],
    }
    try:
        async with lock_11445("mechanic_search", max_wait=120):
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(MECHANIC_URL, json=payload,
                                      headers={"x-kaare-source": "mechanic_search"})
                r.raise_for_status()
                content_out = r.json().get("message", {}).get("content", "").strip()
                return re.sub(r"<think>.*?</think>", "", content_out, flags=re.DOTALL).strip() \
                       or "[No response from Mechanic]"
    except LockTimeout:
        return t("svc_mechanic_busy", lang)
    except Exception as e:
        return f"[Mechanic unavailable: {e}]"


# ── Synchronous Mechanic ask (spør_mechanic) ──────────────────────────────────

async def _ask_mechanic(task: str, arguments: Dict, lang: str) -> str:
    if not task.strip():
        return t("agent_empty_query", lang)
    if not _llm_cfg("mechanic").get("enabled", True):
        return t("agent_mechanic_disabled", lang)

    personality = _load_mechanic_personality()
    memory = _load_mechanic_memory()
    system_parts = [personality]
    if memory:
        system_parts.append(f"## Your memory\n{memory}")

    messages = [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user",   "content": task},
    ]

    try:
        answer = await ask_with_tools(
            messages=messages,
            url=MECHANIC_URL,
            model=MECHANIC_MODEL,
            max_tool_rounds=MAX_TOOL_ROUNDS,
            timeout=TIMEOUT,
            max_tokens=MAX_TOKENS,
        )
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="mechanic",
                query=task,
                response=answer,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return answer if answer else t("agent_mechanic_nothing", lang)
    except Exception as e:
        return t("agent_mechanic_unavailable", lang, error=e)


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def dispatch(name: str, arguments: Dict) -> str:
    lang    = get_lang(arguments.get("_user_id", "global"))
    user_id = arguments.get("_user_id", "global")

    # ── mechanic (unified action-based tool) ──────────────────────────────────
    if name == "mechanic":
        action = arguments.get("action", "")

        if action == "search":
            return await _mechanic_search(arguments, lang)

        if action == "delegate":
            task = arguments.get("task", "").strip()
            if not task:
                return "Error: task cannot be empty."
            if not _llm_cfg("mechanic").get("enabled", True):
                return t("agent_mechanic_disabled", lang)
            job_id = await job_store.start_job(
                task=task,
                role=arguments.get("role", "standard"),
                context=arguments.get("context", ""),
                user_id=user_id,
            )
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare", to_agent="mechanic",
                    query=task, response=f"[delegated job_id={job_id}]",
                    rid=arguments.get("_rid", ""), user_id=user_id,
                ))
            except Exception:
                pass
            return (
                f"Job started. job_id={job_id}\n"
                f"Mechanic is working in the background. "
                f"Poll with mechanic(action='result', job_id='{job_id}'). "
                f"You will also be notified automatically at the next conversation turn."
            )

        if action == "result":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return "Error: job_id is required."
            data   = job_store.get_job(job_id)
            status = data.get("status", "unknown")
            result = data.get("result")
            if status == "running":
                return f"[Job {job_id[:8]}…] Still running — check again in a moment."
            return f"[Job {job_id[:8]}…] {status.upper()}\n{result or '(no output)'}"

        if action == "cancel":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return "Error: job_id is required."
            data   = job_store.cancel_job(job_id)
            status = data.get("status", "unknown")
            return f"[Job {job_id[:8]}…] Cancellation sent. Status: {status}."

        if action == "comment":
            job_id  = arguments.get("job_id", "").strip()
            comment = arguments.get("comment", "").strip()
            if not job_id or not comment:
                return "Error: job_id and comment are required."
            data = job_store.inject_comment(job_id, comment)
            if "not running" in (data.get("result") or ""):
                return f"[Job {job_id[:8]}…] Job is no longer running — comment not delivered."
            return f"[Job {job_id[:8]}…] Comment queued. Mechanic will see it at the next tool round."

        return f"Unknown action for mechanic: '{action}'. Valid: search, delegate, result, cancel, comment."

    # ── søk_i_argus ──────────────────────────────────────────────────────────
    if name == "søk_i_argus":
        return await _search_argus(
            query=arguments.get("query", ""),
            grense=int(arguments.get("limit", 8)),
            lang=lang,
        )

    # ── spør_mechanic (legacy synchronous ask) ────────────────────────────────
    if name == "spør_mechanic":
        return await _ask_mechanic(arguments.get("task", ""), arguments, lang)

    # ── deleger_til_mechanic (legacy delegate) ────────────────────────────────
    if name == "deleger_til_mechanic":
        task = arguments.get("task", "").strip()
        if not task:
            return "Error: task cannot be empty."
        if not _llm_cfg("mechanic").get("enabled", True):
            return t("agent_mechanic_disabled", lang)
        job_id = await job_store.start_job(task=task, user_id=user_id)
        return (
            f"Job started. job_id={job_id}\n"
            f"Mechanic is working in the background. "
            f"Poll with hent_mechanic_svar(job_id='{job_id}')."
        )

    if name == "hent_mechanic_svar":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return "Error: job_id is required."
        data   = job_store.get_job(job_id)
        status = data.get("status", "unknown")
        result = data.get("result")
        if status == "running":
            return f"[Job {job_id[:8]}…] Still running — check again in a moment."
        return f"[Job {job_id[:8]}…] {status.upper()}\n{result or '(no output)'}"

    if name == "avbryt_mechanic":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return "Error: job_id is required."
        data   = job_store.cancel_job(job_id)
        status = data.get("status", "unknown")
        return f"[Job {job_id[:8]}…] Cancellation sent. Status: {status}."

    if name == "kommenter_mechanic":
        job_id  = arguments.get("job_id", "").strip()
        comment = arguments.get("comment", "").strip()
        if not job_id or not comment:
            return "Error: job_id and comment are required."
        data = job_store.inject_comment(job_id, comment)
        if "not running" in (data.get("result") or ""):
            return f"[Job {job_id[:8]}…] Job is no longer running — comment not delivered."
        return f"[Job {job_id[:8]}…] Comment queued. Mechanic will see it at the next tool round."

    # ── restart_docker_container ──────────────────────────────────────────────
    if name == "restart_docker_container":
        container = arguments.get("container", "").strip()
        _ALLOWED_CONTAINERS = ("ollama-kare", "ollama-miss_kare", "ollama-library")
        if container not in _ALLOWED_CONTAINERS:
            return (
                f"[Rejected: '{container}' is not in the allowed list. "
                f"Allowed: {', '.join(_ALLOWED_CONTAINERS)}]"
            )
        try:
            result = _sp.run(
                ["docker", "restart", container],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return (
                    f"Container '{container}' restarted. "
                    "Model reload takes ~3.5 minutes before it is ready again."
                )
            return f"docker restart failed (rc={result.returncode}): {(result.stdout + result.stderr).strip()}"
        except Exception as e:
            return f"restart_docker_container failed: {e}"

    return f"[executor_agents] Unknown tool: '{name}'"
