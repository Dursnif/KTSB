# /kaare/kaare_core/tools/notisblokk.py
"""
Kåre's notepad — temporary notes, to-do items, reminders.
Separate from personality_self.md / knowledge_store's 'personality' domain,
which is only for self-observations.

Backing store: knowledge_store.state_facts, domain='notes' (P105 Phase 3).
`kategori` -> section, `tekst` -> content. Row id is a plain SQLite
auto-increment int (confirmed safe 2026-09-07: notat_id is never referenced
outside this tool's own read->act round trip — see P105 plan Phase log).
"""

from kaare_core.memory import knowledge_store as ks
from kaare_core.tools.i18n import t

_DOMAIN = "notes"


def skriv_notat(tekst: str, kategori: str = "diverse", lang: str = "nb") -> str:
    if not tekst.strip():
        return t("nota_empty_text", lang)
    kat = kategori.strip().lower() or "diverse"
    fact_id = ks.add_fact(domain=_DOMAIN, section=kat, content=tekst.strip(), source="kare")
    if fact_id is None:
        return t("nota_write_error", lang, error="knowledge_store write failed")
    return t("nota_noted", lang, note_id=str(fact_id), text=tekst.strip())


def les_notater(kategori: str | None = None, lang: str = "nb") -> str:
    notater = ks.get_all_facts(_DOMAIN)
    if kategori:
        kat = kategori.strip().lower()
        notater = [n for n in notater if n["section"] == kat]
        if not notater:
            return t("nota_no_category", lang, category=kategori)
    if not notater:
        return t("nota_empty", lang)

    suffix = "er" if len(notater) != 1 else ""
    lines = [t("nota_header", lang, count=len(notater), suffix=suffix) + "\n"]
    siste_kat = None
    for n in notater:
        kat = n["section"] or "diverse"
        if kat != siste_kat:
            lines.append(f"\n[{kat.upper()}]")
            siste_kat = kat
        flag = " ⭐" if n.get("important_tag") else ""
        lines.append(f"  [{n['id']}] {n['content']}{flag}")
    return "\n".join(lines)


def slett_notat(notat_id: str, lang: str = "nb") -> str:
    if not notat_id.strip():
        return t("nota_empty_id", lang)
    try:
        fid = int(notat_id.strip())
    except ValueError:
        return t("nota_id_not_found", lang, note_id=notat_id)
    if not ks.retire_fact(fid):
        return t("nota_id_not_found", lang, note_id=notat_id)
    return t("nota_deleted", lang, note_id=notat_id)


def tøm_notater(kategori: str | None = None, lang: str = "nb") -> str:
    if kategori:
        kat = kategori.strip().lower()
        removed_count = ks.retire_all(_DOMAIN, section=kat)
        if removed_count == 0:
            return t("nota_no_category", lang, category=kategori)
        return t("nota_category_cleared", lang, count=removed_count, category=kategori)
    else:
        removed_count = ks.retire_all(_DOMAIN)
        if removed_count == 0:
            return t("nota_already_empty", lang)
        return t("nota_cleared", lang, count=removed_count)


def marker_notat_viktig(notat_id: str, viktig: bool = True, lang: str = "nb") -> str:
    """Flag/unflag a note as important — forces the reflection job to run at the
    next meeting cycle instead of waiting out the week (P105 Fase 3 Frequency
    decision)."""
    if not notat_id.strip():
        return t("nota_empty_id", lang)
    try:
        fid = int(notat_id.strip())
    except ValueError:
        return t("nota_id_not_found", lang, note_id=notat_id)
    if not ks.set_important_tag(fid, viktig):
        return t("nota_id_not_found", lang, note_id=notat_id)
    return t(
        "nota_marked_important" if viktig else "nota_unmarked_important", lang, note_id=notat_id
    )
