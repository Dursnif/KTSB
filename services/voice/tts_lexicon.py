"""TTS pronunciation lexicon parsing (kept apart from voice_bridge so it is testable without audio libs)."""

import re


def parse_substitutions(lex: dict | None) -> list[tuple[re.Pattern, str]]:
    """Whole-word, case-insensitive patterns from a lexicon dict.

    A `substitutions:` key with nothing under it is YAML null — treated as no entries (a fresh
    install ships empty en/de lexicons).
    """
    subs = (lex or {}).get("substitutions") or {}
    return [
        (re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE), replacement)
        for word, replacement in subs.items()
    ]
