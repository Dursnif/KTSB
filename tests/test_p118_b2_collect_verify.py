"""
P118 B2 — S0 collect + S4 verify (code only), with fixture logs.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b2_collect_verify.py -v
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.dev_slot.changes as changes
import kaare_core.dev_slot.collect as collect
import kaare_core.dev_slot.verify as verify
import kaare_core.memory.learning_store as ls
import kaare_core.model_enforcement as me
import kaare_core.tools.trace_reader as tr
from kaare_core import log_buffer

U1, U2, REFL, MEET, OLD = (
    "rid-1791000000001",
    "rid-1791000000002",
    "rid-refl-1",
    "rid-meet-1",
    "rid-1700000000000",
)


def _w(path, rows):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


@pytest.fixture
def logs(monkeypatch, tmp_path):
    route, llm, tool, think, enf, frig = (
        tmp_path / n
        for n in ("route.log", "llm.log", "tool.log", "think.jsonl", "enforce.log", "frigate.log")
    )
    _w(
        route,
        [
            {
                "ts": "2026-10-01T08:00:00+00:00",
                "rid": OLD,
                "stage": "generate_in",
                "prompt_preview": "old",
            },
            {
                "ts": "2026-10-03T10:00:00+00:00",
                "rid": U1,
                "stage": "generate_in",
                "prompt_preview": "Slå på lyset i stua",
            },
            {
                "ts": "2026-10-03T10:05:00+00:00",
                "rid": U2,
                "stage": "generate_in",
                "prompt_preview": "Nei, det var feil lys",
            },
        ],
    )
    _w(
        llm,
        [
            {
                "ts": "2026-10-03T10:00:02+00:00",
                "rid": U1,
                "source": "user",
                "instance": "fallback_9b",
                "recovered": True,
            },
            {"ts": "2026-10-03T10:05:02+00:00", "rid": U2, "source": "user", "instance": "kare"},
            {"ts": "2026-10-03T11:00:00+00:00", "rid": REFL, "source": "jang", "instance": "kare"},
            {"ts": "2026-10-03T12:00:00+00:00", "rid": MEET, "source": "meet", "instance": "kare"},
        ],
    )
    _w(
        tool,
        [
            {
                "ts": "2026-10-03T10:00:03+00:00",
                "rid": U1,
                "tool": "ha_control",
                "args": {"action": "turn_on"},
                "result_preview": "OK: light.stue_tak on",
            },
            {
                "ts": "2026-10-03T10:05:03+00:00",
                "rid": U2,
                "tool": "library",
                "args": {},
                "result_preview": "no hit",
            },
            {
                "ts": "2026-10-03T12:00:01+00:00",
                "rid": MEET,
                "tool": "self_trace",
                "args": {"rid": U1},
                "result_preview": "fallback_9b used, light.stue_tak on",
            },
            {
                "ts": "2026-10-03T12:00:02+00:00",
                "rid": MEET,
                "tool": "search_argus",
                "args": {},
                "result_preview": "3 events",
            },
        ],
    )
    _w(think, [])
    _w(
        enf,
        [
            {
                "ts": "2026-10-03T10:00:04+00:00",
                "rid": U1,
                "detector": "tool_promise",
                "event": "retry",
            }
        ],
    )
    _w(
        frig,
        [
            {
                "ts": "2026-09-30T13:44:00+00:00",
                "event_id": "ev-1",
                "label": "deer",
                "analysis": "No deer is visible, a false positive.",
            }
        ],
    )
    for name, p in (
        ("_ROUTE_LOG", route),
        ("_LLM_LOG", llm),
        ("_TOOL_LOG", tool),
        ("_THINK_CACHE", think),
        ("_ENFORCE_LOG", enf),
    ):
        monkeypatch.setattr(tr, name, p)
    monkeypatch.setattr(verify, "FRIGATE_LOG", frig)
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    return tmp_path


# ── trace_reader single pass ────────────────────────────────────────────────


def test_collect_traces_one_pass_since_and_source(logs):
    traces = tr.collect_traces("2026-10-02T00:00:00+00:00", "user")
    assert [t["rid"] for t in traces] == [U2, U1], "newest first, old + non-user excluded"
    t1 = traces[1]
    assert t1["prompt_preview"] == "Slå på lyset i stua"
    assert t1["used_fallback"] and t1["enforcer_hits"][0]["detector"] == "tool_promise"
    assert tr.collect_traces("2026-10-02T00:00:00+00:00", "all", limit=2)[0]["rid"] == MEET


def test_get_trace_keeps_its_shape(logs):
    t = tr.get_trace(U1)
    for key in (
        "rid",
        "source",
        "ts_start",
        "ts_end",
        "stages",
        "llm_calls",
        "tool_calls",
        "think_entries",
        "voice_events",
        "total_latency_ms",
        "llm_call_count",
        "tool_count",
        "tool_names",
        "has_images",
        "used_fallback",
        "has_think",
    ):
        assert key in t
    assert t["tool_names"] == ["ha_control"] and t["source"] == "user"


# ── anchors / next turn ─────────────────────────────────────────────────────


def test_anchors_and_next_turn(logs):
    traces = tr.collect_traces("2026-10-02T00:00:00+00:00", "user")
    collect.link_next_turns(traces)
    by = {t["rid"]: t for t in traces}
    assert by[U1]["next_rid"] == U2 and by[U2]["next_rid"] == ""
    a = collect.trace_anchors(by[U1])
    assert a["used_fallback"] and a["recovered"] and a["enforcer_hits"] == ["tool_promise"]
    assert a["tool_names"] == ["ha_control"] and not a["library_called"]
    assert collect.trace_anchors(by[U2])["library_called"]


# ── changes (T2-D19) ────────────────────────────────────────────────────────


def test_changes_baseline_then_diff_and_hash_reuse(logs, tmp_path):
    root, cfg = tmp_path / "root", tmp_path / "cfg"
    (root / "pkg").mkdir(parents=True)
    (root / "venv").mkdir()
    cfg.mkdir()
    (root / "pkg" / "a.py").write_text("print(1)\n")
    (root / "pkg" / "b.py").write_text("print(2)\n")
    (root / "venv" / "skip.py").write_text("x")
    (cfg / "settings.yaml").write_text("location:\n  city: Secretville\ntoken: abc123\n")

    first = changes.snapshot_and_diff(root, cfg)
    assert first["baseline"] and first["code_files"] == 2

    st = (root / "pkg" / "b.py").stat()
    (root / "pkg" / "b.py").write_text("print(9)\n")  # same size …
    os.utime(root / "pkg" / "b.py", ns=(st.st_atime_ns, st.st_mtime_ns))  # … same mtime
    (root / "pkg" / "a.py").write_text("print(1)  # changed\n")
    (root / "pkg" / "c.py").write_text("new\n")
    (cfg / "settings.yaml").write_text("location:\n  city: Othertown\nnew_key: 1\n")

    d = changes.snapshot_and_diff(root, cfg)
    assert d["code"]["added"] == ["pkg/c.py"] and d["code"]["changed"] == ["pkg/a.py"]
    assert "pkg/b.py" not in d["code"]["changed"], "unchanged size+mtime must reuse the stored hash"
    s = d["config"]["settings.yaml"]
    assert s == {"added": ["new_key"], "removed": ["token"], "changed": ["location.city"]}

    conn = ls._get_conn()
    payloads = " ".join(r[0] for r in conn.execute("SELECT payload FROM change_snapshots"))
    conn.close()
    assert "Secretville" not in payloads and "abc123" not in payloads, (
        "config values are never stored"
    )


# ── collect() bundle ────────────────────────────────────────────────────────


def test_collect_bundle_is_stored(logs):
    ls.add_inbox_item(
        "Camera clock 12:21 vs 14:21",
        source="frigate",
        pointer="frigate_analysis.log#event_id=ev-1",
    )
    bundle = asyncio.run(
        collect.collect(
            "run-1", since="2026-10-02T00:00:00+00:00", with_health=False, with_changes=False
        )
    )
    assert bundle["inbox"]["open_topics"] == 1 and bundle["inbox"]["open_items"] == 1
    assert bundle["traces"]["found"] == 2 and bundle["traces"]["reviewed"] == 2
    assert bundle["traces"]["items"][1]["anchors"]["used_fallback"]
    assert ls.load_artifact("run-1", "S0")["run_id"] == "run-1"


def test_collect_respects_trace_cap(logs, monkeypatch):
    monkeypatch.setattr(
        collect,
        "get_dev_slot_config",
        lambda: {"time_budget_minutes": 12, "max_user_traces": 1, "round_cap_per_topic": 4},
    )
    b = asyncio.run(
        collect.collect(
            "run-2", since="2026-10-02T00:00:00+00:00", with_health=False, with_changes=False
        )
    )
    assert b["traces"] == {**b["traces"], "found": 2, "reviewed": 1}


def test_health_check_ok_and_timeout(logs, tmp_path, monkeypatch):
    good = tmp_path / "good.py"
    good.write_text("import json; print('noise'); print(json.dumps({'errors': 0}))\n")
    monkeypatch.setattr(collect, "HEALTH_SCRIPT", good)
    assert asyncio.run(collect.run_health_check()) == {"ok": True, "result": {"errors": 0}}
    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(5)\n")
    monkeypatch.setattr(collect, "HEALTH_SCRIPT", slow)
    res = asyncio.run(collect.run_health_check(timeout_s=1))
    assert not res["ok"] and "timeout" in res["error"]


# ── S4 verify ───────────────────────────────────────────────────────────────


def test_verify_claims(logs):
    item = ls.add_inbox_item("Doorbell motion flaps", source="refl", rid=REFL)
    results = verify.verify_claims(
        [
            {
                "text": "Fallback was used for the light command",
                "evidence_refs": [f"tool:{MEET}#1"],
                "quote": "fallback_9b used",
            },
            {
                "text": "Quote not in the cited output",
                "evidence_refs": [f"tool:{MEET}#1"],
                "quote": "vLLM crashed",
            },
            {"text": "Cites a tool call from another run", "evidence_refs": [f"tool:{U1}#1"]},
            {"text": "Index out of range", "evidence_refs": [f"tool:{MEET}#9"]},
            {"text": "Agenda row exists", "evidence_refs": [f"inbox:{item['item_id']}"]},
            {
                "text": "Frigate event exists",
                "evidence_refs": ["frigate:ev-1"],
                "quote": "no deer is visible",
            },
            {"text": "User trace exists", "evidence_refs": [f"trace:{U2}"]},
            {"text": "No evidence at all", "evidence_refs": []},
            {"text": "Made-up ref form", "evidence_refs": ["llm_fallback.py:46"]},
        ],
        run_rids={MEET},
    )
    assert [r["verified"] for r in results] == [
        True,
        False,
        False,
        False,
        True,
        True,
        True,
        False,
        False,
    ]
    assert [r["reason"] for r in results][1:4] == [
        "quote_not_in_evidence",
        "no_ref_resolves",
        "no_ref_resolves",
    ]
    assert results[2]["refs"][0]["why"] == "not_this_run"
    assert results[3]["refs"][0]["why"] == "no_such_call"
    assert results[7]["reason"] == "no_evidence_cited"
    assert results[8]["refs"][0]["why"] == "unknown_ref_form"
    assert len(results) == 9, "every claim is returned — nothing is dropped"


# ── enforcement log ─────────────────────────────────────────────────────────


def test_enforcer_writes_rid_tagged_line(logs, monkeypatch, tmp_path):
    log = tmp_path / "enf.log"
    monkeypatch.setattr(me, "_ENFORCE_LOG", log)
    enf = me.ModelEnforcer("en", rid="rid-xyz", detectors=[me.ToolPromiseDetector()])
    assert enf.check(text_out="I'll raise it at the next dev meeting.", used_tools=[], meta={})
    enf.resolve("", None)
    log_buffer.flush()
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [(x["rid"], x["detector"], x["event"]) for x in lines] == [
        ("rid-xyz", "tool_promise", "retry"),
        ("rid-xyz", "tool_promise", "resolved"),
    ]
