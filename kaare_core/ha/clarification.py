"""
HA clarification / rescue policy.

Ansvar:
- Håndtere ufullstendige HA-intents
- Forsøke å fylle manglende action / entity / area
- Brukes kun fra HA-pipeline (api_intent_to_ha)

Denne modulen:
- har ingen logging
- har ingen FastAPI-kode
- returnerer strukturert resultat eller None
"""

from typing import Optional, Dict, Any


async def ha_clarification_rescue(
    *,
    prompt: str,
    intent_res: Dict[str, Any],
    capability_hints: Dict[str, Any],
    call_llm,  # async fn(prompt:str)->str
) -> Optional[Dict[str, str]]:
    """
    Forsøk HA-rescue hvis intent forstår HA, men mangler info.

    Returnerer:
      {
        "action": "turn_on|turn_off",
        "entity": "...",
        "area": "..."
      }
    eller None hvis rescue ikke skal brukes.
    """

    if not isinstance(intent_res, dict):
        return None

    if not intent_res.get("ok"):
        return None

    if not intent_res.get("needs_clarification"):
        return None

    intent = intent_res.get("intent") or ""
    if not intent.startswith("ha."):
        return None

    rescue_payload = {
        "mode": "ha_rescue",
        "original_text": prompt,
        "known": {
            "intent": intent_res.get("intent"),
            "area": intent_res.get("area"),
        },
        "missing": ["action", "entity"],
        "intent_log": {
            "needs_clarification": True,
            "confidence": intent_res.get("confidence"),
        },
        "capability_hints": capability_hints,
    }

    rescue_prompt = (
        "Du hjelper til med tolkning av Home Assistant-kommando.\n"
        "Returner KUN stikkord, én per linje.\n\n"
        f"{rescue_payload}\n\n"
        "Svarformat:\n"
        "action: <turn_on|turn_off>\n"
        "entity: <enhet>\n"
        "area: <rom>"
    )

    raw = await call_llm(rescue_prompt)
    if not raw:
        return None
    # call_llm kan returnere dict {"ok": True, "text": "..."} eller ren streng
    if isinstance(raw, dict):
        raw = raw.get("text", "") or ""
    if not raw:
        return None

    result = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k in ("action", "entity", "area") and v:
            result[k] = v

    return result or None
