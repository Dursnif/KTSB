import asyncio
import os
import re
import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, Depends

import kaare_core.app_state as app_state
from kaare_core.background import spawn
from kaare_core.config import now_local
from kaare_core.memory.long_term import get_ltm
from kaare_core.users.auth import require_admin as _require_admin

router = APIRouter()

_NIGHTJOB_STATUS = app_state._NIGHTJOB_STATUS


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


async def _stream_nightjob_proc(proc) -> None:
    st = _NIGHTJOB_STATUS
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            st["log"].append(line)
            if len(st["log"]) > 60:
                st["log"].pop(0)
            m_batch = re.search(r"Komprimerer batch.*\((\d+) interaksjon", line)
            m_ep = re.search(r"Episode (\d+) lagret", line)
            m_done = re.search(r"Nattjobb ferdig: (\d+) episoder laget, (\d+) interaksjon", line)
            if m_done:
                st["episodes"] = int(m_done.group(1))
                st["compressed"] = int(m_done.group(2))
                st["step"] = (
                    f"{st['episodes']} episode(r) laget, {st['compressed']} interaksjoner komprimert"
                )
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
        st["finished_at"] = now_local().isoformat()


@router.get("/api/memory/recent")
def api_memory_recent(limit: int = 20, _u=Depends(_require_admin)):
    try:
        rows = get_ltm().get_recent(limit=limit)
        return {"ok": True, "count": len(rows), "interactions": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/stats")
def api_memory_stats(_u=Depends(_require_admin)):
    try:
        return {"ok": True, **get_ltm().get_stats()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/search")
def api_memory_search(q: str, limit: int = 8, _u=Depends(_require_admin)):
    try:
        hits = get_ltm().search_interactions(q, limit=limit)
        return {"ok": True, "query": q, "count": len(hits), "hits": hits}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/compress")
async def api_memory_compress(_u=Depends(_require_admin)):
    if _NIGHTJOB_STATUS["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "/kaare/kaare_nightjob.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare",
            env=env,
        )
        app_state._NIGHTJOB_PROC = proc
        _NIGHTJOB_STATUS.update(
            {
                "running": True,
                "episodes": 0,
                "compressed": 0,
                "step": "Starter…",
                "log": [],
                "started_at": now_local().isoformat(),
                "finished_at": None,
                "error": None,
            }
        )
        spawn(_stream_nightjob_proc(proc))
        return {"status": "started"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router.get("/api/memory/compress/status")
async def api_memory_compress_status(_u=Depends(_require_admin)):
    return {**_NIGHTJOB_STATUS, "last_episode_ts": _last_episode_ts()}


# /api/memory/add and /api/memory/query removed (P120 Phase 3): they forwarded to a memory-LLM
# at 127.0.0.1:11434 that does not exist — always failed, no caller, and took user_id from the body.
