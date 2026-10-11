"""
Wiki registry — which wiki collections exist, in which order, and what each one holds.

Single source of truth (P111 Phase 3) for:
  - Miss Library's search (all enabled wikis in parallel, primary first),
  - the `library` tool description Kåre sees (what each wiki contains),
  - the per-wiki label on every result.

Config: services.yaml `wiki:`
    primary: no            # empty → derived from settings.yaml `language`
    sources:
      no: {collection: wiki_no, enabled: true, structured_db: /path/to/wiki_no.db}
      en: {collection: wiki_en, enabled: true, structured_db: ""}

Wiki ids are free-form (any language), deliberately NOT tied to the GUI's nb/en/de.
"Has tables/lists" is decided at runtime: a structured_db is configured AND the file exists —
so the description never promises data that isn't there.
"""

from pathlib import Path

from kaare_core.config import get_service, get_settings
from kaare_core.tools.i18n import t

# GUI/onboarding language → wiki id, only used when `wiki.primary` is empty
_LANG_TO_WIKI = {"nb": "no", "nn": "no", "no": "no", "en": "en", "de": "de"}


def _cfg() -> dict:
    return get_service("wiki") or {}


def _enabled_sources() -> dict[str, dict]:
    sources = _cfg().get("sources") or {}
    return {
        str(wid): s
        for wid, s in sources.items()
        if isinstance(s, dict) and s.get("enabled", True) and s.get("collection")
    }


def primary_id() -> str:
    ids = list(_enabled_sources())
    if not ids:
        return ""
    configured = str(_cfg().get("primary") or "")
    if configured in ids:
        return configured
    lang = str((get_settings() or {}).get("language") or "")
    derived = _LANG_TO_WIKI.get(lang, "")
    return derived if derived in ids else ids[0]


def wikis(only: list[str] | None = None) -> list[dict]:
    """Enabled wikis, primary first. `only` narrows to the given ids (order rule unchanged).
    Each entry: {id, collection, structured_db, has_structured, primary}."""
    prim = primary_id()
    out = []
    for wid, s in _enabled_sources().items():
        if only and wid not in only:
            continue
        db = str(s.get("structured_db") or "")
        out.append(
            {
                "id": wid,
                "collection": s["collection"],
                "structured_db": db or None,
                "has_structured": bool(db) and Path(db).is_file(),
                "primary": wid == prim,
            }
        )
    out.sort(key=lambda w: not w["primary"])
    return out


def get_wiki(wid: str) -> dict | None:
    for w in wikis():
        if w["id"] == wid:
            return w
    return None


def parse_wiki_arg(value) -> list[str] | None:
    """The tool's optional `wiki` argument: "en", "no,en", ["no", "en"] or empty (= all)."""
    if not value:
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
    elif isinstance(value, list):
        parts = [str(p).strip() for p in value]
    else:
        return None
    parts = [p for p in parts if p]
    return parts or None


def content_label(w: dict, lang: str) -> str:
    return t("wiki_content_structured" if w["has_structured"] else "wiki_content_prose_only", lang)


def capability_text(lang: str) -> str:
    """One line per wiki for tool descriptions / task prompts, e.g.
    "no (primary): prose + tables + lists; en: prose only"."""
    ws = wikis()
    if not ws:
        return t("wiki_none_configured", lang)
    parts = []
    for w in ws:
        name = f"{w['id']} ({t('wiki_primary', lang)})" if w["primary"] else w["id"]
        parts.append(f"{name}: {content_label(w, lang)}")
    return "; ".join(parts)


def result_label(w: dict, lang: str) -> str:
    """Header put in front of each wiki's material, e.g. "[en — prose only]"."""
    return f"[{w['id']} — {content_label(w, lang)}]"


def auto_resolve_max_rows() -> int:
    """Tables/lists up to this many rows are put inline in a search result (Stian: 30)."""
    try:
        return int(_cfg().get("auto_resolve_max_rows", 30))
    except (TypeError, ValueError):
        return 30


def title_rank_k() -> int:
    """Smoothing constant for choosing which article to read in full: each title scores
    sum(1 / (k + rank)) over its passages. Small k = the top passage weighs most; larger k =
    titles with many passages weigh more. 3 picked on 13 real questions (P111 Phase 6a)."""
    try:
        return max(0, int(_cfg().get("title_rank_k", 3)))
    except (TypeError, ValueError):
        return 3


def inline_max_chars() -> int:
    """Upper bound for inline table text per wiki in one search result."""
    try:
        return int(_cfg().get("inline_max_chars", 6000))
    except (TypeError, ValueError):
        return 6000
