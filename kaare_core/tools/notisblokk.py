# /kaare/kaare_core/tools/notisblokk.py
"""
Kåres notisblokk — midlertidige notater, huskelister og gjøremål.
Separat fra personality_self.md som kun er for selvobservasjoner.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

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


def skriv_notat(tekst: str, kategori: str = "diverse") -> str:
    if not tekst.strip():
        return "Feil: tekst kan ikke være tom."
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
        return f"Notert [{notat['id']}]: {notat['tekst']}"
    except Exception as e:
        return f"Kunne ikke skrive notat: {e}"


def les_notater(kategori: str | None = None) -> str:
    notater = _les_fil()
    if not notater:
        return "Notisblokken er tom."
    if kategori:
        kat = kategori.strip().lower()
        notater = [n for n in notater if n.get("kategori") == kat]
        if not notater:
            return f"Ingen notater i kategori '{kategori}'."

    lines = [f"Notisblokk ({len(notater)} notat{'er' if len(notater) != 1 else ''}):\n"]
    siste_kat = None
    for n in notater:
        kat = n.get("kategori", "diverse")
        if kat != siste_kat:
            lines.append(f"\n[{kat.upper()}]")
            siste_kat = kat
        lines.append(f"  [{n['id']}] {n['dato']} — {n['tekst']}")
    return "\n".join(lines)


def slett_notat(notat_id: str) -> str:
    if not notat_id.strip():
        return "Feil: notat_id kan ikke være tom."
    notater = _les_fil()
    opprinnelig = len(notater)
    notater = [n for n in notater if n.get("id") != notat_id.strip()]
    if len(notater) == opprinnelig:
        return f"Fant ingen notat med id '{notat_id}'."
    try:
        _skriv_fil(notater)
        return f"Notat '{notat_id}' slettet."
    except Exception as e:
        return f"Kunne ikke slette notat: {e}"


def tøm_notater(kategori: str | None = None) -> str:
    notater = _les_fil()
    if not notater:
        return "Notisblokken er allerede tom."
    if kategori:
        kat = kategori.strip().lower()
        antall_før = len(notater)
        notater = [n for n in notater if n.get("kategori") != kat]
        antall_slettet = antall_før - len(notater)
        if antall_slettet == 0:
            return f"Ingen notater i kategori '{kategori}'."
        try:
            _skriv_fil(notater)
            return f"Slettet {antall_slettet} notat(er) fra kategori '{kategori}'."
        except Exception as e:
            return f"Kunne ikke tømme kategori: {e}"
    else:
        antall = len(notater)
        try:
            _skriv_fil([])
            return f"Notisblokken tømt ({antall} notat(er) slettet)."
        except Exception as e:
            return f"Kunne ikke tømme notisblokken: {e}"
