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
from kaare_core.memory.long_term import _dec
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
    """Return all interactions for user_id in the last 24 hours.

    prompt/response may be ENC:-prefixed ciphertext (encrypted at rest, session-key-only).
    This job runs unattended at night, so _dec() is called with allow_system_key=True
    (P87) rather than requiring a live PIN session — this is the fix for the confirmed
    2026-08-01 incident where this job could only ever see "[kryptert]" placeholders and
    wrote a portrait/narrative based on zero real signal. If the system key still can't
    resolve for some reason (e.g. a user who has never logged in even once), _dec() falls
    back to the literal placeholder "[kryptert]" rather than returning ciphertext.
    Interactions where BOTH fields end up as that placeholder carry zero signal and are
    dropped; a real prompt or response for a given row is kept even if its counterpart
    is encrypted.
    """
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
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()

    usable = []
    dropped = 0
    for row in rows:
        row["prompt"] = _dec(user_id, row["prompt"], allow_system_key=True)
        row["response"] = _dec(user_id, row["response"], allow_system_key=True)
        if row["prompt"] == "[kryptert]" and row["response"] == "[kryptert]":
            dropped += 1
            continue
        usable.append(row)
    if dropped:
        log.info("Dropped %d fully-encrypted interaction(s) for %s (no session key).", dropped, user_id)
    return usable


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
# P87: previously always plaintext (Path.write_text directly) — no encryption path
# existed for this file at all, unlike observations.md/.enc. Now goes through the
# same encrypted-text helper, with allow_system_key=True since this job is always
# unattended (see _process_user call sites below).

def _append_observations(user_id: str, text: str, allow_system_key: bool = False) -> None:
    """Append only valid dated observation lines to the portrait file."""
    from kaare_core.crypto import read_user_text_file, write_user_text_file
    path = _portrait_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_lines = [
        line.strip() for line in text.strip().splitlines()
        if re.match(r"^- \[\d{4}-\d{2}-\d{2}\]", line.strip())
    ]
    if not new_lines:
        return
    existing = read_user_text_file(path, user_id, allow_system_key=allow_system_key)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    combined = existing + "\n".join(new_lines) + "\n"
    write_user_text_file(path, combined, user_id, allow_system_key=allow_system_key)


def _trim_portrait(user_id: str, allow_system_key: bool = False) -> None:
    """Remove entries older than _OBS_MAX_DAYS. Identical logic to _trim_observations()."""
    from kaare_core.crypto import read_user_text_file, write_user_text_file
    path = _portrait_path(user_id)
    content = read_user_text_file(path, user_id, allow_system_key=allow_system_key)
    if not content:
        return
    cutoff = (datetime.now() - timedelta(days=_OBS_MAX_DAYS)).strftime("%Y-%m-%d")
    lines = content.splitlines()
    kept = []
    for line in lines:
        m = re.match(r"^- \[(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            if m.group(1) >= cutoff:
                kept.append(line)
        elif line.strip():
            kept.append(line)
    write_user_text_file(path, "\n".join(kept) + "\n", user_id, allow_system_key=allow_system_key)


def _cap_portrait(user_id: str, allow_system_key: bool = False) -> None:
    """Keep only the most recent entries — identical logic to _cap_personality_self()."""
    from kaare_core.crypto import read_user_text_file, write_user_text_file
    path = _portrait_path(user_id)
    text = read_user_text_file(path, user_id, allow_system_key=allow_system_key)
    if not text or len(text) <= _PORTRAIT_CAP:
        return
    truncated = text[-_PORTRAIT_CAP:]
    nl = truncated.find("\n")
    if nl > 0:
        truncated = truncated[nl + 1:]
    write_user_text_file(path, "[… eldre observasjoner ikke vist]\n" + truncated, user_id, allow_system_key=allow_system_key)


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

    _append_observations(user_id, observations, allow_system_key=True)
    _trim_portrait(user_id, allow_system_key=True)
    _cap_portrait(user_id, allow_system_key=True)
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
