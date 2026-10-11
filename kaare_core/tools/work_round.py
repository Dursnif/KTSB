"""
Web search as a work round (P122) — Kåre sends a slow search off and answers the user at once.

  1. Kåre calls web_search / library with `background: true` (his judgement, no classifier).
  2. The search runs as a background job (Mechanic's job_store runner) with a longer deadline;
     Kåre gets «running, id …» immediately and answers the user.
  3. When it is done, the result goes to KÅRE, not the user: an internal Kåre turn
     (POST /api/generate, source «work_round», service token — the same path timers use) gets
     the result; Kåre's interpreted answer is pushed to the chat (queue_chat_notification).
  4. STM never gets the raw result: router_generate stores only the first line of a
     «work_round» prompt (the marker), plus Kåre's answer.

A work-round turn runs its own searches directly (never another background round), so a
re-prompt by Kåre can't chain work rounds forever.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from adapters import web_fetch
from kaare_core.agents.mechanic import job_store
from kaare_core.config import get_service
from kaare_core.service_auth import service_headers
from kaare_core.tools.i18n import get_lang, t
from kaare_core.tools.timer_service import queue_chat_notification

log = logging.getLogger(__name__)

SOURCE = "work_round"
_LIBRARY_ACTIONS = ("search", "law", "online", "fetch_url")
_KARE_TURN_TIMEOUT_S = 300


def wants_background(name: str, args: dict) -> bool:
    """True when Kåre asked for a work round on a tool that supports it."""
    if args.get("_source") == SOURCE:
        return False  # inside a work-round turn: search directly, never chain another round
    flag = args.get("background")
    if not (flag is True or str(flag).strip().lower() == "true"):
        return False
    if name in ("web_search", "søk_nett"):
        return True
    return name == "library" and args.get("action") in _LIBRARY_ACTIONS


def stm_text(prompt: str, source: str) -> str:
    """What STM keeps of a turn's user text: for a work-round turn only the marker line."""
    if source == SOURCE:
        return (prompt or "").split("\n", 1)[0]
    return prompt


def start(name: str, args: dict, run) -> str:
    """Start `run(name, args)` (the executor's own dispatch) as a background job and return the
    text Kåre gets at once. The job's arguments get a longer deadline (`_deadline`)."""
    user_id = args.get("_user_id", "global")
    rid = args.get("_rid", "")
    lang = get_lang(user_id)
    question = str(args.get("query") or args.get("url") or args.get("title") or "").strip()
    deadline = web_fetch.setting("background_timeout_s")
    call = {k: v for k, v in args.items() if k != "background"}
    call["_deadline"] = deadline

    async def work(job: dict) -> str:
        return await asyncio.wait_for(run(name, call), timeout=deadline)

    job_id = job_store.start_background(
        work, kind=SOURCE, user_id=user_id, on_done=_deliver(user_id, rid, question, lang)
    )
    return t("work_round_started", lang, job=job_id[:8])


def _deliver(user_id: str, rid: str, question: str, lang: str):
    async def deliver(job_id: str, job: dict) -> None:
        if not user_id or user_id == "global":
            return
        result = job["result"] or ""
        if job["status"] != "done":
            result = t("work_round_failed", lang, reason=result[:300])
        prompt = t("work_round_prompt_head", lang, question=question) + "\n\n" + result
        async with httpx.AsyncClient(timeout=_KARE_TURN_TIMEOUT_S) as client:
            r = await client.post(
                f"{get_service('internal', 'kaare_api')}/api/generate",
                json={"prompt": prompt, "source": SOURCE, "user_id": user_id},
                headers=service_headers(),  # acts for the job's owner, like a timer (P120)
            )
            r.raise_for_status()
            text = (r.json().get("text") or "").strip()
        if text:
            queue_chat_notification(user_id, text, rid=rid, source_id=job_id)
        log.info(
            "[work_round] %s delivered to %s via Kåre (%d chars)", job_id[:8], user_id, len(text)
        )

    return deliver
