#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kaare_nightjob.py
-----------------
Kåres nattjobb — kjøres daglig kl. 03:00 via systemd-timer.

Hva den gjør:
  1. Henter interaksjoner fra LTM som ikke er komprimert ennå
  2. Grupperer i bolker på ~20 interaksjoner
  3. Kaller LLM for å lage et kort narrativt sammendrag per bolk
  4. Lagrer sammendraget i episodes-tabellen
  5. Logger resultatet

Episodene komprimeres og indekseres i Qdrant (semantisk minnelag).
"""

import asyncio
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
import sys
sys.path.insert(0, "/kaare")
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, PointIdsList
from kaare_core.config import get_service as _svc, get_qdrant_api_key as _qdrant_key
from kaare_core.memory.long_term import _ENC_PREFIX
from kaare_core.memory.semantic_memory import ensure_collection, index_episode
from adapters.llm_adapter import call_llm_chat as _llm_chat

# ──────────────────────────────────────────────────────────────────────────────
# Konfigurasjon
# ──────────────────────────────────────────────────────────────────────────────

DB_PATH            = Path("/kaare/state/memory/interactions.db")
ALIAS_OUT          = Path("/kaare/state/memory/alias_suggestions.yaml")
LOG_PATH           = Path("/kaare/logs/kaare_nightjob.log")
JING_THOUGHTS_PATH = Path("/kaare/state/jing_thoughts.txt")
JING_STATE_PATH    = Path("/kaare/state/jing_last_processed.json")
BATCH_SIZE         = 20   # interaksjoner per episode (per-user)
GLOBAL_BATCH_SIZE  = 30   # jing entries per global episode (shorter entries)

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
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _last_episode_to_id(conn: sqlite3.Connection) -> int:
    """Returnerer to_id for siste episode, eller 0 hvis ingen finnes."""
    row = conn.execute("SELECT MAX(to_id) FROM episodes").fetchone()
    return row[0] or 0


def _fetch_unprocessed(conn: sqlite3.Connection, after_id: int, limit: int) -> list[dict]:
    """Henter interaksjoner som ikke er komprimert ennå."""
    cur = conn.execute(
        """SELECT id, ts, user_id, prompt, intent, entity_id, action, response, outcome, feedback, confidence
           FROM interactions
           WHERE id > ? AND outcome IN ('success', 'llm_fallback')
           ORDER BY id ASC
           LIMIT ?""",
        (after_id, limit),
    )
    return [dict(row) for row in cur.fetchall()]


def _dominant_user(interactions: list[dict]) -> str:
    """Returnerer user_id som forekommer flest ganger i batchen."""
    from collections import Counter
    counts = Counter(ix.get("user_id", "global") for ix in interactions)
    return counts.most_common(1)[0][0] if counts else "global"


def _save_episode(
    conn: sqlite3.Connection,
    from_id: int,
    to_id: int,
    count: int,
    narrative: str,
    topics: str,
    confidence: float,
    user_id: str = "global",
) -> int:
    cur = conn.execute(
        """INSERT INTO episodes (ts_created, user_id, from_id, to_id, interaction_count, narrative, topics, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            user_id, from_id, to_id, count, narrative, topics, confidence,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ──────────────────────────────────────────────────────────────────────────────
# LLM-komprimering
# ──────────────────────────────────────────────────────────────────────────────

def _build_compression_prompt(interactions: list[dict]) -> str:
    lines = [
        "Du er Kåres hukommelsessystem. Her er et sett med interaksjoner fra de siste dagene.",
        "Lag et kort og presist sammendrag (maks 5 setninger) som beskriver:",
        "- Hva brukeren spurte om eller ba om",
        "- Hvilke enheter eller rom som var involvert",
        "- Hva som fungerte, hva som ikke fungerte",
        "- Eventuelle mønstre du ser",
        "",
        "Svar KUN med sammendraget — ingen introduksjon, ingen kommentarer.",
        "Skriv på norsk.",
        "",
        "--- INTERAKSJONER ---",
    ]
    for ix in interactions:
        ts     = ix["ts"][:16].replace("T", " ")
        raw_p  = ix["prompt"] or ""
        raw_r  = ix["response"] or ""
        prompt = ("[kryptert]" if raw_p.startswith(_ENC_PREFIX) else raw_p[:120]).replace("\n", " ")
        resp   = ("[kryptert]" if raw_r.startswith(_ENC_PREFIX) else raw_r[:120]).replace("\n", " ")
        entity = f" [{ix['entity_id']}]" if ix.get("entity_id") else ""
        lines.append(
            f"[{ts}]{entity} Bruker: {prompt} | Svar: {resp} | Utfall: {ix['outcome']}"
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


async def _compress_with_llm(interactions: list[dict]) -> str:
    prompt = _build_compression_prompt(interactions)
    result = await _llm_chat(
        "default",
        [{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": 400},
    )
    if not result.get("ok"):
        log.warning("LLM-komprimering feilet: %s", result.get("error", "ukjent"))
    return result.get("text", "").strip()


# ──────────────────────────────────────────────────────────────────────────────
# Hoved-løkke
# ──────────────────────────────────────────────────────────────────────────────

def _build_stm_summary_prompt(interactions: list[dict]) -> str:
    lines = [
        "You are Kåre's memory system. Below are all interactions from the past 24 hours.",
        "Write a short summary (5–7 sentences in Norwegian) that captures:",
        "- What the user asked about or requested",
        "- Which rooms or devices were involved",
        "- What worked and what didn't",
        "- Any recurring themes or patterns",
        "- Anything that seems important to remember for tomorrow",
        "",
        "Reply ONLY with the summary — no introduction, no comments.",
        "Write in Norwegian.",
        "",
        "--- INTERACTIONS ---",
    ]
    for ix in interactions:
        ts     = ix["ts"][:16].replace("T", " ")
        prompt = (ix["prompt"] or "")[:100].replace("\n", " ")
        resp   = (ix["response"] or "")[:100].replace("\n", " ")
        entity = f" [{ix['entity_id']}]" if ix.get("entity_id") else ""
        lines.append(f"[{ts}]{entity} User: {prompt} | Reply: {resp} | Outcome: {ix['outcome']}")
    return "\n".join(lines)


async def compress_daily_stm(conn: sqlite3.Connection) -> None:
    """Compress yesterday's interactions into per-user daily STM summaries and save to DB."""
    from collections import defaultdict
    from datetime import date, timedelta
    from kaare_core.memory.long_term import save_daily_summary

    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    rows = conn.execute(
        """SELECT id, ts, user_id, prompt, response, entity_id, intent, outcome
           FROM interactions
           WHERE ts >= ? AND ts < ?
           ORDER BY id ASC""",
        (yesterday + "T00:00:00", today + "T00:00:00"),
    ).fetchall()

    if not rows:
        log.info("No interactions for %s — skipping STM daily summary.", yesterday)
        return

    fields = ["id", "ts", "user_id", "prompt", "response", "entity_id", "intent", "outcome"]
    by_user: dict[str, list] = defaultdict(list)
    for r in rows:
        ix = dict(zip(fields, r))
        by_user[ix["user_id"]].append(ix)

    for uid, interactions in by_user.items():
        existing = conn.execute(
            "SELECT id FROM stm_daily_summary WHERE date = ? AND user_id = ?", (today, uid)
        ).fetchone()
        if existing:
            log.info("STM daily summary for %s/%s already exists — skipping.", today, uid)
            continue

        log.info("Building STM daily summary for %s user=%s (%d interactions).", yesterday, uid, len(interactions))
        prompt = _build_stm_summary_prompt(interactions)
        result = await _llm_chat(
            "default",
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 350},
        )
        if not result.get("ok"):
            log.warning("STM daily summary LLM call failed for %s: %s", uid, result.get("error", "ukjent"))
            continue
        summary = result.get("text", "").strip()
        if not summary:
            log.warning("LLM returned empty STM daily summary for %s — skipping.", uid)
            continue
        save_daily_summary(today, summary, len(interactions), user_id=uid)
        log.info("STM daily summary saved for %s/%s: %s…", today, uid, summary[:80])


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
        content = block[ts_match.end():].strip()
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
    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """INSERT INTO interactions (ts, prompt, source, response, user_id, confidence)
           VALUES (?, ?, 'jing', '', 'global', 0.8)""",
        (ts_str, f"{ts_label}\n{content}"),
    )
    conn.commit()


def _last_global_episode_to_id(conn: sqlite3.Connection) -> int:
    """Return the last interaction id covered by a global episode, or 0 if none."""
    row = conn.execute(
        "SELECT MAX(to_id) FROM episodes WHERE user_id = 'global'"
    ).fetchone()
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
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _build_global_compression_prompt(interactions: list[dict]) -> str:
    lines = [
        "Du er Kåres hukommelsessystem. Her er et sett med hendelser fra hjemmet — registrert av Jing (Kåres raske indre stemme).",
        "Lag et kort og presist sammendrag (maks 5 setninger) som beskriver:",
        "- Hvilke hjemhendelser som skjedde (bevegelse, kamera, lys, osv.)",
        "- Om noen var til stede hjemme",
        "- Eventuelle mønstre eller uvanlige hendelser",
        "",
        "Svar KUN med sammendraget — ingen introduksjon, ingen kommentarer.",
        "Skriv på norsk.",
        "",
        "--- HENDELSER ---",
    ]
    for ix in interactions:
        ts = ix["ts"][:16].replace("T", " ")
        prompt = ix["prompt"][:200].replace("\n", " ")
        lines.append(f"[{ts}] {prompt}")
    return "\n".join(lines)


async def _compress_global_with_llm(interactions: list[dict]) -> str:
    prompt = _build_global_compression_prompt(interactions)
    result = await _llm_chat(
        "default",
        [{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": 300},
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
    new_content = content[last_offset:]

    if new_content.strip():
        blocks = _parse_jing_blocks(new_content)
        inserted = 0
        for ts_label, block_content in blocks:
            _insert_global_interaction(conn, ts_label, block_content)
            inserted += 1
        if inserted:
            log.info("Ingested %d new Jing entries as global interactions", inserted)
        _save_jing_state({"last_offset": len(content.encode("utf-8"))})
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

        narrative = await _compress_global_with_llm(batch)
        if not narrative:
            log.warning("LLM gav tomt svar for global batch — hopper over")
            after_id = to_id
            continue

        topics = _extract_topics(batch)
        ep_id = _save_episode(conn, from_id, to_id, count, narrative, topics, 0.8, user_id="global")
        log.info("Global episode %d lagret (topics: %s)", ep_id, topics or "ingen")

        # 3. Index in Qdrant — user_id='global' means all users can see this
        indexed = await index_episode(
            episode_id=ep_id,
            narrative=narrative,
            topics=topics,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            from_id=from_id,
            to_id=to_id,
            interaction_count=count,
            user_id="global",
        )
        if indexed:
            log.info("Global episode %d indeksert i Qdrant.", ep_id)
        else:
            log.warning("Global episode %d ble IKKE indeksert i Qdrant.", ep_id)

        after_id = to_id
        global_episodes_made += 1

    log.info("=== Global komprimering ferdig: %d episoder ===", global_episodes_made)


async def run_nightjob() -> None:
    log.info("=== Kåre nattjobb starter ===")

    # Sørg for at Qdrant-collection eksisterer
    try:
        ensure_collection()
    except Exception as e:
        log.warning("Qdrant collection-sjekk feilet: %s — fortsetter uten Qdrant.", e)

    conn = _get_conn()
    after_id     = _last_episode_to_id(conn)
    episodes_made = 0
    total_compressed = 0

    log.info("Siste episode dekket tom interaction_id=%d", after_id)

    while True:
        batch = _fetch_unprocessed(conn, after_id, BATCH_SIZE)
        if not batch:
            log.info("Ingen flere interaksjoner å komprimere.")
            break

        from_id = batch[0]["id"]
        to_id   = batch[-1]["id"]
        count   = len(batch)
        log.info("Komprimerer batch: id %d–%d (%d interaksjoner)", from_id, to_id, count)

        narrative = await _compress_with_llm(batch)
        if not narrative:
            log.warning("LLM ga tomt svar — hopper over denne bolken.")
            after_id = to_id
            continue

        topics     = _extract_topics(batch)
        # Confidence: gjennomsnitt av interaksjonenes confidence
        avg_conf   = sum(ix.get("confidence", 0.5) for ix in batch) / count
        batch_user = _dominant_user(batch)
        ep_id = _save_episode(conn, from_id, to_id, count, narrative, topics, round(avg_conf, 3), user_id=batch_user)

        log.info("Episode %d lagret (conf=%.2f, topics: %s)", ep_id, avg_conf, topics or "ingen")
        log.info("Narrativ: %s", narrative[:200])

        # Indekser i Qdrant (semantisk lag)
        indexed = await index_episode(
            episode_id=ep_id,
            narrative=narrative,
            topics=topics,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            from_id=from_id,
            to_id=to_id,
            interaction_count=count,
            user_id=batch_user,
        )
        if indexed:
            log.info("Episode %d indeksert i Qdrant.", ep_id)
        else:
            log.warning("Episode %d ble IKKE indeksert i Qdrant.", ep_id)

        after_id = to_id
        episodes_made   += 1
        total_compressed += count

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

    # Slett argus-hendelser eldre enn 30 dager
    # ts er lagret som ISO-streng — bruk scroll+Python-sammenligning (Range krever float)
    try:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        qc = QdrantClient(url=_svc("storage", "qdrant"), api_key=_qdrant_key(write=True))
        ids_to_delete: list = []
        offset = None
        while True:
            scroll_result = qc.scroll(
                collection_name="argus_events",
                limit=200,
                offset=offset,
                with_payload=["ts"],
                with_vectors=False,
            )
            for point in scroll_result[0]:
                ts_val = (point.payload or {}).get("ts", "")
                if ts_val and ts_val < cutoff_iso:
                    ids_to_delete.append(point.id)
            offset = scroll_result[1]
            if offset is None:
                break
        if ids_to_delete:
            qc.delete(
                collection_name="argus_events",
                points_selector=PointIdsList(points=ids_to_delete),
            )
            log.info("Argus-cleanup: slettet %d hendelser eldre enn %s", len(ids_to_delete), cutoff_iso[:10])
        else:
            log.info("Argus-cleanup: ingen gamle hendelser å slette.")
    except Exception as e:
        log.warning("Argus-cleanup feil: %s — continuing.", e)

    conn.close()
    log.info(
        "=== Nattjobb ferdig: %d episoder laget, %d interaksjoner komprimert ===",
        episodes_made, total_compressed,
    )


if __name__ == "__main__":
    asyncio.run(run_nightjob())
