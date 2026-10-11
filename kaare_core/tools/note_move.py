"""
Move one of Kåre's kare-list notes to another drawer in one call (P133 C5 support).

`note(action='move', list_name='kare', note_id=…, to='observations'|'agenda'|'lessons')`
writes the note to the target drawer first and removes it from the kare list **only when the
write succeeded** — nothing can be lost between the two steps, and it halves the tool calls of
a cleanup (sorting 100 notes one write + one delete at a time drowned the 27B, 2026-10-09).
An optional `text` replaces the note's text in the target (e.g. a shorter lesson).
"""

from __future__ import annotations

import logging

from kaare_core.tools import agenda, lister
from kaare_core.tools.i18n import t
from kaare_core.tools.lessons import lesson_store

logger = logging.getLogger(__name__)

TARGETS = ("observations", "agenda", "lessons")


def move_kare_note(
    note_id: str,
    to: str,
    *,
    title: str = "",
    theme: str = "",
    text: str = "",
    user_id: str = "global",
    rid: str = "",
    source: str = "",
    lang: str = "nb",
) -> str:
    to = (to or "").strip().lower()
    if to not in TARGETS:
        return t("note_move_bad_target", lang, valid=", ".join(TARGETS))
    item = lister.kare_note_get(note_id)
    if not item:
        return t("list_item_not_found", lang, id=note_id)
    body = (text or "").strip() or lister._strip_date_stamp(item.get("tekst", ""))
    title = (title or "").strip() or (item.get("tittel") or "").strip()
    context = (item.get("kontekst") or "").strip()

    if to == "lessons":
        fid, reply = lesson_store(body, title, lang)
        if fid is None:
            return t("note_move_failed", lang, id=item["id"], reason=reply)
        target = f"lessons [{fid}]"
    else:
        res = agenda._add(
            "agenda" if to == "agenda" else "observation",
            body,
            context,
            user_id=user_id,
            rid=rid,
            source=source,
            pointer=f"kare_huske.json#{item['id']}",
            lang=lang,
            theme=theme,
            title=title,
        )
        if isinstance(res, str):
            return t("note_move_failed", lang, id=item["id"], reason=res)
        target = f"{to} #{res['topic_id']}"
        if res.get("resolved"):
            target += " " + t("note_move_echo", lang, topic=res["resolved"]["topic_id"])
    lister.kare_note_done(item["id"], lang=lang)
    return t("note_move_done", lang, id=item["id"], target=target)
