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
from pathlib import Path as _Path

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
from kaare_core.background import spawn
from kaare_core.tools.i18n import get_lang as _get_lang
from kaare_core.tools.i18n import get_lang as _lang_for_notif
from kaare_core.tools.i18n import t as _t_notif
from kaare_core.tools.timer_service import queue_chat_notification

log = logging.getLogger("mechanic.job_store")

_JOB_TTL = 1800  # seconds — completed jobs kept this long before cleanup
_CLEANUP_INTERVAL = 300
_PENDING_DIR = Path("/kaare/state/pending_mechanic")

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


# ── Generic background runner (Mechanic jobs and P122 work rounds) ─────────────


async def _run(job_id: str, work, on_done) -> None:
    """Run `work(job) -> str` for one job, record status/result, then hand over to `on_done`."""
    job = _jobs.get(job_id)
    if job is None:
        return
    try:
        result = await work(job)
        job["result"] = result
        if job["status"] == "running":
            job["status"] = "done"
        log.info("[job_store] %s job %s done: %s", job["kind"], job_id[:8], (result or "")[:80])
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["result"] = "[Cancelled]"
        log.info("[job_store] %s job %s cancelled", job["kind"], job_id[:8])
        return
    except Exception as e:
        job["status"] = "error"
        job["result"] = f"[Error: {e}]"
        log.error("[job_store] %s job %s error: %s", job["kind"], job_id[:8], e)
    if on_done is not None:
        try:
            await on_done(job_id, job)
        except Exception as e:
            log.warning("[job_store] %s job %s delivery failed: %s", job["kind"], job_id[:8], e)


def start_background(work, *, kind: str, user_id: str = "global", on_done=None) -> str:
    """Start `work(job) -> str` as a background job; returns the job_id at once. `on_done(job_id,
    job)` runs after it finished (also after an error) — e.g. pushing the result somewhere."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "kind": kind,
        "status": "running",
        "result": None,
        "created_at": time.monotonic(),
        "user_id": user_id,
        "injected": None,
    }
    # Keep a reference: a bare create_task can be garbage-collected mid-run.
    _jobs[job_id]["task"] = asyncio.create_task(_run(job_id, work, on_done))
    log.info("[job_store] Started %s job %s for user %s", kind, job_id[:8], user_id)
    return job_id


# ── Mechanic job ─────────────────────────────────────────────────────────────


async def _mechanic_work(
    job: dict, task: str, role: str, context: str, user_id: str, rid: str
) -> str:
    role_tools = {
        "undersøker": UNDERSØKER_TOOLS,
        "kritiker": KRITIKER_TOOLS,
        "analytiker": ANALYTIKER_TOOLS,
    }.get(role, MECHANIC_TOOLS)

    lang = _get_lang(user_id)
    suffix = "" if lang == "nb" else f"_{lang}"

    # Role-specific personality file if role is set and exists; default otherwise.
    # Previously written as a nested conditional expression that left `personality`
    # unassigned (UnboundLocalError) whenever role was falsy — a background task crash
    # here was never caught, so the job stayed stuck on "running" forever instead of being
    # marked "error". Rewritten plain (and now inside the generic runner's try/except).
    pers_path = _Path(__file__).parent
    personality = ""
    if role and role != "standard":
        custom = pers_path / f"personlighet_{role}{suffix}.md"
        if not custom.exists():
            custom = pers_path / f"personlighet_{role}.md"
        if custom.exists():
            personality = custom.read_text(encoding="utf-8")
    if not personality:
        default = pers_path / f"personlighet{suffix}.md"
        if not default.exists():
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
        {"role": "user", "content": task},
    ]
    return await ask_with_tools(
        messages=messages,
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        max_tool_rounds=MAX_TOOL_ROUNDS,
        timeout=TIMEOUT,
        max_tokens=MAX_TOKENS,
        job_state=job,
        tools=role_tools,
        rid=rid,
        lang=lang,
    )


def _mechanic_deliver(user_id: str, rid: str):
    async def deliver(job_id: str, job: dict) -> None:
        if not user_id or user_id == "global":
            return
        # Pending result so router_generate injects it into Kåre's next turn …
        _write_pending_result(user_id, job_id, (job["result"] or "")[:300])
        # … and surfaced proactively in chat via timer_service's queue/poll/inject pipeline
        # (frontend polls every 30s), so the user sees it without sending another message.

        _lang = _lang_for_notif(user_id)
        _key = (
            "agent_job_error_notification"
            if job["status"] == "error"
            else "agent_job_done_notification"
        )
        queue_chat_notification(
            user_id,
            _t_notif(_key, _lang, result=(job["result"] or "")[:600]),
            rid=rid,
            source_id=job_id,
        )

    return deliver


# ── Public API ────────────────────────────────────────────────────────────────


async def start_job(
    task: str,
    role: str = "standard",
    context: str = "",
    user_id: str = "global",
    rid: str = "",
) -> str:
    """Start a background Mechanic job. Returns job_id immediately.
    rid: the originating request's trace id, if any — threaded through so the job's own tool
    calls land in tool_calls.log under the same rid (see ask_with_tools' docstring)."""
    return start_background(
        lambda job: _mechanic_work(job, task, role, context, user_id, rid),
        kind="mechanic",
        user_id=user_id,
        on_done=_mechanic_deliver(user_id, rid),
    )


def get_job(job_id: str) -> dict:
    """Return job status dict, or error dict if not found."""
    job = _jobs.get(job_id)
    if job is None:
        return {
            "status": "not_found",
            "result": f"Job {job_id[:8]}… not found (expired or invalid).",
        }
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
        return {
            "status": job["status"],
            "result": "Job is no longer running — comment not delivered.",
        }
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
    spawn(_cleanup_loop())
    log.info("[job_store] Background cleanup started (TTL=%ds)", _JOB_TTL)
