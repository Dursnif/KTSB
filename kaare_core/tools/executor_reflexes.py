"""
Reflex executor — manages fastpath learning.

Actions:
  suggest  → analyze LTM + RID traces, save candidates to state/reflex_proposals.json
  confirm  → write an approved proposal to configs/fastpath_rules.yaml (admin only)
  reject   → mark a proposal as rejected (admin only)
  list     → show pending proposals

Exported: REFLEX_TOOLS, dispatch()
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import yaml

from kaare_core.memory.long_term import DB_PATH as _LTM_DB_PATH
from kaare_core.tools.i18n import t, get_lang
from kaare_core.tools.trace_reader import get_recent_traces

_RULES_PATH = Path("/kaare/configs/fastpath_rules.yaml")
_PROPOSALS_PATH = Path("/kaare/state/reflex_proposals.json")
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

REFLEX_TOOLS = {"skriv_reflex"}


def _get_threshold() -> int:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return int(cfg.get("fastpath_reflex_threshold", 10))
    except Exception:
        return 10


def _load_proposals() -> list:
    if not _PROPOSALS_PATH.exists():
        return []
    try:
        return json.loads(_PROPOSALS_PATH.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _save_proposals(proposals: list) -> None:
    _PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROPOSALS_PATH.write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _normalize_phrase(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _load_rules() -> dict:
    if not _RULES_PATH.exists():
        return {"settings": {"cache_ttl_seconds": 30}, "reflexes": []}
    try:
        return yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"settings": {}, "reflexes": []}


def _save_rules(data: dict) -> None:
    _RULES_PATH.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _suggest(lang: str, user_id: str) -> str:
    threshold = _get_threshold()

    # Find repeated HA commands in LTM with consistent outcomes
    ltm_candidates = []
    try:
        conn = sqlite3.connect(str(_LTM_DB_PATH))
        cur = conn.execute(
            """
            SELECT LOWER(TRIM(prompt)) as norm_prompt,
                   action,
                   entity_id,
                   COUNT(*) as freq
            FROM interactions
            WHERE action   != ''
              AND entity_id != ''
              AND outcome NOT IN ('failed', 'error', 'unknown', 'pending')
            GROUP BY LOWER(TRIM(prompt)), action, entity_id
            HAVING freq >= ?
            ORDER BY freq DESC
            LIMIT 20
            """,
            (threshold,),
        )
        ltm_candidates = [
            {"prompt": row[0], "action": row[1], "entity_id": row[2], "freq": row[3]}
            for row in cur.fetchall()
        ]
        conn.close()
    except Exception as exc:
        return t("reflex_ltm_error", lang, error=str(exc))

    if not ltm_candidates:
        return t("reflex_suggest_none", lang, threshold=threshold)

    # Cross-reference: count simple RID traces (no think, no tools, 1 LLM call)
    # We use this as a proxy for "trivial" commands — the model didn't need to reason
    simple_trace_count = 0
    try:
        for trace in get_recent_traces(200, source="user"):
            if (
                trace.get("llm_call_count", 0) == 1
                and trace.get("tool_count", 0) == 0
                and not trace.get("has_think", False)
            ):
                simple_trace_count += 1
    except Exception:
        pass

    # Exclude phrases already in rules or pending proposals
    existing = _load_rules()
    existing_phrases = {
        _normalize_phrase(r.get("phrase", ""))
        for r in existing.get("reflexes", [])
    }
    existing_proposals = _load_proposals()
    pending_phrases = {
        _normalize_phrase(p.get("phrase", ""))
        for p in existing_proposals
        if p.get("status") not in ("confirmed", "rejected")
    }

    new_proposals = []
    ts_base = int(datetime.now(timezone.utc).timestamp())
    for i, candidate in enumerate(ltm_candidates):
        norm = _normalize_phrase(candidate["prompt"])
        if norm in existing_phrases or norm in pending_phrases:
            continue
        proposal_id = f"prop_{ts_base}_{i:02d}"
        new_proposals.append(
            {
                "id": proposal_id,
                "phrase": candidate["prompt"],
                "provider": "ha",
                "action": candidate["action"],
                "entity_id": candidate["entity_id"],
                "freq": candidate["freq"],
                "simple_traces_total": simple_trace_count,
                "status": "pending",
                "suggested_at": datetime.now(timezone.utc).isoformat(),
                "suggested_by": user_id,
            }
        )

    if not new_proposals:
        return t("reflex_suggest_no_new", lang)

    _save_proposals(existing_proposals + new_proposals)

    lines = [t("reflex_suggest_header", lang, count=len(new_proposals), threshold=threshold)]
    for p in new_proposals:
        lines.append(
            f"  [{p['id']}] \"{p['phrase']}\" → {p['action']} {p['entity_id']} (×{p['freq']})"
        )
    lines.append(t("reflex_suggest_confirm_hint", lang))
    return "\n".join(lines)


def _confirm(proposal_id: str, lang: str) -> str:
    proposals = _load_proposals()
    proposal = next((p for p in proposals if p.get("id") == proposal_id), None)
    if not proposal:
        return t("reflex_proposal_not_found", lang, pid=proposal_id)
    if proposal.get("status") != "pending":
        return t("reflex_proposal_not_pending", lang, pid=proposal_id, status=proposal["status"])

    rules = _load_rules()
    reflexes = rules.get("reflexes") or []

    existing_ids = {r.get("id", "") for r in reflexes}
    idx = len(reflexes) + 1
    while f"rf_{idx:03d}" in existing_ids:
        idx += 1
    reflex_id = f"rf_{idx:03d}"

    new_reflex: Dict = {
        "id": reflex_id,
        "phrase": proposal["phrase"],
        "match": "contains",
        "provider": proposal.get("provider", "ha"),
        "active": True,
        "learned": True,
        "confirmed_by": "admin",
    }
    if proposal.get("provider", "ha") == "ha":
        new_reflex["action"] = proposal.get("action", "")
        new_reflex["entity_id"] = proposal.get("entity_id", "")
    elif proposal.get("provider") == "mqtt":
        new_reflex["topic"] = proposal.get("topic", "")
        new_reflex["payload"] = proposal.get("payload", "{}")

    reflexes.append(new_reflex)
    rules["reflexes"] = reflexes
    _save_rules(rules)

    try:
        import kaare_fastpath as _fp
        _fp.invalidate_cache()
    except Exception:
        pass

    for p in proposals:
        if p.get("id") == proposal_id:
            p["status"] = "confirmed"
            p["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            p["reflex_id"] = reflex_id
    _save_proposals(proposals)

    return t("reflex_confirmed", lang, reflex_id=reflex_id, phrase=proposal["phrase"])


def _reject(proposal_id: str, lang: str) -> str:
    proposals = _load_proposals()
    proposal = next((p for p in proposals if p.get("id") == proposal_id), None)
    if not proposal:
        return t("reflex_proposal_not_found", lang, pid=proposal_id)

    for p in proposals:
        if p.get("id") == proposal_id:
            p["status"] = "rejected"
            p["rejected_at"] = datetime.now(timezone.utc).isoformat()
    _save_proposals(proposals)

    return t("reflex_rejected", lang, phrase=proposal.get("phrase", proposal_id))


def _list_proposals(lang: str) -> str:
    proposals = _load_proposals()
    pending = [p for p in proposals if p.get("status") == "pending"]
    if not pending:
        return t("reflex_list_empty", lang)

    lines = [t("reflex_list_header", lang, count=len(pending))]
    for p in pending:
        lines.append(
            f"  [{p['id']}] \"{p.get('phrase', '')}\" → "
            f"{p.get('action', '')} {p.get('entity_id', '')} (×{p.get('freq', '?')})"
        )
    return "\n".join(lines)


async def dispatch(name: str, arguments: Dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "skriv_reflex":
        action = arguments.get("action", "")
        if action == "suggest":
            return _suggest(lang, arguments.get("_user_id", "global"))
        if action == "confirm":
            return _confirm(arguments.get("proposal_id", ""), lang)
        if action == "reject":
            return _reject(arguments.get("proposal_id", ""), lang)
        if action == "list":
            return _list_proposals(lang)
        return (
            f"Unknown action for skriv_reflex: '{action}'. "
            "Valid: suggest, confirm, reject, list."
        )

    return f"Unknown tool in executor_reflexes: {name}"
