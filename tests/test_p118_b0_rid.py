"""
P118 B0 — RID everywhere.

- One Jang reflection cycle = one rid, shared by every LLM round and every tool call.
- interactions.rid exists (fresh DB and migrated old DB) and log_interaction stores it.
- A reflection-cycle tool call and a chat turn both resolve via get_trace(rid).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b0_rid.py -v
"""

import asyncio
import json
import sqlite3
import sys

sys.path.insert(0, "/kaare")

import kaare_core.memory.long_term as long_term
import kaare_core.model_enforcement as me
import kaare_core.reflection_loop as reflection_loop
import kaare_core.tools.executor as executor
import kaare_core.tools.shared_tools as shared_tools
import kaare_core.tools.trace_reader as trace_reader
from kaare_core import log_buffer

# ── Reflection loop: one rid per cycle ───────────────────────────────────────


def _run_cycle(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")
    thoughts = tmp_path / "inner_thoughts.txt"
    thoughts.write_text("[Jang 2026-10-03 12:00]\nA new thought.", encoding="utf-8")
    monkeypatch.setattr(reflection_loop, "_INNER_THOUGHTS_PATH", thoughts)
    monkeypatch.setattr(reflection_loop, "_filter_new_thoughts", lambda raw, since: raw)
    monkeypatch.setattr(reflection_loop, "_read_jang_last_read", lambda: None)
    monkeypatch.setattr(reflection_loop, "_write_jang_last_read", lambda: None)

    llm_rids, tool_args = [], []
    replies = iter(
        [
            {
                "text": "",
                "tool_calls": [
                    {"function": {"name": "note", "arguments": {"action": "write", "text": "x"}}},
                    {"function": {"name": "self_image", "arguments": {"action": "read"}}},
                ],
            },
            {
                "text": "",
                "tool_calls": [
                    {"function": {"name": "note", "arguments": {"action": "read"}}},
                ],
            },
            {"text": "Done.", "tool_calls": None},
        ]
    )

    async def fake_llm(**kwargs):
        llm_rids.append(kwargs["rid"])
        return next(replies)

    async def fake_tool(name, args):
        tool_args.append(dict(args))
        return "ok"

    monkeypatch.setattr(reflection_loop, "ask_llm_with_tools", fake_llm)
    monkeypatch.setattr(executor, "execute_tool", fake_tool)
    monkeypatch.setattr(reflection_loop, "execute_tool", fake_tool)  # patched where used
    asyncio.run(reflection_loop._run_jang_reflection())
    return llm_rids, tool_args


def test_reflection_cycle_uses_one_rid(monkeypatch, tmp_path):
    llm_rids, tool_args = _run_cycle(monkeypatch, tmp_path)
    assert len(llm_rids) == 3
    assert len(set(llm_rids)) == 1, f"rid changed between rounds: {llm_rids}"
    assert llm_rids[0].startswith("rid-refl-")
    assert len(tool_args) == 3
    assert all(a.get("_rid") == llm_rids[0] for a in tool_args)


def test_reflection_prompt_follows_pipe_language(monkeypatch, tmp_path):
    seen = {}

    async def fake_llm(**kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        seen["tools"] = kwargs["tools"]
        return {"text": "", "tool_calls": None}

    for lang, marker in (("nb", "Ingen bruker"), ("en", "No user"), ("de", "Kein Benutzer")):
        thoughts = tmp_path / "inner_thoughts.txt"
        thoughts.write_text("[Jang 2026-10-03 12:00]\nT", encoding="utf-8")
        monkeypatch.setattr(reflection_loop, "_INNER_THOUGHTS_PATH", thoughts)
        monkeypatch.setattr(reflection_loop, "_filter_new_thoughts", lambda raw, since: raw)
        monkeypatch.setattr(reflection_loop, "_read_jang_last_read", lambda: None)
        monkeypatch.setattr(reflection_loop, "_write_jang_last_read", lambda: None)
        monkeypatch.setattr(reflection_loop, "get_lang", lambda uid, _l=lang: _l)
        monkeypatch.setattr(reflection_loop, "ask_llm_with_tools", fake_llm)
        asyncio.run(reflection_loop._run_jang_reflection())
        assert marker in seen["prompt"]
        assert {t["function"]["name"] for t in seen["tools"]} == {"self_image", "note"}


# ── interactions.rid ─────────────────────────────────────────────────────────

_OLD_INTERACTIONS = """
CREATE TABLE interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'global', prompt TEXT NOT NULL,
    source TEXT DEFAULT '', intent TEXT DEFAULT '', entity_id TEXT DEFAULT '',
    action TEXT DEFAULT '', response TEXT NOT NULL DEFAULT '',
    outcome TEXT DEFAULT 'unknown', feedback TEXT DEFAULT 'unknown',
    model_used TEXT DEFAULT '', repaired INTEGER DEFAULT 0, confidence REAL DEFAULT 0.5
);
INSERT INTO interactions (ts, prompt) VALUES ('2026-01-01T00:00:00+00:00', 'old row');
"""


def _cols(db):
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(interactions)")}
    finally:
        conn.close()


def test_fresh_db_has_rid_column(monkeypatch, tmp_path):
    db = tmp_path / "interactions.db"
    monkeypatch.setattr(long_term, "DB_PATH", db)
    long_term._init_db()
    assert "rid" in _cols(db)


def test_old_db_is_migrated_without_data_loss(monkeypatch, tmp_path):
    db = tmp_path / "interactions.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_INTERACTIONS)
    conn.commit()
    conn.close()
    monkeypatch.setattr(long_term, "DB_PATH", db)
    long_term._init_db()
    assert "rid" in _cols(db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT prompt, rid FROM interactions").fetchall()
    conn.close()
    assert rows == [("old row", "")]


def test_log_interaction_stores_rid(monkeypatch, tmp_path):
    db = tmp_path / "interactions.db"
    monkeypatch.setattr(long_term, "DB_PATH", db)
    monkeypatch.setattr(long_term, "_enc", lambda uid, text: text)
    ltm = long_term.LongTermMemory()
    iid = asyncio.run(
        ltm.log_interaction(prompt="hi", user_id="global", source="gui", rid="rid-abc")
    )
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT rid FROM interactions WHERE id=?", (iid,)).fetchone()
    conn.close()
    assert row == ("rid-abc",)


# ── get_trace resolves both paths ────────────────────────────────────────────


def test_get_trace_resolves_reflection_and_chat(monkeypatch, tmp_path):
    tool_log = tmp_path / "tool_calls.log"
    llm_log = tmp_path / "llm_calls.log"
    route_log = tmp_path / "route_decisions.log"
    llm_log.write_text("", encoding="utf-8")
    route_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", tool_log)
    monkeypatch.setattr(trace_reader, "_TOOL_LOG", tool_log)
    monkeypatch.setattr(trace_reader, "_LLM_LOG", llm_log)
    monkeypatch.setattr(trace_reader, "_ROUTE_LOG", route_log)

    refl_rid, chat_rid = "rid-refl-1791000000000", "rid-1791000000001"
    # Reflection loop path: the rid travels in arguments["_rid"] (what B0 now sets).
    shared_tools.log_tool_call("note", {"action": "write", "_rid": refl_rid}, "ok", 5)
    # Chat path: router_generate sets args["_rid"] the same way.
    shared_tools.log_tool_call("ha_read", {"action": "status", "_rid": chat_rid}, "ok", 5)

    log_buffer.flush()
    lines = [json.loads(line) for line in tool_log.read_text(encoding="utf-8").splitlines()]
    assert [line["rid"] for line in lines] == [refl_rid, chat_rid]

    refl = trace_reader.get_trace(refl_rid)
    chat = trace_reader.get_trace(chat_rid)
    assert refl["source"] == "refl"
    assert [tc.get("tool") for tc in refl["tool_calls"]] == ["note"]
    assert [tc.get("tool") for tc in chat["tool_calls"]] == ["ha_read"]
