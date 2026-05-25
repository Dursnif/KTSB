"""Persistent conversation memory using SQLite.

Stores conversation summaries per speaker with topic tags.
Supports search and recent context retrieval.
"""
from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)


class ConversationMemory:
    """Persistent conversation memory.

    Args:
        db_path: Path to SQLite database file.
    """

    def __init__(self, db_path: str = "data/conversations.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker TEXT NOT NULL,
                summary TEXT NOT NULL,
                topics TEXT NOT NULL DEFAULT '[]',
                satellite_id TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_summaries_speaker ON summaries(speaker);
            CREATE INDEX IF NOT EXISTS idx_summaries_created ON summaries(created_at);
        """)
        self._conn.commit()

    def store_summary(
        self,
        speaker: str,
        summary: str,
        topics: list[str] | None = None,
        satellite_id: str = "",
    ) -> None:
        """Store a conversation summary."""
        self._conn.execute(
            "INSERT INTO summaries (speaker, summary, topics, satellite_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (speaker, summary, json.dumps(topics or []), satellite_id, time.time()),
        )
        self._conn.commit()
        log.info("Stored summary for %s: %s", speaker, summary[:80])

    def get_recent_context(self, speaker: str, days: int = 3) -> str:
        """Get recent conversation context for a speaker.

        Returns a formatted string suitable for injection into system prompt.
        """
        cutoff = time.time() - (days * 86400)
        rows = self._conn.execute(
            "SELECT summary, topics, created_at FROM summaries "
            "WHERE speaker = ? AND created_at > ? ORDER BY created_at DESC LIMIT 10",
            (speaker, cutoff),
        ).fetchall()

        if not rows:
            return ""

        lines = []
        for row in rows:
            dt = datetime.datetime.fromtimestamp(row["created_at"])
            lines.append(f"- [{dt:%d.%m %H:%M}] {row['summary']}")

        return "\n".join(lines)

    def search(self, query: str) -> list[dict]:
        """Search conversation summaries."""
        rows = self._conn.execute(
            "SELECT speaker, summary, topics, created_at FROM summaries "
            "WHERE summary LIKE ? ORDER BY created_at DESC LIMIT 20",
            (f"%{query}%",),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
