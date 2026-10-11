"""
Kåre's lessons drawer — `note(list_name='lessons')` (P133 C1, D3/D7).

Rules Kåre has learned about how he works («law questions go via lovdata, never web_search»,
«look up the alias before ha_control»). Unlike the kare list (follow-ups, shown as an index)
lessons are always in the system prompt as short prose, capped by
`memory_prompt.lessons_max_chars`, because a rule has to steer behaviour without being asked
for. Stored in knowledge.db, domain 'lessons' (validity + soft delete reused); deliberately
not part of the weekly knowledge re-validation, so a lesson is only changed by Kåre or a human.
"""

from __future__ import annotations

import logging

from kaare_core.config import get_memory_prompt_config
from kaare_core.memory import knowledge_store as ks
from kaare_core.tools.i18n import t

logger = logging.getLogger(__name__)

DOMAIN = "lessons"
_TITLE_CHARS = 80


def _text_max() -> int:
    """Max length of one lesson — `memory_prompt.lessons_write_max_chars` (GUI, N25)."""
    return get_memory_prompt_config()["lessons_write_max_chars"]


def lesson_store(text: str, title: str = "", lang: str = "nb") -> tuple[int | None, str]:
    """(id, reply). id is set when the lesson is stored or already exists (same text)."""
    text = (text or "").strip()
    if not text:
        return None, t("list_empty_text", lang)
    limit = _text_max()
    if len(text) > limit:
        return None, t("lesson_too_long", lang, max=limit, n=len(text))
    for row in ks.get_all_facts(DOMAIN):
        if row["content"].strip().lower() == text.lower():
            return row["id"], t("lesson_exists", lang, id=row["id"])
    fid = ks.add_fact(
        domain=DOMAIN, section=(title or "").strip()[:_TITLE_CHARS], content=text, importance=0.8
    )
    if fid is None:
        return None, t("agenda_write_failed", lang, error="knowledge.db")
    return fid, t("lesson_added", lang, id=fid, text=text)


def lesson_add(text: str, title: str = "", lang: str = "nb") -> str:
    return lesson_store(text, title, lang)[1]


def lesson_read(lang: str = "nb") -> str:
    rows = ks.get_all_facts(DOMAIN)
    if not rows:
        return t("lesson_empty", lang)
    lines = [t("lesson_read_title", lang, count=len(rows))]
    for r in rows:
        title = f"{r['section']}: " if r.get("section") else ""
        lines.append(f"  [{r['id']}] {title}{r['content']}")
    return "\n".join(lines)


def _id(raw) -> int | None:
    try:
        return int(str(raw).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


def _owned(fid: int | None) -> bool:
    return fid is not None and any(r["id"] == fid for r in ks.get_all_facts(DOMAIN))


def lesson_edit(lesson_id, text: str, title: str = "", lang: str = "nb") -> str:
    fid = _id(lesson_id)
    text = (text or "").strip()
    if not _owned(fid):
        return t("list_item_not_found", lang, id=lesson_id)
    if not text:
        return t("list_empty_text", lang)
    limit = _text_max()
    if len(text) > limit:
        return t("lesson_too_long", lang, max=limit, n=len(text))
    ks.update_fact(fid, content=text, section=(title.strip()[:_TITLE_CHARS] or None))
    return t("lesson_edited", lang, id=fid, text=text)


def lesson_delete(lesson_id, lang: str = "nb") -> str:
    """Soft delete (retired, kept as history) — knowledge_store never hard-deletes."""
    fid = _id(lesson_id)
    if not _owned(fid):
        return t("list_item_not_found", lang, id=lesson_id)
    ks.retire_fact(fid)
    return t("lesson_retired", lang, id=fid)


def lessons_for_prompt(max_chars: int, lang: str = "nb") -> str:
    """Prose block for the system prompt, highest importance first, cut at a lesson boundary."""
    try:
        rows = ks.get_top_facts(DOMAIN, limit=50)
    except Exception as e:
        logger.warning("lessons_for_prompt failed: %s", e)
        return ""
    if not rows:
        return ""
    header = t("lesson_prompt_title", lang)
    out, used = [header], len(header)
    for r in rows:
        line = f"- {r['content']}"
        if used + len(line) + 1 > max_chars and len(out) > 1:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)
