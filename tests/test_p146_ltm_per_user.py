"""P146: the nightjob compresses long-term memory per user.

Before this fix the nightjob took the next 20 rows in id order regardless of user, wrote one narrative and saved it
under the user with most rows (DBG02: 44/188 personal episodes mixed users; a person's rows filed as «global»).
A failed LLM reply also skipped the batch forever (340 rows never compressed), and the decrypted narrative was
written into the nightjob log.
"""

import asyncio
import logging
import sqlite3
from datetime import timedelta

import pytest

import kaare_nightjob as nj
from kaare_core.config import now_local
from kaare_core.memory import long_term, semantic_memory

SECRET_PROMPT = "PROMPT-SECRET-7f3a"
SECRET_NARRATIVE = "NARRATIVE-SECRET-91bc"


class _NoQdrant:
    """The Argus cleanup in run_nightjob must never reach the live Qdrant from a test."""

    def __init__(self, *a, **kw):
        raise RuntimeError("no qdrant in tests")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "interactions.db"
    monkeypatch.setattr(long_term, "DB_PATH", path)
    monkeypatch.setattr(long_term, "_ltm", None)
    long_term.get_ltm()
    monkeypatch.setattr(nj, "DB_PATH", path)
    monkeypatch.setattr(nj, "JING_THOUGHTS_PATH", tmp_path / "no_jing.txt")
    monkeypatch.setattr(nj.argus_size, "run_nightly", lambda *a, **kw: {"status": "test"})  # P150: Argus step, no live Qdrant
    monkeypatch.setattr(nj, "_compute_baseline", lambda: None)
    monkeypatch.setattr(nj, "ensure_collection", lambda *a, **kw: None)
    monkeypatch.setattr(nj, "_enc", lambda uid, text, allow_system_key=False: text)
    monkeypatch.setattr(nj, "_dec", lambda uid, text, allow_system_key=False: text)
    monkeypatch.setattr(nj, "_get_lang", lambda *a: "en")
    monkeypatch.setattr(nj, "save_daily_summary", lambda *a, **kw: None)
    # P146-s01: the one-time mixed-episode cleanup and the semantic-memory heal reach Qdrant through semantic_memory
    # / semantic_reindex — never the live one from a test.
    monkeypatch.setattr(semantic_memory, "_get_client", lambda write=False: _NoQdrant())

    async def no_heal(*a, **kw):
        return {}

    monkeypatch.setattr(nj, "reindex_missing", no_heal)
    indexed = []

    async def fake_index(**kw):
        indexed.append(kw)
        return True

    monkeypatch.setattr(nj, "index_episode", fake_index)
    return {"path": path, "indexed": indexed}


def _llm(monkeypatch, replies):
    """replies: list of texts, one per call; '' = failed/empty reply. Exhausted → narrative."""
    calls = []

    async def fake_chat(role, messages, **kw):
        calls.append(messages[0]["content"])
        text = replies.pop(0) if replies else SECRET_NARRATIVE
        return {"ok": bool(text), "text": text, "error": "" if text else "down"}

    monkeypatch.setattr(nj, "_llm_chat", fake_chat)
    return calls


def _add_rows(path, users, ts="2026-10-01T10:00:00+00:00", outcome="success"):
    conn = sqlite3.connect(str(path))
    for u in users:
        conn.execute(
            "INSERT INTO interactions (ts, user_id, prompt, response, outcome) VALUES (?, ?, ?, ?, ?)",
            (ts, u, f"{SECRET_PROMPT} from {u}", "ok", outcome),
        )
    conn.commit()
    conn.close()


def _episodes(path):
    conn = sqlite3.connect(str(path))
    rows = conn.execute("SELECT id, user_id, from_id, to_id, interaction_count FROM episodes").fetchall()
    conn.close()
    return rows


def _episode_users(path):
    """For every episode: the set of user_ids among the rows it covers (owner's rows, loop's outcome filter)."""
    conn = sqlite3.connect(str(path))
    out = []
    for ep_id, owner, a, b, _ in _episodes(path):
        users = {
            r[0]
            for r in conn.execute(
                "SELECT user_id FROM interactions WHERE id BETWEEN ? AND ? "
                "AND outcome IN ('success','llm_fallback') AND user_id = ?",
                (a, b, owner),
            )
        }
        foreign = {
            r[0]
            for r in conn.execute(
                "SELECT user_id FROM interactions WHERE id BETWEEN ? AND ? "
                "AND outcome IN ('success','llm_fallback') AND user_id != ?",
                (a, b, owner),
            )
        }
        out.append((ep_id, owner, users, foreign))
    conn.close()
    return out


def test_interleaved_users_never_share_an_episode(db, monkeypatch):
    _llm(monkeypatch, [])
    _add_rows(db["path"], ["alice", "bob"] * 15)  # 30 rows, woven
    asyncio.run(nj.run_nightjob())
    eps = _episodes(db["path"])
    owners = sorted(e[1] for e in eps)
    assert owners == ["alice", "bob"], eps  # one episode per user (15 rows each)
    assert all(e[4] == 15 for e in eps), eps  # each episode counts only its own user's rows
    # every Qdrant index call carries the episode's own owner
    assert sorted(i["user_id"] for i in db["indexed"]) == ["alice", "bob"]


def test_each_batch_holds_one_user_only(db, monkeypatch):
    calls = _llm(monkeypatch, [])
    _add_rows(db["path"], ["alice", "bob", "alice", "bob"])
    asyncio.run(nj.run_nightjob())
    personal = [c for c in calls if SECRET_PROMPT in c]
    assert len(personal) == 2
    for prompt in personal:
        assert ("from alice" in prompt) != ("from bob" in prompt)  # never both in one prompt


def test_person_rows_never_filed_as_global(db, monkeypatch):
    _llm(monkeypatch, [])
    _add_rows(db["path"], ["global", "global", "global", "alice"])  # old code: dominant = global
    asyncio.run(nj.run_nightjob())
    for _, owner, _, foreign in _episode_users(db["path"]):
        if owner == "global":
            assert "alice" not in foreign
    assert [e for e in _episodes(db["path"]) if e[1] == "alice"], "alice got no episode of her own"


def test_global_rows_never_land_in_a_person_episode(db, monkeypatch):
    _llm(monkeypatch, [])
    _add_rows(db["path"], ["alice", "alice", "alice", "global"])  # old code: dominant = alice
    asyncio.run(nj.run_nightjob())
    for _, owner, _, foreign in _episode_users(db["path"]):
        if owner == "alice":
            assert not foreign, foreign
    assert {e[1] for e in _episodes(db["path"])} <= {"alice", "global"}


def test_failed_llm_batch_is_retried_next_night(db, monkeypatch):
    """The May 2026 loss: batch 1 fails, batch 2 succeeds the same night → the cursor jumped past batch 1."""
    _add_rows(db["path"], ["alice"] * 23)  # batch 1 = 20 rows, batch 2 = 3 rows
    _llm(monkeypatch, [""])  # night 1: first call fails, later calls succeed
    asyncio.run(nj.run_nightjob())
    _llm(monkeypatch, [])  # night 2
    asyncio.run(nj.run_nightjob())
    eps = _episodes(db["path"])
    covered = sum(e[4] for e in eps if e[1] == "alice")
    assert covered == 23, eps
    assert min(e[2] for e in eps) == 1, eps  # rows 1–20 were not lost


def test_rows_from_before_the_upgrade_are_not_recompressed(db, monkeypatch):
    """Old clean episodes stay as they are: the per-user cursor starts at the last row the old code covered.
    (Old *mixed* episodes are marked and their persons re-queued by the P146-s01 cleanup —
    tests/test_p146_s01_mixed_cleanup.py.)"""
    _add_rows(db["path"], ["alice"] * 6)
    conn = sqlite3.connect(str(db["path"]))
    conn.execute(
        "INSERT INTO episodes (ts_created, user_id, from_id, to_id, interaction_count, narrative) "
        "VALUES ('2026-10-01T03:00:00+00:00', 'alice', 1, 6, 6, 'old clean')"
    )
    conn.execute("DROP TABLE ltm_cursors")  # simulate a database from before P146 …
    conn.commit()
    conn.close()
    long_term.ensure_schema()  # … being upgraded: the floor is set from the old episodes
    _add_rows(db["path"], ["bob"])  # id 7, new
    _llm(monkeypatch, [])
    asyncio.run(nj.run_nightjob())
    new = [e for e in _episodes(db["path"]) if e[0] != 1]
    assert [(e[1], e[2], e[3]) for e in new] == [("bob", 7, 7)], new


def test_nightjob_log_has_no_memory_text(db, monkeypatch, caplog):
    _llm(monkeypatch, [])
    _add_rows(db["path"], ["alice", "bob"])
    with caplog.at_level(logging.DEBUG):
        asyncio.run(nj.run_nightjob())
    assert _episodes(db["path"]), "nothing was compressed — the test proves nothing"
    assert SECRET_NARRATIVE not in caplog.text
    assert SECRET_PROMPT not in caplog.text


def test_daily_summary_log_has_no_summary_text(db, monkeypatch, caplog):
    yesterday = (now_local() - timedelta(days=1)).date().isoformat()
    _add_rows(db["path"], ["alice"], ts=f"{yesterday}T12:00:00")
    saved = []
    monkeypatch.setattr(nj, "save_daily_summary", lambda *a, **kw: saved.append(a))
    _llm(monkeypatch, [])
    conn = nj._get_conn()
    with caplog.at_level(logging.DEBUG):
        asyncio.run(nj.compress_daily_stm(conn))
    conn.close()
    assert saved, "no daily summary was made — the test proves nothing"
    assert SECRET_NARRATIVE not in caplog.text
    assert SECRET_PROMPT not in caplog.text


def test_save_episode_refuses_rows_of_another_user(db):
    conn = nj._get_conn()
    with pytest.raises(ValueError):
        nj._save_episode(conn, 1, 2, 2, "n", "", 0.5, user_id="alice", row_user_ids=["alice", "bob"])
    conn.close()
    assert _episodes(db["path"]) == []
