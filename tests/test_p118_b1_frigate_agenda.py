"""
P118 B1 — Frigate event analysis keeps its «next dev meeting» promise (Stian's observation 1).

The analysis call itself has no tools; when its text promises the dev meeting, one background
round with only the agenda tool lets Kåre write the item (source=frigate, pointer to the event).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b1_frigate_agenda.py -v
"""

import asyncio
import sys

import pytest

sys.path.insert(0, "/kaare")

import adapters.llm_adapter as llm_adapter
import kaare_core.domain.frigate_responder as fr
import kaare_core.memory.learning_store as ls
import kaare_core.tools.shared_tools as shared_tools

_EVENT = {
    "event_id": "1790000000.1-abc",
    "camera": "cam_test",
    "label": "deer",
    "score": 0.76,
    "top_score": 0.76,
    "duration": 0.0,
}


@pytest.fixture(autouse=True)
def tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", tmp_path / "tool_calls.log")


def _run(monkeypatch, analysis_text, followup_reply):
    calls = {"followup": []}

    async def fake_ask_llm(prompt, images=None, rid="", **kw):
        calls["rid"] = rid
        return {"text": analysis_text}

    async def fake_with_tools(**kwargs):
        calls["followup"].append(kwargs)
        return followup_reply

    monkeypatch.setattr(llm_adapter, "ask_llm", fake_ask_llm)
    monkeypatch.setattr(llm_adapter, "ask_llm_with_tools", fake_with_tools)
    monkeypatch.setattr(fr, "ask_llm", fake_ask_llm)  # patched where used
    monkeypatch.setattr(fr, "ask_llm_with_tools", fake_with_tools)

    async def go():
        res = await fr.analyze_event(dict(_EVENT), img_b64_override="aGVsbG8=")
        for _ in range(5):
            await asyncio.sleep(0)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
        return res

    return asyncio.run(go()), calls


def test_promise_triggers_one_agenda_round(monkeypatch):
    reply = {
        "text": "",
        "tool_calls": [
            {
                "function": {
                    "name": "note",
                    "arguments": {
                        "action": "write",
                        "list_name": "agenda",
                        "text": "Frigate said deer 76 %, no deer visible on the terrace",
                    },
                }
            }
        ],
    }
    res, calls = _run(
        monkeypatch,
        "No deer is visible — a false positive. I note it for the next dev meeting.",
        reply,
    )
    assert res["rid"].startswith("rid-frigate-") and calls["rid"] == res["rid"]
    assert len(calls["followup"]) == 1
    tools = calls["followup"][0]["tools"]
    assert len(tools) == 1 and tools[0]["function"]["parameters"]["properties"]["list_name"][
        "enum"
    ] == ["agenda"]
    rows = ls.list_inbox()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "frigate" and row["rid"] == res["rid"]
    assert row["pointer"] == "frigate_analysis.log#event_id=1790000000.1-abc"


def test_no_promise_no_followup(monkeypatch):
    res, calls = _run(
        monkeypatch, "A person walks calmly across the driveway.", {"text": "", "tool_calls": None}
    )
    assert res["analysis"].startswith("A person") and calls["followup"] == []
    assert ls.list_inbox() == []


def test_model_declines_nothing_written(monkeypatch):
    _res, calls = _run(
        monkeypatch,
        "Camera clock 12:21 vs 14:21 — check at the next dev meeting.",
        {"text": "OK.", "tool_calls": None},
    )
    assert len(calls["followup"]) == 1 and ls.list_inbox() == []


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_followup_prompt_in_pipe_language(monkeypatch, lang):
    monkeypatch.setattr(fr, "get_lang", lambda *a, **k: lang)
    _, calls = _run(
        monkeypatch, "jeg noterer det til neste dev-møte", {"text": "", "tool_calls": None}
    )
    last = calls["followup"][0]["messages"][-1]["content"]
    assert "agenda" in last and last.startswith("[SYSTEM")
    assert {"nb": "dev-møte", "en": "dev meeting", "de": "Entwicklertreffen"}[lang] in last
