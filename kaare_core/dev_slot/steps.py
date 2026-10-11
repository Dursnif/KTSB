"""
Janus + Kåre — S1 clean, S2 review, S3 develop (P118 B3), with S4 code verification of S3
findings. Two contexts on the 27B (T2-D20): Janus sees history and priorities only (no tools,
own system prompt); Kåre runs through his full pipe (personality + memory, ask_llm_with_tools)
with one topic or trace at a time. No debate rounds: Janus → Kåre → code → stored.

Code never decides (T2-D9): it orders, counts, links, checks citations and applies the status
that follows from a 27B verdict. Whatever does not fit in the time budget stays `new` for the
next night — nothing is dropped.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adapters.llm_adapter import ask_llm_with_tools, call_llm_chat
from kaare_core.config import get_local_tz
from kaare_core.dev_slot import verifier
from kaare_core.dev_slot.json_out import extract_json
from kaare_core.dev_slot.tools import SlotTools
from kaare_core.dev_slot.verify import verify_claims
from kaare_core.memory import learning_store, relevance
from kaare_core.memory.agenda_themes import FALLBACK_THEME, THEMES
from kaare_core.model_enforcement import strip_language_tag
from kaare_core.think_strip import strip_think_block
from kaare_core.tools.i18n import t

log = logging.getLogger("dev_slot")

_PRIORITIES = {"new", "develop", "seen_before", "drop", "same_as_resolved", "contradicts"}
_RESOLVED_MATCHES = 2  # nearest resolved topics shown to Janus per topic (P133 C4)
_FOLLOWUP_DAYS = 30  # confirmed proposals followed up this long
_KINDS = {"problem", "event", "known", "not_real"}
_SAMPLE_CHARS = 160
_ROW_CHARS = 400
_JANUS_TIMEOUT_S = 300
_JANUS_BATCH = 25  # topics per Janus call — one call over ~100 topics hit the output cap
_JANUS_MAX_TOKENS = (
    4096  # one line per topic; thinking is off (it ate the whole cap, live 2026-10-03)
)
_PRIORITY_OBJ_RE = re.compile(r'\{\s*"topic_id"\s*:\s*\d+[^{}]*\}')


class Budget:
    """Wall-clock budget for the whole slot (PR2: time_budget_minutes)."""

    def __init__(self, minutes: int, clock=time.monotonic):
        self._clock = clock
        self._deadline = clock() + minutes * 60

    def left(self) -> bool:
        return self._clock() < self._deadline


# ── LLM calls ───────────────────────────────────────────────────────────────


async def _janus_call(system: str, user: str, rid: str) -> str:
    res = await call_llm_chat(
        "default",
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        rid=rid,
        source="meet",
        timeout=_JANUS_TIMEOUT_S,
        disable_thinking=True,
        options={"num_predict": _JANUS_MAX_TOKENS},
    )
    return (res.get("text") or "") if res.get("ok") else ""


async def kare_json(
    task: str,
    *,
    rid: str,
    lang: str,
    tools: SlotTools | None,
    max_rounds: int,
    budget: Budget,
    think: bool = True,
) -> tuple[dict | None, str]:
    """Kåre through his full pipe: up to `max_rounds` tool rounds, then a forced answer; the
    reply must be one JSON object (one retry if it is not). Returns (data, last_text)."""
    messages: list[dict] = [{"role": "user", "content": task}]
    schemas = tools.schemas if tools else []
    text = ""
    rounds = 0
    forced = 0
    while True:
        if not budget.left():
            return None, text
        res = await ask_llm_with_tools(
            messages=messages,
            tools=schemas,
            rid=rid,
            user_id="",
            source="meet",
            disable_thinking=not think,
        )
        if not res.get("ok") and not res.get("tool_calls"):
            return None, res.get("text") or ""
        calls = res.get("tool_calls") or []
        text = res.get("text") or ""
        if calls and tools and rounds < max_rounds:
            messages.append(
                res.get("message") or {"role": "assistant", "content": text, "tool_calls": calls}
            )
            for tc in calls:
                fn = tc.get("function", {})
                out = await tools.execute(fn.get("name", ""), fn.get("arguments", {}))
                messages.append({"role": "tool", "name": fn.get("name", ""), "content": out})
            rounds += 1
            continue
        if calls:  # round cap reached (or no tools offered): force the answer once
            forced += 1
            if forced > 1:
                return None, text
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": t("slot_answer_now", lang)})
            schemas = []
            tools = None
            continue
        data = extract_json(text)
        if data is not None:
            return data, text
        if messages[-1].get("content") == t("slot_json_retry", lang):
            return None, text
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": t("slot_json_retry", lang)})
        schemas = []
        tools = None


# ── Janus ───────────────────────────────────────────────────────────────────


async def resolved_matches(topics: list[dict]) -> dict[int, list[dict]]:
    """Per tonight's topic: the nearest resolved topics by meaning, with their conclusion
    (P133 C4/D5). Empty when nothing is resolved or no embedder is configured."""
    try:
        resolved = learning_store.resolved_topics(limit=200)
        if not resolved or relevance.provider() is None:
            return {}
        docs = [
            {
                "key": f"resolved:{r['id']}",
                "text": f"{r.get('label') or ''}: {r['conclusion']}",
                "id": r["id"],
                "conclusion": r["conclusion"],
                "resolved_at": str(r.get("resolved_at") or "")[:10],
            }
            for r in resolved
            if r.get("conclusion")
        ]
        await relevance.sync_vectors(docs)
        out: dict[int, list[dict]] = {}
        for tp in topics:
            query = " ".join(
                [str(tp.get("label") or "")]
                + [str(i.get("text", ""))[:_SAMPLE_CHARS] for i in tp.get("items", [])[:2]]
            )
            hits = await relevance.relevant(query, docs, k=_RESOLVED_MATCHES)
            if hits:
                out[tp["id"]] = [
                    {
                        "resolved_id": h["id"],
                        "conclusion": h["conclusion"],
                        "resolved_at": h["resolved_at"],
                        "score": h["score"],
                    }
                    for h in hits
                ]
        return out
    except Exception as e:
        log.warning("resolved matches failed: %s", e)
        return {}


def janus_payload(bundle: dict, matches: dict[int, list[dict]] | None = None) -> dict:
    topics = bundle.get("inbox", {}).get("topics", [])
    earlier = learning_store.earlier_verdicts([tp["id"] for tp in topics])
    matches = matches or {}
    rows = []
    for tp in topics:
        items = tp.get("items", [])
        rows.append(
            {
                "topic_id": tp["id"],
                "count": tp.get("count", 0),
                "open_rows": tp.get("open_items", 0),
                "first_seen": tp.get("first_seen", ""),
                "last_seen": tp.get("last_seen", ""),
                "sources": sorted({i.get("source", "") for i in items}),
                "samples": [str(i.get("text", ""))[:_SAMPLE_CHARS] for i in items[:2]],
                "earlier_verdicts": earlier.get(tp["id"], []),
                **({"resolved_matches": matches[tp["id"]]} if tp["id"] in matches else {}),
            }
        )
    health = bundle.get("health", {})
    changes = bundle.get("changes", {})
    return {
        "topics": rows,
        "user_traces": bundle.get("traces", {}).get("found", 0),
        "health_ok": bool(health.get("ok")) and (health.get("result") or {}).get("ok", False),
        "changes": {k: changes.get(k) for k in ("baseline", "version", "since") if k in changes}
        | (
            {"code_changed": len((changes.get("code") or {}).get("changed", []))}
            if isinstance(changes.get("code"), dict)
            else {}
        ),
    }


def parse_priorities(text: str) -> list[dict]:
    """Priority objects from Janus's reply. Uses the whole JSON when it parses; when the reply was
    cut off mid-way, every complete {"topic_id": …} object before the cut is still kept."""
    data = extract_json(text)
    if data and isinstance(data.get("priorities"), list):
        return [p for p in data["priorities"] if isinstance(p, dict)]
    out = []
    for m in _PRIORITY_OBJ_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


async def janus_priorities(
    bundle: dict,
    *,
    rid: str,
    lang: str,
    run_id: str,
    budget: Budget | None = None,
    matches: dict[int, list[dict]] | None = None,
) -> dict[int, dict]:
    """topic_id → {"priority", "reason"}, asked in batches of _JANUS_BATCH topics. Topics Janus
    gives nothing usable for are treated as 'new' in first-seen order — a mechanical default,
    recorded as such (model "janus:default"), not a judgement."""
    payload = janus_payload(bundle, matches)
    all_topics = payload["topics"]
    out: dict[int, dict] = {}
    raws = []
    for start in range(0, len(all_topics), _JANUS_BATCH):
        if budget is not None and not budget.left():
            break
        part = dict(payload, topics=all_topics[start : start + _JANUS_BATCH])
        text = await _janus_call(
            t("slot_janus_system", lang),
            t("slot_janus_user", lang, data=json.dumps(part, ensure_ascii=False)),
            rid,
        )
        raws.append(text[:4000])
        wanted = {tp["topic_id"] for tp in part["topics"]}
        for p in parse_priorities(text):
            try:
                tid = int(p.get("topic_id"))
            except (TypeError, ValueError):
                continue
            prio = str(p.get("priority", "")).strip()
            if tid in wanted and prio in _PRIORITIES:
                entry = {"priority": prio, "reason": str(p.get("reason", ""))[:300]}
                if prio in ("same_as_resolved", "contradicts"):
                    # Only a resolved topic Janus was actually shown for this topic counts —
                    # anything else falls back to judging it as new (code never guesses).
                    try:
                        target = int(str(p.get("resolved_id", "")).lstrip("#"))
                    except (TypeError, ValueError):
                        target = None
                    shown = {m["resolved_id"] for m in (matches or {}).get(tid, [])}
                    if target not in shown:
                        entry = {"priority": "new", "reason": entry["reason"]}
                    else:
                        entry["resolved_id"] = target
                out[tid] = entry
    answered = len(out)
    for tp in all_topics:
        if tp["topic_id"] not in out:
            out[tp["topic_id"]] = {"priority": "new", "reason": "", "default": True}
    for tid, p in out.items():
        learning_store.add_verdict(
            run_id,
            "priority",
            f"topic:{tid}",
            p["priority"],
            [],
            model="janus" + (":default" if p.get("default") else ""),
        )
    learning_store.save_artifact(
        run_id,
        "janus_start",
        {
            "raw": raws,
            "answered": answered,
            "topics": len(all_topics),
            "fallback": answered == 0,
            "priorities": out,
        },
    )
    return out


# ── S1 / S2 / S3 ────────────────────────────────────────────────────────────


def _rows_text(topic: dict) -> str:
    lines = []
    for i in topic.get("items", []):
        ctx = f" → {i['context']}" if i.get("context") else ""
        lines.append(
            f"- inbox:{i['id']} | {str(i.get('ts', ''))[:16]} | {i.get('source', '')} | "
            f"{str(i.get('text', ''))[:_ROW_CHARS]}{ctx}"
        )
    return "\n".join(lines)


def _known_text(known: dict[int, str]) -> str:
    return "\n".join(f"- #{tid}: {label}" for tid, label in known.items()) or "-"


async def s1_clean(
    topic: dict,
    *,
    rid: str,
    lang: str,
    run_id: str,
    budget: Budget,
    known: dict[int, str] | None = None,
) -> dict | None:
    """One topic. `known` = topics already judged (id → label); Kåre may answer same_as: #id,
    and code then links this topic to that one (status 'merged') instead of developing it twice."""
    known = known or {}
    task = t(
        "slot_s1_task",
        lang,
        topic_id=topic["id"],
        count=topic.get("count", 0),
        first_seen=str(topic.get("first_seen", ""))[:10],
        last_seen=str(topic.get("last_seen", ""))[:10],
        rows=_rows_text(topic),
        known=_known_text(known),
    )
    # No thinking: classifying given rows is structured work; thinking cost 40–50 s per topic and
    # can eat the whole output budget (live run 2026-10-03). S3 keeps thinking.
    data, raw = await kare_json(
        task, rid=rid, lang=lang, tools=None, max_rounds=0, budget=budget, think=False
    )
    kind = str((data or {}).get("kind", "")).strip()
    if kind not in _KINDS:
        learning_store.save_artifact(run_id, f"S1:{topic['id']}:unparsed", {"raw": raw[:4000]})
        return None
    refs = [str(r) for r in (data.get("evidence_refs") or [])]
    check = verify_claims([{"text": data.get("summary", ""), "evidence_refs": refs}], set())[0]
    same_as = None
    try:
        cand = int(str(data.get("same_as") or "").lstrip("#"))
        same_as = cand if cand in known and cand != topic["id"] else None
    except (TypeError, ValueError):
        same_as = None
    verdict = f"same_as:{same_as}" if same_as else kind
    learning_store.add_verdict(
        run_id,
        "clean",
        f"topic:{topic['id']}",
        verdict,
        refs,
        verified=check["verified"],
        model="kare",
    )
    if same_as:
        learning_store.link_topic(topic["id"], same_as)
    else:
        learning_store.apply_topic_verdict(topic["id"], kind, label=str(data.get("label", "")))
    return {
        "topic_id": topic["id"],
        "kind": kind,
        "label": data.get("label", ""),
        "same_as": same_as,
        "summary": data.get("summary", ""),
        "verified": check["verified"],
    }


async def s2_review(
    trace: dict, *, rid: str, lang: str, run_id: str, budget: Budget, next_prompts: dict[str, str]
) -> dict | None:
    view = {**trace, "next_prompt": next_prompts.get(trace.get("next_rid", ""), "")}
    task = t("slot_s2_task", lang, trace=json.dumps(view, ensure_ascii=False), rid=trace["rid"])
    data, raw = await kare_json(
        task, rid=rid, lang=lang, tools=None, max_rounds=0, budget=budget, think=False
    )
    if not data or "ok" not in data:
        learning_store.save_artifact(run_id, f"S2:{trace['rid']}:unparsed", {"raw": raw[:4000]})
        return None
    ok = bool(data.get("ok"))
    refs = [str(r) for r in (data.get("evidence_refs") or [])] or [f"trace:{trace['rid']}"]
    check = verify_claims([{"text": data.get("did", ""), "evidence_refs": refs}], set())[0]
    learning_store.add_verdict(
        run_id,
        "trace",
        f"trace:{trace['rid']}",
        "ok" if ok else "problem",
        refs,
        verified=check["verified"],
        model="kare",
    )
    result = {
        "rid": trace["rid"],
        "ok": ok,
        "asked": data.get("asked", ""),
        "did": data.get("did", ""),
        "problem": data.get("problem", ""),
        "verified": check["verified"],
    }
    if not ok and str(data.get("problem", "")).strip():
        # A reviewed problem joins the agenda like any other write (source=review), so it is
        # grouped, counted and developed the same way — and stays if tonight runs out of time.
        added = learning_store.add_inbox_item(
            str(data["problem"]).strip(),
            source="review",
            rid=trace["rid"],
            pointer=f"trace:{trace['rid']}",
        )
        learning_store.apply_topic_verdict(added["topic_id"], "problem")
        result["topic_id"] = added["topic_id"]
    return result


async def s3_develop(
    topic_id: int,
    problem: str,
    *,
    rid: str,
    lang: str,
    run_id: str,
    budget: Budget,
    round_cap: int,
    tools: SlotTools,
) -> dict | None:
    context = learning_store.topic_history(topic_id, item_limit=5, verdict_limit=3) or {}
    task = t(
        "slot_s3_task",
        lang,
        problem=problem,
        context=json.dumps(context, ensure_ascii=False, default=str)[:4000],
        rounds=round_cap,
    )
    data, raw = await kare_json(
        task, rid=rid, lang=lang, tools=tools, max_rounds=round_cap, budget=budget
    )
    if data is None:
        learning_store.save_artifact(run_id, f"S3:{topic_id}:unparsed", {"raw": raw[:4000]})
        return None
    claims = [
        {
            "text": str(f.get("text", "")),
            "evidence_refs": [str(r) for r in (f.get("evidence_refs") or [])],
            "quote": f.get("quote"),
        }
        for f in (data.get("findings") or [])
        if isinstance(f, dict)
    ]
    checks = verify_claims(claims, {rid}, tool_texts=tools.results)
    stored = []
    first_verified: int | None = None
    for claim, check in zip(claims, checks, strict=False):
        # B4: the 9B evidence verifier on top of the code check — only where the code check passed.
        verdict = (
            await verifier.check_claim(claim["text"], check["evidence"], rid=rid, lang=lang)
            if check["verified"]
            else {"status": "skipped"}
        )
        ok = verifier.final_verified(check["verified"], verdict)
        reason = check["reason"]
        if check["verified"] and not ok:
            reason = (
                f"verifier_{verdict.get('supports', 'no')}"
                if verdict.get("quote_found")
                else "verifier_quote_not_in_evidence"
            )
        fid = learning_store.add_finding(
            run_id, claim["text"], claim["evidence_refs"], ok, topic_id=topic_id, verifier=verdict
        )
        if ok and first_verified is None:
            first_verified = fid
        stored.append(
            {
                "finding_id": fid,
                **{k: v for k, v in check.items() if k != "evidence"},
                "verified": ok,
                "reason": reason,
                "verifier": verdict,
            }
        )
    proposal = str(data.get("proposal", "") or "").strip()
    proposal_id = None
    if proposal and first_verified is not None:
        # Proposals only from verified findings (P115 D2 S4); a human confirms in chat.
        proposal_id = learning_store.add_proposal(first_verified, proposal)
    return {
        "topic_id": topic_id,
        "findings": stored,
        "proposal": proposal,
        "proposal_id": proposal_id,
    }


# ── S3 selection (P118 B8: themes, topics per meeting) ──────────────────────


def select_s3(
    candidates: list[int], info: dict[int, dict], limit: int | None
) -> tuple[list[int], dict[str, dict]]:
    """Which problems S3 takes tonight, in order. Round-robin over THEMES: first one topic from
    every theme that has something queued, then a second from each, … until `limit` (None = no
    count limit, the time budget alone stops S3). Within a theme: never developed first, then
    least recently developed, then oldest. Returns (picked ids, {theme: {"queued": n}})."""
    by_theme: dict[str, list[int]] = {}
    for tid in dict.fromkeys(candidates):  # keep first occurrence, drop repeats
        theme = (info.get(tid) or {}).get("theme") or FALLBACK_THEME
        by_theme.setdefault(theme if theme in THEMES else FALLBACK_THEME, []).append(tid)
    for ids in by_theme.values():
        ids.sort(
            key=lambda tid: (
                (info.get(tid) or {}).get("last_developed") is not None,
                (info.get(tid) or {}).get("last_developed") or "",
                (info.get(tid) or {}).get("first_seen") or "",
            )
        )
    queues = [list(by_theme[t]) for t in THEMES if by_theme.get(t)]
    picked: list[int] = []
    while queues and (limit is None or len(picked) < limit):
        for q in list(queues):
            if limit is not None and len(picked) >= limit:
                break
            picked.append(q.pop(0))
            if not q:
                queues.remove(q)
    counts = {t: {"queued": len(by_theme[t])} for t in THEMES if by_theme.get(t)}
    return picked, counts


# ── Run B3 over an S0 bundle ────────────────────────────────────────────────


async def run_b3(
    bundle: dict,
    *,
    run_id: str,
    rid: str,
    lang: str,
    budget: Budget,
    round_cap: int,
    on_step=None,
    topics_per_meeting: int | None = None,
) -> dict:
    step = on_step or (lambda code: None)
    step("janus")
    learning_store.set_run_step(run_id, "janus")
    matches = await resolved_matches(bundle.get("inbox", {}).get("topics", []))
    priorities = await janus_priorities(
        bundle, rid=rid, lang=lang, run_id=run_id, budget=budget, matches=matches
    )

    topics = {tp["id"]: tp for tp in bundle.get("inbox", {}).get("topics", [])}
    order = sorted(
        topics,
        key=lambda tid: (
            {"develop": 0, "new": 1, "contradicts": 1}.get(
                priorities.get(tid, {}).get("priority"), 9
            ),
            topics[tid].get("first_seen", ""),
        ),
    )

    summary: dict = {
        "s1": [],
        "s2": [],
        "s3": [],
        "skipped_for_time": [],
        "janus": {
            p: sum(1 for v in priorities.values() if v["priority"] == p) for p in _PRIORITIES
        },
    }

    # Janus's "drop": the topic is known/settled for now — status follows his verdict.
    # P133 C4: "same_as_resolved" → the topic becomes an echo of the resolved one (the solution
    # holds); "contradicts" → a reopen proposal a human decides on, and the topic is judged.
    summary["resolved_links"] = []
    summary["reopen_proposals"] = []
    for tid, p in priorities.items():
        if tid not in topics:
            continue
        if p["priority"] == "drop":
            learning_store.apply_topic_verdict(tid, "known")
        elif p["priority"] == "same_as_resolved":
            moved = learning_store.echo_topic(tid, p["resolved_id"])
            summary["resolved_links"].append(
                {"topic_id": tid, "resolved_id": p["resolved_id"], "rows": moved}
            )
        elif p["priority"] == "contradicts":
            pid = learning_store.add_reopen_proposal(p["resolved_id"], p.get("reason", ""))
            if pid:
                summary["reopen_proposals"].append(
                    {"topic_id": tid, "resolved_id": p["resolved_id"], "proposal_id": pid}
                )
    try:
        since = (datetime.now(get_local_tz()) - timedelta(days=_FOLLOWUP_DAYS)).astimezone(UTC)
        summary["proposal_followups"] = learning_store.proposal_followups(
            since.isoformat(timespec="seconds")
        )
    except Exception as e:
        log.warning("proposal follow-ups failed: %s", e)
        summary["proposal_followups"] = []

    step("S1")
    learning_store.set_run_step(run_id, "S1")
    problems: list[tuple[int, str]] = []
    known: dict[int, str] = {}  # judged tonight and not linked away: id → label
    for tid in order:
        if priorities.get(tid, {}).get("priority") not in ("new", "develop", "contradicts"):
            continue
        if not budget.left():
            summary["skipped_for_time"].append(f"topic:{tid}")
            continue
        res = await s1_clean(
            topics[tid], rid=rid, lang=lang, run_id=run_id, budget=budget, known=known
        )
        if res:
            summary["s1"].append(res)
            if res["same_as"]:
                continue
            known[tid] = str(res["label"] or "")[:80]
            if res["kind"] == "problem":
                problems.append((tid, res["summary"] or res["label"]))

    step("S2")
    learning_store.set_run_step(run_id, "S2")
    traces = bundle.get("traces", {}).get("items", [])
    next_prompts = {tr["rid"]: tr.get("prompt_preview", "") for tr in traces}
    for tr in traces:
        if not budget.left():
            summary["skipped_for_time"].append(f"trace:{tr['rid']}")
            continue
        res = await s2_review(
            tr, rid=rid, lang=lang, run_id=run_id, budget=budget, next_prompts=next_prompts
        )
        if res:
            summary["s2"].append(res)
            if res.get("topic_id"):
                problems.append((res["topic_id"], res["problem"]))

    step("S3")
    learning_store.set_run_step(run_id, "S3")
    # Queue = problems from earlier nights S3 has not developed + tonight's. B8: one topic per
    # theme first, then fill up to topics_per_meeting; the time budget stays the ceiling.
    tonight = {tid for tid, _ in problems}
    carried = [
        (p["id"], p["label"]) for p in learning_store.pending_problems() if p["id"] not in tonight
    ]
    summary["carried_problems"] = [tid for tid, _ in carried]
    texts = dict(carried)
    texts.update(dict(problems))  # tonight's summary wins over an older label
    queue = list(dict.fromkeys(tid for tid, _ in carried + problems))
    info = learning_store.topic_info(queue)
    picked, theme_counts = select_s3(queue, info, topics_per_meeting)
    summary["themes"] = theme_counts
    summary["not_selected"] = [f"problem:{tid}" for tid in queue if tid not in set(picked)]
    tools = SlotTools(rid, lang)
    for tid in picked:
        problem = texts.get(tid, "")
        theme = (info.get(tid) or {}).get("theme") or FALLBACK_THEME
        if not budget.left():
            summary["skipped_for_time"].append(f"problem:{tid}")
            continue
        learning_store.mark_developed(tid)
        theme_counts.setdefault(theme, {"queued": 0})
        theme_counts[theme]["developed"] = theme_counts[theme].get("developed", 0) + 1
        res = await s3_develop(
            tid,
            problem,
            rid=rid,
            lang=lang,
            run_id=run_id,
            budget=budget,
            round_cap=round_cap,
            tools=tools,
        )
        if res:
            summary["s3"].append(res)

    learning_store.save_artifact(run_id, "B3", summary)
    learning_store.set_run_step(run_id, "B3-done")
    return summary


# ── B4: Janus end call + S5 morning report ──────────────────────────────────

REPORT_DIR = Path("/kaare/state/memory/dev_meetings")
LATEST_REPORT = Path("/kaare/state/memory/dev_meeting_latest.md")


async def janus_end(
    findings: list[dict],
    *,
    rid: str,
    lang: str,
    run_id: str,
    followups: list[dict] | None = None,
) -> dict[int, str]:
    """Janus weighs tonight's VERIFIED findings against each topic's history, and judges for
    confirmed proposals whether their symptom stopped (P133 C4 — code counted the rows).
    topic_id → note."""
    verified = [f for f in findings if f.get("verified") and f.get("topic_id")]
    followups = [f for f in (followups or []) if f.get("topic_id")]
    if not verified and not followups:
        return {}
    topics = sorted({f["topic_id"] for f in verified} | {f["topic_id"] for f in followups})
    data = {
        "findings": [
            {"topic_id": f["topic_id"], "topic": f.get("topic_label", ""), "text": f["text"]}
            for f in verified
        ],
        "history": {
            tid: learning_store.topic_history(tid, item_limit=3, verdict_limit=5) for tid in topics
        },
        "confirmed_proposal_followups": [
            {
                "proposal_id": f["id"],
                "topic_id": f["topic_id"],
                "proposal": f.get("text", ""),
                "decided_at": str(f.get("decided_at") or "")[:10],
                "new_rows_since": f.get("new_rows_since", 0),
            }
            for f in followups
        ],
    }
    text = await _janus_call(
        t("slot_janus_system", lang),
        t(
            "slot_janus_end_user",
            lang,
            data=json.dumps(data, ensure_ascii=False, default=str)[:20000],
        ),
        rid,
    )
    notes: dict[int, str] = {}
    for n in (extract_json(text) or {}).get("notes") or []:
        try:
            tid = int(n.get("topic_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if tid in topics and str(n.get("note", "")).strip():
            notes[tid] = str(n["note"]).strip()[:300]
    learning_store.save_artifact(run_id, "janus_end", {"raw": text[:4000], "notes": notes})
    return notes


async def s5_summary(payload: dict, *, rid: str, lang: str, budget: Budget) -> str:
    """Kåre's first-person morning report, fresh context (only the payload), no tools, no thinking."""
    if not budget.left():
        return ""
    res = await ask_llm_with_tools(
        messages=[
            {
                "role": "user",
                "content": t("slot_s5_task", lang, data=json.dumps(payload, ensure_ascii=False)),
            }
        ],
        tools=[],
        rid=rid,
        user_id="",
        source="meet",
        disable_thinking=True,
    )
    text, _think, _closed = strip_think_block(res.get("text") or "")
    return strip_language_tag((text or "").strip()) if res.get("ok") else ""


def _ref_list(refs) -> str:
    try:
        items = json.loads(refs) if isinstance(refs, str) else list(refs or [])
    except Exception:
        items = []
    return ", ".join(f"`{r}`" for r in items)


def build_report(
    *,
    date: str,
    lang: str,
    summary_text: str,
    findings: list[dict],
    notes: dict[int, str],
    proposals: list[dict],
    b3: dict,
) -> str:
    """Markdown report. Only the summary paragraph is LLM text; every list below is built by code
    from stored rows, so nothing unverified can be presented as verified."""
    none = t("slot_report_none", lang)
    out = [
        t("slot_report_title", lang, date=date),
        "",
        t("slot_report_summary_h", lang),
        summary_text or t("slot_report_summary_failed", lang),
        "",
    ]

    verified = [f for f in findings if f.get("verified")]
    out.append(t("slot_report_verified_h", lang))
    if verified:
        for f in verified:
            v = f.get("verifier")
            v = json.loads(v) if isinstance(v, str) and v else (v or {})
            off = (
                f" {t('slot_report_verifier_off', lang)}"
                if v.get("status") not in ("ok", None)
                else ""
            )
            label = f"**#{f['topic_id']} {f.get('topic_label') or ''}**".strip()
            theme = (
                f" _({_theme_name(f.get('topic_theme'), lang)})_" if f.get("topic_theme") else ""
            )
            out.append(f"- {label}{theme}: {f['text']} — {_ref_list(f.get('evidence_refs'))}{off}")
    else:
        out.append(none)
    out.append("")

    if notes:
        out.append(t("slot_report_janus_h", lang))
        for tid, note in notes.items():
            out.append(f"- #{tid}: {note}")
        out.append("")

    suspicions = [f for f in findings if not f.get("verified")]
    out.append(t("slot_report_suspicions_h", lang))
    if suspicions:
        out.extend(
            f"- #{f.get('topic_id')}: {f['text']} ({t('slot_report_reason', lang, reason=f.get('reason', ''))})"
            for f in suspicions
        )
    else:
        out.append(none)
    out.append("")

    out.append(t("slot_report_proposals_h", lang))
    if proposals:
        out.extend(f"- **#{p['id']}** {p['text']}" for p in proposals)
        out.append("")
        out.append(t("slot_report_proposal_hint", lang, id=proposals[0]["id"]))
    else:
        out.append(none)
    out.append("")

    # P133 C4: what Janus did against resolved topics, echoes, and confirmed-proposal follow-up.
    links = b3.get("resolved_links") or []
    reopens = b3.get("reopen_proposals") or []
    echoes = b3.get("echoes_24h") or {}
    followups = b3.get("proposal_followups") or []
    if links or reopens or echoes or followups:
        out.append(t("slot_report_resolved_h", lang))
        out.extend(
            t("slot_report_resolved_link", lang, topic=x["topic_id"], resolved=x["resolved_id"])
            for x in links
        )
        out.extend(
            t(
                "slot_report_resolved_reopen",
                lang,
                topic=rp["topic_id"],
                resolved=rp["resolved_id"],
                id=rp["proposal_id"],
            )
            for rp in reopens
        )
        out.extend(
            t("slot_report_resolved_echoes", lang, resolved=rid_, n=n)
            for rid_, n in sorted(echoes.items())
        )
        out.extend(
            t(
                "slot_report_followup",
                lang,
                id=f["id"],
                topic=f.get("topic_id") or "-",
                date=str(f.get("decided_at") or "")[:10],
                n=f.get("new_rows_since", 0),
            )
            for f in followups
        )
        out.append("")

    s6 = b3.get("s6") or {}
    if s6.get("written") or s6.get("retired"):
        out.append(t("slot_report_consolidated_h", lang))
        names = [
            f"{t('slot_level_' + w.split(':', 1)[0], lang)} {w.split(':', 1)[1]}"
            for w in s6.get("written", [])
            if ":" in w
        ]
        out.append(
            t(
                "slot_report_consolidated_line",
                lang,
                written=", ".join(names) or "-",
                retired=s6.get("retired", 0),
            )
        )
        out.append("")

    s1 = b3.get("s1", [])
    out.append(t("slot_report_agenda_h", lang))
    out.append(
        t(
            "slot_report_agenda_line",
            lang,
            judged=len(s1),
            linked=sum(1 for r in s1 if r.get("same_as")),
            problems=sum(1 for r in s1 if r.get("kind") == "problem" and not r.get("same_as")),
            events=sum(1 for r in s1 if r.get("kind") == "event" and not r.get("same_as")),
            other=sum(
                1 for r in s1 if r.get("kind") in ("known", "not_real") and not r.get("same_as")
            ),
            traces=len(b3.get("s2", [])),
            skipped=_not_reached(b3),
        )
    )
    themes = b3.get("themes") or {}
    if themes:
        parts = [
            f"{_theme_name(th, lang)} {c.get('developed', 0)}/{c.get('queued', 0)}"
            for th, c in sorted(themes.items(), key=lambda kv: THEMES.index(kv[0]))
            if th in THEMES
        ]
        out.append("")
        out.append(t("slot_report_themes_line", lang, themes=" · ".join(parts)))
    return "\n".join(out) + "\n"


def _theme_name(theme: str | None, lang: str) -> str:
    return t(f"agenda_theme_{theme if theme in THEMES else FALLBACK_THEME}", lang)


def _not_reached(b3: dict) -> int:
    """Problems and topics left for a later night: out of time, or not picked under the
    topics-per-meeting limit (B8)."""
    return len(b3.get("skipped_for_time", [])) + len(b3.get("not_selected", []))


def _free_report_path(target_dir: Path, date: str) -> Path:
    """`YYYY-MM-DD.md`, or `YYYY-MM-DD-2.md`, `-3` … when a report for that date already exists
    (e.g. the old meeting's file, or a manual run the same day) — a report is never overwritten."""
    path = target_dir / f"{date}.md"
    n = 2
    while path.exists():
        path = target_dir / f"{date}-{n}.md"
        n += 1
    return path


async def run_b4(
    b3: dict,
    *,
    run_id: str,
    rid: str,
    lang: str,
    budget: Budget,
    report_dir: Path | None = None,
    date: str | None = None,
    on_step=None,
) -> Path:
    """Janus end call → S5 summary → report file. Returns the report path."""
    (on_step or (lambda code: None))("S5")
    learning_store.set_run_step(run_id, "S5")
    findings = learning_store.run_findings(run_id)
    for f in findings:
        f["reason"] = next(
            (
                x.get("reason")
                for r in b3.get("s3", [])
                for x in r.get("findings", [])
                if x.get("finding_id") == f["id"]
            ),
            "",
        )
    try:
        since = (datetime.now(UTC) - timedelta(hours=24)).isoformat(timespec="seconds")
        b3["echoes_24h"] = learning_store.echo_counts(since)
    except Exception as e:
        log.warning("echo counts failed: %s", e)
    notes = (
        await janus_end(
            findings,
            rid=rid,
            lang=lang,
            run_id=run_id,
            followups=b3.get("proposal_followups") or [],
        )
        if budget.left()
        else {}
    )
    proposal_ids = {r.get("proposal_id") for r in b3.get("s3", []) if r.get("proposal_id")}
    proposals = [p for p in learning_store.list_proposals("pending") if p["id"] in proposal_ids]
    s1 = b3.get("s1", [])
    payload = {
        "verified_findings": [
            {"topic": f.get("topic_label", ""), "text": f["text"]}
            for f in findings
            if f.get("verified")
        ],
        "janus_notes": list(notes.values()),
        "counts": {
            "topics_judged": len(s1),
            "linked": sum(1 for r in s1 if r.get("same_as")),
            "problems_developed": len(b3.get("s3", [])),
            "unverified": sum(1 for f in findings if not f.get("verified")),
            "proposals_waiting": len(proposals),
            "not_reached": _not_reached(b3),
        },
        "themes": {
            _theme_name(th, lang): c for th, c in (b3.get("themes") or {}).items() if th in THEMES
        },
    }
    summary_text = await s5_summary(payload, rid=rid, lang=lang, budget=budget)
    date = date or datetime.now(get_local_tz()).strftime("%Y-%m-%d")
    report = build_report(
        date=date,
        lang=lang,
        summary_text=summary_text,
        findings=findings,
        notes=notes,
        proposals=proposals,
        b3=b3,
    )
    target_dir = report_dir or REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = _free_report_path(target_dir, date)
    path.write_text(report, encoding="utf-8")
    if report_dir is None:
        # read_meeting(type=development) without a date reads this file (written by the old
        # meeting until B5) — keep it pointing at the newest report.
        try:
            LATEST_REPORT.write_text(report, encoding="utf-8")
        except Exception as e:
            log.warning("could not update %s: %s", LATEST_REPORT, e)
    learning_store.save_artifact(run_id, "S5", {"path": str(path), "summary": summary_text})
    learning_store.set_run_step(run_id, "S5-done")
    return path
