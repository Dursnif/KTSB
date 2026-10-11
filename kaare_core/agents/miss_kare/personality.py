"""
Miss Kåre's one locked personality (P115 Phase 1 / P99 Decision): `personlighet.md` in nb,
`personlighet_en.md` / `personlighet_de.md` siblings, nb as fallback. Used by every place
that speaks as Miss Kåre — the live evaluator, the portrait nightjob and the reflection meeting.
"""

from pathlib import Path

from kaare_core.tools.i18n import t

_DIR = Path(__file__).parent


def load_personality(lang: str = "nb") -> str:
    suffix = "" if lang == "nb" else f"_{lang}"
    for path in (_DIR / f"personlighet{suffix}.md", _DIR / "personlighet.md"):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return t("mk_personality_fallback", lang)
