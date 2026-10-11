# /kaare/kaare_core/tools/trace_reader.py
"""
Assembles per-request traces from the three log files:
  route_decisions.log  — routing stages (user requests only)
  llm_calls.log        — LLM invocations (user + refl + meet after P17)
  tool_calls.log       — tool executions
  think_cache.jsonl    — think-block entries

No external dependencies — stdlib only (json, pathlib, datetime).
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kaare_core import log_buffer

_TR: dict[str, dict[str, str]] = {
    "empty_trace": {"nb": "Tom trace.", "en": "Empty trace.", "de": "Leere Trace."},
    "no_traces": {
        "nb": "Ingen traces funnet.",
        "en": "No traces found.",
        "de": "Keine Traces gefunden.",
    },
    "none_tools": {"nb": "[ingen]", "en": "[none]", "de": "[keine]"},
    "fallback": {"nb": "[FALLBACK]", "en": "[FALLBACK]", "de": "[FALLBACK]"},
    "empty_resp": {"nb": "[tomt svar]", "en": "[empty response]", "de": "[leere Antwort]"},
    "routing": {"nb": "Ruting:", "en": "Routing:", "de": "Routing:"},
    "llm_label": {"nb": "LLM:", "en": "LLM:", "de": "LLM:"},
    "tools_label": {"nb": "Tools:", "en": "Tools:", "de": "Tools:"},
    "think_label": {"nb": "Think:", "en": "Think:", "de": "Think:"},
    "vision": {"nb": "vision", "en": "vision", "de": "Vision"},
    "think_key": {"nb": "think", "en": "think", "de": "think"},
    "pattern_header": {
        "nb": "Trace-mønsteranalyse ({n} traces totalt):",
        "en": "Trace pattern analysis ({n} traces total):",
        "de": "Trace-Musteranalyse ({n} Traces insgesamt):",
    },
    "src_summary": {
        "nb": "snitt latency {lat}s | snitt LLM {llm}s | fallback {fb}%",
        "en": "avg latency {lat}s | avg LLM {llm}s | fallback {fb}%",
        "de": "Ø Latenz {lat}s | Ø LLM {llm}s | Fallback {fb}%",
    },
    "top_tools": {"nb": "Topp tools (user):", "en": "Top tools (user):", "de": "Top-Tools (user):"},
    "think_blocks": {"nb": "Think-blokker:", "en": "Think blocks:", "de": "Think-Blöcke:"},
    "images_label": {"nb": "Bilder:", "en": "Images:", "de": "Bilder:"},
    "empty_resps": {
        "nb": "Tomme svar (recovered):",
        "en": "Empty responses (recovered):",
        "de": "Leere Antworten (recovered):",
    },
    "slowest": {
        "nb": "Tregeste user-trace:",
        "en": "Slowest user trace:",
        "de": "Langsamste User-Trace:",
    },
}


def _tr(key: str, lang: str, **kwargs: object) -> str:
    s = _TR.get(key, {}).get(lang) or _TR.get(key, {}).get("nb") or key
    return s.format(**kwargs) if kwargs else s


_LOG_DIR = Path("/kaare/logs")
_ROUTE_LOG = _LOG_DIR / "route_decisions.log"
_LLM_LOG = _LOG_DIR / "llm_calls.log"
_TOOL_LOG = _LOG_DIR / "tool_calls.log"
_THINK_CACHE = Path("/kaare/state/think_cache.jsonl")
_ENFORCE_LOG = _LOG_DIR / "enforcement.log"  # ModelEnforcer hits, one line per forced retry (P118)

_MAX_SCAN_LINES = 10_000


def infer_source(rid: str) -> str:
    """Which path a request came in on, from the rid prefix — the one shared vocabulary for
    traces and for anything that records its origin (e.g. the dev-meeting agenda, P118)."""
    if rid.startswith("rid-refl-"):
        return "refl"
    if rid.startswith("rid-meet-"):
        return "meet"
    if rid.startswith("rid-timer-"):
        return "timer"
    if rid.startswith("rid-stt-"):
        return "stt"
    if rid.startswith("rid-frigate-"):
        return "frigate"
    return "user"


_infer_source = infer_source  # old private name, kept for existing callers


def _read_jsonl(path: Path, max_lines: int = _MAX_SCAN_LINES) -> list[dict]:
    log_buffer.flush(path)  # include lines still buffered in RAM in this process (P131)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-max_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result
    except Exception:
        return []


def _ts_to_ms(ts: str) -> int:
    """Convert ISO timestamp to milliseconds since epoch."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


_VOICE_STAGES = {"stt_pipeline_start", "stt_done", "stt_speaker", "stt_kaare_done"}


def _iter_jsonl_since(path: Path, since_iso: str):
    """Stream a JSONL log once, yielding entries with ts >= since_iso (one pass, no full read
    into memory — used by collect_traces for the nightly dev slot)."""
    log_buffer.flush(path)  # include lines still buffered in RAM in this process (P131)
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("ts", "") >= since_iso:
                    yield entry
    except Exception:
        return


def _empty_parts() -> dict:
    return {
        "stages": [],
        "llm_calls": [],
        "tool_calls": [],
        "think_entries": [],
        "voice_events": [],
        "enforcer_hits": [],
        "prompt_preview": "",
    }


def _add_route(parts: dict, entry: dict) -> None:
    stage = entry.get("stage", "")
    parts["stages"].append({"ts": entry.get("ts", ""), "stage": stage, "hit": entry.get("hit")})
    if stage == "generate_in" and not parts["prompt_preview"]:
        parts["prompt_preview"] = entry.get("prompt_preview", "")
    if stage in _VOICE_STAGES:
        ve: dict[str, Any] = {"stage": stage, "ts": entry.get("ts", "")}
        for key in (
            "node",
            "room",
            "text",
            "stt_ms",
            "confirmed_by",
            "user",
            "best_guess",
            "confidence",
            "total_ms",
        ):
            if key in entry:
                ve[key] = entry[key]
        parts["voice_events"].append(ve)


def _add_llm(parts: dict, entry: dict) -> None:
    parts["llm_calls"].append(
        {
            "ts": entry.get("ts", ""),
            "source": entry.get("source", "user"),
            "instance": entry.get("instance", ""),
            "model": entry.get("model", ""),
            "latency_ms": entry.get("latency_ms", 0),
            "has_tools": entry.get("has_tools", False),
            "has_think": entry.get("has_think", False),
            "has_images": entry.get("has_images", False),
            "recovered": entry.get("recovered", False),
            "status": entry.get("status", ""),
            "prompt_meta": entry.get("prompt_meta"),
        }
    )


def _add_tool(parts: dict, entry: dict) -> None:
    parts["tool_calls"].append(
        {
            "ts": entry.get("ts", ""),
            "tool": entry.get("tool", ""),
            "args": entry.get("args", {}),
            "result_preview": entry.get("result_preview", ""),
            "duration_ms": entry.get("duration_ms", 0),
        }
    )


def _add_think(parts: dict, entry: dict) -> None:
    parts["think_entries"].append(
        {
            "ts": entry.get("ts", ""),
            "think_summary": entry.get("think_summary", ""),
            "latency_ms": entry.get("latency_ms", 0),
            "recovered": entry.get("recovered", False),
        }
    )


def _add_enforce(parts: dict, entry: dict) -> None:
    parts["enforcer_hits"].append(
        {
            "ts": entry.get("ts", ""),
            "detector": entry.get("detector", ""),
            "event": entry.get("event", ""),
        }
    )


_SOURCES = (  # (log path attribute name, adder) — attribute names so tests can monkeypatch paths
    ("_ROUTE_LOG", _add_route),
    ("_LLM_LOG", _add_llm),
    ("_TOOL_LOG", _add_tool),
    ("_THINK_CACHE", _add_think),
    ("_ENFORCE_LOG", _add_enforce),
)


def _assemble(rid: str, parts: dict) -> dict:
    stages, llm_calls = parts["stages"], parts["llm_calls"]
    tool_calls, think_entries = parts["tool_calls"], parts["think_entries"]
    all_ts = (
        [x["ts"] for x in stages if x["ts"]]
        + [c["ts"] for c in llm_calls if c["ts"]]
        + [x["ts"] for x in tool_calls if x["ts"]]
        + [e["ts"] for e in think_entries if e["ts"]]
    )
    ts_start = min(all_ts) if all_ts else ""
    ts_end = max(all_ts) if all_ts else ""

    total_latency_ms = 0
    if ts_start and ts_end and ts_start != ts_end:
        total_latency_ms = _ts_to_ms(ts_end) - _ts_to_ms(ts_start)

    tool_names = list(dict.fromkeys(x["tool"] for x in tool_calls if x["tool"]))

    return {
        "rid": rid,
        "source": _infer_source(rid),
        "ts_start": ts_start,
        "ts_end": ts_end,
        "stages": stages,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "think_entries": think_entries,
        "voice_events": parts["voice_events"],
        "enforcer_hits": parts["enforcer_hits"],
        "prompt_preview": parts["prompt_preview"],
        "total_latency_ms": total_latency_ms,
        "llm_call_count": len(llm_calls),
        "tool_count": len(tool_calls),
        "tool_names": tool_names,
        "has_images": any(c["has_images"] for c in llm_calls),
        "used_fallback": any(c["instance"] in ("fallback_9b",) for c in llm_calls),
        "has_think": any(c["has_think"] for c in llm_calls) or bool(think_entries),
    }


def get_trace(rid: str) -> dict:
    """
    Assemble one structured trace for the given rid from all log sources.
    Returns a partial dict if only some sources have data — never raises.
    """
    if not rid:
        return {}
    parts = _empty_parts()
    module = globals()
    for path_name, add in _SOURCES:
        for entry in _read_jsonl(module[path_name]):
            if entry.get("rid") == rid:
                add(parts, entry)
    return _assemble(rid, parts)


def tools_by_rid(rids: set[str]) -> dict[str, list[str]]:
    """Tool names per rid from tool_calls.log only (one scan for many rids — cheaper than
    get_trace per rid). Used to give agenda rows a theme by what the turn did (P118 B8)."""
    out: dict[str, list[str]] = {}
    if not rids:
        return out
    for entry in _read_jsonl(globals()["_TOOL_LOG"]):
        rid = entry.get("rid")
        if rid in rids and entry.get("tool"):
            out.setdefault(rid, []).append(str(entry["tool"]))
    return out


def collect_traces(since_iso: str, source: str = "user", limit: int | None = None) -> list[dict]:
    """All traces with activity since `since_iso`, newest first, reading each log ONCE.

    For the nightly dev slot (P118 S0): get_trace() per rid re-reads every log file for every
    trace (≈ 4 × 10 MB per call); this does one streaming pass per file instead.
    source: "user" / "refl" / "meet" / "timer" / "stt" / "frigate" / "all".
    """
    buckets: dict[str, dict] = {}
    module = globals()
    for path_name, add in _SOURCES:
        for entry in _iter_jsonl_since(module[path_name], since_iso):
            rid = entry.get("rid", "")
            if not rid or (source != "all" and _infer_source(rid) != source):
                continue
            add(buckets.setdefault(rid, _empty_parts()), entry)
    traces = [_assemble(rid, parts) for rid, parts in buckets.items()]
    traces.sort(key=lambda tr: tr["ts_end"] or tr["ts_start"], reverse=True)
    return traces[:limit] if limit else traces


def get_recent_traces(n: int = 50, source: str = "all") -> list[dict]:
    """
    Return the last N traces, newest first.
    source: "user" / "refl" / "meet" / "all"
    Collects rids from both route_decisions.log and llm_calls.log
    so that background jobs (refl/meet) are included.
    """
    n = min(max(n, 1), 200)
    seen: dict[str, str] = {}  # rid → ts

    for entry in _read_jsonl(_ROUTE_LOG):
        rid = entry.get("rid", "")
        ts = entry.get("ts", "")
        if rid and (rid not in seen or ts > seen[rid]):
            seen[rid] = ts

    for entry in _read_jsonl(_LLM_LOG):
        rid = entry.get("rid", "")
        ts = entry.get("ts", "")
        if rid and (rid not in seen or ts > seen[rid]):
            seen[rid] = ts

    def _src_ok(rid: str) -> bool:
        if source == "all":
            return True
        return _infer_source(rid) == source

    sorted_rids = sorted(
        [r for r in seen if _src_ok(r)],
        key=lambda r: seen[r],
        reverse=True,
    )

    return [get_trace(rid) for rid in sorted_rids[:n]]


def format_trace_for_kare(trace: dict, lang: str = "nb") -> str:
    """Format one trace as readable text for LLM context."""
    if not trace:
        return _tr("empty_trace", lang)
    rid = trace.get("rid", "?")
    source = trace.get("source", "?")
    total = trace.get("total_latency_ms", 0)
    lines = [f"{rid} [{source}] ({total / 1000:.1f}s):"]

    voice_events = trace.get("voice_events", [])
    if voice_events:
        for ve in voice_events:
            stage = ve.get("stage", "")
            if stage == "stt_pipeline_start":
                lines.append(f"  [Voice] node={ve.get('node', '?')} room={ve.get('room', '?')}")
            elif stage == "stt_speaker":
                conf = ve.get("confidence", 0.0)
                lines.append(
                    f"  [Voice] speaker={ve.get('confirmed_by', '?')} "
                    f"user={ve.get('user', '?')} guess={ve.get('best_guess')} conf={conf:.2f}"
                )
            elif stage == "stt_done":
                lines.append(f'  [Voice] "{ve.get("text", "?")}" (STT {ve.get("stt_ms", 0)}ms)')
            elif stage == "stt_kaare_done":
                lines.append(f"  [Voice] total (excl. TTS) {ve.get('total_ms', 0)}ms")

    stages = trace.get("stages", [])
    if stages:
        kare_stages = [s["stage"] for s in stages if not s["stage"].startswith("stt_")]
        if kare_stages:
            lines.append(f"  {_tr('routing', lang)} {' → '.join(kare_stages)}")

    yes = {"nb": "ja", "en": "yes", "de": "ja"}.get(lang, "yes")
    no = {"nb": "nei", "en": "no", "de": "nein"}.get(lang, "no")

    for call in trace.get("llm_calls", []):
        inst = call.get("instance", "?")
        lat = call.get("latency_ms", 0)
        vision = yes if call.get("has_images") else no
        think = yes if call.get("has_think") else no
        fallback = f" {_tr('fallback', lang)}" if call.get("instance") == "fallback_9b" else ""
        recovered = f" {_tr('empty_resp', lang)}" if call.get("recovered") else ""
        vk = _tr("vision", lang)
        tk = _tr("think_key", lang)
        lines.append(
            f"  {_tr('llm_label', lang)} {inst}, {lat}ms, {vk}={vision}, {tk}={think}{fallback}{recovered}"
        )

    tool_names = trace.get("tool_names", [])
    if tool_names:
        lines.append(f"  {_tr('tools_label', lang)} {', '.join(tool_names)}")
    elif trace.get("tool_count", 0) == 0:
        lines.append(f"  {_tr('tools_label', lang)} {_tr('none_tools', lang)}")

    for entry in trace.get("think_entries", []):
        summary = entry.get("think_summary", "")[:120]
        if summary:
            lines.append(f'  {_tr("think_label", lang)} "{summary}"')

    return "\n".join(lines)


def format_patterns_for_kare(traces: list[dict], lang: str = "nb") -> str:
    """Analyse patterns across N traces and return a readable summary."""
    if not traces:
        return _tr("no_traces", lang)

    by_source: dict[str, list[dict]] = {"user": [], "stt": [], "refl": [], "meet": []}
    for t in traces:
        src = t.get("source", "user")
        if src in by_source:
            by_source[src].append(t)
        else:
            by_source["user"].append(t)

    tool_counter: dict[str, int] = {}
    for t in by_source["user"]:
        for name in t.get("tool_names", []):
            tool_counter[name] = tool_counter.get(name, 0) + 1

    top_tools = sorted(tool_counter.items(), key=lambda x: x[1], reverse=True)[:5]

    lines = [_tr("pattern_header", lang, n=len(traces))]

    for src, src_traces in by_source.items():
        if not src_traces:
            continue
        latencies = [t["total_latency_ms"] for t in src_traces if t["total_latency_ms"] > 0]
        avg_lat = int(sum(latencies) / len(latencies)) if latencies else 0
        llm_lats = [
            c["latency_ms"]
            for tr in src_traces
            for c in tr.get("llm_calls", [])
            if c.get("latency_ms", 0) > 0
        ]
        avg_llm = int(sum(llm_lats) / len(llm_lats)) if llm_lats else 0
        fallback_n = sum(1 for t in src_traces if t.get("used_fallback"))
        fallback_pct = int(100 * fallback_n / len(src_traces)) if src_traces else 0

        summary = _tr(
            "src_summary",
            lang,
            lat=f"{avg_lat / 1000:.1f}",
            llm=f"{avg_llm / 1000:.1f}",
            fb=fallback_pct,
        )
        lines.append(f"\n[{src.upper()}] {len(src_traces)} traces | {summary}")

    if top_tools:
        tool_str = ", ".join(f"{n} ({c}x)" for n, c in top_tools)
        lines.append(f"\n{_tr('top_tools', lang)} {tool_str}")

    user_traces = by_source["user"]
    if user_traces:
        think_n = sum(1 for t in user_traces if t.get("has_think"))
        image_n = sum(1 for t in user_traces if t.get("has_images"))
        recover_n = sum(
            1 for t in user_traces for c in t.get("llm_calls", []) if c.get("recovered")
        )
        lines.append(
            f"{_tr('think_blocks', lang)} {think_n}/{len(user_traces)} | "
            f"{_tr('images_label', lang)} {image_n}/{len(user_traces)} | "
            f"{_tr('empty_resps', lang)} {recover_n}"
        )

    if user_traces:
        by_lat = sorted(user_traces, key=lambda t: t["total_latency_ms"], reverse=True)
        slowest = by_lat[0]
        lines.append(
            f"{_tr('slowest', lang)} {slowest['rid']} ({slowest['total_latency_ms'] / 1000:.1f}s)"
        )

    return "\n".join(lines)
