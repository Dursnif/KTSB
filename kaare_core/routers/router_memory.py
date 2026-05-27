import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
import kaare_core.app_state as app_state
from kaare_core.users.auth import require_admin as _require_admin

router = APIRouter()

_NIGHTJOB_STATUS = app_state._NIGHTJOB_STATUS

MEMORY_LLM_BASE    = os.getenv("MEMORY_LLM_BASE",    "http://127.0.0.1:11434")
MEMORY_LLM_MODEL   = os.getenv("MEMORY_LLM_MODEL",   "qwen3:8b")
MEMORY_LLM_TIMEOUT = float(os.getenv("MEMORY_LLM_TIMEOUT", "30"))
MEMORY_LOG_PATH    = os.getenv("MEMORY_LOG_PATH",    "/kaare/logs/memory_events.jsonl")


def _load_env_file(path: str, env: dict) -> None:
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                env.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def _last_episode_ts() -> str | None:
    try:
        conn = sqlite3.connect("/kaare/state/memory/interactions.db")
        row = conn.execute("SELECT MAX(ts_created) FROM episodes").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _append_memory_log(line: dict) -> None:
    try:
        os.makedirs(os.path.dirname(MEMORY_LOG_PATH), exist_ok=True)
        with open(MEMORY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _stream_nightjob_proc(proc) -> None:
    st = _NIGHTJOB_STATUS
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            st["log"].append(line)
            if len(st["log"]) > 60:
                st["log"].pop(0)
            m_batch = re.search(r"Komprimerer batch.*\((\d+) interaksjon", line)
            m_ep    = re.search(r"Episode (\d+) lagret", line)
            m_done  = re.search(r"Nattjobb ferdig: (\d+) episoder laget, (\d+) interaksjon", line)
            if m_done:
                st["episodes"]   = int(m_done.group(1))
                st["compressed"] = int(m_done.group(2))
                st["step"] = f"{st['episodes']} episode(r) laget, {st['compressed']} interaksjoner komprimert"
            elif m_ep:
                st["step"] = f"Episode {m_ep.group(1)} lagret…"
            elif m_batch:
                st["step"] = f"Komprimerer {m_batch.group(1)} interaksjoner…"
            elif line.startswith("===") or "ferdig" in line.lower():
                st["step"] = line
        await proc.wait()
        st["error"] = None if proc.returncode == 0 else f"Exitkode {proc.returncode}"
    except Exception as exc:
        st["log"].append(f"[feil: {exc}]")
        st["error"] = str(exc)
    finally:
        st["running"] = False
        st["finished_at"] = datetime.now().isoformat()


@router.get("/api/memory/recent")
def api_memory_recent(limit: int = 20, _u=Depends(_require_admin)):
    from kaare_core.memory.long_term import get_ltm
    try:
        rows = get_ltm().get_recent(limit=limit)
        return {"ok": True, "count": len(rows), "interactions": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/stats")
def api_memory_stats(_u=Depends(_require_admin)):
    from kaare_core.memory.long_term import get_ltm
    try:
        return {"ok": True, **get_ltm().get_stats()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/search")
def api_memory_search(q: str, limit: int = 8, _u=Depends(_require_admin)):
    from kaare_core.memory.long_term import get_ltm
    try:
        hits = get_ltm().search_interactions(q, limit=limit)
        return {"ok": True, "query": q, "count": len(hits), "hits": hits}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/compress")
async def api_memory_compress():
    if _NIGHTJOB_STATUS["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "/kaare/kaare_nightjob.py",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare", env=env,
        )
        app_state._NIGHTJOB_PROC = proc
        _NIGHTJOB_STATUS.update({
            "running": True, "episodes": 0, "compressed": 0,
            "step": "Starter…", "log": [],
            "started_at": datetime.now().isoformat(), "finished_at": None, "error": None,
        })
        asyncio.create_task(_stream_nightjob_proc(proc))
        return {"status": "started"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router.get("/api/memory/compress/status")
async def api_memory_compress_status():
    return {**_NIGHTJOB_STATUS, "last_episode_ts": _last_episode_ts()}


class MemoryAddRequest(BaseModel):
    text: str
    scope: str = "default"
    user_id: str = "default"
    metadata: dict | None = None


class MemoryQueryRequest(BaseModel):
    query: str
    scope: str = "default"
    user_id: str = "default"
    limit: int = 20


@router.post("/api/memory/add")
async def api_memory_add(req: MemoryAddRequest):
    import time
    t0 = time.perf_counter()
    payload = {
        "model": MEMORY_LLM_MODEL,
        "mode": "add",
        "scope": req.scope,
        "user_id": req.user_id,
        "text": req.text,
        "metadata": req.metadata or {},
    }
    try:
        async with httpx.AsyncClient(timeout=MEMORY_LLM_TIMEOUT) as client:
            r = await client.post(f"{MEMORY_LLM_BASE}/api/memory/add", json=payload)
            data = r.json() if r.status_code < 300 else {"ok": False, "error": r.text}
    except Exception as e:
        data = {"ok": False, "error": f"memory_llm_add_failed: {e}"}
    dt_ms = round((time.perf_counter() - t0) * 1000)
    _append_memory_log({
        "ts": datetime.now(timezone.utc).isoformat(),
        "op": "add",
        "scope": req.scope,
        "user_id": req.user_id,
        "latency_ms": dt_ms,
    })
    return {"ok": True, "latency_ms": dt_ms, "result": data}


@router.post("/api/memory/query")
async def api_memory_query(req: MemoryQueryRequest):
    import time
    t0 = time.perf_counter()
    payload = {
        "model": MEMORY_LLM_MODEL,
        "mode": "query",
        "scope": req.scope,
        "user_id": req.user_id,
        "query": req.query,
        "limit": req.limit,
    }
    try:
        async with httpx.AsyncClient(timeout=MEMORY_LLM_TIMEOUT) as client:
            r = await client.post(f"{MEMORY_LLM_BASE}/api/memory/query", json=payload)
            data = r.json() if r.status_code < 300 else {"ok": False, "error": r.text}
    except Exception as e:
        dt_ms = round((time.perf_counter() - t0) * 1000)
        _append_memory_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": "query",
            "scope": req.scope,
            "user_id": req.user_id,
            "latency_ms": dt_ms,
            "query_preview": req.query[:80],
            "error": str(e),
        })
        return {"ok": False, "error": f"memory_llm_query_failed: {e}"}
    dt_ms = round((time.perf_counter() - t0) * 1000)
    _append_memory_log({
        "ts": datetime.now(timezone.utc).isoformat(),
        "op": "query",
        "scope": req.scope,
        "user_id": req.user_id,
        "latency_ms": dt_ms,
        "query_preview": req.query[:80],
    })
    return {"ok": True, "latency_ms": dt_ms, "result": data}
