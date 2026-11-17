"""P144 / run-queue J7 — the camera pipe's model calls in the dev meeting's traces.

(a) a rid-based role call logs a short prompt preview (length + first characters, never images) and whether images went
    with it; a one-call trace shows the call's own latency instead of 0 s;
(b) `frigate-*` rids are the camera pipe (source «frigate»), so the dev slot's user traces never contain them;
(c) the call logs the role that was asked (`frigate`) and the endpoint it ran on — not only the shared role whose model
    it borrowed (`miss_kare`).
All logs are tmp files; no model, no HTTP."""

import asyncio
import json

from adapters import llm_adapter
from kaare_core.tools import trace_reader as tr


def test_frigate_rids_are_the_camera_pipe():
    for rid in ("frigate-scene-12", "frigate-conclude-12-1", "frigate-frames-3-2", "rid-frigate-shadow-x"):
        assert tr.infer_source(rid) == "frigate", rid
    assert tr.infer_source("rid-1793940672727") == "user"


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_user_traces_leave_camera_calls_out(tmp_path, monkeypatch):
    for name in ("_ROUTE_LOG", "_LLM_LOG", "_TOOL_LOG", "_THINK_CACHE", "_ENFORCE_LOG"):
        monkeypatch.setattr(tr, name, tmp_path / f"{name}.log")
    _write(tmp_path / "_LLM_LOG.log", [
        {"ts": "2026-11-06T01:00:00+00:00", "rid": "frigate-scene-12", "source": "frigate", "instance": "miss_kare"},
        {"ts": "2026-11-06T01:00:01+00:00", "rid": "rid-1", "source": "user", "instance": "default"},
    ])
    rids = [t["rid"] for t in tr.collect_traces("2026-11-06T00:00:00+00:00", "user")]
    assert rids == ["rid-1"]
    assert [t["rid"] for t in tr.collect_traces("2026-11-06T00:00:00+00:00", "frigate")] == ["frigate-scene-12"]


def test_one_call_trace_reads_preview_role_and_latency(tmp_path, monkeypatch):
    for name in ("_ROUTE_LOG", "_LLM_LOG", "_TOOL_LOG", "_THINK_CACHE", "_ENFORCE_LOG"):
        monkeypatch.setattr(tr, name, tmp_path / f"{name}.log")
    _write(tmp_path / "_LLM_LOG.log", [
        {"ts": "2026-11-06T01:00:00+00:00", "rid": "frigate-scene-12", "source": "frigate", "instance": "miss_kare",
         "role": "frigate", "endpoint": "http://127.0.0.1:11447", "latency_ms": 8400, "has_images": True,
         "prompt_preview": "[1834 chars] Describe the scene"},
    ])
    t = tr.collect_traces("2026-11-06T00:00:00+00:00", "frigate")[0]
    assert t["prompt_preview"] == "[1834 chars] Describe the scene"
    assert t["total_latency_ms"] == 8400 and t["has_images"] is True
    assert t["llm_calls"][0]["role"] == "frigate" and t["llm_calls"][0]["endpoint"] == "http://127.0.0.1:11447"


def test_call_log_extra_never_carries_images():
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 300, "images": ["QUJD" * 1000]}]
    extra = llm_adapter.call_log_extra(msgs, "http://127.0.0.1:11447", "frigate")
    assert extra["role"] == "frigate" and extra["endpoint"] == "http://127.0.0.1:11447"
    assert extra["prompt_preview"].startswith("[300 chars] ") and len(extra["prompt_preview"]) < 160
    assert "QUJD" not in json.dumps(extra) and extra["has_images"] is True
    parts = [{"role": "user", "content": [{"type": "text", "text": "hello"},
                                          {"type": "image_url", "image_url": {"url": "data:..."}}]}]
    e2 = llm_adapter.call_log_extra(parts, "u", None)
    assert e2["has_images"] is True and e2["prompt_preview"] == "[5 chars] hello" and "role" not in e2


def test_ask_llm_role_passes_the_asked_role(monkeypatch):
    seen = {}

    async def fake(target, messages, **kw):
        seen.update(kw)
        return {"ok": True, "text": "t"}

    monkeypatch.setattr(llm_adapter, "call_llm_chat", fake)
    monkeypatch.setattr(llm_adapter, "_resolve_shared_role", lambda role: ("miss_kare", {"provider": "vllm"}))
    asyncio.run(llm_adapter.ask_llm_role("frigate", "p", rid="frigate-scene-1"))
    assert seen.get("asked_role") == "frigate"
