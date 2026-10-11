"""
Shared utilities for Jing and Jang runners:
path constants, config loaders, think-block stripper.
"""

import re
from pathlib import Path

import yaml

KAARE_BASE = Path("/kaare")

SETTINGS_PATH = KAARE_BASE / "configs" / "settings.yaml"
SERVICES_PATH = KAARE_BASE / "configs" / "services.yaml"
JING_THOUGHTS = KAARE_BASE / "state" / "jing_thoughts.txt"
INNER_THOUGHTS = KAARE_BASE / "state" / "inner_thoughts.txt"
FACE_EVENTS = KAARE_BASE / "state" / "argus" / "face_events.txt"
DIGEST = KAARE_BASE / "state" / "argus" / "digest.txt"
PERSONALITY = KAARE_BASE / "state" / "personality_self.md"
MODEL_CACHE_DIR = KAARE_BASE / "services" / "inner_voices" / "ov_cache"


def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks, including unclosed ones at token limit."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
    return text.strip()


def load_behavior_config(name: str) -> dict:
    """Load jing or jang behavioral settings from settings.yaml."""
    try:
        return yaml.safe_load(SETTINGS_PATH.read_text()).get(name, {})
    except Exception:
        return {}


def load_service_config(name: str) -> dict:
    """Load jing or jang infrastructure config from services.yaml."""
    try:
        return yaml.safe_load(SERVICES_PATH.read_text()).get(name, {})
    except Exception:
        return {}


def get_pipe_lang() -> str:
    """Resolve the active pipe language (nb/en/de) from kare_language.

    Standalone duplicate of kaare_core.tools.i18n.get_lang()'s collapse logic (household-level
    only, no per-user override — Jing/Jang have no concept of a single active user) rather than
    an import of kaare_core: this module runs in its own OpenVINO venv on the inner-voice node, reachable from
    /kaare only via the SSHFS mount that already backs load_behavior_config()/load_service_config()
    above — importing the full kaare_core package would add fragile cross-environment coupling
    for a five-line function (P100 Phase 8).
    """
    try:
        cfg = yaml.safe_load(SETTINGS_PATH.read_text()) or {}
        lang = cfg.get("kare_language", "nb")
        if lang in ("nb", "en", "de"):
            return lang
    except Exception:
        pass
    return "en"


def t(key: str, lang: str = "nb") -> str:
    return _T.get(key, {}).get(lang) or _T.get(key, {}).get("nb") or key


_T: dict = {
    "jing_system": {
        "nb": (
            "Du er Jing, et sanse-lag i Kares kognitive system.\n"
            "Du mottar tre forhåndssorterte kategorier med ferske hendelser.\n"
            "Skriv 1-3 korte, faktabaserte observasjoner per kategori som har innhold.\n"
            "Utelat hele kategorien hvis den er tom (ingen data). Maks 2 setninger per kategori.\n"
            "Skriv på norsk. Ingen refleksjon, ingen meninger — kun hva du faktisk ser i dataene."
        ),
        "en": (
            "You are Jing, a sensory layer in Kåre's cognitive system.\n"
            "You receive three pre-sorted categories of fresh events.\n"
            "Write 1-3 short, fact-based observations per category that has content.\n"
            "Omit the entire category if it is empty (no data). Max 2 sentences per category.\n"
            "Write in English. No reflection, no opinions — only what you actually see in the data."
        ),
        "de": (
            "Du bist Jing, eine Sinnesebene in Kåres kognitivem System.\n"
            "Du erhältst drei vorsortierte Kategorien mit aktuellen Ereignissen.\n"
            "Schreibe 1-3 kurze, faktenbasierte Beobachtungen pro Kategorie mit Inhalt.\n"
            "Lasse die gesamte Kategorie aus, wenn sie leer ist (keine Daten). Max. 2 Sätze pro Kategorie.\n"
            "Schreibe auf Deutsch. Keine Reflexion, keine Meinungen — nur was du tatsächlich in den Daten siehst."
        ),
    },
    "jang_system": {
        "nb": (
            "Du er Jang. Du mottar de siste Jing-blokkene — råobservasjoner fra Kares sansesystem.\n"
            "Destiller til maksimalt 1-2 linjer per kategori som har faktisk innhold.\n"
            "Utelat en kategori helt hvis alle Jing-blokkene viser (ingen) for den kategorien.\n"
            "Prioriter den nyeste Jing-blokken (siste [Jing]-oppføring).\n"
            "Skriv output slik:\n"
            "[MENNESKER] <1-2 linjer, eller utelat hele linjen>\n"
            "[ANDRE] <1-2 linjer, eller utelat hele linjen>\n"
            "[ARGUS] <1-2 linjer, eller utelat hele linjen>\n"
            "Skriv på norsk. Ingen forklaringer — kun de destillerte linjene."
        ),
        "en": (
            "You are Jang. You receive the latest Jing blocks — raw observations from Kåre's sensory system.\n"
            "Distill to at most 1-2 lines per category that has actual content.\n"
            "Omit a category entirely if all the Jing blocks show (none) for that category.\n"
            "Prioritize the newest Jing block (the last [Jing] entry).\n"
            "Write the output like this:\n"
            "[PEOPLE] <1-2 lines, or omit the entire line>\n"
            "[OTHER] <1-2 lines, or omit the entire line>\n"
            "[ARGUS] <1-2 lines, or omit the entire line>\n"
            "Write in English. No explanations — only the distilled lines."
        ),
        "de": (
            "Du bist Jang. Du erhältst die neuesten Jing-Blöcke — Rohbeobachtungen aus Kåres Sinnessystem.\n"
            "Destilliere auf maximal 1-2 Zeilen pro Kategorie mit tatsächlichem Inhalt.\n"
            "Lasse eine Kategorie ganz aus, wenn alle Jing-Blöcke (keine) für diese Kategorie zeigen.\n"
            "Priorisiere den neuesten Jing-Block (letzter [Jing]-Eintrag).\n"
            "Schreibe die Ausgabe so:\n"
            "[MENSCHEN] <1-2 Zeilen, oder die ganze Zeile auslassen>\n"
            "[ANDERE] <1-2 Zeilen, oder die ganze Zeile auslassen>\n"
            "[ARGUS] <1-2 Zeilen, oder die ganze Zeile auslassen>\n"
            "Schreibe auf Deutsch. Keine Erklärungen — nur die destillierten Zeilen."
        ),
    },
    "inner_none": {"nb": "(ingen)", "en": "(none)", "de": "(keine)"},
    "inner_label_mennesker": {"nb": "MENNESKER", "en": "PEOPLE", "de": "MENSCHEN"},
    "inner_label_andre": {"nb": "ANDRE", "en": "OTHER", "de": "ANDERE"},
    "inner_label_argus": {"nb": "ARGUS", "en": "ARGUS", "de": "ARGUS"},
    "jang_context_jing_header": {
        "nb": "=== Jing-blokker (nyeste sist) ===",
        "en": "=== Jing blocks (newest last) ===",
        "de": "=== Jing-Blöcke (neueste zuletzt) ===",
    },
    "jang_context_personality_header": {
        "nb": "=== Kares personlighet ===",
        "en": "=== Kåre's personality ===",
        "de": "=== Kåres Persönlichkeit ===",
    },
}
