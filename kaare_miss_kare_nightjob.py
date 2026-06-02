#!/usr/bin/env python3
"""
kaare_miss_kare_nightjob.py — per-user portrait synthesis.

For each active user: reads the last 24h of interactions from LTM, calls Miss Kåre (9B)
for 1–3 dated observations, and appends them to miss_kare_portrait.md.

Called from kaare_night_sequence.py (step 2, after nightjob, before reflection).
"""

import asyncio
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/kaare")

from kaare_core.model_lock import lock_11445, LockTimeout
from kaare_core.users.store import list_users
from kaare_core.users.profile_manager import get_display_name
from adapters.llm_adapter import call_llm_chat

_DB_PATH = Path("/kaare/state/memory/interactions.db")
_USERS_DIR = Path("/kaare/state/users")
_PORTRAIT_CAP = 3000  # chars — identical to _PERSONALITY_SELF_CAP in llm_adapter.py
_OBS_MAX_DAYS = 90    # same as OBS_MAX_DAYS in profile_manager.py

log = logging.getLogger("miss_kare_nightjob")


# ── File paths ─────────────────────────────────────────────────────────────────

def _portrait_path(user_id: str) -> Path:
    return _USERS_DIR / user_id / "miss_kare_portrait.md"


# ── Database ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _fetch_today_interactions(user_id: str) -> list[dict]:
    """Return all interactions for user_id in the last 24 hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        cur = conn.execute(
            """SELECT ts, prompt, response
               FROM interactions
               WHERE user_id = ? AND ts >= ?
                 AND outcome IN ('success', 'llm_fallback')
               ORDER BY id ASC""",
            (user_id, since),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


# ── LLM prompt ────────────────────────────────────────────────────────────────

def _build_prompt(name: str, today: str, interactions: list[dict]) -> str:
    lines = [
        f"Du er Miss Kåre — varm, moderlig observatør. Her er dagens samtaler mellom {name} og Kåre.",
        f"Skriv 1–3 korte, datostemplede observasjoner om {name} — noe som berørte deg, et mønster",
        "du ser, noe nytt du lærte om dem. Skriv kun observasjoner som er verdt å huske.",
        "Skriv INGENTING hvis det ikke var noe bemerkelsesverdig i dag.",
        "",
        f"Format: - [{today}] observasjon",
        "",
        "--- SAMTALER ---",
    ]
    for ix in interactions:
        ts = ix["ts"][:16].replace("T", " ")
        prompt = (ix["prompt"] or "")[:150].replace("\n", " ")
        resp = (ix["response"] or "")[:150].replace("\n", " ")
        lines.append(f"[{ts}] Bruker: {prompt} | Kåre: {resp}")
    return "\n".join(lines)


# ── Portrait file management ──────────────────────────────────────────────────

def _append_observations(user_id: str, text: str) -> None:
    """Append only valid dated observation lines to the portrait file."""
    path = _portrait_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for line in text.strip().splitlines():
            line = line.strip()
            if re.match(r"^- \[\d{4}-\d{2}-\d{2}\]", line):
                f.write(line + "\n")


def _trim_portrait(user_id: str) -> None:
    """Remove entries older than _OBS_MAX_DAYS. Identical logic to _trim_observations()."""
    path = _portrait_path(user_id)
    if not path.exists():
        return
    cutoff = (datetime.now() - timedelta(days=_OBS_MAX_DAYS)).strftime("%Y-%m-%d")
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = []
    for line in lines:
        m = re.match(r"^- \[(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            if m.group(1) >= cutoff:
                kept.append(line)
        elif line.strip():
            kept.append(line)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _cap_portrait(user_id: str) -> None:
    """Keep only the most recent entries — identical logic to _cap_personality_self()."""
    path = _portrait_path(user_id)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if len(text) <= _PORTRAIT_CAP:
        return
    truncated = text[-_PORTRAIT_CAP:]
    nl = truncated.find("\n")
    if nl > 0:
        truncated = truncated[nl + 1:]
    path.write_text("[… eldre observasjoner ikke vist]\n" + truncated, encoding="utf-8")


# ── Per-user processing ───────────────────────────────────────────────────────

async def _process_user(user_id: str) -> None:
    name = get_display_name(user_id) or user_id
    interactions = _fetch_today_interactions(user_id)
    if not interactions:
        log.info("No interactions for %s today — skipping.", user_id)
        return

    log.info("Synthesising portrait for %s (%d interactions).", user_id, len(interactions))
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = _build_prompt(name, today, interactions)

    try:
        async with lock_11445("miss_kare_nightjob", max_wait=120):
            result = await call_llm_chat(
                "miss_kare",
                [{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 300},
            )
    except LockTimeout:
        log.error("Could not acquire model lock for %s — skipping.", user_id)
        return

    if not result.get("ok"):
        log.warning("Portrait LLM call failed for %s: %s", user_id, result.get("error"))
        return

    observations = result.get("text", "").strip()
    if not observations:
        log.info("LLM returned empty response for %s — skipping.", user_id)
        return

    _append_observations(user_id, observations)
    _trim_portrait(user_id)
    _cap_portrait(user_id)
    log.info("Portrait updated for %s.", user_id)


# ── Entry point ───────────────────────────────────────────────────────────────

async def run() -> None:
    log.info("=== Miss Kåre portrait nightjob starting ===")
    users = [
        u["username"]
        for u in list_users()
        if u["is_active"] and u["username"] != "admin"
    ]
    if not users:
        log.info("No active users — nothing to do.")
        log.info("=== Miss Kåre portrait nightjob done ===")
        return

    for user_id in users:
        try:
            await _process_user(user_id)
        except Exception as e:
            log.error("Portrait job failed for %s: %s", user_id, e)

    log.info("=== Miss Kåre portrait nightjob done ===")


if __name__ == "__main__":
    asyncio.run(run())
