"""N18 — LTM keyword search must decrypt before matching and stay inside the caller's rows.

Before N18, search_interactions() filtered in SQL on ENC:<ciphertext>, so encrypted users
always got zero hits, all keywords had to match, and the tool searched across all users.
"""

import asyncio
import sqlite3
import time

import pytest

from kaare_core import session_keys
from kaare_core.crypto import encrypt_text, generate_keypair
from kaare_core.memory import long_term
from kaare_core.tools import executor_memory


@pytest.fixture
def ltm(tmp_path, monkeypatch):
    monkeypatch.setattr(long_term, "DB_PATH", tmp_path / "interactions.db")
    monkeypatch.setattr(long_term, "_ltm", None)
    inst = long_term.get_ltm()
    keys = {u: generate_keypair()[1] for u in ("alice", "bob")}
    for u, k in keys.items():
        session_keys._sessions[u] = session_keys._SessionEntry(k, time.time() + 600)
    yield inst, keys
    for u in keys:
        session_keys._sessions.pop(u, None)


def _insert(
    user_id, prompt, response, key=None, feedback="unknown", ts="2026-10-05T19:47:00+00:00"
):
    if key is not None:
        prompt = "ENC:" + encrypt_text(prompt, key)
        response = "ENC:" + encrypt_text(response, key)
    conn = sqlite3.connect(str(long_term.DB_PATH))
    conn.execute(
        "INSERT INTO interactions (ts, user_id, prompt, response, outcome, feedback) "
        "VALUES (?, ?, ?, ?, 'success', ?)",
        (ts, user_id, prompt, response, feedback),
    )
    conn.commit()
    conn.close()


def test_encrypted_rows_are_found(ltm):
    inst, keys = ltm
    _insert("alice", "test fargesekvens blå rød", "Blå, rød.", keys["alice"])
    res = inst.search_interactions_ranked("fargesekvens", user_id="alice")
    assert res["total"] == 1
    assert "fargesekvens" in res["hits"][0]["prompt"]


def test_other_users_rows_never_returned_even_when_their_key_is_in_ram(ltm):
    inst, keys = ltm
    _insert("bob", "hemmelig fargesekvens", "ok", keys["bob"])
    _insert("alice", "noe annet", "ok", keys["alice"])
    assert inst.search_interactions_ranked("fargesekvens", user_id="alice")["total"] == 0


def test_global_rows_included_for_user(ltm):
    inst, _ = ltm
    _insert("global", "Jing: fargesekvens på kamera", "")
    assert inst.search_interactions_ranked("fargesekvens", user_id="alice")["total"] == 1


def test_rows_without_key_never_match(ltm):
    inst, _ = ltm
    _, other_key = generate_keypair()
    _insert("carol", "fargesekvens", "x", other_key)  # carol not logged in
    assert inst.search_interactions_ranked("fargesekvens", user_id="carol")["total"] == 0


def test_any_keyword_matches_ranked_by_count_then_newest(ltm):
    inst, keys = ltm
    _insert("alice", "regnestykke 5+5", "10", keys["alice"])  # 1 keyword
    _insert("alice", "fargesekvens regnestykke", "ok", keys["alice"])  # 2 keywords
    _insert("alice", "regnestykke igjen", "ok", keys["alice"])  # 1 keyword, newest
    res = inst.search_interactions_ranked("fargesekvens regnestykke responstid", user_id="alice")
    assert res["total"] == 3
    prompts = [h["prompt"] for h in res["hits"]]
    assert prompts == ["fargesekvens regnestykke", "regnestykke igjen", "regnestykke 5+5"]


def test_total_reports_all_matches_beyond_limit(ltm):
    inst, keys = ltm
    for i in range(9):
        _insert("alice", f"kamera snapshot {i}", "ok", keys["alice"])
    res = inst.search_interactions_ranked("kamera", limit=6, user_id="alice")
    assert len(res["hits"]) == 6 and res["total"] == 9


def test_unverified_newest_first_with_totals(ltm):
    inst, keys = ltm
    _insert("alice", "gammel", "x", keys["alice"], ts="2026-04-17T10:00:00+00:00")
    _insert("alice", "ny", "y", keys["alice"], ts="2026-10-05T19:47:00+00:00")
    rows = inst.get_unverified_interactions("alice", limit=1, newest_first=True)
    assert rows[0]["prompt"] == "ny"
    assert inst.get_unverified_interactions("alice", limit=1)[0]["prompt"] == "gammel"
    stats = inst.count_unverified("alice")
    assert stats["total"] == 2 and stats["first_ts"].startswith("2026-04-17")


def test_tool_search_passes_logged_in_user(ltm, monkeypatch):
    inst, keys = ltm

    async def _no_episodes(query, limit=3, user_id="global"):
        return []

    monkeypatch.setattr(executor_memory, "search_memory", _no_episodes)
    monkeypatch.setattr(executor_memory, "get_ltm", lambda: inst)
    _insert("alice", "fargesekvens alice", "ok", keys["alice"])
    _insert("bob", "fargesekvens bob", "ok", keys["bob"])
    out = asyncio.run(
        executor_memory.dispatch(
            "memory", {"action": "search", "query": "fargesekvens", "_user_id": "alice"}
        )
    )
    assert "fargesekvens alice" in out
    assert "bob" not in out
