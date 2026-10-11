"""
Weekly knowledge_store reflection job (P105 Fase 3 Step 6).

Re-validates every active row in state_facts: retires facts that no longer
hold, adjusts importance where warranted, and stamps last_validated on
everything reviewed. Runs on the main 27B model (`default` role) with Kåre's
full personality + LTM loaded (Decision 7, "Kåre sine filer = Kåre sin jobb") —
not the shared 9B pool, because judging "does this still matter to this
household" benefits from Kåre's actual accumulated context, not a shallow
model's generic pass.

Frequency (hybrid, per P105 Decision): runs when a week has passed since the
last pass, OR when an unprocessed important-tagged row exists (an entry Kåre
flagged as important during the day forces the next reflection-meeting cycle
instead of waiting out the week).

Called from kaare_night_sequence.py as its own step, alongside the existing
portrait-synthesis/reflection-meeting jobs — not a standalone schedule.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adapters import llm_adapter
from adapters.llm_adapter import _build_system, call_llm_chat
from kaare_core.config import get_settings
from kaare_core.memory import knowledge_dedup
from kaare_core.memory import knowledge_store as ks

logger = logging.getLogger(__name__)

_LAST_RUN_PATH = Path("/kaare/state/knowledge_reflection_last_run.json")
_INTERVAL_DAYS = 7
_DOMAINS = ("world", "personality", "notes")

_REVALIDATE_SYSTEM_SUFFIX = """

---

You are now reviewing your own accumulated knowledge store — the structured facts
that replaced your old world.md/personality_self.md/notat files. You will receive a
JSON list of currently active facts (id, domain, section, content, importance,
valid_from, last_validated). Your job: decide which of these still hold, which are
now stale/superseded/no longer relevant, and whether any importance score should
change given what you actually know about this household.

Rules:
1. Only report facts that need a CHANGE. Anything not mentioned is treated as
   confirmed-as-is (still holds, importance unchanged) — don't list unchanged facts.
2. "retire" = this fact is no longer true, no longer relevant, or has been fully
   superseded by something more recent you know. Use this when something is
   genuinely outdated, not just because it's old — a durable fact (hardware specs,
   architecture) doesn't go stale just from age.
3. "reimportance" = the importance score (0.1-0.9) should change given what you now
   know matters to this household — e.g. something that turned out to be a recurring
   real concern deserves a higher score; a one-off situational note that never
   resurfaced deserves a lower one.
4. Never invent or rewrite the content of a fact — only retire it or rescore it.
5. Keep "reason" to 8 words or fewer — it is a log note, not an explanation.
6. Output ONLY a JSON array, no markdown fences, no prose. Each element one of:
   {"id": 42, "action": "retire", "reason": "..."}
   {"id": 17, "action": "reimportance", "new_importance": 0.7, "reason": "..."}
   If nothing needs a change, output an empty array: []
"""

_MAX_FACTS_PER_CALL_PREDICT = 6000


def _repair_truncated_json_array(raw: str) -> list:
    """Best-effort recovery when the model's output got cut off mid-object
    (hit num_predict before finishing) — salvage every complete {...} object
    up to the truncation point instead of discarding the whole response.
    Raises json.JSONDecodeError if nothing usable can be recovered."""
    depth = 0
    last_complete_end = None
    in_string = False
    escape = False
    for i, ch in enumerate(raw):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_complete_end = i + 1
    if last_complete_end is None:
        raise json.JSONDecodeError("no complete object found", raw, 0)
    salvaged = raw[:last_complete_end].rstrip().rstrip(",")
    return json.loads(f"[{salvaged}]" if not salvaged.lstrip().startswith("[") else salvaged + "]")


def _get_last_run() -> datetime | None:
    if not _LAST_RUN_PATH.exists():
        return None
    try:
        data = json.loads(_LAST_RUN_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["last_run"])
    except Exception:
        return None


def _set_last_run(dt: datetime) -> None:
    try:
        _LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_RUN_PATH.write_text(json.dumps({"last_run": dt.isoformat()}), encoding="utf-8")
    except Exception as e:
        logger.warning("[knowledge_reflection] could not persist last_run: %s", e)


def should_run() -> bool:
    """Hybrid frequency check: a week has passed since the last pass, OR an
    unprocessed important-tagged row exists (forces an early run)."""
    if ks.has_unprocessed_important_tag():
        return True
    last_run = _get_last_run()
    if last_run is None:
        return True
    return datetime.now(UTC) - last_run >= timedelta(days=_INTERVAL_DAYS)


def _collect_active_facts() -> list[dict]:
    return [
        {
            "id": r["id"],
            "domain": domain,
            "section": r["section"],
            "content": r["content"],
            "importance": r["importance"],
            "valid_from": r["valid_from"],
            "last_validated": r["last_validated"],
        }
        for domain in _DOMAINS
        for r in ks.get_all_facts(domain)
    ]


def _parse_llm_response(raw: str) -> list[dict]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Likely hit num_predict mid-object — salvage whatever complete
        # objects exist rather than discarding the whole pass.
        recovered = _repair_truncated_json_array(raw)
        logger.warning(
            "[knowledge_reflection] LLM output was truncated — "
            "recovered %d complete change(s) from a cut-off response",
            len(recovered),
        )
        return recovered


_DEGENERATE_REPEAT_THRESHOLD = 5  # same exact reason string this many times = suspect
_DEGENERATE_REPEAT_FRACTION = 0.4  # or this fraction of a large batch sharing one reason


def _looks_degenerate(changes: list[dict]) -> str | None:
    """Detect the model falling into a repetitive-output failure mode — copying
    the same boilerplate reason across many structurally-similar facts instead
    of judging each on its own merits (observed live 2026-09-07: a 116-fact
    'notes' batch got the identical reason 'Recurring instability needs
    prioritized fix' applied to every single entry, including ones with
    nothing to do with instability — real production data got corrupted with
    fake uniform importance scores before this guard existed).

    Returns a reason string if the batch looks degenerate, else None. This is
    a hard backstop, not a substitute for good prompting — it exists because
    a weekly *unsupervised* job cannot tolerate "usually good, occasionally
    garbage" behavior on real household data.
    """
    if len(changes) < _DEGENERATE_REPEAT_THRESHOLD:
        return None
    reasons = [c.get("reason", "") for c in changes if c.get("reason")]
    if not reasons:
        return None

    counts = Counter(reasons)
    most_common_reason, most_common_count = counts.most_common(1)[0]
    if (
        most_common_count >= _DEGENERATE_REPEAT_THRESHOLD
        and most_common_count / len(changes) >= _DEGENERATE_REPEAT_FRACTION
    ):
        return (
            f"{most_common_count}/{len(changes)} changes share the identical "
            f"reason '{most_common_reason}' — looks like repetitive/degenerate "
            f"output, not per-fact judgment"
        )
    return None


def _apply_changes(
    changes: list[dict], valid_ids: set[int], id_to_cluster_ids: dict[int, list[int]] | None = None
) -> dict:
    """Apply retire/reimportance decisions. When `id_to_cluster_ids` is given,
    a decision on a cluster representative's id fans out to every member of
    that near-duplicate cluster (see knowledge_dedup.py) — one LLM judgment,
    applied to every fact it actually represents."""
    result = {"retired": 0, "reimportanced": 0}
    for change in changes:
        fid = change.get("id")
        if fid not in valid_ids:
            logger.warning("[knowledge_reflection] LLM referenced unknown id %s — skipped", fid)
            continue
        action = change.get("action")
        target_ids = (id_to_cluster_ids or {}).get(fid, [fid])
        if action == "retire":
            n = sum(1 for tid in target_ids if ks.retire_fact(tid))
            result["retired"] += n
            if n:
                suffix = f" (fans out to {n} near-duplicate facts)" if n > 1 else ""
                logger.info(
                    "[knowledge_reflection] retired fact %s%s: %s",
                    fid,
                    suffix,
                    change.get("reason", ""),
                )
        elif action == "reimportance":
            new_imp = change.get("new_importance")
            if isinstance(new_imp, (int, float)) and 0.0 <= new_imp <= 1.0:
                n = sum(1 for tid in target_ids if ks.update_fact(tid, importance=float(new_imp)))
                result["reimportanced"] += n
                if n:
                    suffix = f" (fans out to {n} near-duplicate facts)" if n > 1 else ""
                    logger.info(
                        "[knowledge_reflection] reimportanced fact %s%s -> %.2f: %s",
                        fid,
                        suffix,
                        new_imp,
                        change.get("reason", ""),
                    )
        else:
            logger.warning(
                "[knowledge_reflection] unknown action '%s' for id %s — skipped", action, fid
            )
    return result


_CHUNK_SIZE = 30  # facts per LLM call within a domain — see _revalidate_chunk docstring


async def _revalidate_chunk(
    domain: str,
    chunk_label: str,
    facts: list[dict],
    system_text: str,
    id_to_cluster_ids: dict[int, list[int]] | None = None,
) -> dict:
    """One LLM call over a bounded slice of facts. Chunked (rather than one
    call per whole domain) because live testing 2026-09-07 showed a single
    call over 116-117 structurally-similar facts reliably collapsed into
    repetitive output (the model copies the same boilerplate reason across
    every entry instead of judging each one) — caught both times by
    _looks_degenerate() before anything was applied, but that just means the
    whole domain silently gets skipped every week, not actually reviewed.
    Smaller, more varied chunks are far less likely to trigger the collapse.

    `facts` here are cluster representatives, not raw rows — see
    _revalidate_domain(). `id_to_cluster_ids` fans a representative's
    decision out to every fact it stands in for."""

    result = {"retired": 0, "reimportanced": 0, "errors": 0}
    if not facts:
        return result

    task_prompt = (
        f"Here are {len(facts)} currently active '{domain}' knowledge_store facts to review "
        f'({chunk_label}). Each may include a "similar_count" field — that many '
        f"near-identical facts (already deduplicated) will get the exact same "
        f"decision you make for this one:\n\n" + json.dumps(facts, ensure_ascii=False, indent=1)
    )

    llm_result = await call_llm_chat(
        "default",
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": task_prompt},
        ],
        options={"num_predict": _MAX_FACTS_PER_CALL_PREDICT, "temperature": 0.3},
        disable_thinking=True,
        timeout=300,
        source="knowledge_reflection",
    )

    if not llm_result.get("ok", True) and not llm_result.get("text"):
        logger.error(
            "[knowledge_reflection] domain '%s' %s — LLM call failed: %s",
            domain,
            chunk_label,
            llm_result.get("error"),
        )
        result["errors"] += 1
        return result

    try:
        changes = _parse_llm_response(llm_result.get("text") or "[]")
    except json.JSONDecodeError as e:
        logger.error(
            "[knowledge_reflection] domain '%s' %s — could not parse LLM output as JSON: %s",
            domain,
            chunk_label,
            e,
        )
        result["errors"] += 1
        return result

    degenerate_reason = _looks_degenerate(changes)
    if degenerate_reason:
        logger.error(
            "[knowledge_reflection] domain '%s' %s — rejected whole chunk, looks degenerate: %s",
            domain,
            chunk_label,
            degenerate_reason,
        )
        result["errors"] += 1
        return result

    valid_ids = {f["id"] for f in facts}
    applied = _apply_changes(changes, valid_ids, id_to_cluster_ids)
    result["retired"] += applied["retired"]
    result["reimportanced"] += applied["reimportanced"]
    return result


async def _revalidate_domain(domain: str, facts: list[dict], system_text: str) -> dict:
    """Reviews one domain's facts: first clusters near-duplicates
    (knowledge_dedup.py) so the LLM only ever judges one representative per
    cluster — both cheaper and, per the 2026-09-07 incident, structurally
    safer than letting a large batch of near-identical facts reach the model
    at all. Representatives are then reviewed in bounded chunks of
    _CHUNK_SIZE (see _revalidate_chunk), each independently guarded against
    degenerate output."""
    result = {"retired": 0, "reimportanced": 0, "errors": 0}
    if not facts:
        return result

    clusters = await knowledge_dedup.cluster_facts(facts)
    multi = sum(1 for c in clusters if len(c) > 1)
    logger.info(
        "[knowledge_reflection] domain '%s': %d facts -> %d clusters (%d near-duplicate groups)",
        domain,
        len(facts),
        len(clusters),
        multi,
    )

    representatives = []
    id_to_cluster_ids: dict[int, list[int]] = {}
    for cluster in clusters:
        rep = dict(cluster[0])  # copy — don't mutate the shared fact dict
        member_ids = [f["id"] for f in cluster]
        if len(cluster) > 1:
            rep["similar_count"] = len(cluster)
        representatives.append(rep)
        id_to_cluster_ids[rep["id"]] = member_ids

    chunks = [
        representatives[i : i + _CHUNK_SIZE] for i in range(0, len(representatives), _CHUNK_SIZE)
    ]
    for idx, chunk in enumerate(chunks, start=1):
        chunk_label = f"chunk {idx}/{len(chunks)}"
        chunk_result = await _revalidate_chunk(
            domain, chunk_label, chunk, system_text, id_to_cluster_ids
        )
        result["retired"] += chunk_result["retired"]
        result["reimportanced"] += chunk_result["reimportanced"]
        result["errors"] += chunk_result["errors"]
    return result


def _acting_user() -> str:
    """The household user whose context (profile, observations, knowledge) the review runs
    with — P105 Decision 7: Kåre's full context, not an isolated prompt. Read from
    settings.yaml `knowledge_reflection.acting_user`; blank → household-level context only.
    Was hardcoded to one install's user name until 2026-09-28 (BUG-log, CLAUDE.md rule 13)."""
    user = str((get_settings().get("knowledge_reflection") or {}).get("acting_user") or "").strip()
    if not user:
        logger.info(
            "[knowledge_reflection] no acting_user configured — running without user context"
        )
    return user


async def run() -> dict:
    """Run one reflection pass, one LLM call per domain. Returns a summary
    dict for logging."""

    all_facts = _collect_active_facts()
    summary = {"reviewed": len(all_facts), "retired": 0, "reimportanced": 0, "errors": 0}

    if not all_facts:
        logger.info("[knowledge_reflection] no active facts — nothing to review")
        ks.clear_all_important_tags()
        _set_last_run(datetime.now(UTC))
        return summary

    cfg = llm_adapter.CFG["default"]  # read per call: reload_config() rebinds CFG
    acting_user = _acting_user()
    system_text = (
        _build_system(cfg.get("system", ""), user_id=acting_user) + _REVALIDATE_SYSTEM_SUFFIX
    )

    for domain in _DOMAINS:
        domain_facts = [f for f in all_facts if f["domain"] == domain]
        domain_result = await _revalidate_domain(domain, domain_facts, system_text)
        summary["retired"] += domain_result["retired"]
        summary["reimportanced"] += domain_result["reimportanced"]
        summary["errors"] += domain_result["errors"]

    ks.touch_all_validated()
    ks.clear_all_important_tags()
    _set_last_run(datetime.now(UTC))

    logger.info("[knowledge_reflection] pass complete: %s", summary)
    return summary
