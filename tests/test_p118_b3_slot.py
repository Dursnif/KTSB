"""
P118 B3 — Janus + Kåre (S1/S2/S3) with the read-only slot tools and S4 verification.
LLM calls are faked; tools, storage and verification are real (temp paths).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b3_slot.py -v
"""

import asyncio
import contextlib
import json
import sys

import pytest

sys.path.insert(0, "/kaare")

import hashlib

import kaare_core.dev_slot.steps as steps
import kaare_core.dev_slot.tools as slot_tools
import kaare_core.memory.learning_store as ls
import kaare_core.tools.shared_tools as shared_tools
import kaare_core.tools.trace_reader as tr
from kaare_core import log_buffer

RID = "rid-meet-1791100000000"


@pytest.fixture(autouse=True)
def tmp_env(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    tool_log = tmp_path / "tool_calls.log"
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", tool_log)
    monkeypatch.setattr(tr, "_TOOL_LOG", tool_log)
    for name in ("_ROUTE_LOG", "_LLM_LOG", "_THINK_CACHE", "_ENFORCE_LOG"):
        p = tmp_path / f"{name}.log"
        p.write_text("")
        monkeypatch.setattr(tr, name, p)

    async def no_9b(claim, evidence, rid="", lang=""):
        return {"status": "unavailable", "error": "test"}

    monkeypatch.setattr(steps.verifier, "check_claim", no_9b)

    async def fake_mech(name, args, lang=None):
        return (
            f"MECH {name} {json.dumps(args, ensure_ascii=False)} → line 46: return json.dumps(data)"
        )

    monkeypatch.setattr(slot_tools.mechanic_tools, "execute_tool", fake_mech)
    return tmp_path


def _tool_lines(tmp_path):
    p = tmp_path / "tool_calls.log"
    log_buffer.flush()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── Slot tools ──────────────────────────────────────────────────────────────


def test_only_read_tools_offered():
    names = slot_tools.SlotTools(RID, "en").names
    assert names == {
        "self_trace",
        "search_argus",
        "memory",
        "utforsk",
        "inspiser",
        "dev_history",
        "delegate_read",
    }
    assert "read_meeting" not in names, (
        "old meeting reports are the recycling channel, not evidence"
    )


def test_refusals_are_not_logged_and_refs_count_up(tmp_env):
    tools = slot_tools.SlotTools(RID, "nb")
    for name, args in (
        ("ha_control", {"action": "turn_on"}),
        ("note", {"action": "write"}),
        ("memory", {"action": "confirm"}),
        ("inspiser", {"action": "git_diff"}),
        ("shell", {"cmd": "rm -rf /"}),
    ):
        assert "bare lese" in asyncio.run(tools.execute(name, args))
    assert _tool_lines(tmp_env) == [] and tools.calls == 0
    t1 = ls.add_inbox_item(
        "Camera clock skew", source="frigate", pointer="frigate_analysis.log#event_id=e1"
    )
    r1 = asyncio.run(
        tools.execute("utforsk", {"action": "les", "sti": "/kaare/kaare_core/llm_fallback.py"})
    )
    r2 = asyncio.run(tools.execute("dev_history", {"topic_id": t1["topic_id"]}))
    assert r1.startswith(f"[tool:{RID}#1]") and r2.startswith(f"[tool:{RID}#2]")
    assert [(x["rid"], x["tool"]) for x in _tool_lines(tmp_env)] == [
        (RID, "utforsk"),
        (RID, "dev_history"),
    ]


def test_delegate_runs_only_offered_call_and_returns_raw(monkeypatch, tmp_env):
    @contextlib.asynccontextmanager
    async def fake_lock(ports, caller, max_wait=0):
        yield "http://127.0.0.1:11447"

    replies = iter(
        [
            {
                "message": {
                    "content": "I'll just run a shell",
                    "tool_calls": [
                        {"function": {"name": "shell", "arguments": {"cmd": "cat /etc/shadow"}}}
                    ],
                }
            },
            {
                "message": {
                    "content": "prose the slot must not pass on",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "inspiser",
                                "arguments": {"action": "logg", "fil": "llm_calls.log"},
                            }
                        }
                    ],
                }
            },
        ]
    )

    async def fake_post(client, url, payload, headers=None, rid="", source=""):
        assert {t["function"]["name"] for t in payload["tools"]} == {
            "utforsk",
            "inspiser",
            "søk_argus",
        }
        return next(replies)

    monkeypatch.setattr(slot_tools, "lock_agent_pool", fake_lock)
    monkeypatch.setattr(slot_tools, "post_chat", fake_post)
    tools = slot_tools.SlotTools(RID, "en")
    first = asyncio.run(tools.execute("delegate_read", {"task": "show /etc/shadow"}))
    assert "no valid read call" in first and tools.calls == 0
    second = asyncio.run(tools.execute("delegate_read", {"task": "tail llm_calls.log"}))
    assert second.startswith(f"[tool:{RID}#1]") and "MECH inspiser" in second
    assert "prose the slot must not pass on" not in second


# ── kare_json loop ──────────────────────────────────────────────────────────


def _fake_kare(monkeypatch, replies, seen):
    it = iter(replies)

    async def fake(messages, tools, rid="", user_id="", source="", **kw):
        seen.append(
            {
                "tools": [x["function"]["name"] for x in tools],
                "last": messages[-1]["content"],
                "user_id": user_id,
                "source": source,
                "rid": rid,
                "no_think": kw.get("disable_thinking"),
            }
        )
        return next(it)

    monkeypatch.setattr(steps, "ask_llm_with_tools", fake)


def test_kare_json_tool_round_then_answer(monkeypatch, tmp_env):
    seen = []
    _fake_kare(
        monkeypatch,
        [
            {
                "ok": True,
                "text": "",
                "tool_calls": [{"function": {"name": "utforsk", "arguments": {"action": "liste"}}}],
                "message": {"role": "assistant", "content": ""},
            },
            {"ok": True, "text": 'Here: {"findings": [], "proposal": ""}', "tool_calls": None},
        ],
        seen,
    )
    tools = slot_tools.SlotTools(RID, "en")
    data, _ = asyncio.run(
        steps.kare_json(
            "task", rid=RID, lang="en", tools=tools, max_rounds=2, budget=steps.Budget(5)
        )
    )
    assert data == {"findings": [], "proposal": ""}
    assert seen[0]["user_id"] == "" and seen[0]["source"] == "meet" and seen[0]["rid"] == RID
    assert seen[0]["no_think"] is False, "S3-style calls keep thinking"
    assert seen[1]["last"].startswith(f"[tool:{RID}#1]")


def test_kare_json_round_cap_and_json_retry(monkeypatch, tmp_env):
    seen = []
    call = {
        "ok": True,
        "text": "",
        "tool_calls": [{"function": {"name": "utforsk", "arguments": {"action": "liste"}}}],
        "message": {"role": "assistant", "content": ""},
    }
    _fake_kare(
        monkeypatch,
        [
            call,
            call,
            {"ok": True, "text": "not json", "tool_calls": None},
            {"ok": True, "text": '{"findings": []}', "tool_calls": None},
        ],
        seen,
    )
    data, _ = asyncio.run(
        steps.kare_json(
            "task",
            rid=RID,
            lang="nb",
            tools=slot_tools.SlotTools(RID, "nb"),
            max_rounds=1,
            budget=steps.Budget(5),
        )
    )
    assert data == {"findings": []}
    assert "Rundetaket" in seen[2]["last"] and seen[2]["tools"] == []
    assert "JSON" in seen[3]["last"] and seen[3]["tools"] == []


def test_kare_json_stops_when_budget_is_spent(monkeypatch):
    seen = []
    _fake_kare(monkeypatch, [], seen)
    clock = iter([0, 10_000])
    budget = steps.Budget(1, clock=lambda: next(clock))
    assert asyncio.run(
        steps.kare_json("x", rid=RID, lang="en", tools=None, max_rounds=0, budget=budget)
    ) == (None, "")
    assert seen == []


# ── Janus ───────────────────────────────────────────────────────────────────


def _bundle(topics, traces=()):
    return {
        "inbox": {"topics": topics},
        "traces": {"items": list(traces), "found": len(traces)},
        "health": {"ok": True, "result": {"ok": True}},
        "changes": {"baseline": True},
    }


def test_janus_priorities_and_mechanical_default(monkeypatch, tmp_env):
    a = ls.add_inbox_item("Topic A", source="refl", rid="rid-refl-1")
    b = ls.add_inbox_item("Completely different B", source="chat", rid="rid-2")
    topics = ls.open_topics()

    async def janus_ok(role, messages, **kw):
        assert messages[0]["role"] == "system" and "Janus" in messages[0]["content"]
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "priorities": [
                        {"topic_id": a["topic_id"], "priority": "develop", "reason": "new rows"},
                        {"topic_id": b["topic_id"], "priority": "bogus"},
                    ]
                }
            ),
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus_ok)
    pr = asyncio.run(steps.janus_priorities(_bundle(topics), rid=RID, lang="en", run_id="r1"))
    assert pr[a["topic_id"]]["priority"] == "develop"
    assert pr[b["topic_id"]] == {"priority": "new", "reason": "", "default": True}

    async def janus_garbage(role, messages, **kw):
        return {"ok": True, "text": "I think everything matters."}

    monkeypatch.setattr(steps, "call_llm_chat", janus_garbage)
    pr = asyncio.run(steps.janus_priorities(_bundle(topics), rid=RID, lang="en", run_id="r2"))
    assert all(v["priority"] == "new" for v in pr.values())
    assert ls.load_artifact("r2", "janus_start")["fallback"] is True


# ── Full B3 run ─────────────────────────────────────────────────────────────


def test_run_b3_end_to_end(monkeypatch, tmp_env):
    t1 = ls.add_inbox_item(
        "Fallback flag sticks after vLLM restart", source="refl", rid="rid-refl-1"
    )
    t2 = ls.add_inbox_item(
        "Car parked on the east camera again",
        source="frigate",
        pointer="frigate_analysis.log#event_id=e2",
    )
    t3 = ls.add_inbox_item("Doorbell motion flaps on and off", source="refl", rid="rid-refl-3")
    trace = {
        "rid": "rid-1791000000001",
        "prompt_preview": "Slå på lyset",
        "next_rid": "",
        "anchors": {},
    }

    async def janus(role, messages, **kw):
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "priorities": [
                        {"topic_id": t1["topic_id"], "priority": "develop"},
                        {"topic_id": t2["topic_id"], "priority": "drop"},
                        {"topic_id": t3["topic_id"], "priority": "seen_before"},
                    ]
                }
            ),
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus)

    async def kare(messages, tools, rid="", user_id="", source="", **kw):
        task = messages[0]["content"]
        assert kw["disable_thinking"] is (("S1" in task) or ("S2" in task))
        if "S1" in task:
            return {
                "ok": True,
                "tool_calls": None,
                "text": json.dumps(
                    {
                        "topic_id": t1["topic_id"],
                        "label": "Fallback flag",
                        "kind": "problem",
                        "summary": "Fallback stays on",
                        "evidence_refs": [f"inbox:{t1['item_id']}"],
                    }
                ),
            }
        if "S2" in task:
            return {
                "ok": True,
                "tool_calls": None,
                "text": json.dumps(
                    {
                        "rid": trace["rid"],
                        "ok": False,
                        "asked": "light on",
                        "did": "turned on the wrong light",
                        "problem": "Wrong light chosen for «lyset i stua»",
                        "evidence_refs": [f"trace:{trace['rid']}"],
                    }
                ),
            }
        # S3: one tool round, then findings (one with a real quote, one invented)
        if messages[-1]["role"] == "user":
            return {
                "ok": True,
                "text": "",
                "message": {"role": "assistant", "content": ""},
                "tool_calls": [
                    {
                        "function": {
                            "name": "utforsk",
                            "arguments": {
                                "action": "les",
                                "sti": "/kaare/kaare_core/llm_fallback.py",
                            },
                        }
                    }
                ],
            }
        ref = messages[-1]["content"].split("]")[0].lstrip("[")
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "text": "Line 46 serializes the state",
                            "evidence_refs": [ref],
                            "quote": "line 46: return json.dumps(data)",
                        },
                        {
                            "text": "Line 46 collapses None",
                            "evidence_refs": [ref],
                            "quote": "None collapse",
                        },
                    ],
                    "proposal": "Check the flag reset on recovery",
                }
            ),
        }

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)

    bundle = _bundle(ls.open_topics(), [trace])
    out = asyncio.run(
        steps.run_b3(
            bundle, run_id="run-x", rid=RID, lang="en", budget=steps.Budget(10), round_cap=3
        )
    )
    assert out["janus"]["develop"] == 1 and out["janus"]["drop"] == 1
    assert [r["kind"] for r in out["s1"]] == ["problem"]
    assert out["s2"][0]["ok"] is False and out["s2"][0]["topic_id"]
    assert len(out["s3"]) == 2, "S1 problem + S2 problem are both developed"
    flags = [f["verified"] for f in out["s3"][0]["findings"]]
    assert flags == [True, False], "real quote verified, invented one kept as unverified"
    status = {r["topic_id"]: r["status"] for r in ls.list_inbox()}
    assert status[t1["topic_id"]] == "problem"
    assert status[t2["topic_id"]] == "known", "Janus 'drop' → known"
    assert status[t3["topic_id"]] == "new", "seen_before waits; nothing dropped"
    conn = ls._get_conn()
    n_find = conn.execute(
        "SELECT COUNT(*), SUM(verified) FROM findings WHERE run_id='run-x'"
    ).fetchone()
    conn.close()
    assert n_find == (4, 2)


def test_run_b3_out_of_time_leaves_everything_new(monkeypatch, tmp_env):
    a = ls.add_inbox_item("Topic A", source="refl", rid="rid-refl-1")

    async def janus(role, messages, **kw):
        return {
            "ok": True,
            "text": json.dumps({"priorities": [{"topic_id": a["topic_id"], "priority": "new"}]}),
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus)
    clock = iter([0] + [10_000] * 50)
    out = asyncio.run(
        steps.run_b3(
            _bundle(ls.open_topics(), [{"rid": "rid-1", "anchors": {}}]),
            run_id="run-y",
            rid=RID,
            lang="en",
            budget=steps.Budget(1, clock=lambda: next(clock)),
            round_cap=2,
        )
    )
    assert out["s1"] == [] and set(out["skipped_for_time"]) == {
        f"topic:{a['topic_id']}",
        "trace:rid-1",
    }
    assert ls.list_inbox()[0]["status"] == "new"


def test_parse_priorities_salvages_cut_off_reply():
    cut = '{"priorities":[{"topic_id":1,"priority":"new","reason":"a"},{"topic_id":2,"priority":"drop","reason":"b"},{"topic_id":3,"prio'
    assert [p["topic_id"] for p in steps.parse_priorities(cut)] == [1, 2]


def test_janus_is_asked_in_batches(monkeypatch, tmp_env):
    for i in range(60):
        words = " ".join(hashlib.sha1(f"{i}-{j}".encode()).hexdigest()[:8] for j in range(6))
        ls.add_inbox_item(words, source="refl", rid=f"rid-refl-{i}")
    topics = ls.open_topics()
    sizes = []

    async def janus(role, messages, **kw):
        data = json.loads(messages[1]["content"].split("\n\n")[0].split(":\n", 1)[1])
        sizes.append(len(data["topics"]))
        assert (
            kw["options"]["num_predict"] == steps._JANUS_MAX_TOKENS
            and kw["disable_thinking"] is True
        )
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "priorities": [
                        {"topic_id": tp["topic_id"], "priority": "seen_before"}
                        for tp in data["topics"]
                    ]
                }
            ),
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus)
    pr = asyncio.run(steps.janus_priorities(_bundle(topics), rid=RID, lang="en", run_id="rb"))
    assert sizes == [25, 25, 10]
    assert len(pr) == 60 and all(v["priority"] == "seen_before" for v in pr.values())


def test_s1_same_as_links_topics_and_only_main_topic_is_developed(monkeypatch, tmp_env):
    a = ls.add_inbox_item(
        "Sensor updates every ten minutes on the doorbell camera", source="legacy", pointer="p1"
    )
    b = ls.add_inbox_item(
        "Lights toggle when motion sensors flap in the hallway", source="legacy", pointer="p2"
    )
    c = ls.add_inbox_item(
        "Fridge door contact reports open while closed", source="legacy", pointer="p3"
    )

    async def janus(role, messages, **kw):
        return {
            "ok": True,
            "text": json.dumps(
                {"priorities": [{"topic_id": x["topic_id"], "priority": "new"} for x in (a, b, c)]}
            ),
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus)
    seen_known = []

    async def kare(messages, tools, rid="", user_id="", source="", **kw):
        task = messages[0]["content"]
        if "S1" in task:
            tid = int(task.split("#")[1].split(" ")[0])
            seen_known.append(task)
            same = a["topic_id"] if tid == b["topic_id"] else None  # B is «the same» as A
            return {
                "ok": True,
                "tool_calls": None,
                "text": json.dumps(
                    {
                        "topic_id": tid,
                        "label": f"label {tid}",
                        "kind": "problem",
                        "same_as": same,
                        "summary": "s",
                        "evidence_refs": [],
                    }
                ),
            }
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps({"findings": [], "proposal": ""}),
        }

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)

    out = asyncio.run(
        steps.run_b3(
            _bundle(ls.open_topics()),
            run_id="run-m",
            rid=RID,
            lang="en",
            budget=steps.Budget(10),
            round_cap=1,
        )
    )
    assert f"#{a['topic_id']}: label {a['topic_id']}" in seen_known[1], (
        "S1 sees topics judged earlier tonight"
    )
    assert [r["topic_id"] for r in out["s3"]] == [a["topic_id"], c["topic_id"]], (
        "linked topic is not developed"
    )
    conn = ls._get_conn()
    rows = dict(conn.execute("SELECT id, merged_into FROM topics").fetchall())
    counts = dict(conn.execute("SELECT id, count FROM topics").fetchall())
    texts = [r[0] for r in conn.execute("SELECT text FROM inbox_items ORDER BY id")]
    conn.close()
    assert rows[b["topic_id"]] == a["topic_id"] and counts[a["topic_id"]] == 2
    assert {r["topic_id"]: r["status"] for r in ls.list_inbox()}[b["topic_id"]] == "merged"
    assert len(texts) == 3, "linking never deletes or rewrites a row"


def test_same_as_must_point_to_a_known_topic(monkeypatch, tmp_env):
    a = ls.add_inbox_item("Topic alpha about cameras", source="refl", rid="r1")

    async def kare(messages, tools, **kw):
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps(
                {
                    "topic_id": a["topic_id"],
                    "label": "x",
                    "kind": "event",
                    "same_as": 999,
                    "summary": "",
                    "evidence_refs": [],
                }
            ),
        }

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)
    res = asyncio.run(
        steps.s1_clean(
            ls.open_topics()[0], rid=RID, lang="en", run_id="r", budget=steps.Budget(5), known={}
        )
    )
    assert res["same_as"] is None and ls.list_inbox()[0]["status"] == "event"


def test_problem_without_finding_is_carried_to_next_night(monkeypatch, tmp_env):
    a = ls.add_inbox_item("Fallback flag sticks", source="refl", rid="r1")

    async def janus(role, messages, **kw):
        return {
            "ok": True,
            "text": json.dumps({"priorities": [{"topic_id": a["topic_id"], "priority": "new"}]}),
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus)

    async def kare_s1(messages, tools, **kw):
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps(
                {
                    "topic_id": a["topic_id"],
                    "label": "Fallback flag",
                    "kind": "problem",
                    "summary": "s",
                    "evidence_refs": [],
                }
            ),
        }

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare_s1)
    # Night 1: budget runs out right after S1 → S3 skipped.
    ticks = iter([0, 0, 0, 0] + [10_000] * 50)
    out1 = asyncio.run(
        steps.run_b3(
            _bundle(ls.open_topics()),
            run_id="n1",
            rid=RID,
            lang="en",
            budget=steps.Budget(1, clock=lambda: next(ticks)),
            round_cap=1,
        )
    )
    assert out1["s3"] == [] and ls.open_topics() == []
    assert [p["id"] for p in ls.pending_problems()] == [a["topic_id"]]
    # Night 2: no new inbox rows, but the carried problem is developed.
    developed = []

    async def kare_s3(messages, tools, **kw):
        developed.append(messages[0]["content"])
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps(
                {"findings": [{"text": "x", "evidence_refs": [], "quote": ""}], "proposal": ""}
            ),
        }

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare_s3)
    out2 = asyncio.run(
        steps.run_b3(
            _bundle([]), run_id="n2", rid=RID, lang="en", budget=steps.Budget(10), round_cap=1
        )
    )
    assert out2["carried_problems"] == [a["topic_id"]] and len(developed) == 1
    assert ls.pending_problems() == [], "a stored finding (even unverified) takes it off the queue"
