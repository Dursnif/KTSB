"""
Miss Kåre – evaluator.
Receives (user_msg, kare_response, user_id), queries Miss Kåre's LLM,
and returns [STILLE] or a short comment.
"""

import asyncio
import difflib
import json
import logging
from pathlib import Path

import httpx

from kaare_core.model_lock import lock_11445, LockTimeout
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, is_agent_tool_enabled
from kaare_core.llm_fallback import is_fallback_active
from kaare_core.users.profile_manager import (
    get_display_name,
    get_section_fields,
    get_writable_sections,
    load_profile,
    vault_or_update_nested_field,
)

MISS_KARE_URL    = _llm("miss_kare")["base_url"] + "/api/chat"
MISS_KARE_MODEL  = _cfg_model("miss_kare")
TIMEOUT          = _llm("miss_kare")["timeout"]
MAX_TOKENS       = 200  # evaluator deliberately uses shorter responses than general miss_kare

log = logging.getLogger("miss_kare")

_PERSONALITY_PATH = Path(__file__).parent / "personlighet.md"

def _load_personality() -> str:
    if _PERSONALITY_PATH.exists():
        return _PERSONALITY_PATH.read_text(encoding="utf-8")
    return "Du er Miss Kåre – varm, moderlig, jordnær."


def _load_portrait(user_id: str) -> str:
    if not user_id or user_id == "global":
        return ""
    from kaare_core.crypto import read_user_text_file
    path = Path(f"/kaare/state/users/{user_id}/miss_kare_portrait.md")
    # allow_system_key=True (P87): Miss Kåre is explicitly named in the plan's Background
    # as an entity that should always have access to a user's own data "under the hood" —
    # her sideview evaluation runs on every response regardless of PIN-session state.
    return read_user_text_file(path, user_id, allow_system_key=True).strip()[:3000]


async def _ask_library(query: str, user_id: str = "global") -> str:
    """Ask Miss Library directly (in-process). Returns answer or empty string."""
    if not _llm("library").get("enabled", True):
        return ""
    try:
        from kaare_core.tools.executor_library import _ask_library_wiki
        from kaare_core.memory.long_term import get_ltm
        svar = await _ask_library_wiki(query)
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="miss_kare",
                to_agent="miss_library",
                query=query,
                response=svar,
                rid="",
                user_id=user_id,
            ))
        except Exception:
            pass
        return svar
    except Exception as e:
        log.warning("[Miss Kare] Library call failed: %s", e)
        return ""


async def _llm_call(messages: list, max_tokens: int = MAX_TOKENS) -> str:
    _cfg = _llm("miss_kare")
    payload = {
        "model": MISS_KARE_MODEL,
        "stream": _cfg.get("stream", False),
        "options": {**_cfg.get("options", {}), "num_predict": max_tokens},
        "messages": messages,
    }
    if "think" in _cfg:
        payload["think"] = _cfg["think"]
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(MISS_KARE_URL, json=payload)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()


async def evaluate(
    user_msg: str,
    kare_response: str,
    user_id: str = "global",
    addressed_directly: bool = False,
) -> str:
    """
    Returns [STILLE] if Miss Kåre has nothing to say,
    or a short comment (2-3 sentences) if something moved her.

    If addressed_directly=True the user started their message with "Miss Kåre" —
    she knows she's being spoken to and should respond rather than just observe.

    If Miss Kåre replies with [SJEKK: <question>] she looks it up in
    Miss Library's wiki and responds with that information.
    """
    # Kåre is using the 9B model himself — Miss Kåre stays silent so they
    # don't compete for the same resource.
    if is_fallback_active():
        return "[STILLE]"

    if addressed_directly:
        situation = (
            "Brukeren henvendte seg direkte til deg — de startet meldingen sin med 'Miss Kåre'. "
            "Du er blitt talt til. Svar varmt og direkte til brukeren. "
            "Kåres kommentar (hvis han sa noe) kan du gjerne forholde deg til, men du er ikke nødt.\n\n"
        )
    else:
        situation = (
            "Du har nettopp lest en samtale mellom brukeren og Kåre. "
            "Avgjør om du har noe å si – noe som berørte deg, som fortjener en reaksjon.\n\n"
            "Hvis du ikke har noe å tilføye: svar kun med [STILLE]\n"
        )

    user_name = get_display_name(user_id) if user_id and user_id != "global" else None
    user_block = f"# Nåværende bruker\nDu snakker nå med **{user_name}**. Bruk alltid dette navnet — aldri et annet.\n\n---\n\n" if user_name else ""

    portrait = _load_portrait(user_id)
    portrait_block = (
        f"\n\n---\n\n# Dine tidligere observasjoner om {user_name}\n{portrait}\n"
        if portrait and user_name else ""
    )

    system = _load_personality() + portrait_block + (
        "\n\n---\n\n"
        + user_block
        + situation
        + "Hvis du vil slå opp noe faktisk i wikien til Miss Library, svar kun med:\n"
        "[SJEKK: <presist spørsmål til Miss Library>]\n\n"
        "Hvis du vil si noe direkte: si det varmt, maks 3 setninger. Ingen innledning."
    )

    user_content = (
        f"Brukeren sa:\n{user_msg}\n\n"
        f"Kåre svarte:\n{kare_response}"
    )

    messages = [
        {"role": "system", "content": f"/no_think\n{system}"},
        {"role": "user",   "content": user_content},
    ]

    try:
        # Round 1: first LLM call
        async with lock_11445("miss_kare", max_wait=60):
            reply = await _llm_call(messages)
        log.info("[Miss Kare] first reply: %s", reply[:100])

        # Check if Miss Kare wants to look something up in Miss Library (direct call — no lock)
        if reply.startswith("[SJEKK:") and "]" in reply and is_agent_tool_enabled("miss_kare", "spør_miss_library", default=True):
            query = reply[7:reply.index("]")].strip()
            log.info("[Miss Kare] asking Miss Library: %s", query)
            bibliotek_svar = await _ask_library(query, user_id)
            if bibliotek_svar:
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": (
                    f"Miss Library svarte:\n{bibliotek_svar}\n\n"
                    "Bruk dette til å si noe varmt og konkret til brukeren. Maks 3 setninger."
                )})
                # Round 2: second LLM call with library response
                async with lock_11445("miss_kare", max_wait=60):
                    reply = await _llm_call(messages)
                log.info("[Miss Kare] reply after library lookup: %s", reply[:100])

        return reply if reply and reply != "[STILLE]" else "[STILLE]"

    except LockTimeout:
        log.warning("[Miss Kare] lock timeout — Mechanic holds the model, skipping evaluation")
        return "[STILLE]"
    except Exception as e:
        log.warning("[Miss Kare] evaluator failed: %s", e)
        return "[STILLE]"


_NOVELTY_THRESHOLD = 0.75  # matches the kare_huske.json dedup threshold (P82)
# Generous despite the tiny JSON output: abliterated Qwen3 models emit thinking tokens
# even with /no_think (documented limitation, CLAUDE.md), and num_predict caps the total —
# too small a budget truncates before any content is produced at all.
_EXTRACT_MAX_TOKENS = 400


def _is_near_duplicate(existing, value: str) -> bool:
    """SAGE-style novelty gate: skip writes that just restate what's already known."""
    if not existing:
        return False
    candidates = existing if isinstance(existing, list) else [existing]
    return any(
        difflib.SequenceMatcher(None, str(c).strip().lower(), value.strip().lower()).ratio()
        >= _NOVELTY_THRESHOLD
        for c in candidates
    )


async def _extract_fact_json(exchange_text: str, situation_hint: str) -> dict | None:
    """
    Shared low-level step: ask the 9B model for at most one {section, field, value}
    biographical fact from a piece of text, validated against the real
    profile_template.yaml schema. Returns None if nothing usable was found. Does not
    write anything and does not novelty-gate — callers do that against their own user_id.

    Respects the shared-model contention rules: skips cleanly (returns None) if the 9B
    is currently standing in as Kåre's own fallback, same as evaluate() does.
    """
    if is_fallback_active():
        log.info("[Miss Kare] extract: 9B fallback active, skipping to avoid contention")
        return None

    sections = get_writable_sections()
    catalog = "\n".join(f"- {sec}: {', '.join(get_section_fields(sec))}" for sec in sections)
    system = (
        f"Du er Miss Kåre. {situation_hint}\n"
        "Avgjør om teksten inneholder ETT konkret, biografisk faktum verdt å huske "
        "(f.eks. bursdag, jobb, hobby, forhold, religion, bosted) — ikke stemning, "
        "mening om Kåre, eller noe teknisk.\n\n"
        "Du MÅ velge feltnavn fra denne listen — finn ikke på nye feltnavn:\n"
        f"{catalog}\n\n"
        "Svar KUN med JSON, ingen annen tekst:\n"
        '{"section": "<seksjonsnavn fra listen>", "field": "<feltnavn fra listen>", "value": "<verdien>"}\n'
        'Hvis ingenting konkret ble sagt, eller ingen felt passer: {"section": null}'
    )
    messages = [
        {"role": "system", "content": f"/no_think\n{system}"},
        {"role": "user", "content": exchange_text},
    ]

    try:
        async with lock_11445("miss_kare_extract", max_wait=30):
            raw = (await _llm_call(messages, max_tokens=_EXTRACT_MAX_TOKENS)).strip()
    except LockTimeout:
        log.info("[Miss Kare] extract: lock timeout, skipping")
        return None
    except Exception as e:
        log.warning("[Miss Kare] extract: LLM call failed: %s", e)
        return None

    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except Exception:
        log.info("[Miss Kare] extract: no parseable JSON in reply, skipping")
        return None

    section = parsed.get("section")
    if not section:
        return None
    field = (parsed.get("field") or "").strip()
    value = (parsed.get("value") or "").strip()
    if section not in sections or field not in get_section_fields(section) or not value:
        log.info("[Miss Kare] extract: invalid section/field '%s.%s', skipping", section, field)
        return None
    return {"section": section, "field": field, "value": value}


def _write_fact_if_novel(user_id: str, section: str, field: str, value: str) -> None:
    """Shared novelty-gated write — skips near-duplicates of what's already stored.

    Uses vault_or_update_nested_field(), not a direct update, because this is called
    from both a live path (Phase 4 — session key present) and an unattended nightly
    path (Phase 5, reflection meeting — no session key, ever). A direct write would
    silently fall through to save_profile()'s unencrypted-plaintext fallback for the
    nightly case; the vault path keeps it encrypted (SealedBox, user's public key) until
    the user's next login, same as every other agent-writes-while-offline path already
    does for the flat-field case.
    """
    profile = load_profile(user_id)
    current = (profile.get(section) or {}).get(field)
    if _is_near_duplicate(current, value):
        log.info("[Miss Kare] extract: '%s.%s' near-duplicate of existing value, skipping", section, field)
        return
    result = vault_or_update_nested_field(user_id, section, field, value)
    log.info("[Miss Kare] extract: wrote %s.%s for %s (%s)", section, field, user_id, result)


async def extract_and_store_fact(user_id: str, prior_question: str, user_reply: str) -> None:
    """
    Called when a user replies to Miss Kåre directly (addressed_directly=True) and she
    had asked something the turn before. Extracts at most one concrete biographical fact
    from the exchange and writes it into the user's structured profile
    (profile_template.yaml sections), skipping near-duplicates of what's already stored.

    Fire-and-forget by design — never raises, never blocks the user-facing response.
    """
    if not user_id or user_id == "global" or not prior_question or not user_reply.strip():
        return
    exchange_text = f"Du spurte: {prior_question}\nBrukeren svarte: {user_reply}"
    fact = await _extract_fact_json(exchange_text, "Du stilte nettopp et spørsmål, og brukeren svarte.")
    if fact:
        _write_fact_if_novel(user_id, fact["section"], fact["field"], fact["value"])


async def extract_facts_from_meeting(user_id: str, kare_closing: str) -> None:
    """
    Called after the nightly reflection meeting concludes. Extracts at most one concrete
    biographical fact from Kåre's closing synthesis about the user and writes it into the
    structured profile, same schema and novelty gate as the live path above. Replaces the
    old pattern of writing ad-hoc `preferences`/`concerns`/`delights` fields that don't
    exist in profile_template.yaml.

    Fire-and-forget-safe by design — never raises.
    """
    if not user_id or user_id == "global" or not kare_closing.strip():
        return
    fact = await _extract_fact_json(
        kare_closing.strip(),
        "Dette er Kåres avsluttende oppsummering av kveldens refleksjonsmøte om brukeren.",
    )
    if fact:
        _write_fact_if_novel(user_id, fact["section"], fact["field"], fact["value"])
