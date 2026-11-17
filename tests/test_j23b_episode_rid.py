"""J23b — one rid per camera episode, the steps under it (main session 2026-11-12).

`rid-frig-ep<n>:<step>[:<i>]` — look, scene, change:<i>, frames:<i>, doubt, conclude:<attempt>; an urgent single event
`rid-frig-ev<event>:urgent`. The trace reader puts the whole episode together under `rid-frig-ep<n>`; the dev meeting
still sees it as camera (source «frigate», J7), never as a user trace; old `frigate-*` rids in older logs still count as
camera. All logs are tmp files; no model."""

import inspect
import json
import re

from kaare_core.frigate_user import conclude
from kaare_core.tools import trace_reader as tr


def test_rid_format():
    assert conclude.episode_rid(42, "look") == "rid-frig-ep42:look"
    assert conclude.episode_rid(42, "frames", 3) == "rid-frig-ep42:frames:3"
    assert tr.trace_key("rid-frig-ep42:frames:3") == "rid-frig-ep42" and tr.trace_key("rid-123") == "rid-123"
    for rid in ("rid-frig-ep42:look", "rid-frig-ev1.2-abc:urgent", "frigate-scene-12"):
        assert tr.infer_source(rid) == "frigate", rid


def test_conclude_builds_no_old_style_rid():
    src = inspect.getsource(conclude)
    assert not re.search(r'f"frigate-(look|scene|change|frames|doubt|conclude|urgent)-', src)


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_one_episode_one_trace(tmp_path, monkeypatch):
    for name in ("_ROUTE_LOG", "_LLM_LOG", "_TOOL_LOG", "_THINK_CACHE", "_ENFORCE_LOG"):
        monkeypatch.setattr(tr, name, tmp_path / f"{name}.log")
    rows = [{"ts": f"2026-11-12T01:00:0{i}+00:00", "rid": rid, "source": "frigate", "latency_ms": 1000}
            for i, rid in enumerate(("rid-frig-ep42:look", "rid-frig-ep42:frames:1", "rid-frig-ep42:frames:2",
                                     "rid-frig-ep42:conclude:1", "rid-frig-ep43:look"))]
    rows.append({"ts": "2026-11-12T01:00:09+00:00", "rid": "rid-9", "source": "user"})
    _write(tmp_path / "_LLM_LOG.log", rows)
    traces = tr.collect_traces("2026-11-12T00:00:00+00:00", "frigate")
    assert sorted(t["rid"] for t in traces) == ["rid-frig-ep42", "rid-frig-ep43"]
    t = tr.get_trace("rid-frig-ep42")
    assert len(t["llm_calls"]) == 4
    assert [x["rid"] for x in tr.collect_traces("2026-11-12T00:00:00+00:00", "user")] == ["rid-9"]
