#!/usr/bin/env python3
"""
kaare_nightjob.py
-----------------
Kåres nattjobb — kjøres daglig kl. 03:00 via systemd-timer.

Hva den gjør:
  1. Henter interaksjoner fra LTM som ikke er komprimert ennå — per bruker (P146)
  2. Grupperer i bolker på ~20 interaksjoner fra én og samme bruker
  3. Kaller LLM for å lage et kort narrativt sammendrag per bolk
  4. Lagrer sammendraget i episodes-tabellen under den brukeren
  5. Kameraminnet (frigate.db) → én LTM-episode per ferdig døgn, eier «frigate» (P144-s17)
  6. Logger resultatet (bare id-er, antall og brukernavn — aldri minnetekst)

Episodene komprimeres og indekseres i Qdrant (semantisk minnelag).
"""

import asyncio
import json
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaare_core.storage import sqlite_files

sys.path.insert(0, "/kaare")

from adapters.llm_adapter import call_llm_chat as _llm_chat
from kaare_core import receipts
from kaare_core.config import get_memory_embed_config, now_local
from kaare_core.memory import frigate_ltm
from kaare_core.memory.long_term import (
    _ENC_PREFIX,
    LTM_CURSOR_FLOOR,
    USER_GLOBAL,
    _dec,
    _enc,
    ensure_schema,
    save_daily_skip,
    save_daily_summary,
)
from kaare_core.memory.ltm_mixed_cleanup import run_once as run_mixed_cleanup
from kaare_core.memory.semantic_memory import ensure_collection, index_episode
from kaare_core.memory.semantic_reindex import reindex_missing
from kaare_core.normalcy import compute_baseline as _compute_baseline
from kaare_core.retention import argus_size
from kaare_core.tools.i18n import get_lang as _get_lang
from kaare_core.tools.i18n import t as _t_i18n

# ──────────────────────────────────────────────────────────────────────────────
# Konfigurasjon
# ──────────────────────────────────────────────────────────────────────────────

DB_PATH = Path("/kaare/state/memory/interactions.db")
ALIAS_OUT = Path("/kaare/state/memory/alias_suggestions.yaml")
LOG_PATH = Path("/kaare/logs/kaare_nightjob.log")
JING_THOUGHTS_PATH = Path("/kaare/state/jing_thoughts.txt")
JING_STATE_PATH = Path("/kaare/state/jing_last_processed.json")
BATCH_SIZE = 20  # interaksjoner per episode (per-user)
# Ids that are not persons (P146): «global» has its own loop (Jing + unauthenticated voice), «kare» none yet,
# «frigate» gets its own loop in P144. Never compressed into a person's episodes, never a person's rows under them.
NON_PERSON_USERS = (USER_GLOBAL, "kare", "frigate")
_PERSONAL_OUTCOMES = ("success", "llm_fallback")
GLOBAL_BATCH_SIZE = 30  # jing entries per global episode (shorter entries)
# Internal safety bound (P146 decision, built in P144-s17 — not a GUI setting): at most this many compression batches
# (= 27B calls) per loop per night, for the per-user loop and the Frigate step each. Nothing is lost when it stops —
# a cursor/marker only moves with a saved episode. 60 × ~15 s ≈ 15 min, well inside the night sequence's 60 min.
MAX_BATCHES_PER_NIGHT = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("nightjob")


# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────


def _get_conn() -> sqlite3.Connection:
    conn = sqlite_files.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _user_cursor(conn: sqlite3.Connection, user_id: str) -> int:
    """Last interaction id compressed for this user (P146); a user without a row starts at the upgrade floor."""
    row = conn.execute("SELECT last_id FROM ltm_cursors WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT last_id FROM ltm_cursors WHERE user_id = ?", (LTM_CURSOR_FLOOR,)
        ).fetchone()
    return row[0] if row else 0


def _users_to_compress(conn: sqlite3.Connection) -> list[str]:
    """Persons with interactions above the lowest cursor — each is then fetched from its own cursor."""
    row = conn.execute("SELECT MIN(last_id) FROM ltm_cursors").fetchone()
    lowest = row[0] if row and row[0] is not None else 0
    marks = ",".join("?" * len(NON_PERSON_USERS))
    out_marks = ",".join("?" * len(_PERSONAL_OUTCOMES))
    cur = conn.execute(
        f"""SELECT DISTINCT user_id FROM interactions
            WHERE id > ? AND outcome IN ({out_marks}) AND user_id NOT IN ({marks})
            ORDER BY user_id""",
        (lowest, *_PERSONAL_OUTCOMES, *NON_PERSON_USERS),
    )
    return [r[0] for r in cur.fetchall()]


def _decrypt_rows(rows: list[dict]) -> list[dict]:
    """Decrypt prompt/response in-place using the system key (P87).

    This job runs unattended at 03:00 — no live session ever exists here. Before this
    fix, raw ciphertext (or, worse, the un-checked case in _build_stm_summary_prompt)
    was fed straight into the LLM compression prompt, producing the confabulated
    narrative summaries described in P87's Background. allow_system_key=True is safe
    here: it can only decrypt a row's *own* user_id, never another user's data.
    """
    for row in rows:
        uid = row.get("user_id", "global")
        row["prompt"] = _dec(uid, row.get("prompt") or "", allow_system_key=True)
        row["response"] = _dec(uid, row.get("response") or "", allow_system_key=True)
    return rows


def _fetch_unprocessed(
    conn: sqlite3.Connection, user_id: str, after_id: int, limit: int
) -> list[dict]:
    """One user's interactions that are not compressed yet (P146 — never rows of another user)."""
    out_marks = ",".join("?" * len(_PERSONAL_OUTCOMES))
    cur = conn.execute(
        f"""SELECT id, ts, user_id, prompt, intent, entity_id, action, response, outcome, feedback, confidence
           FROM interactions
           WHERE id > ? AND user_id = ? AND outcome IN ({out_marks})
           ORDER BY id ASC
           LIMIT ?""",
        (after_id, user_id, *_PERSONAL_OUTCOMES, limit),
    )
    return _decrypt_rows([dict(row) for row in cur.fetchall()])


def _save_episode(
    conn: sqlite3.Connection,
    from_id: int,
    to_id: int,
    count: int,
    narrative: str,
    topics: str,
    confidence: float,
    user_id: str = "global",
    allow_system_key: bool = False,
    row_user_ids: list[str] | None = None,
    advance_cursor: bool = False,
) -> int:
    """Insert a compressed episode. narrative/topics are encrypted at rest for personal
    users (P87 Phase 6 — was plaintext-by-omission before this; _enc() no-ops for
    user_id='global'). allow_system_key=True: this job always runs unattended.

    P146: refuses an episode whose rows belong to anyone but its owner (row_user_ids), and with
    advance_cursor moves the owner's compression cursor to to_id in the same commit."""
    foreign = sorted({u for u in (row_user_ids or []) if u != user_id})
    if foreign:
        raise ValueError(f"episode for {user_id} holds rows of {foreign} (ids {from_id}–{to_id})")
    stored_narrative = _enc(user_id, narrative, allow_system_key=allow_system_key)
    stored_topics = _enc(user_id, topics, allow_system_key=allow_system_key)
    cur = conn.execute(
        """INSERT INTO episodes (ts_created, user_id, from_id, to_id, interaction_count, narrative, topics, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(UTC).isoformat(timespec="seconds"),
            user_id,
            from_id,
            to_id,
            count,
            stored_narrative,
            stored_topics,
            confidence,
        ),
    )
    if advance_cursor:
        conn.execute(
            """INSERT INTO ltm_cursors (user_id, last_id, ts_updated) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET last_id = excluded.last_id, ts_updated = excluded.ts_updated""",
            (user_id, to_id, datetime.now(UTC).isoformat(timespec="seconds")),
        )
    conn.commit()
    receipts.log_saved("ltm_episode", where="interactions.db", refs=[f"{user_id}:{cur.lastrowid}"])
    return cur.lastrowid


# ──────────────────────────────────────────────────────────────────────────────
# LLM-komprimering
# ──────────────────────────────────────────────────────────────────────────────


def _build_compression_prompt(interactions: list[dict], lang: str = "nb") -> str:
    encrypted_label = _t_i18n("nightjob_encrypted_placeholder", lang)
    label_user = _t_i18n("nightjob_label_user", lang)
    label_reply = _t_i18n("nightjob_label_reply", lang)
    label_outcome = _t_i18n("nightjob_label_outcome", lang)
    lines = [
        _t_i18n("nightjob_ltm_header", lang),
        _t_i18n("nightjob_ltm_instructions", lang),
        "",
        _t_i18n("nightjob_sep_interactions", lang),
    ]
    for ix in interactions:
        ts = ix["ts"][:16].replace("T", " ")
        # Rows are decrypted upstream (_decrypt_rows, P87) — a lingering ENC: prefix here
        # means the system key genuinely couldn't resolve, so this still degrades safely.
        raw_p = ix["prompt"] or ""
        raw_r = ix["response"] or ""
        prompt = (encrypted_label if raw_p.startswith(_ENC_PREFIX) else raw_p[:120]).replace(
            "\n", " "
        )
        resp = (encrypted_label if raw_r.startswith(_ENC_PREFIX) else raw_r[:120]).replace(
            "\n", " "
        )
        entity = f" [{ix['entity_id']}]" if ix.get("entity_id") else ""
        lines.append(
            f"[{ts}]{entity} {label_user}: {prompt} | {label_reply}: {resp} | {label_outcome}: {ix['outcome']}"
        )
    return "\n".join(lines)


def _extract_topics(interactions: list[dict]) -> str:
    """Enkel topic-ekstraksjon fra entity_id-er og intent-typer."""
    topics: set[str] = set()
    for ix in interactions:
        if ix.get("entity_id"):
            # "light.taklys_verksted" → "taklys_verksted"
            eid = ix["entity_id"].split(".")[-1] if "." in ix["entity_id"] else ix["entity_id"]
            topics.add(eid)
        if ix.get("intent") and ix["intent"] not in ("tools", ""):
            topics.add(ix["intent"].lower())
    return ", ".join(sorted(topics)[:10])


async def _compress_with_llm(interactions: list[dict], lang: str = "nb") -> str:
    prompt = _build_compression_prompt(interactions, lang)
    result = await _llm_chat(
        "default",
        [{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": 400},
        disable_thinking=True,
    )
    if not result.get("ok"):
        log.warning("LLM-komprimering feilet: %s", result.get("error", "ukjent"))
    return result.get("text", "").strip()


# ──────────────────────────────────────────────────────────────────────────────
# Hoved-løkke
# ──────────────────────────────────────────────────────────────────────────────


def _build_stm_summary_prompt(interactions: list[dict], lang: str = "nb") -> str:
    label_user = _t_i18n("nightjob_label_user", lang)
    label_reply = _t_i18n("nightjob_label_reply", lang)
    label_outcome = _t_i18n("nightjob_label_outcome", lang)
    lines = [
        _t_i18n("nightjob_stm_header", lang),
        _t_i18n("nightjob_stm_instructions", lang),
        "",
        _t_i18n("nightjob_sep_interactions", lang),
    ]
    for ix in interactions:
        ts = ix["ts"][:16].replace("T", " ")
        prompt = (ix["prompt"] or "")[:100].replace("\n", " ")
        resp = (ix["response"] or "")[:100].replace("\n", " ")
        entity = f" [{ix['entity_id']}]" if ix.get("entity_id") else ""
        lines.append(
            f"[{ts}]{entity} {label_user}: {prompt} | {label_reply}: {resp} | {label_outcome}: {ix['outcome']}"
        )
    return "\n".join(lines)


async def compress_daily_stm(conn: sqlite3.Connection) -> None:
    """Compress yesterday's interactions into per-user daily STM summaries and save to DB."""
    today = now_local().date().isoformat()
    yesterday = (now_local().date() - timedelta(days=1)).isoformat()

    rows = conn.execute(
        """SELECT id, ts, user_id, prompt, response, entity_id, intent, outcome
           FROM interactions
           WHERE ts >= ? AND ts < ?
           ORDER BY id ASC""",
        (yesterday + "T00:00:00", today + "T00:00:00"),
    ).fetchall()

    if not rows:
        log.info("No interactions for %s — skipping STM daily summary.", yesterday)
        save_daily_skip(today, covers=yesterday, reason="no_interactions")  # P150: every day has a row
        return

    fields = ["id", "ts", "user_id", "prompt", "response", "entity_id", "intent", "outcome"]
    by_user: dict[str, list] = defaultdict(list)
    for r in rows:
        ix = dict(zip(fields, r, strict=False))
        by_user[ix["user_id"]].append(ix)
    for interactions in by_user.values():
        _decrypt_rows(interactions)

    for uid, interactions in by_user.items():
        existing = conn.execute(
            "SELECT id FROM stm_daily_summary WHERE date = ? AND user_id = ?", (today, uid)
        ).fetchone()
        if existing:
            log.info("STM daily summary for %s/%s already exists — skipping.", today, uid)
            continue

        log.info(
            "Building STM daily summary for %s user=%s (%d interactions).",
            yesterday,
            uid,
            len(interactions),
        )
        prompt = _build_stm_summary_prompt(interactions, _get_lang(uid))
        result = await _llm_chat(
            "default",
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 350},
            disable_thinking=True,
        )
        if not result.get("ok"):
            log.warning(
                "STM daily summary LLM call failed for %s: %s", uid, result.get("error", "ukjent")
            )
            continue
        summary = result.get("text", "").strip()
        if not summary:
            log.warning("LLM returned empty STM daily summary for %s — skipping.", uid)
            continue
        save_daily_summary(today, summary, len(interactions), user_id=uid, allow_system_key=True)
        log.info("STM daily summary saved for %s/%s (%d chars).", today, uid, len(summary))


# ──────────────────────────────────────────────────────────────────────────────
# Global event compression (Jing → LTM → Qdrant)
# ──────────────────────────────────────────────────────────────────────────────


def _load_jing_state() -> dict:
    try:
        return json.loads(JING_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_offset": 0}


def _save_jing_state(state: dict) -> None:
    try:
        JING_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        log.warning("Could not save jing state: %s", e)


def _parse_jing_blocks(text: str) -> list[tuple[str, str]]:
    """
    Parse a jing_thoughts.txt text fragment into (ts_label, content) pairs.
    Only returns blocks with at least some meaningful content (not all "Ingen data.").
    """
    result = []
    blocks = re.split(r"\n(?=\[Jing )", text.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        ts_match = re.match(r"(\[Jing \d{2}:\d{2}\])", block)
        if not ts_match:
            continue
        ts_label = ts_match.group(1)
        content = block[ts_match.end() :].strip()
        if not content:
            continue
        # Strip category headers and "Ingen data." to check if anything meaningful remains
        # Handles both [KATEGORI] and **KATEGORI**: and plain KATEGORI: formats
        stripped = re.sub(r"[-*\[\]]+", "", content)
        stripped = re.sub(r"(MENNESKER|ANDRE|ARGUS|STM)[\s:]*", "", stripped, flags=re.IGNORECASE)
        stripped = stripped.replace("Ingen data.", "").strip()
        if not stripped:
            continue
        result.append((ts_label, content))
    return result


def _insert_global_interaction(conn: sqlite3.Connection, ts_label: str, content: str) -> None:
    """Insert a Jing thought block as a global interaction in the LTM database."""
    ts_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """INSERT INTO interactions (ts, prompt, source, response, user_id, confidence)
           VALUES (?, ?, 'jing', '', 'global', 0.8)""",
        (ts_str, f"{ts_label}\n{content}"),
    )
    conn.commit()


def _last_global_episode_to_id(conn: sqlite3.Connection) -> int:
    """Return the last interaction id covered by a global episode, or 0 if none."""
    row = conn.execute("SELECT MAX(to_id) FROM episodes WHERE user_id = 'global'").fetchone()
    return row[0] or 0


def _fetch_unprocessed_global(conn: sqlite3.Connection, after_id: int, limit: int) -> list[dict]:
    """Fetch unprocessed global interactions (user_id='global', source='jing')."""
    cursor = conn.execute(
        """SELECT id, ts, user_id, prompt, intent, entity_id, action, response, outcome, feedback, confidence
           FROM interactions
           WHERE id > ? AND user_id = 'global' AND (repaired = 0 OR repaired IS NULL)
           ORDER BY id ASC LIMIT ?""",
        (after_id, limit),
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row, strict=False)) for row in cursor.fetchall()]


def _build_global_compression_prompt(interactions: list[dict], lang: str = "nb") -> str:
    lines = [
        _t_i18n("nightjob_global_header", lang),
        _t_i18n("nightjob_global_instructions", lang),
        "",
        _t_i18n("nightjob_sep_events", lang),
    ]
    for ix in interactions:
        ts = ix["ts"][:16].replace("T", " ")
        prompt = ix["prompt"][:200].replace("\n", " ")
        lines.append(f"[{ts}] {prompt}")
    return "\n".join(lines)


async def _compress_global_with_llm(interactions: list[dict], lang: str = "nb") -> str:
    prompt = _build_global_compression_prompt(interactions, lang)
    result = await _llm_chat(
        "default",
        [{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": 300},
        disable_thinking=True,
    )
    if not result.get("ok"):
        log.warning("Global LLM-komprimering feilet: %s", result.get("error", "ukjent"))
    return result.get("text", "").strip()


async def _compress_global_events(conn: sqlite3.Connection) -> None:
    """
    Steg 4: Global STM/LTM.
    1. Parse new Jing entries from jing_thoughts.txt → insert as user_id='global' interactions
    2. Compress unprocessed global interactions → global episodes
    3. Index global episodes in Qdrant (user_id='global' — visible to all users)
    """
    log.info("=== Global event compression (Jing → LTM) ===")

    # 1. Ingest new Jing entries
    if not JING_THOUGHTS_PATH.exists():
        log.info("jing_thoughts.txt not found — skipping global ingestion")
        return

    state = _load_jing_state()
    last_offset = state.get("last_offset", 0)
    content = JING_THOUGHTS_PATH.read_text(encoding="utf-8")
    if last_offset > len(content):
        log.warning(
            "Jing offset (%d) exceeds current file length (%d) — jing_thoughts.txt was "
            "likely trimmed/reset since the last run; resetting offset to 0",
            last_offset,
            len(content),
        )
        last_offset = 0
    new_content = content[last_offset:]

    if new_content.strip():
        blocks = _parse_jing_blocks(new_content)
        inserted = 0
        for ts_label, block_content in blocks:
            _insert_global_interaction(conn, ts_label, block_content)
            inserted += 1
        if inserted:
            log.info("Ingested %d new Jing entries as global interactions", inserted)
        _save_jing_state({"last_offset": len(content)})
    else:
        log.info("No new Jing entries since last run (offset=%d)", last_offset)

    # 2. Compress unprocessed global interactions → episodes
    after_id = _last_global_episode_to_id(conn)
    global_episodes_made = 0

    while True:
        batch = _fetch_unprocessed_global(conn, after_id, GLOBAL_BATCH_SIZE)
        if not batch:
            log.info("Ingen globale interaksjoner å komprimere.")
            break

        from_id = batch[0]["id"]
        to_id = batch[-1]["id"]
        count = len(batch)
        log.info("Komprimerer globale hendelser: id %d–%d (%d)", from_id, to_id, count)

        narrative = await _compress_global_with_llm(batch, _get_lang())
        if not narrative:
            # P146: never skip a batch for good — the cursor only moves with a saved episode.
            log.warning("Empty LLM reply for global ids %d–%d — retrying next night.", from_id, to_id)
            break

        topics = _extract_topics(batch)
        ep_id = _save_episode(
            conn,
            from_id,
            to_id,
            count,
            narrative,
            topics,
            0.8,
            user_id="global",
            row_user_ids=[ix["user_id"] for ix in batch],
        )
        log.info("Global episode %d lagret (topics: %s)", ep_id, topics or "ingen")

        # 3. Index in Qdrant — user_id='global' means all users can see this
        indexed = await index_episode(
            episode_id=ep_id,
            narrative=narrative,
            topics=topics,
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            from_id=from_id,
            to_id=to_id,
            interaction_count=count,
            user_id="global",
        )
        if indexed:
            log.info("Global episode %d indeksert i Qdrant.", ep_id)
        elif not get_memory_embed_config().get("enabled", False):
            log.info(
                "Global episode %d not indexed — semantic memory is off (Settings → LLM → Semantic memory).",
                ep_id,
            )
        else:
            log.warning("Global episode %d was NOT indexed in Qdrant.", ep_id)

        after_id = to_id
        global_episodes_made += 1

    log.info("=== Global komprimering ferdig: %d episoder ===", global_episodes_made)


async def compress_personal_episodes(conn: sqlite3.Connection) -> tuple[int, int]:
    """Compress each person's new interactions into episodes of that person only (P146).

    Batches never hold rows of two users; the cursor per user only moves when an episode is saved, so a failed
    LLM reply is retried next night instead of skipped for good. Returns (episodes made, interactions compressed).
    """
    episodes_made = 0
    total_compressed = 0
    calls = 0
    for uid in _users_to_compress(conn):
        after_id = _user_cursor(conn, uid)
        while True:
            batch = _fetch_unprocessed(conn, uid, after_id, BATCH_SIZE)
            if not batch:
                break
            if calls >= MAX_BATCHES_PER_NIGHT:
                log.warning(
                    "Per-night bound hit (%d batches): stopped before user=%s ids from %d — the rest waits for the "
                    "next night.", MAX_BATCHES_PER_NIGHT, uid, batch[0]["id"],
                )
                return episodes_made, total_compressed
            calls += 1

            from_id = batch[0]["id"]
            to_id = batch[-1]["id"]
            count = len(batch)
            log.info("Compressing user=%s ids %d–%d (%d interactions)", uid, from_id, to_id, count)

            narrative = await _compress_with_llm(batch, _get_lang(uid))
            if not narrative:
                log.warning(
                    "Empty LLM reply for user=%s ids %d–%d — retrying next night.", uid, from_id, to_id
                )
                break

            topics = _extract_topics(batch)
            # Confidence: gjennomsnitt av interaksjonenes confidence
            avg_conf = sum(ix.get("confidence", 0.5) for ix in batch) / count
            ep_id = _save_episode(
                conn,
                from_id,
                to_id,
                count,
                narrative,
                topics,
                round(avg_conf, 3),
                user_id=uid,
                allow_system_key=True,
                row_user_ids=[ix["user_id"] for ix in batch],
                advance_cursor=True,
            )
            log.info("Episode %d saved (user=%s, %d interactions, conf=%.2f)", ep_id, uid, count, avg_conf)

            # Indekser i Qdrant (semantisk lag)
            indexed = await index_episode(
                episode_id=ep_id,
                narrative=narrative,
                topics=topics,
                ts=datetime.now(UTC).isoformat(timespec="seconds"),
                from_id=from_id,
                to_id=to_id,
                interaction_count=count,
                user_id=uid,
            )
            if indexed:
                log.info("Episode %d indeksert i Qdrant.", ep_id)
            elif not get_memory_embed_config().get("enabled", False):
                log.info(
                    "Episode %d not indexed — semantic memory is off (Settings → LLM → Semantic memory).",
                    ep_id,
                )
            else:
                log.warning("Episode %d was NOT indexed in Qdrant.", ep_id)

            after_id = to_id
            episodes_made += 1
            total_compressed += count
    return episodes_made, total_compressed


async def compress_frigate_memory(conn: sqlite3.Connection) -> dict:
    """The Frigate user's concluded camera memory → one LTM episode per finished day, owner 'frigate' (P144-s17).

    Saved through _save_episode like every episode (status 'ok', from_id = to_id = 0: it covers no interactions row)
    and indexed like every episode. frigate has no keypair today, so _enc stores it as plain as frigate.db holds it.
    """

    async def compress(prompt: str) -> str:
        result = await _llm_chat(
            "default",
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 500},
            disable_thinking=True,
        )
        if not result.get("ok"):
            log.warning("Frigate LTM compression failed: %s", result.get("error", "ukjent"))
        return result.get("text", "")

    async def save(batch: frigate_ltm.Batch, narrative: str) -> int:
        ep_id = _save_episode(
            conn,
            0,
            0,
            len(batch.lines),
            narrative,
            batch.topics,
            batch.confidence,
            user_id=frigate_ltm.USER_ID,
            allow_system_key=True,
        )
        indexed = await index_episode(
            episode_id=ep_id,
            narrative=narrative,
            topics=batch.topics,
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            from_id=0,
            to_id=0,
            interaction_count=len(batch.lines),
            user_id=frigate_ltm.USER_ID,
        )
        if not indexed and get_memory_embed_config().get("enabled", False):
            log.warning("Frigate LTM episode %d was NOT indexed in Qdrant (the heal step retries).", ep_id)
        return ep_id

    return await frigate_ltm.run(compress, save, MAX_BATCHES_PER_NIGHT, _get_lang())


async def run_nightjob() -> None:
    log.info("=== Kåre nattjobb starter ===")

    # Sørg for at Qdrant-collection eksisterer
    try:
        ensure_collection()
    except Exception as e:
        log.warning("Qdrant collection-sjekk feilet: %s — fortsetter uten Qdrant.", e)

    episodes_made = 0
    total_compressed = 0
    try:
        ensure_schema()  # P146: episodes.status + ltm_cursors exist before the first per-user run
        try:
            # P146-s01: once per install — mark the old loop's mixed episodes, re-queue the affected persons so the
            # per-user loop below compresses their rows again (no-op once done).
            await asyncio.to_thread(run_mixed_cleanup, DB_PATH, NON_PERSON_USERS)
        except Exception as e:
            log.error("LTM mixed cleanup failed: %s — continuing.", e)
        conn = _get_conn()
        try:
            episodes_made, total_compressed = await compress_personal_episodes(conn)
        finally:
            conn.close()
    except Exception as e:
        log.error("Per-user LTM compression failed: %s — continuing.", e)

    # STM daily summary — once per day, after episode compression
    log.info("=== STM daily summary ===")
    try:
        stm_conn = _get_conn()
        await compress_daily_stm(stm_conn)
        stm_conn.close()
    except Exception as e:
        log.warning("STM daily summary failed: %s — continuing.", e)

    # Global event compression (Jing → LTM → Qdrant)
    try:
        global_conn = _get_conn()
        await _compress_global_events(global_conn)
        global_conn.close()
    except Exception as e:
        log.warning("Global event compression failed: %s — continuing.", e)

    # P144-s17: the cameras' concluded memory → LTM (owner 'frigate'), one episode per finished day
    log.info("=== Frigate memory → LTM ===")
    try:
        frigate_conn = _get_conn()
        try:
            stats = await compress_frigate_memory(frigate_conn)
        finally:
            frigate_conn.close()
        log.info("Frigate memory → LTM: %s", stats)
    except Exception as e:
        log.warning("Frigate memory → LTM failed: %s — continuing.", e)

    # P146-s01: index the 'ok' episodes the active collection lacks (an earlier index/reindex had to skip them,
    # e.g. an owner whose key the night could not read yet). Ids/counts only in the log.
    if get_memory_embed_config().get("enabled", False):
        try:
            heal = await reindex_missing()
            if heal.get("total"):
                log.info("Semantic memory heal: %s", heal)
        except Exception as e:
            log.warning("Semantic memory heal failed: %s — continuing.", e)

    # Argus is kept by size, not by age (P150, 2026-11-13): over the GUI limit (Settings → Logs/Storage),
    # the oldest days go first — each only after its rollup is written and verified. Never raises.
    argus_rep = await asyncio.to_thread(argus_size.run_nightly, "nightjob")
    log.info("Argus size limit: %s", argus_rep.get("status"))

    # Normalcy baseline — nightly recompute from Frigate event history
    try:
        _compute_baseline()
        log.info("Normalcy baseline recomputed")
    except Exception as e:
        log.warning("Normalcy baseline failed: %s", e)

    log.info(
        "=== Nattjobb ferdig: %d episoder laget, %d interaksjoner komprimert ===",
        episodes_made,
        total_compressed,
    )


if __name__ == "__main__":
    asyncio.run(run_nightjob())
