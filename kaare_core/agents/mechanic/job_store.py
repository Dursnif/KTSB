"""
Mechanic async job store.

Kåre can delegate long-running tasks to Mechanic (background asyncio task),
respond to the user immediately, and poll for results later.

Job lifecycle: running → done | error | cancelled
Pending results are written to state/pending_mechanic/{user_id}.json so
router_generate.py injects them into the next conversation turn automatically.
"""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("mechanic.job_store")

_JOB_TTL      = 1800   # seconds — completed jobs kept this long before cleanup
_CLEANUP_INTERVAL = 300
_PENDING_DIR  = Path("/kaare/state/pending_mechanic")

# job_id → {"status", "result", "created_at", "user_id", "injected"}
_jobs: dict[str, dict] = {}


# ── Pending result notifications ──────────────────────────────────────────────

def _pending_path(user_id: str) -> Path:
    return _PENDING_DIR / f"{user_id}.json"


def _write_pending_result(user_id: str, job_id: str, summary: str) -> None:
    try:
        path = _pending_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append({"job_id": job_id, "summary": summary})
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("[job_store] Failed to write pending result: %s", e)


def get_pending_mechanic_results(user_id: str) -> list[dict]:
    """Return all pending Mechanic results for a user (consumed once by router_generate)."""
    path = _pending_path(user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def ack_mechanic_results(user_id: str) -> None:
    """Remove pending results after they have been injected into context."""
    try:
        _pending_path(user_id).unlink(missing_ok=True)
    except Exception:
        pass


# ── Job runner ────────────────────────────────────────────────────────────────

async def _run_job(job_id: str, task: str, role: str, context: str, user_id: str) -> None:
    from kaare_core.agents.mechanic.tools import (
        ask_with_tools,
        MECHANIC_URL, MECHANIC_MODEL, MAX_TOOL_ROUNDS, TIMEOUT, MAX_TOKENS,
        MECHANIC_TOOLS, UNDERSØKER_TOOLS, KRITIKER_TOOLS, ANALYTIKER_TOOLS,
    )
    from pathlib import Path as _Path

    job = _jobs.get(job_id)
    if job is None:
        return

    role_tools = {
        "undersøker": UNDERSØKER_TOOLS,
        "kritiker":   KRITIKER_TOOLS,
        "analytiker": ANALYTIKER_TOOLS,
    }.get(role, MECHANIC_TOOLS)

    pers_path = _Path(__file__).parent
    if role and role != "standard":
        custom = pers_path / f"personlighet_{role}.md"
        personality = custom.read_text(encoding="utf-8") if custom.exists() else ""
    if not personality if role != "standard" else True:
        default = pers_path / "personlighet.md"
        personality = default.read_text(encoding="utf-8") if default.exists() else "Du er Mechanic."

    memory_path = _Path("/kaare/state/mechanic_memory.md")
    memory_block = ""
    try:
        memory_block = memory_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass

    system_parts = [personality]
    if memory_block:
        system_parts.append(f"## Your memory\n{memory_block}")
    if context:
        system_parts.append(f"## Context\n{context}")

    messages = [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user",   "content": task},
    ]

    try:
        result = await ask_with_tools(
            messages=messages,
            url=MECHANIC_URL,
            model=MECHANIC_MODEL,
            max_tool_rounds=MAX_TOOL_ROUNDS,
            timeout=TIMEOUT,
            max_tokens=MAX_TOKENS,
            job_state=job,
            tools=role_tools,
        )
        job["status"] = "done"
        job["result"] = result
        log.info("[job_store] Job %s done: %s", job_id[:8], result[:80])
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["result"] = "[Cancelled]"
        log.info("[job_store] Job %s cancelled", job_id[:8])
        return
    except Exception as e:
        job["status"] = "error"
        job["result"] = f"[Error: {e}]"
        log.error("[job_store] Job %s error: %s", job_id[:8], e)

    # Ping: write result to pending_mechanic so router_generate picks it up
    if user_id and user_id != "global":
        summary = (job["result"] or "")[:300]
        _write_pending_result(user_id, job_id, summary)


# ── Public API ────────────────────────────────────────────────────────────────

async def start_job(
    task: str,
    role: str = "standard",
    context: str = "",
    user_id: str = "global",
) -> str:
    """Start a background Mechanic job. Returns job_id immediately."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status":     "running",
        "result":     None,
        "created_at": time.monotonic(),
        "user_id":    user_id,
        "injected":   None,
    }
    asyncio.create_task(_run_job(job_id, task, role, context, user_id))
    log.info("[job_store] Started job %s for user %s", job_id[:8], user_id)
    return job_id


def get_job(job_id: str) -> dict:
    """Return job status dict, or error dict if not found."""
    job = _jobs.get(job_id)
    if job is None:
        return {"status": "not_found", "result": f"Job {job_id[:8]}… not found (expired or invalid)."}
    return {"status": job["status"], "result": job["result"]}


def cancel_job(job_id: str) -> dict:
    """Mark job as cancelled. The running task checks this between tool rounds."""
    job = _jobs.get(job_id)
    if job is None:
        return {"status": "not_found", "result": None}
    if job["status"] == "running":
        job["status"] = "cancelled"
        job["result"] = "[Cancelled by request]"
    return {"status": job["status"], "result": job["result"]}


def inject_comment(job_id: str, comment: str) -> dict:
    """Inject a mid-task comment — Mechanic reads it between tool rounds."""
    job = _jobs.get(job_id)
    if job is None:
        return {"status": "not_found", "result": None}
    if job["status"] != "running":
        return {"status": job["status"], "result": "Job is no longer running — comment not delivered."}
    job["injected"] = comment
    return {"status": "running", "result": "Comment queued."}


# ── Background cleanup ────────────────────────────────────────────────────────

async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        cutoff = time.monotonic() - _JOB_TTL
        expired = [jid for jid, j in list(_jobs.items()) if j["created_at"] < cutoff]
        for jid in expired:
            _jobs.pop(jid, None)
        if expired:
            log.info("[job_store] Cleaned up %d expired jobs", len(expired))


def start_background_tasks() -> None:
    """Called from kaare_api.py startup to start the cleanup loop."""
    asyncio.create_task(_cleanup_loop())
    log.info("[job_store] Background cleanup started (TTL=%ds)", _JOB_TTL)
