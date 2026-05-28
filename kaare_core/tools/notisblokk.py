# /kaare/kaare_core/tools/notisblokk.py
"""
Kåres notisblokk — midlertidige notater, huskelister og gjøremål.
Separat fra personality_self.md som kun er for selvobservasjoner.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from kaare_core.tools.i18n import t

_NOTATER_PATH = Path("/kaare/state/notater.json")


def _les_fil() -> list:
    if not _NOTATER_PATH.exists():
        return []
    try:
        data = json.loads(_NOTATER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _skriv_fil(notater: list) -> None:
    _NOTATER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _NOTATER_PATH.write_text(
        json.dumps(notater, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def skriv_notat(tekst: str, kategori: str = "diverse", lang: str = "nb") -> str:
    if not tekst.strip():
        return t("nota_empty_text", lang)
    notater = _les_fil()
    notat = {
        "id": uuid.uuid4().hex[:8],
        "dato": datetime.now().strftime("%Y-%m-%d"),
        "kategori": kategori.strip().lower() or "diverse",
        "tekst": tekst.strip(),
    }
    notater.append(notat)
    try:
        _skriv_fil(notater)
        return t("nota_noted", lang, note_id=notat["id"], text=notat["tekst"])
    except Exception as e:
        return t("nota_write_error", lang, error=e)


def les_notater(kategori: str | None = None, lang: str = "nb") -> str:
    notater = _les_fil()
    if not notater:
        return t("nota_empty", lang)
    if kategori:
        kat = kategori.strip().lower()
        notater = [n for n in notater if n.get("kategori") == kat]
        if not notater:
            return t("nota_no_category", lang, category=kategori)

    suffix = "er" if len(notater) != 1 else ""
    lines = [t("nota_header", lang, count=len(notater), suffix=suffix) + "\n"]
    siste_kat = None
    for n in notater:
        kat = n.get("kategori", "diverse")
        if kat != siste_kat:
            lines.append(f"\n[{kat.upper()}]")
            siste_kat = kat
        lines.append(f"  [{n['id']}] {n['dato']} — {n['tekst']}")
    return "\n".join(lines)


def slett_notat(notat_id: str, lang: str = "nb") -> str:
    if not notat_id.strip():
        return t("nota_empty_id", lang)
    notater = _les_fil()
    opprinnelig = len(notater)
    notater = [n for n in notater if n.get("id") != notat_id.strip()]
    if len(notater) == opprinnelig:
        return t("nota_id_not_found", lang, note_id=notat_id)
    try:
        _skriv_fil(notater)
        return t("nota_deleted", lang, note_id=notat_id)
    except Exception as e:
        return t("nota_delete_error", lang, error=e)


def tøm_notater(kategori: str | None = None, lang: str = "nb") -> str:
    notater = _les_fil()
    if not notater:
        return t("nota_already_empty", lang)
    if kategori:
        kat = kategori.strip().lower()
        antall_før = len(notater)
        notater = [n for n in notater if n.get("kategori") != kat]
        antall_slettet = antall_før - len(notater)
        if antall_slettet == 0:
            return t("nota_no_category", lang, category=kategori)
        try:
            _skriv_fil(notater)
            return t("nota_category_cleared", lang, count=antall_slettet, category=kategori)
        except Exception as e:
            return t("nota_category_clear_error", lang, error=e)
    else:
        antall = len(notater)
        try:
            _skriv_fil([])
            return t("nota_cleared", lang, count=antall)
        except Exception as e:
            return t("nota_clear_error", lang, error=e)
