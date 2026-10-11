"""
Agents executor — Mechanic, Argus search, Docker restart.

All Mechanic calls are handled in-process via job_store.py (asyncio background tasks)
and mechanic/tools.py (direct tool loop with model_lock). No HTTP to port 11450.
Exported: AGENT_TOOLS, dispatch()
"""

import asyncio
import getpass
import json as _json
import re
import subprocess as _sp
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from kaare_core.agents.mechanic import job_store
from kaare_core.agents.mechanic.tools import (
    ANALYTIKER_TOOLS,
    KRITIKER_TOOLS,
    MAX_TOKENS,
    MAX_TOOL_ROUNDS,
    MECHANIC_MODEL,
    MECHANIC_TOOLS,
    MECHANIC_URL,
    TIMEOUT,
    UNDERSØKER_TOOLS,
    ask_with_tools,
)
from kaare_core.argus_query import build_argus_filter
from kaare_core.background import spawn
from kaare_core.config import get_llm_config as _llm_cfg
from kaare_core.config import get_local_tz
from kaare_core.config import get_qdrant_api_key as _qdrant_key
from kaare_core.config import get_service as _svc
from kaare_core.config import pool_call_options as _pool_opts
from kaare_core.memory.long_term import get_ltm
from kaare_core.model_lock import (
    LockTimeout,
    lock_agent_pool,
    pool_order,
    restartable_containers,
    role_url,
)
from kaare_core.think_strip import strip_think_block
from kaare_core.tools.i18n import get_lang, t
from kaare_core.tools.shared_tools import english_content_note
from kaare_core.tools.think_cache import log_think

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_MECHANIC_DIR = Path(__file__).parent.parent / "agents" / "mechanic"
_MAX_SEARCH_CHARS = 12000
_ALLOWED_BASE = "/kaare"

AGENT_TOOLS = {
    "mechanic",
    "search_argus",
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
    # Cached config, no disk read; legacy lokasjon.tidssone fallback; UTC when unset (P116 B19)
    return get_local_tz()


def _fmt_ts_local(ts_raw: str) -> str:
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")


def _load_mechanic_personality(role: str = "standard", lang: str = "nb") -> str:
    suffix = "" if lang == "nb" else f"_{lang}"
    base_name = f"personlighet_{role}" if role and role != "standard" else "personlighet"
    path = _MECHANIC_DIR / f"{base_name}{suffix}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    fallback = _MECHANIC_DIR / f"{base_name}.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    if base_name != "personlighet":
        path = _MECHANIC_DIR / f"personlighet{suffix}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        fallback = _MECHANIC_DIR / "personlighet.md"
        if fallback.exists():
            return fallback.read_text(encoding="utf-8")
    return t("mech_personality_fallback", lang)


def _load_mechanic_memory() -> str:
    try:
        content = Path("/kaare/state/mechanic_memory.md").read_text(encoding="utf-8").strip()
        return content if content else ""
    except Exception:
        return ""


def _tools_for_role(role: str) -> list:
    return {
        "undersøker": UNDERSØKER_TOOLS,
        "kritiker": KRITIKER_TOOLS,
        "analytiker": ANALYTIKER_TOOLS,
    }.get(role, MECHANIC_TOOLS)


# ── Argus semantic search ─────────────────────────────────────────────────────


async def _search_argus(
    query: str,
    grense: int = 8,
    lang: str = "nb",
    entity_id: str = "",
    since: str = "",
    until: str = "",
) -> str:
    if not query.strip():
        return t("agent_empty_query", lang)
    grense = max(1, min(grense, 20))
    try:
        embedding_on = bool((_svc("embedding") or {}).get("enabled", True))
    except Exception:
        embedding_on = True  # unknown → try the search and report a real error
    if not embedding_on:
        return t("agent_argus_embedding_off", lang)
    qdrant_url = _svc("storage", "qdrant")
    embed_url = _svc("ollama", "embed") + "/api/embed"
    payload_filter = build_argus_filter(entity_id, since, until)
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
        return t("agent_argus_unavailable", lang, error=e)
    hits = data.get("result", {}).get("points", [])
    if not hits:
        return t("agent_no_argus_events", lang, query=query)
    lines = [t("agent_argus_results", lang, count=len(hits)) + "\n"]
    for h in hits:
        f = h.get("payload", {})
        ts = _fmt_ts_local(f.get("ts", ""))
        msg = f.get("message", "")
        level = f.get("level", "info")
        prefix = "⚠️ " if level == "warning" else "❌ " if level == "error" else ""
        lines.append(f"[{ts}] {prefix}{msg}")
    return "\n".join(lines)


# ── Mechanic search (file/grep/log fetch + one-shot summary) ─────────────────

_LOG_DIR = Path("/kaare/logs")


def _job_result_text(job_id: str, lang: str) -> str:
    data = job_store.get_job(job_id)
    status = data.get("status", "unknown")
    if status == "running":
        return t("agent_job_running", lang, job=job_id[:8])
    return t(
        "agent_job_result",
        lang,
        job=job_id[:8],
        status=status.upper(),
        result=data.get("result") or t("agent_job_no_output", lang),
    )


def _job_cancel_text(job_id: str, lang: str) -> str:
    status = job_store.cancel_job(job_id).get("status", "unknown")
    return t("agent_job_cancelled", lang, job=job_id[:8], status=status)


def _job_comment_text(job_id: str, comment: str, lang: str) -> str:
    data = job_store.inject_comment(job_id, comment)
    if "not running" in (data.get("result") or ""):
        return t("agent_job_comment_not_running", lang, job=job_id[:8])
    return t("agent_job_comment_queued", lang, job=job_id[:8])


def _filter_lines(text: str, log_filter: str) -> str:
    """Keep lines matching `log_filter`. The model writes filters as regex ("ERROR|Traceback"),
    so it is applied case-insensitively as a regex; an invalid regex falls back to a plain
    substring match instead of failing the call."""
    try:
        rx = re.compile(log_filter, re.IGNORECASE)
        return "\n".join(ln for ln in text.splitlines() if rx.search(ln))
    except re.error:
        needle = log_filter.lower()
        return "\n".join(ln for ln in text.splitlines() if needle in ln.lower())


def _available_logs() -> str:
    """Log files that exist, with size — shown when the model asks for one that doesn't."""
    try:
        files = sorted(
            f for f in _LOG_DIR.iterdir() if f.is_file() and f.suffix in (".log", ".jsonl", ".txt")
        )
    except OSError:
        return ""
    return ", ".join(f"{f.name} ({f.stat().st_size // 1024} KB)" for f in files)


async def _run(cmd: list[str], timeout: int) -> str:
    # subprocess.run blocks; run it in a worker thread so a slow journalctl/grep never
    # stalls the event loop (rule 10).
    result = await asyncio.to_thread(
        _sp.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.stdout.strip()


_JOURNAL_NO_ACCESS_HINTS = (
    "not seeing messages from other users",
    "insufficient permissions",
    "No journal files were opened",
    "Permission denied",
)


async def _journal(service: str, n: int, lang: str) -> str:
    """journalctl for one service. Separates «really empty» from «this user may not read the
    journal» — without the group, journalctl prints only '-- No entries --' plus a hint on
    stderr, which was thrown away, so Mechanic reported a frozen/empty service (P116 B29)."""
    try:
        result = await asyncio.to_thread(
            _sp.run,
            ["journalctl", "-u", service, "-n", str(n), "--no-pager"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except FileNotFoundError:
        return t("svc_journal_unavailable", lang)
    out, err = result.stdout.strip(), result.stderr.strip()
    if out in ("", "-- No entries --") and any(h in err for h in _JOURNAL_NO_ACCESS_HINTS):
        return t("svc_journal_no_access", lang, user=getpass.getuser())
    if out in ("", "-- No entries --"):
        return t("svc_log_empty_service", lang, service=service)
    return out


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
                parts.append(f"### {path_str}\n{t('svc_path_rejected', lang)}")
                continue
            try:
                all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                if from_line and to_line:
                    chunk = all_lines[from_line - 1 : to_line]
                elif from_line:
                    chunk = all_lines[from_line - 1 : from_line + 299]
                else:
                    chunk = all_lines
                text = "\n".join(
                    f"{i + 1}: {ln}" for i, ln in enumerate(chunk, start=(from_line or 1) - 1)
                )
                parts.append(f"### {path_str}\n{text[:per_file]}")
            except Exception as e:
                parts.append(f"### {path_str}\n{t('svc_read_error', lang, error=e)}")
        return "\n\n".join(parts)

    if search_type == "grep":
        if not pattern:
            return t("svc_no_grep_pattern", lang)
        search_dir = directory if directory.startswith(_ALLOWED_BASE) else _ALLOWED_BASE
        try:
            out = await _run(
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
                    pattern,
                    search_dir,
                ],
                timeout=15,
            )
            return (
                out[:_MAX_SEARCH_CHARS]
                if out
                else t("svc_no_grep_results", lang, pattern=pattern, path=search_dir)
            )
        except Exception as e:
            return t("svc_grep_failed", lang, error=e)

    if search_type in ("log", "logg"):
        n = min(max(lines, 10), 500)
        if service:
            try:
                out = await _journal(service, n, lang)
                if out.startswith("[") and out.endswith("]"):
                    return out  # empty / no access / journalctl missing — already explained
                if log_filter:
                    out = _filter_lines(out, log_filter)
                    if not out:
                        return t(
                            "svc_log_filter_no_match",
                            lang,
                            source=service,
                            filter=log_filter,
                            lines=n,
                        )
                return out[:_MAX_SEARCH_CHARS]
            except Exception as e:
                return t("svc_journal_failed", lang, error=e)
        if log_file:
            log_path = _LOG_DIR / Path(log_file).name
            if not log_path.exists():
                return t(
                    "svc_log_file_not_found",
                    lang,
                    file=log_file,
                    available=_available_logs() or "—",
                )
            try:
                out = await _run(["tail", "-n", str(n), str(log_path)], timeout=10)
                if not out:
                    return t("svc_log_empty_file", lang, file=log_file)
                if log_filter:
                    out = _filter_lines(out, log_filter)
                    if not out:
                        return t(
                            "svc_log_filter_no_match",
                            lang,
                            source=log_file,
                            filter=log_filter,
                            lines=n,
                        )
                return out[:_MAX_SEARCH_CHARS]
            except Exception as e:
                return t("svc_log_read_failed", lang, error=e)
        return t("svc_log_need_source", lang, available=_available_logs() or "—")

    return t("svc_unknown_search_type", lang, search_type=search_type)


async def _mechanic_search(arguments: dict, lang: str) -> str:
    """File/grep/log fetch followed by one-shot Mechanic summary."""
    search_type = arguments.get("type", arguments.get("search_type", "files"))
    question = arguments.get("query", arguments.get("question", "Summarize the content."))

    if not _llm_cfg("mechanic").get("enabled", True):
        return t("agent_mechanic_disabled", lang)

    raw_files = arguments.get("files", [])
    if isinstance(raw_files, str):
        try:
            raw_files = _json.loads(raw_files)
        except Exception:
            raw_files = []

    def _as_int(val, default=None):
        # LLM tool-call arguments arrive as JSON but numeric fields sometimes come through
        # as strings (e.g. lines="50") — min()/max() on a mixed int/str pair raises
        # TypeError, so every numeric arg from the model must be coerced here, not assumed.
        if val is None or val == "":
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    content = await _fetch_search_content(
        search_type=search_type,
        files=raw_files,
        from_line=_as_int(arguments.get("from_line")),
        to_line=_as_int(arguments.get("to_line")),
        pattern=arguments.get("pattern", ""),
        directory=arguments.get("directory", "/kaare"),
        service=arguments.get("service", ""),
        log_file=arguments.get("log_file", ""),
        lines=_as_int(arguments.get("lines"), 100),
        log_filter=arguments.get("filter", ""),
        lang=lang,
    )
    if not content:
        return t("svc_no_content", lang)
    content = english_content_note(lang) + content

    system = _load_mechanic_personality(lang=lang)
    memory = _load_mechanic_memory()
    if memory:
        system = f"{system}\n\n## Your memory\n{memory}"

    user_msg = f"Content:\n{content}\n\nTask: {question}"
    payload = {
        "model": MECHANIC_MODEL,
        "stream": False,
        "think": False,
        "options": _pool_opts(_llm_cfg("mechanic"), {"num_predict": 800}),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    try:
        async with (
            lock_agent_pool(
                pool_order(role_url("mechanic")), "mechanic_search", max_wait=120
            ) as inst_url,
            httpx.AsyncClient(timeout=90.0) as client,
        ):
            r = await client.post(
                inst_url + "/api/chat",
                json=payload,
                headers={"x-kaare-source": "mechanic_search"},
            )
            r.raise_for_status()
            content_out = r.json().get("message", {}).get("content", "").strip()
            answer, _think_content, _think_closed = strip_think_block(content_out)
            if _think_content:
                try:
                    log_think(
                        think_text=_think_content,
                        response=answer,
                        role="mechanic",
                        model=MECHANIC_MODEL,
                        recovered=False,
                        rid=arguments.get("_rid", ""),
                    )
                except Exception:
                    pass
            answer = answer or t("agent_mechanic_no_response", lang)
            try:
                spawn(
                    get_ltm().log_agent_message(
                        from_agent="kare",
                        to_agent="mechanic",
                        query=question,
                        response=answer,
                        rid=arguments.get("_rid", ""),
                        user_id=arguments.get("_user_id", "global"),
                    )
                )
            except Exception:
                pass
            return answer
    except LockTimeout:
        return t("svc_mechanic_busy", lang)
    except Exception as e:
        return t("mech_mechanic_unavailable", lang, error=e)


# ── Synchronous Mechanic ask (spør_mechanic) ──────────────────────────────────


async def _ask_mechanic(task: str, arguments: dict, lang: str) -> str:
    if not task.strip():
        return t("agent_empty_query", lang)
    if not _llm_cfg("mechanic").get("enabled", True):
        return t("agent_mechanic_disabled", lang)

    personality = _load_mechanic_personality(lang=lang)
    memory = _load_mechanic_memory()
    system_parts = [personality]
    if memory:
        system_parts.append(f"## Your memory\n{memory}")

    messages = [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": task},
    ]

    try:
        answer = await ask_with_tools(
            messages=messages,
            url=MECHANIC_URL,
            model=MECHANIC_MODEL,
            max_tool_rounds=MAX_TOOL_ROUNDS,
            timeout=TIMEOUT,
            max_tokens=MAX_TOKENS,
            rid=arguments.get("_rid", ""),
            lang=lang,
        )
        try:
            spawn(
                get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="mechanic",
                    query=task,
                    response=answer,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                )
            )
        except Exception:
            pass
        return answer if answer else t("agent_mechanic_nothing", lang)
    except Exception as e:
        return t("agent_mechanic_unavailable", lang, error=e)


# ── Dispatcher ────────────────────────────────────────────────────────────────


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))
    user_id = arguments.get("_user_id", "global")

    # ── mechanic (unified action-based tool) ──────────────────────────────────
    if name == "mechanic":
        action = arguments.get("action", "")

        if action == "search":
            return await _mechanic_search(arguments, lang)

        if action == "delegate":
            task = arguments.get("task", "").strip()
            if not task:
                return t("agent_empty_task", lang)
            if not _llm_cfg("mechanic").get("enabled", True):
                return t("agent_mechanic_disabled", lang)
            job_id = await job_store.start_job(
                task=task,
                role=arguments.get("role", "standard"),
                context=arguments.get("context", ""),
                user_id=user_id,
                rid=arguments.get("_rid", ""),
            )
            try:
                spawn(
                    get_ltm().log_agent_message(
                        from_agent="kare",
                        to_agent="mechanic",
                        query=task,
                        response=f"[delegated job_id={job_id}]",
                        rid=arguments.get("_rid", ""),
                        user_id=user_id,
                    )
                )
            except Exception:
                pass
            return t("agent_delegate_started", lang, job_id=job_id)

        if action == "result":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return t("agent_job_id_required", lang)
            return _job_result_text(job_id, lang)

        if action == "cancel":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return t("agent_job_id_required", lang)
            return _job_cancel_text(job_id, lang)

        if action == "comment":
            job_id = arguments.get("job_id", "").strip()
            comment = arguments.get("comment", "").strip()
            if not job_id or not comment:
                return t("agent_job_comment_required", lang)
            return _job_comment_text(job_id, comment, lang)

        return t(
            "agent_unknown_action",
            lang,
            tool="mechanic",
            action=action,
            valid="search, delegate, result, cancel, comment",
        )

    # ── search_argus ─────────────────────────────────────────────────────────
    if name in ("search_argus", "søk_i_argus"):
        return await _search_argus(
            query=arguments.get("query", ""),
            grense=int(arguments.get("limit", 8)),
            lang=lang,
            entity_id=arguments.get("entity_id", ""),
            since=arguments.get("since", ""),
            until=arguments.get("until", ""),
        )

    # ── spør_mechanic (legacy synchronous ask) ────────────────────────────────
    if name == "spør_mechanic":
        return await _ask_mechanic(arguments.get("task", ""), arguments, lang)

    # ── deleger_til_mechanic (legacy delegate) ────────────────────────────────
    if name == "deleger_til_mechanic":
        task = arguments.get("task", "").strip()
        if not task:
            return t("agent_empty_task", lang)
        if not _llm_cfg("mechanic").get("enabled", True):
            return t("agent_mechanic_disabled", lang)
        job_id = await job_store.start_job(
            task=task, user_id=user_id, rid=arguments.get("_rid", "")
        )
        return t("agent_delegate_started", lang, job_id=job_id)

    if name == "hent_mechanic_svar":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return t("agent_job_id_required", lang)
        return _job_result_text(job_id, lang)

    if name == "avbryt_mechanic":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return t("agent_job_id_required", lang)
        return _job_cancel_text(job_id, lang)

    if name == "kommenter_mechanic":
        job_id = arguments.get("job_id", "").strip()
        comment = arguments.get("comment", "").strip()
        if not job_id or not comment:
            return t("agent_job_comment_required", lang)
        return _job_comment_text(job_id, comment, lang)

    # ── restart_docker_container ──────────────────────────────────────────────
    if name == "restart_docker_container":
        container = arguments.get("container", "").strip()
        allowed = restartable_containers()
        if container not in allowed:
            return t(
                "agent_container_rejected", lang, container=container, allowed=", ".join(allowed)
            )
        try:
            result = await asyncio.to_thread(
                _sp.run,
                ["docker", "restart", container],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return t("agent_container_restarted", lang, container=container)
            return t(
                "agent_container_restart_failed",
                lang,
                rc=result.returncode,
                output=(result.stdout + result.stderr).strip(),
            )
        except Exception as e:
            return t("agent_container_restart_failed", lang, rc="—", output=e)

    return t("agent_unknown_tool", lang, name=name)
