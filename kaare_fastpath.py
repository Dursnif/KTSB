"""
kaare_fastpath.py
-----------------
Enkle fastpath-regler for Kåre.

Bruk:
- Få inn en tekst (prompt)
- match_fastpath(prompt) returnerer et lite oppslags-objekt
  hvis dette er en kjent, “hardkodet” kommando.
- Hvis ingen match: returnerer None, og Kåre bruker vanlig pipeline.
"""

from typing import Optional, Dict


def match_fastpath(prompt: str) -> Optional[Dict]:
    """
    Sjekk om prompt er en kjent fastpath-kommando.

    Returnerer:
        dict med nøkler:
          - route: hvilken intern rute dette tilhører (f.eks. 'ha_fastpath')
          - target: hvilket subsystem dette er ment for (f.eks. 'ha_bridge')
          - action: konkret HA-action ('turn_on' / 'turn_off')
          - entity_id: hvilken enhet som skal styres
          - source: liten debug-tag for å se at det kom fra fastpath

        eller:
        None hvis ingen fastpath-regel passer.
    """
    p = (prompt or "").strip().lower()

    # Fastpath 1: skru PÅ taklys verksted
    if p == "skru på taklys verksted":
        return {
            "route": "ha_fastpath",
            "target": "ha_bridge",
            "action": "turn_on",
            "entity_id": "switch.grenuttak_verksted_bryter_3",
            "source": "fastpath_exact",
        }

    # Fastpath 2: skru AV taklys verksted
    if p == "skru av taklys verksted":
        return {
            "route": "ha_fastpath",
            "target": "ha_bridge",
            "action": "turn_off",
            "entity_id": "switch.grenuttak_verksted_bryter_3",
            "source": "fastpath_exact",
        }

    # Fastpath 3: klokka (lokal host-tid)
    if p in {
        "hva er klokka",
        "hvor mye er klokka",
        "hva er klokka nå",
        "hvor mye er klokka nå",
        "klokka",
    }:
        return {
            "route": "clock_fastpath",
            "source": "fastpath_clock",
        }



    # Ingen fastpath-match – Kåre må bruke vanlig intent/LLM-løype
    return None
