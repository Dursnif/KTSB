"""
Miss Kåre – evaluator.
Tar imot (user_msg, kare_response, user_id), spør Miss Kåres LLM,
og returnerer [STILLE] eller en kort kommentar.
"""

import logging
from pathlib import Path

import httpx

from kaare_core.model_lock import lock_11445, LockTimeout
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, get_service as _svc, is_agent_tool_enabled
from kaare_core.llm_fallback import is_fallback_active
from kaare_core.users.profile_manager import get_display_name

MISS_KARE_URL    = _llm("miss_kare")["base_url"] + "/api/chat"
MISS_KARE_MODEL  = _cfg_model("miss_kare")
TIMEOUT          = _llm("miss_kare")["timeout"]
MAX_TOKENS       = 200  # evaluator bruker bevisst kortere svar enn generell miss_kare

log = logging.getLogger("miss_kare")

_PERSONALITY_PATH = Path(__file__).parent / "personlighet.md"

def _load_personality() -> str:
    if _PERSONALITY_PATH.exists():
        return _PERSONALITY_PATH.read_text(encoding="utf-8")
    return "Du er Miss Kåre – varm, moderlig, jordnær."


async def _ask_library(spørsmål: str, user_id: str = "global") -> str:
    """Kaller Frøken Library direkte (port 11450). Returnerer svar eller tom streng."""
    if not _llm("library").get("enabled", True):
        return ""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library",
                json={"question": spørsmål},
            )
            r.raise_for_status()
            svar = r.json().get("answer", "").strip()

        try:
            import asyncio
            from kaare_core.memory.long_term import get_ltm
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="miss_kare",
                to_agent="miss_library",
                query=spørsmål,
                response=svar,
                rid="",
                user_id=user_id,
            ))
        except Exception:
            pass

        return svar
    except Exception as e:
        log.warning("[Miss Kåre] Frøken Library-kall feilet: %s", e)
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
    Frøken Library's wiki and responds with that information.
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

    system = _load_personality() + (
        "\n\n---\n\n"
        + user_block
        + situation
        + "Hvis du vil slå opp noe faktisk i wikien til Frøken Library, svar kun med:\n"
        "[SJEKK: <presist spørsmål til Frøken Library>]\n\n"
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
        # Runde 1: første LLM-kall
        async with lock_11445("miss_kare", max_wait=60):
            reply = await _llm_call(messages)
        log.info("[Miss Kåre] første svar: %s", reply[:100])

        # Sjekk om Miss Kåre vil slå opp noe hos Frøken Library (port 11450 – ingen lås)
        if reply.startswith("[SJEKK:") and "]" in reply and is_agent_tool_enabled("miss_kare", "spør_frøken_library", default=True):
            spørsmål = reply[7:reply.index("]")].strip()
            log.info("[Miss Kåre] ber Frøken Library om: %s", spørsmål)
            bibliotek_svar = await _ask_library(spørsmål, user_id)
            if bibliotek_svar:
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": (
                    f"Frøken Library svarte:\n{bibliotek_svar}\n\n"
                    "Bruk dette til å si noe varmt og konkret til brukeren. Maks 3 setninger."
                )})
                # Runde 2: andre LLM-kall med biblioteksvar
                async with lock_11445("miss_kare", max_wait=60):
                    reply = await _llm_call(messages)
                log.info("[Miss Kåre] svar etter bibliotek: %s", reply[:100])

        return reply if reply and reply != "[STILLE]" else "[STILLE]"

    except LockTimeout:
        log.warning("[Miss Kåre] lock timeout — Mechanic holder modellen, hopper over evaluering")
        return "[STILLE]"
    except Exception as e:
        log.warning("[Miss Kåre] evaluator feilet: %s", e)
        return "[STILLE]"
