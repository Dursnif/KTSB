"""
Agents executor module — Mechanic, Argus search, Docker restart.
Exported: AGENT_TOOLS, dispatch()
"""

import asyncio
import json as _json
import subprocess as _sp
import httpx
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

from kaare_core.config import get_service as _svc, get_llm_config as _llm_cfg
from kaare_core.memory.long_term import get_ltm
from kaare_core.tools.i18n import t, get_lang

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

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


async def _søk_i_argus(spørsmål: str, grense: int = 8, lang: str = "nb") -> str:
    if not spørsmål.strip():
        return t("agent_empty_query", lang)
    grense = max(1, min(grense, 20))
    qdrant_url = _svc("storage", "qdrant")
    embed_url  = _svc("ollama", "embed") + "/api/embed"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            emb_r = await client.post(embed_url, json={"model": "bge-m3", "input": spørsmål}, timeout=15.0)
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
        return t("agent_no_argus_events", lang, query=spørsmål)
    lines = [t("agent_argus_results", lang, count=len(hits)) + "\n"]
    for h in hits:
        f = h.get("payload", {})
        ts    = _fmt_ts_local(f.get("ts", ""))
        msg   = f.get("message", "")
        level = f.get("level", "info")
        prefix = "⚠️ " if level == "warning" else "❌ " if level == "error" else ""
        lines.append(f"[{ts}] {prefix}{msg}")
    return "\n".join(lines)


async def _spør_mechanic(oppgave: str, arguments: Dict, lang: str = "nb") -> str:
    if not oppgave.strip():
        return t("agent_empty_query", lang)
    if not _llm_cfg("mechanic").get("enabled", True):
        return t("agent_mechanic_disabled", lang)
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/mechanic",
                json={"task": oppgave},
            )
            r.raise_for_status()
            svar = r.json().get("answer", "").strip()
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="mechanic",
                    query=oppgave,
                    response=svar,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                ))
            except Exception:
                pass
            return svar if svar else t("agent_mechanic_nothing", lang)
    except Exception as e:
        return t("agent_mechanic_unavailable", lang, error=e)


async def dispatch(name: str, arguments: Dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "mechanic":
        action = arguments.get("action", "")
        if action == "søk":
            search_type = arguments.get("type", "filer")
            spørsmål    = arguments.get("spørsmål", "Oppsummer innholdet.")
            if not _llm_cfg("mechanic").get("enabled", True):
                return t("agent_mechanic_disabled", lang)
            raw_filer = arguments.get("filer", [])
            if isinstance(raw_filer, str):
                try:
                    raw_filer = _json.loads(raw_filer)
                except Exception:
                    raw_filer = []
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    r = await client.post(
                        f"{_svc('internal', 'agents')}/ask/mechanic/søk",
                        json={
                            "search_type": search_type,
                            "files":       raw_filer,
                            "from_line":   arguments.get("fra_linje"),
                            "to_line":     arguments.get("til_linje"),
                            "pattern":     arguments.get("mønster", ""),
                            "directory":   arguments.get("mappe", "/kaare"),
                            "service":     arguments.get("tjeneste", ""),
                            "log_file":    arguments.get("logg_fil", ""),
                            "lines":       arguments.get("linjer", 100),
                            "log_filter":  arguments.get("filter", ""),
                            "question":    spørsmål,
                        },
                    )
                    r.raise_for_status()
                    svar = r.json().get("answer", "").strip()
                try:
                    asyncio.create_task(get_ltm().log_agent_message(
                        from_agent="kare", to_agent="mechanic",
                        query=spørsmål, response=svar,
                        rid=arguments.get("_rid", ""),
                        user_id=arguments.get("_user_id", "global"),
                    ))
                except Exception:
                    pass
                return svar or t("agent_mechanic_nothing", lang)
            except Exception as e:
                return t("agent_mechanic_search_failed", lang, error=e)

        if action == "deleger":
            oppgave = arguments.get("oppgave", "").strip()
            if not oppgave:
                return "Error: oppgave cannot be empty."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(
                        f"{_svc('internal', 'agents')}/jobs/mechanic",
                        json={"task": oppgave},
                    )
                    r.raise_for_status()
                    data = r.json()
                job_id = data.get("job_id", "")
                status = data.get("status", "")
                if status == "error":
                    return f"Could not start job: {data.get('result', 'unknown error')}"
                return (
                    f"Job started. job_id={job_id}\n"
                    f"Mechanic is working in the background. "
                    f"Monitor with ssh_kommando/local_kommando, "
                    f"then poll with mechanic(action='svar', job_id='{job_id}')."
                )
            except Exception as e:
                return f"mechanic deleger failed: {e}"

        if action == "svar":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return "Error: job_id is required."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(
                        f"{_svc('internal', 'agents')}/jobs/mechanic/{job_id}",
                    )
                    r.raise_for_status()
                    data = r.json()
                status = data.get("status", "unknown")
                result = data.get("result")
                if status == "running":
                    return f"[Job {job_id[:8]}…] Still running — check again in a moment."
                return f"[Job {job_id[:8]}…] {status.upper()}\n{result or '(no output)'}"
            except Exception as e:
                return f"mechanic svar failed: {e}"

        if action == "avbryt":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return "Error: job_id is required."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.delete(
                        f"{_svc('internal', 'agents')}/jobs/mechanic/{job_id}",
                    )
                    r.raise_for_status()
                    data = r.json()
                status = data.get("status", "unknown")
                return (
                    f"[Job {job_id[:8]}…] Cancellation sent. Status: {status}. "
                    "Mechanic's current LLM call is being aborted."
                )
            except Exception as e:
                return f"mechanic avbryt failed: {e}"

        if action == "kommenter":
            job_id = arguments.get("job_id", "").strip()
            comment = arguments.get("comment", "").strip()
            if not job_id or not comment:
                return "Error: job_id and comment are required."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.patch(
                        f"{_svc('internal', 'agents')}/jobs/mechanic/{job_id}",
                        json={"comment": comment},
                    )
                    r.raise_for_status()
                    data = r.json()
                if "not running" in (data.get("result") or ""):
                    return f"[Job {job_id[:8]}…] Job is no longer running — comment not delivered."
                return f"[Job {job_id[:8]}…] Comment queued. Mechanic will see it at the next tool round."
            except Exception as e:
                return f"mechanic kommenter failed: {e}"

        return f"Unknown action for mechanic: '{action}'. Valid: søk, deleger, svar, avbryt, kommenter."

    if name == "søk_i_argus":
        return await _søk_i_argus(
            spørsmål=arguments.get("spørsmål", ""),
            grense=int(arguments.get("grense", 8)),
            lang=lang,
        )

    if name == "spør_mechanic":
        return await _spør_mechanic(arguments.get("oppgave", ""), arguments, lang)

    if name == "deleger_til_mechanic":
        oppgave = arguments.get("oppgave", "").strip()
        if not oppgave:
            return "Error: oppgave cannot be empty."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{_svc('internal', 'agents')}/jobs/mechanic",
                    json={"task": oppgave},
                )
                r.raise_for_status()
                data = r.json()
            job_id = data.get("job_id", "")
            status = data.get("status", "")
            if status == "error":
                return f"Could not start job: {data.get('result', 'unknown error')}"
            return (
                f"Job started. job_id={job_id}\n"
                f"Mechanic is working in the background. "
                f"Monitor with ssh_kommando/local_kommando, then poll with hent_mechanic_svar(job_id='{job_id}')."
            )
        except Exception as e:
            return f"deleger_til_mechanic failed: {e}"

    if name == "hent_mechanic_svar":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return "Error: job_id is required."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{_svc('internal', 'agents')}/jobs/mechanic/{job_id}",
                )
                r.raise_for_status()
                data = r.json()
            status = data.get("status", "unknown")
            result = data.get("result")
            if status == "running":
                return f"[Job {job_id[:8]}…] Still running — check again in a moment."
            return f"[Job {job_id[:8]}…] {status.upper()}\n{result or '(no output)'}"
        except Exception as e:
            return f"hent_mechanic_svar failed: {e}"

    if name == "avbryt_mechanic":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return "Error: job_id is required."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.delete(
                    f"{_svc('internal', 'agents')}/jobs/mechanic/{job_id}",
                )
                r.raise_for_status()
                data = r.json()
            status = data.get("status", "unknown")
            return (
                f"[Job {job_id[:8]}…] Cancellation sent. "
                f"Status: {status}. "
                "Mechanic's current LLM call is being aborted — Ollama will stop generating."
            )
        except Exception as e:
            return f"avbryt_mechanic failed: {e}"

    if name == "kommenter_mechanic":
        job_id = arguments.get("job_id", "").strip()
        comment = arguments.get("comment", "").strip()
        if not job_id or not comment:
            return "Error: job_id and comment are required."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.patch(
                    f"{_svc('internal', 'agents')}/jobs/mechanic/{job_id}",
                    json={"comment": comment},
                )
                r.raise_for_status()
                data = r.json()
            if "not running" in (data.get("result") or ""):
                return f"[Job {job_id[:8]}…] Job is no longer running — comment not delivered."
            return f"[Job {job_id[:8]}…] Comment queued. Mechanic will see it at the next tool round."
        except Exception as e:
            return f"kommenter_mechanic failed: {e}"

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
            out = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                return (
                    f"Container '{container}' restarted. "
                    "Model reload takes ~3.5 minutes before it is ready again."
                )
            return f"docker restart failed (rc={result.returncode}): {out}"
        except Exception as e:
            return f"restart_docker_container failed: {e}"

    return f"[executor_agents] Unknown tool: '{name}'"
