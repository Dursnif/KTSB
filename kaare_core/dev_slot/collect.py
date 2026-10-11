"""
S0 — collect (P118 B2). Code only: gathers, groups, counts and links. Never judges or drops.

Bundle for the night:
  inbox   — open agenda topics with their newest rows and pointers (learning.db)
  traces  — user traces since the last finished run (cap: max_user_traces), each with
            mechanical anchors (fallback, recovered, enforcer hits, tool use, next turn)
  health  — scripts/health_check.py --json (imports, configs, live endpoints)
  changes — what changed since the previous night (dev_slot/changes.py)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaare_core.dev_slot import changes as changes_mod
from kaare_core.dev_slot.config import get_dev_slot_config
from kaare_core.memory import learning_store
from kaare_core.tools.trace_reader import collect_traces

KAARE_ROOT = Path("/kaare")
HEALTH_SCRIPT = KAARE_ROOT / "scripts" / "health_check.py"
_HEALTH_TIMEOUT_S = 180
_NEXT_TURN_WINDOW_S = 600  # a user turn this soon after a trace is linked as its "next turn"
_PREVIEW = 160  # chars kept from tool results / prompts in the bundle


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def trace_anchors(trace: dict) -> dict:
    """Mechanical facts about one trace — what happened, not whether it was good."""
    llm = trace.get("llm_calls", [])
    tools = trace.get("tool_calls", [])
    return {
        "used_fallback": bool(trace.get("used_fallback")),
        "recovered": any(c.get("recovered") for c in llm)
        or any(e.get("recovered") for e in trace.get("think_entries", [])),
        "enforcer_hits": [
            h.get("detector", "")
            for h in trace.get("enforcer_hits", [])
            if h.get("event") == "retry"
        ],
        "llm_calls": len(llm),
        "tool_names": trace.get("tool_names", []),
        "tools": [
            {"tool": tc.get("tool", ""), "result": str(tc.get("result_preview", ""))[:_PREVIEW]}
            for tc in tools
        ],
        "library_called": any(tc.get("tool") == "library" for tc in tools),
        "has_images": bool(trace.get("has_images")),
        "latency_ms": trace.get("total_latency_ms", 0),
    }


def link_next_turns(traces: list[dict]) -> None:
    """Add `next_rid` to each trace: the next user trace that started within the window after it
    ended (a pointer the 27B can follow to see whether the user corrected Kåre). In place."""
    ordered = sorted(traces, key=lambda tr: tr.get("ts_start", ""))
    for i, tr in enumerate(ordered):
        tr["next_rid"] = ""
        end = _parse_ts(tr.get("ts_end") or tr.get("ts_start", ""))
        if not end:
            continue
        for nxt in ordered[i + 1 :]:
            start = _parse_ts(nxt.get("ts_start", ""))
            if not start:
                continue
            gap = (start - end).total_seconds()
            if gap < 0:
                continue
            if gap <= _NEXT_TURN_WINDOW_S:
                tr["next_rid"] = nxt["rid"]
            break


async def run_health_check(timeout_s: int = _HEALTH_TIMEOUT_S) -> dict:
    """scripts/health_check.py --json in a subprocess; never raises."""
    if not HEALTH_SCRIPT.exists():
        return {"ok": False, "error": "health_check.py not found"}
    env = dict(os.environ, PYTHONPATH=str(KAARE_ROOT))
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(HEALTH_SCRIPT),
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return {"ok": False, "error": f"timeout after {timeout_s}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        data = json.loads(out.decode("utf-8", errors="replace").strip().splitlines()[-1])
        return {"ok": True, "result": data}
    except Exception:
        return {
            "ok": False,
            "error": "unparseable output",
            "output": out.decode("utf-8", errors="replace")[-500:],
        }


async def collect(
    run_id: str,
    *,
    since: str | None = None,
    now: datetime | None = None,
    with_health: bool = True,
    with_changes: bool = True,
) -> dict:
    """Build and store the S0 bundle for `run_id`. Returns it."""
    cfg = get_dev_slot_config()
    now = now or datetime.now(UTC)
    since = (
        since or learning_store.last_finished_run_start() or (now - timedelta(hours=24)).isoformat()
    )

    learning_store.start_run(run_id, "S0")

    topics = await asyncio.to_thread(learning_store.open_topics)
    all_traces = await asyncio.to_thread(collect_traces, since, "user")
    link_next_turns(all_traces)
    picked = all_traces[: cfg["max_user_traces"]]
    traces = [
        {
            "rid": tr["rid"],
            "ts_start": tr.get("ts_start", ""),
            "ts_end": tr.get("ts_end", ""),
            "prompt_preview": str(tr.get("prompt_preview", ""))[:_PREVIEW],
            "next_rid": tr.get("next_rid", ""),
            "anchors": trace_anchors(tr),
        }
        for tr in picked
    ]

    health = await run_health_check() if with_health else {"ok": False, "error": "skipped"}
    changes = (
        (await asyncio.to_thread(changes_mod.snapshot_and_diff))
        if with_changes
        else {"skipped": True}
    )

    bundle = {
        "run_id": run_id,
        "since": since,
        "collected_at": now.isoformat(),
        "config": cfg,
        "inbox": {
            "topics": topics,
            "open_topics": len(topics),
            "open_items": sum(t.get("open_items", 0) for t in topics),
        },
        "traces": {"items": traces, "found": len(all_traces), "reviewed": len(traces)},
        "health": health,
        "changes": changes,
    }
    learning_store.save_artifact(run_id, "S0", bundle)
    learning_store.set_run_step(run_id, "S0-done")
    return bundle
