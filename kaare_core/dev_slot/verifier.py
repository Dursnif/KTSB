"""
9B evidence verifier (P118 B4, P115 D14 / PR1).

Asks the 9B one narrow question per claim: does THIS evidence support THIS claim — yes / partly /
no, plus the exact words from the evidence that show it. Code then checks that those words really
are in the evidence. It judges claim ↔ evidence only — never Kåre, never a user. A "no" or
"partly" only marks the finding `unverified`; nothing is deleted.

Runs only on claims that already passed the code check (dev_slot/verify.py). When no 9B is
reachable (an install with one LLM, or the pool is busy), the code check stands alone and the
finding says so — the slot works from the defaults (rule 24).
"""

from __future__ import annotations

import logging

from adapters.llm_adapter import call_llm_chat
from kaare_core.dev_slot.json_out import extract_json
from kaare_core.dev_slot.verify import quote_in
from kaare_core.model_lock import LockTimeout, lock_agent_pool, pool_order, role_url
from kaare_core.tools.i18n import t

log = logging.getLogger("dev_slot")

VERIFIER_ROLE = "library"  # the 9B pool role that already does quote/evidence work (P111)
_LOCK_WAIT_S = 60
_SCHEMA = {
    "type": "object",
    "properties": {
        "supports": {"type": "string", "enum": ["yes", "partly", "no"]},
        "quote": {"type": "string"},
    },
    "required": ["supports", "quote"],
}


def _role_url() -> str | None:
    try:
        return role_url(VERIFIER_ROLE) or None
    except Exception:
        return None


async def _ask(messages: list[dict], rid: str) -> dict:
    url = _role_url()
    if url is None:
        return {"ok": False, "error": "no 9B role configured", "text": ""}
    try:
        async with lock_agent_pool(
            pool_order(url), "dev_slot_verify", max_wait=_LOCK_WAIT_S
        ) as inst_url:
            return await call_llm_chat(
                VERIFIER_ROLE,
                messages,
                rid=rid,
                source="meet",
                base_url=inst_url,
                response_format=_SCHEMA,
            )
    except LockTimeout:
        return {"ok": False, "error": "model busy", "text": ""}
    except Exception as e:
        return {"ok": False, "error": str(e), "text": ""}


async def check_claim(claim: str, evidence: str, *, rid: str, lang: str) -> dict:
    """{"status": ok | unavailable | unparsed, "supports", "quote", "quote_found", "error"?}"""
    messages = [
        {"role": "system", "content": t("slot_verifier_system", lang)},
        {"role": "user", "content": t("slot_verifier_user", lang, claim=claim, evidence=evidence)},
    ]
    res = await _ask(messages, rid)
    if not res.get("ok"):
        return {"status": "unavailable", "error": str(res.get("error", ""))[:200]}
    data = extract_json(res.get("text") or "")
    supports = str((data or {}).get("supports", "")).strip().lower()
    if supports not in ("yes", "partly", "no"):
        return {"status": "unparsed", "raw": (res.get("text") or "")[:300]}
    quote = str(data.get("quote", "")).strip()
    return {
        "status": "ok",
        "supports": supports,
        "quote": quote[:500],
        "quote_found": bool(quote) and quote_in(quote, evidence),
    }


def final_verified(code_verified: bool, verdict: dict) -> bool:
    """The finding is verified when the code check passed and the 9B either agrees with a quote
    that is really in the evidence, or could not be asked (then the code check stands alone)."""
    if not code_verified:
        return False
    if verdict.get("status") != "ok":
        return True
    return verdict.get("supports") == "yes" and bool(verdict.get("quote_found"))
