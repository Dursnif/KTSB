"""
Reindekserer alle episoder fra SQLite til Qdrant med korrekt user_id.

Kjøres én gang for å rette opp at eksisterende Qdrant-punkter
mangler user_id i payload (og for å indeksere episoder 1-10 som manglet).

Bruk:
  PYTHONPATH=/mnt/ai_disk venv/bin/python scripts/reindex_episodes.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kaare_core.memory.semantic_memory import ensure_collection, index_episode

DB_PATH = Path("/kaare/state/memory/interactions.db")


async def main() -> None:
    ensure_collection()

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        """SELECT id, ts_created, user_id, from_id, to_id, interaction_count, narrative, topics
           FROM episodes ORDER BY id"""
    ).fetchall()
    conn.close()

    print(f"Fant {len(rows)} episoder. Starter reindeksering...")

    ok = 0
    fail = 0
    for row in rows:
        ep_id, ts, user_id, from_id, to_id, count, narrative, topics = row
        user_id = user_id or "global"
        success = await index_episode(
            episode_id=ep_id,
            narrative=narrative or "",
            topics=topics or "",
            ts=ts or "",
            from_id=from_id or 0,
            to_id=to_id or 0,
            interaction_count=count or 0,
            user_id=user_id,
        )
        if success:
            ok += 1
            print(f"  ✓ Episode {ep_id} (user={user_id})")
        else:
            fail += 1
            print(f"  ✗ Episode {ep_id} FEILET")

    print(f"\nFerdig: {ok} OK, {fail} feilet.")


if __name__ == "__main__":
    asyncio.run(main())
