"""P146-s01: every install finds and marks the episodes the old cross-user nightjob loop mixed — once, by itself.

P146 fixed the writer (one user per episode) and this house's 55 bad episodes were marked by a private one-off script.
Every other install still has its old mixed episodes, readable as one person's memory by another. The nightjob now runs
a one-time step: judge only episodes written by the old loop (created before the P146 floor row), mark the mixed ones
(SQLite + Qdrant, never deleted), and let the per-user loop re-compress the affected persons' rows.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import kaare_nightjob as nj
from kaare_core.memory import long_term, semantic_memory
from kaare_core.memory.memory_embed_models import MODELS

SECRET_NARRATIVE = "NARRATIVE-SECRET-5d21"
OLD_TS = "2026-05-01T03:00:00+00:00"
COLLECTION = MODELS["mpnet"]["collection"]
VEC = [1.0, 0.0, 0.0, 0.0]


class _NoQdrant:
    def __init__(self, *a, **kw):
        raise RuntimeError("no qdrant in tests")


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A nightjob wired to a tmp DB (state/memory/interactions.db layout) and an in-memory Qdrant."""
    path = tmp_path / "memory" / "interactions.db"
    path.parent.mkdir()
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

    async def no_heal(*a, **kw):
        return {}

    monkeypatch.setattr(nj, "reindex_missing", no_heal, raising=False)
    indexed = []

    async def fake_index(**kw):
        indexed.append(kw)
        return True

    monkeypatch.setattr(nj, "index_episode", fake_index)

    client = QdrantClient(":memory:")
    client.create_collection(COLLECTION, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    state = {"up": True, "writes": 0}
    real_set_payload = client.set_payload

    def counting_set_payload(*a, **kw):
        state["writes"] += 1
        return real_set_payload(*a, **kw)

    client.set_payload = counting_set_payload

    def get_client(write=False):
        if not state["up"]:
            raise ConnectionError("qdrant down")
        return client

    monkeypatch.setattr(semantic_memory, "_get_client", get_client)
    calls = []

    async def fake_chat(role, messages, **kw):
        calls.append(messages[0]["content"])
        return {"ok": True, "text": SECRET_NARRATIVE}

    monkeypatch.setattr(nj, "_llm_chat", fake_chat)
    return {"path": path, "tmp": tmp_path, "client": client, "qdrant": state, "indexed": indexed, "llm": calls}


def _sql(path, query, args=()):
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(query, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _rows(path, spec):
    """spec: list of (user_id, outcome). Ids are 1.. in order."""
    for uid, outcome in spec:
        _sql(
            path,
            "INSERT INTO interactions (ts, user_id, prompt, response, outcome) VALUES (?, ?, ?, 'ok', ?)",
            (OLD_TS, uid, f"prompt of {uid}", outcome),
        )


def _episode(path, ep_id, owner, a, b, count, ts=OLD_TS, status="ok"):
    _sql(
        path,
        "INSERT INTO episodes (id, ts_created, user_id, from_id, to_id, interaction_count, narrative, topics, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)",
        (ep_id, ts, owner, a, b, count, f"old narrative {ep_id}", status),
    )


def _upgrade(path):
    """A database from before P146 opened by P146 code: the floor row is written now, from the old episodes."""
    _sql(path, "DROP TABLE ltm_cursors")
    long_term.ensure_schema()


def _status(path):
    return dict(_sql(path, "SELECT id, status FROM episodes"))


def _cursors(path):
    return dict(_sql(path, "SELECT user_id, last_id FROM ltm_cursors"))


def _points(client, ids, status=None):
    client.upsert(
        COLLECTION,
        points=[
            PointStruct(id=i, vector=VEC, payload={"user_id": "x", **({"status": status} if status else {})})
            for i in ids
        ],
    )


def _point_status(client, ids):
    return {p.id: (p.payload or {}).get("status", "ok") for p in client.retrieve(COLLECTION, ids=ids)}


def _backups(tmp):
    d = tmp / "backups" / "ltm_mixed_mark"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _night():
    asyncio.run(nj.run_nightjob())


def _upgraded_install(path):
    """Old-loop episodes of every kind, then the upgrade, then one new per-user episode.

    A 1–4  alice  (alice, bob, alice, alice)      → mixed (bob's row under alice)
    B 5–6  alice  (alice, alice)                  → clean, but alice is re-compressed → superseded
    C 7–8  alice  (global voice, alice)           → mixed (a global row counts as another user)
    D 9–10 global (carol, carol)                  → mixed (a person's rows filed as global)
    E 11–13 global, Jing loop (jing, dave, jing)  → clean: a global-loop episode, its range may hold person rows
    F 14   erin   (erin)                          → clean, erin untouched
    G 15–17 erin, NEW per-user episode (erin, bob, erin) — range holds bob's row legitimately → never judged
    """
    _rows(
        path,
        [("alice", "success"), ("bob", "success"), ("alice", "success"), ("alice", "llm_fallback"),
         ("alice", "success"), ("alice", "success"),
         ("global", "success"), ("alice", "success"),
         ("carol", "success"), ("carol", "success"),
         ("global", "unknown"), ("dave", "success"), ("global", "unknown"),
         ("erin", "success")],
    )
    for args in [(1, "alice", 1, 4, 4), (2, "alice", 5, 6, 2), (3, "alice", 7, 8, 2), (4, "global", 9, 10, 2),
                 (5, "global", 11, 13, 2), (6, "erin", 14, 14, 1)]:
        _episode(path, *args)
    _upgrade(path)
    _rows(path, [("erin", "success"), ("bob", "success"), ("erin", "success")])
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat(timespec="seconds")
    _episode(path, 7, "erin", 15, 17, 2, ts=future)
    _sql(path, "INSERT INTO ltm_cursors (user_id, last_id, ts_updated) VALUES ('erin', 17, ?)", (future,))


def test_fresh_install_has_nothing_to_mark(house):
    _night()
    assert _status(house["path"]) == {}
    assert _backups(house["tmp"]) == []
    assert house["qdrant"]["writes"] == 0


def test_upgraded_install_marks_only_old_mixed_episodes(house):
    path, client = house["path"], house["client"]
    _upgraded_install(path)
    _points(client, range(1, 8))
    _night()
    st = _status(path)
    assert {i: st[i] for i in range(1, 8)} == {
        1: "mixed", 2: "superseded", 3: "mixed", 4: "mixed", 5: "ok", 6: "ok", 7: "ok"
    }
    assert _point_status(client, list(range(1, 8))) == {
        1: "mixed", 2: "superseded", 3: "mixed", 4: "mixed", 5: "ok", 6: "ok", 7: "ok"
    }
    assert len(_backups(house["tmp"])) == 1  # SQLite backup API copy before the first write


def test_affected_persons_are_recompressed_per_user(house):
    path = house["path"]
    _upgraded_install(path)
    _night()
    new = _sql(path, "SELECT user_id, from_id, to_id, interaction_count FROM episodes WHERE id > 7 ORDER BY id")
    by_user = {u: (a, b, n) for u, a, b, n in new}
    assert by_user["alice"] == (1, 8, 6)  # rows 1,3,4,5,6,8 — her mixed and her superseded clean episode
    assert by_user["bob"] == (2, 16, 2)  # row 2 from the mixed episode + his new row 16
    assert by_user["carol"] == (9, 10, 2)  # rows that were filed as global
    assert "erin" not in by_user and "dave" not in by_user and "global" not in by_user
    for ep_id, owner, a, b, _ in _sql(path, "SELECT id, user_id, from_id, to_id, 0 FROM episodes WHERE id > 7"):
        users = {r[0] for r in _sql(path, "SELECT user_id FROM interactions WHERE id BETWEEN ? AND ? AND "
                                          "outcome IN ('success','llm_fallback') AND user_id = ?", (a, b, owner))}
        assert users == {owner}, ep_id


def test_second_run_is_a_noop(house):
    path = house["path"]
    _upgraded_install(path)
    _points(house["client"], range(1, 8))
    _night()
    before = (_status(path), _cursors(path), _backups(house["tmp"]), house["qdrant"]["writes"])
    calls = len(house["llm"])
    _night()
    assert (_status(path), _cursors(path), _backups(house["tmp"]), house["qdrant"]["writes"]) == before
    assert len(house["llm"]) == calls


def test_house_already_marked_is_a_noop(house):
    """This house: the 55 were marked by hand (SQLite + Qdrant); userone/admin were deliberately not re-compressed."""
    path, client = house["path"], house["client"]
    _upgraded_install(path)
    for ep_id in (1, 3, 4):
        _sql(path, "UPDATE episodes SET status = 'mixed' WHERE id = ?", (ep_id,))
    _points(client, [1, 3, 4], status="mixed")
    _points(client, [2, 5, 6, 7])
    cursors = _cursors(path)
    _night()
    st = _status(path)
    assert {i: st[i] for i in range(1, 8)} == {1: "mixed", 2: "ok", 3: "mixed", 4: "mixed", 5: "ok", 6: "ok", 7: "ok"}
    assert _backups(house["tmp"]) == []
    assert house["qdrant"]["writes"] == 0
    after = _cursors(path)
    assert "alice" not in after and "carol" not in after, after  # nobody re-queued
    assert after["erin"] == cursors["erin"] == 17
    assert after["bob"] == 16  # only bob's new row 16, compressed the normal way


def test_qdrant_down_marks_sqlite_and_retries_qdrant_next_night(house):
    path, client = house["path"], house["client"]
    _upgraded_install(path)
    _points(client, range(1, 8))
    house["qdrant"]["up"] = False
    _night()
    assert _status(path)[1] == "mixed"
    assert _point_status(client, [1, 2, 3, 4]) == {1: "ok", 2: "ok", 3: "ok", 4: "ok"}
    house["qdrant"]["up"] = True
    _night()
    assert _point_status(client, [1, 2, 3, 4]) == {1: "mixed", 2: "superseded", 3: "mixed", 4: "mixed"}
    assert len(_backups(house["tmp"])) == 1  # SQLite was not marked twice


def test_missing_points_are_counted_not_fatal(house):
    path, client = house["path"], house["client"]
    _upgraded_install(path)
    _points(client, [1])  # 2, 3, 4 have no point (the ep 14 case)
    _night()
    assert _point_status(client, [1]) == {1: "mixed"}
    state = json.loads((path.parent / "ltm_mixed_cleanup.json").read_text(encoding="utf-8"))
    assert state["qdrant_done"]


def test_unreadable_owner_waits_instead_of_recompressing_placeholders(house, monkeypatch):
    """A person whose rows are encrypted and whose key the night cannot resolve (no PIN login since the system key
    came) would be re-compressed from «[kryptert]» placeholders — wait for the key instead."""
    path = house["path"]
    _upgraded_install(path)
    _sql(path, "UPDATE interactions SET prompt = 'ENC:blob' WHERE user_id = 'bob'")
    readable = {"bob": False}

    def fake_dec(uid, text, allow_system_key=False):
        if text.startswith("ENC:") and not readable.get(uid, True):
            return "[kryptert]"
        return text

    monkeypatch.setattr(long_term, "_dec", fake_dec)
    _night()
    new = _sql(path, "SELECT user_id, from_id FROM episodes WHERE id > 7")
    assert "alice" in {u for u, _ in new}
    assert all(a > 2 for u, a in new if u == "bob"), new  # bob's old row 2 waits (his new row 16 goes normally)
    readable["bob"] = True
    _night()
    bob = _sql(path, "SELECT from_id, interaction_count FROM episodes WHERE id > 7 AND user_id = 'bob' AND status = 'ok'")
    assert bob and bob[0][0] == 2, bob


def test_cleanup_logs_ids_and_counts_only(house, caplog):
    path = house["path"]
    _upgraded_install(path)
    with caplog.at_level("INFO"):
        _night()
    text = caplog.text
    assert "old narrative" not in text and "prompt of" not in text and SECRET_NARRATIVE not in text
    assert "mixed" in text.lower()
