"""
Miss Kåre – evaluator.
Receives (user_msg, kare_response, user_id), queries Miss Kåre's LLM,
and returns [STILLE] or a short comment.
"""

import asyncio
import logging
from pathlib import Path

import httpx

from kaare_core.model_lock import lock_11445, LockTimeout
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, is_agent_tool_enabled
from kaare_core.llm_fallback import is_fallback_active
from kaare_core.users.profile_manager import get_display_name

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
    path = Path(f"/kaare/state/users/{user_id}/miss_kare_portrait.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()[:3000]


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


async def _llm_call(messages: list) -> str:
    _cfg = _llm("miss_kare")
    payload = {
        "model": MISS_KARE_MODEL,
        "stream": _cfg.get("stream", False),
        "options": {**_cfg.get("options", {}), "num_predict": MAX_TOKENS},
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
