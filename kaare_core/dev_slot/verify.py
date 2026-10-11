"""
S4 — verify (P118 B2). Code only: does each claim cite evidence that actually exists?

A claim is `verified` only when at least one of its evidence refs resolves AND, if it carries a
quote, that quote is found (whitespace/case-normalized) in the text of a resolved ref. Anything
else is `unverified` — kept with the reasons, never dropped (T2-D9, I46 bad examples).
The 9B evidence verifier (B4) runs on top of this; code never decides what is true, only
whether the cited evidence exists and contains what is quoted.

Evidence ref forms:
  tool:<rid>#<n>     n-th (1-based) tool call of <rid> in tool_calls.log — only rids that belong
                     to THIS run count (a claim cannot cite yesterday's meeting as proof); the text
                     checked is the full result from `tool_texts` (what Kåre saw) when given
  trace:<rid>        any logged activity for <rid> (a user trace the claim is about)
  inbox:<id>         an agenda inbox row in learning.db
  frigate:<event_id> a Frigate event analysis in logs/frigate_analysis.log
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from kaare_core.memory import learning_store
from kaare_core.tools import trace_reader

FRIGATE_LOG = Path("/kaare/logs/frigate_analysis.log")

_REF_RE = re.compile(r"^(tool|trace|inbox|frigate):(.+)$")
_EVIDENCE_MAX = 4000  # chars of resolved evidence handed to the 9B verifier per claim
_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip().lower()


_ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…)\s*")
_QUOTE_CHARS = "\"'«»“”„‘’`"
_MIN_PIECE = 8  # chars — shorter pieces between ellipses prove nothing


def quote_in(quote: str, evidence: str) -> bool:
    """Is the quote verbatim in the evidence? Tolerates what models do around a real quote —
    surrounding quote marks and pieces joined by «...» / «…» — but every piece must still be in
    the evidence word for word (whitespace and case normalized)."""
    ev = _norm(evidence)
    pieces = [p.strip().strip(_QUOTE_CHARS).strip() for p in _ELLIPSIS_RE.split(str(quote or ""))]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    if any(len(p) < _MIN_PIECE for p in pieces) and len(pieces) > 1:
        pieces = [p for p in pieces if len(p) >= _MIN_PIECE]
        if not pieces:
            return False
    return all(_norm(p) in ev for p in pieces)


class _Evidence:
    """Loads each source at most once per verify_claims() call (one pass per log)."""

    def __init__(self, run_rids: set[str], tool_texts: dict[str, str] | None = None):
        self.run_rids = set(run_rids)
        self.tool_texts = tool_texts or {}
        self._tools: dict[str, list[dict]] | None = None
        self._frigate: dict[str, str] | None = None
        self._traces: dict[str, dict] = {}

    def tools(self) -> dict[str, list[dict]]:
        if self._tools is None:
            self._tools = {}
            for entry in trace_reader._read_jsonl(trace_reader._TOOL_LOG, max_lines=10**9):
                rid = entry.get("rid", "")
                if rid in self.run_rids:
                    self._tools.setdefault(rid, []).append(entry)
        return self._tools

    def frigate(self) -> dict[str, str]:
        if self._frigate is None:
            self._frigate = {}
            for entry in trace_reader._read_jsonl(FRIGATE_LOG, max_lines=10**9):
                eid = entry.get("event_id", "")
                if eid:
                    self._frigate[eid] = json.dumps(entry, ensure_ascii=False)
        return self._frigate

    def trace(self, rid: str) -> dict:
        if rid not in self._traces:
            self._traces[rid] = trace_reader.get_trace(rid)
        return self._traces[rid]


def _resolve(ref: str, ev: _Evidence) -> dict:
    m = _REF_RE.match(str(ref or "").strip())
    if not m:
        return {"ref": ref, "ok": False, "why": "unknown_ref_form", "text": ""}
    kind, value = m.group(1), m.group(2).strip()

    if kind == "tool":
        rid, _, n = value.partition("#")
        if rid not in ev.run_rids:
            return {"ref": ref, "ok": False, "why": "not_this_run", "text": ""}
        calls = ev.tools().get(rid, [])
        try:
            idx = int(n) - 1
        except ValueError:
            return {"ref": ref, "ok": False, "why": "bad_index", "text": ""}
        if idx < 0 or idx >= len(calls):
            return {"ref": ref, "ok": False, "why": "no_such_call", "text": ""}
        c = calls[idx]
        # The full result this run's tool returned (what Kåre saw), when the caller has it;
        # otherwise the log's preview. Plain text, not JSON — escaped newlines broke quotes.
        full = ev.tool_texts.get(ref.strip())
        text = (
            f"{c.get('tool')} {json.dumps(c.get('args'), ensure_ascii=False)}\n"
            f"{full if full is not None else c.get('result_preview', '')}"
        )
        return {"ref": ref, "ok": True, "why": "", "text": text}

    if kind == "trace":
        tr = ev.trace(value)
        if not tr or not tr.get("ts_start"):
            return {"ref": ref, "ok": False, "why": "no_such_trace", "text": ""}
        return {
            "ref": ref,
            "ok": True,
            "why": "",
            "text": json.dumps(tr, ensure_ascii=False, default=str),
        }

    if kind == "inbox":
        try:
            row = learning_store.get_inbox_item(int(value))
        except ValueError:
            row = None
        if not row:
            return {"ref": ref, "ok": False, "why": "no_such_inbox_row", "text": ""}
        return {
            "ref": ref,
            "ok": True,
            "why": "",
            "text": f"{row.get('text', '')} {row.get('context', '')}",
        }

    # frigate
    text = ev.frigate().get(value, "")
    if not text:
        return {"ref": ref, "ok": False, "why": "no_such_event", "text": ""}
    return {"ref": ref, "ok": True, "why": "", "text": text}


def verify_claims(
    claims: list[dict], run_rids: set[str], tool_texts: dict[str, str] | None = None
) -> list[dict]:
    """claims: [{"text": str, "evidence_refs": [str, ...], "quote": str | None}, ...]
    Returns one result per claim, same order:
      {"text", "verified", "reason", "quote_found", "refs": [{"ref", "ok", "why"}], "evidence"}
    `evidence` is the text of the refs that resolved (capped) — what the 9B verifier reads."""
    ev = _Evidence(run_rids, tool_texts)
    results = []
    for claim in claims:
        refs = [r for r in (claim.get("evidence_refs") or []) if str(r).strip()]
        resolved = [_resolve(r, ev) for r in refs]
        good = [r for r in resolved if r["ok"]]
        quote = (claim.get("quote") or "").strip()
        quote_found = None
        if quote:
            quote_found = any(quote_in(quote, r["text"]) for r in good)
        if not refs:
            reason = "no_evidence_cited"
        elif not good:
            reason = "no_ref_resolves"
        elif quote and not quote_found:
            reason = "quote_not_in_evidence"
        else:
            reason = "ok"
        results.append(
            {
                "text": claim.get("text", ""),
                "verified": reason == "ok",
                "reason": reason,
                "quote_found": quote_found,
                "refs": [{"ref": r["ref"], "ok": r["ok"], "why": r["why"]} for r in resolved],
                "evidence": "\n---\n".join(f"[{r['ref']}] {r['text']}" for r in good)[
                    :_EVIDENCE_MAX
                ],
            }
        )
    return results
