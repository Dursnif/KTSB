"""
Kåres listeystem — tre separate lister med ulik semantikk:
  handle   : felles handleliste for husstanden (state/lister/handleliste.json)
  huske    : per-bruker huskeliste (state/lister/brukere/{user_id}_huske.json)
  kare     : Kåres egne oppfølgingspunkter (state/lister/kare_huske.json)

Alle lister er JSON-arrays. Atomisk skriving via tmp-fil + rename.
"""

import difflib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from kaare_core.config import get_settings as _get_settings
from kaare_core.tools.i18n import t

_LISTER_DIR = Path("/kaare/state/lister")
_HANDLE_PATH = _LISTER_DIR / "handleliste.json"
_KARE_PATH = _LISTER_DIR / "kare_huske.json"


def _huske_path(user_id: str) -> Path:
    return _LISTER_DIR / "brukere" / f"{user_id}_huske.json"


def _les(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _skriv(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _ny_id() -> str:
    return uuid.uuid4().hex[:8]


def _dato() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────
# HANDLELISTE
# ─────────────────────────────────────────────

def handle_legg_til(tekst: str, mengde: str = "", enhet: str = "", lagt_til_av: str = "", lang: str = "nb") -> str:
    if not tekst.strip():
        return t("list_empty_text", lang)
    items = _les(_HANDLE_PATH)
    item = {
        "id": _ny_id(),
        "tekst": tekst.strip(),
        "dato": _dato(),
        "kjøpt": False,
    }
    if mengde.strip():
        item["mengde"] = mengde.strip()
    if enhet.strip():
        item["enhet"] = enhet.strip()
    if lagt_til_av.strip():
        item["lagt_til_av"] = lagt_til_av.strip()
    items.append(item)
    _skriv(_HANDLE_PATH, items)
    mengde_str = f" ({mengde} {enhet})".rstrip() if mengde else ""
    return t("list_added", lang, text=tekst, amount=mengde_str, id=item["id"])


def handle_les(kun_uhandlet: bool = True, lang: str = "nb") -> str:
    items = _les(_HANDLE_PATH)
    if not items:
        return "Handlelisten er tom."
    vis = [i for i in items if not i.get("kjøpt")] if kun_uhandlet else items
    if not vis:
        return t("list_all_bought", lang)
    lines = [f"Handleliste ({len(vis)} vare{'r' if len(vis) != 1 else ''}):\n"]
    for i in vis:
        mengde = f" — {i['mengde']} {i.get('enhet', '')}".rstrip() if i.get("mengde") else ""
        av = f" (lagt til av {i['lagt_til_av']})" if i.get("lagt_til_av") else ""
        lines.append(f"  [{i['id']}] {i['tekst']}{mengde}{av}")
    return "\n".join(lines)


def handle_merk_kjøpt(notat_id: str, lang: str = "nb") -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_HANDLE_PATH)
    for item in items:
        if item.get("id") == notat_id.strip():
            item["kjøpt"] = True
            _skriv(_HANDLE_PATH, items)
            return t("list_marked_bought", lang, text=item["tekst"])
    return t("list_item_not_found", lang, id=notat_id)


def handle_slett(notat_id: str, lang: str = "nb") -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_HANDLE_PATH)
    ny = [i for i in items if i.get("id") != notat_id.strip()]
    if len(ny) == len(items):
        return t("list_item_not_found", lang, id=notat_id)
    _skriv(_HANDLE_PATH, ny)
    return t("list_item_deleted", lang)


def handle_tøm_kjøpte(lang: str = "nb") -> str:
    items = _les(_HANDLE_PATH)
    ny = [i for i in items if not i.get("kjøpt")]
    antall = len(items) - len(ny)
    if antall == 0:
        return t("list_no_bought_to_clear", lang)
    _skriv(_HANDLE_PATH, ny)
    return t("list_cleared_bought", lang, count=antall)


def handle_tøm(lang: str = "nb") -> str:
    antall = len(_les(_HANDLE_PATH))
    _skriv(_HANDLE_PATH, [])
    return t("list_cleared_all", lang, count=antall)


# ─────────────────────────────────────────────
# BRUKER-HUSKELISTE
# ─────────────────────────────────────────────

def huske_husk(tekst: str, user_id: str, påminn_ved_login: bool = False, lang: str = "nb") -> str:
    if not tekst.strip():
        return t("list_empty_text", lang)
    if not user_id or user_id == "global":
        return "Feil: ingen innlogget bruker."
    path = _huske_path(user_id)
    items = _les(path)
    item = {
        "id": _ny_id(),
        "tekst": tekst.strip(),
        "dato": _dato(),
        "ferdig": False,
        "påminn_ved_login": påminn_ved_login,
    }
    items.append(item)
    _skriv(path, items)
    påminn_str = t("list_remind_on_login", lang) if påminn_ved_login else ""
    return t("list_reminder_added", lang, text=tekst, remind=påminn_str, id=item["id"])


def huske_les(user_id: str, kun_aktive: bool = True) -> str:
    if not user_id or user_id == "global":
        return "Feil: ingen innlogget bruker."
    items = _les(_huske_path(user_id))
    if not items:
        return "Huskelisten din er tom."
    vis = [i for i in items if not i.get("ferdig")] if kun_aktive else items
    if not vis:
        return "Ingen aktive huskenotater. Alt er ferdig."
    lines = [f"Din huskeliste ({len(vis)} punkt{'er' if len(vis) != 1 else ''}):\n"]
    for i in vis:
        påminn = " ★" if i.get("påminn_ved_login") else ""
        lines.append(f"  [{i['id']}] {i['dato']} — {i['tekst']}{påminn}")
    return "\n".join(lines)


def huske_ferdig(notat_id: str, user_id: str) -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    if not user_id or user_id == "global":
        return "Feil: ingen innlogget bruker."
    path = _huske_path(user_id)
    items = _les(path)
    for item in items:
        if item.get("id") == notat_id.strip():
            item["ferdig"] = True
            item["påminn_ved_login"] = False
            _skriv(path, items)
            return f"Ferdig: {item['tekst']}"
    return f"Fant ingen huskenotat med id '{notat_id}'."


def huske_slett(notat_id: str, user_id: str) -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    if not user_id or user_id == "global":
        return "Feil: ingen innlogget bruker."
    path = _huske_path(user_id)
    items = _les(path)
    ny = [i for i in items if i.get("id") != notat_id.strip()]
    if len(ny) == len(items):
        return f"Fant ingen huskenotat med id '{notat_id}'."
    _skriv(path, ny)
    return "Huskenotat slettet."


def huske_tøm(user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return "Feil: ingen innlogget bruker."
    path = _huske_path(user_id)
    antall = len(_les(path))
    _skriv(path, [])
    return t("list_reminder_cleared", lang, count=antall)


def huske_hent_påminnelser(user_id: str) -> list[str]:
    """Returnerer tekst for alle items med påminn_ved_login=True — brukes av login-flow."""
    if not user_id or user_id == "global":
        return []
    items = _les(_huske_path(user_id))
    return [i["tekst"] for i in items if i.get("påminn_ved_login") and not i.get("ferdig")]


# ─────────────────────────────────────────────
# KÅRES HUSKELISTE
# ─────────────────────────────────────────────

# Dedup-on-write for Kåres huskeliste: hvor mange av de siste (skriverekkefølge,
# ikke pin-sortert visning) oppføringene som sjekkes for nesten-duplikat, og
# likhetsgrensen (difflib SequenceMatcher-ratio) for å regnes som duplikat.
# Tunet for å fange Jangs nesten-identiske gjentatte observasjoner uten å
# blokkere legitimt beslektede, men ulike, notater.
_KARE_DEDUP_WINDOW = 10
_KARE_DEDUP_THRESHOLD = 0.75


def _kare_settings() -> dict:
    return (_get_settings() or {}).get("kare_reminders", {}) or {}


def _finn_naer_duplikat(items: list, tekst: str) -> dict | None:
    """Se etter en nesten-lik oppføring blant de _KARE_DEDUP_WINDOW siste skrevne
    (fysisk array-rekkefølge). Returnerer selve dict-objektet (samme referanse som
    i items) slik at kalleren kan mutere den i-place, eller None."""
    tekst_l = tekst.lower()
    for item in items[-_KARE_DEDUP_WINDOW:]:
        ratio = difflib.SequenceMatcher(None, tekst_l, item.get("tekst", "").lower()).ratio()
        if ratio >= _KARE_DEDUP_THRESHOLD:
            return item
    return None


def _evict_til_tak(items: list, max_entries: int) -> list:
    """FIFO-evict eldste **upinnede** oppføringer først, til listen er innenfor taket.
    Pinnede oppføringer evictes aldri automatisk."""
    if len(items) <= max_entries:
        return items
    pinnet = [i for i in items if i.get("pinned")]
    upinnet = [i for i in items if not i.get("pinned")]
    plass_til_upinnet = max(0, max_entries - len(pinnet))
    # Behold de nyeste upinnede (kutt fra starten = eldst først, kronologisk skriverekkefølge)
    upinnet = upinnet[-plass_til_upinnet:] if plass_til_upinnet else []
    # Gjenopprett original relativ rekkefølge (pin_forst-sortering skjer uansett ved lesing)
    behold_ids = {i["id"] for i in pinnet} | {i["id"] for i in upinnet}
    return [i for i in items if i["id"] in behold_ids]


def _sorter_pin_forst(items: list) -> list:
    """Pinnede oppføringer først (i sin eksisterende relative rekkefølge), deretter
    resten kronologisk. Stabil sortering — flytter ingenting fysisk på disk."""
    return sorted(items, key=lambda i: not i.get("pinned", False))


def kare_husk(tekst: str, kontekst: str = "", lang: str = "nb") -> str:
    if not tekst.strip():
        return t("list_empty_text", lang)
    tekst = tekst.strip()
    items = _les(_KARE_PATH)

    duplikat = _finn_naer_duplikat(items, tekst)
    if duplikat is not None:
        duplikat["dato"] = _dato()
        if kontekst.strip():
            duplikat["kontekst"] = kontekst.strip()
        _skriv(_KARE_PATH, items)
        return t("list_kare_refreshed", lang, text=duplikat["tekst"], id=duplikat["id"])

    item = {
        "id": _ny_id(),
        "tekst": tekst,
        "dato": _dato(),
        "pinned": False,
    }
    if kontekst.strip():
        item["kontekst"] = kontekst.strip()
    items.append(item)

    max_entries = int(_kare_settings().get("max_entries", 0) or 0)
    if max_entries:
        items = _evict_til_tak(items, max_entries)

    _skriv(_KARE_PATH, items)
    kontekst_str = f" (kontekst: {kontekst})" if kontekst else ""
    return t("list_kare_added", lang, text=tekst, context=kontekst_str, id=item["id"])


def kare_les(søkeord: str = "", fra_dato: str = "", til_dato: str = "") -> str:
    """Full/selektiv lesing av Kåres huskeliste — går utenom det som auto-injiseres.
    søkeord: fritekst-substreng-match mot tekst+kontekst (store/små bokstaver ignoreres).
    fra_dato/til_dato: YYYY-MM-DD, inkluderende grenser."""
    items = _sorter_pin_forst(_les(_KARE_PATH))
    total = len(items)
    filtered = bool(søkeord.strip() or fra_dato.strip() or til_dato.strip())

    if søkeord.strip():
        q = søkeord.strip().lower()
        items = [i for i in items if q in i.get("tekst", "").lower() or q in i.get("kontekst", "").lower()]
    if fra_dato.strip():
        items = [i for i in items if i.get("dato", "") >= fra_dato.strip()]
    if til_dato.strip():
        items = [i for i in items if i.get("dato", "") <= til_dato.strip()]

    if not items:
        return "Ingen treff i min huskeliste." if filtered else "Min huskeliste er tom."
    tittel = f"Treff i min huskeliste ({len(items)} av {total})" if filtered else f"Min huskeliste ({len(items)} punkt{'er' if len(items) != 1 else ''})"
    lines = [f"{tittel}:\n"]
    for i in items:
        kontekst = f" → {i['kontekst']}" if i.get("kontekst") else ""
        pin = " 📌" if i.get("pinned") else ""
        lines.append(f"  [{i['id']}] {i['dato']} — {i['tekst']}{kontekst}{pin}")
    return "\n".join(lines)


def kare_flytt_til_topp(notat_id: str, lang: str = "nb") -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_KARE_PATH)
    for item in items:
        if item.get("id") == notat_id.strip():
            item["pinned"] = True
            _skriv(_KARE_PATH, items)
            return t("list_kare_pinned", lang, text=item["tekst"])
    return t("list_item_not_found", lang, id=notat_id)


def kare_fjern_pin(notat_id: str, lang: str = "nb") -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_KARE_PATH)
    for item in items:
        if item.get("id") == notat_id.strip():
            item["pinned"] = False
            _skriv(_KARE_PATH, items)
            return t("list_kare_unpinned", lang, text=item["tekst"])
    return t("list_item_not_found", lang, id=notat_id)


def kare_ferdig(notat_id: str) -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_KARE_PATH)
    ny = [i for i in items if i.get("id") != notat_id.strip()]
    if len(ny) == len(items):
        return f"Fant ingen punkt med id '{notat_id}'."
    _skriv(_KARE_PATH, ny)
    return "Punkt fjernet fra min huskeliste."


def kare_slett(notat_id: str) -> str:
    return kare_ferdig(notat_id)


def kare_tøm(lang: str = "nb") -> str:
    antall = len(_les(_KARE_PATH))
    _skriv(_KARE_PATH, [])
    return t("list_kare_cleared", lang, count=antall)


def kare_les_for_injeksjon(max_chars: int | None = None, lang: str = "nb") -> str:
    """Kompakt tekst for system-prompt-injeksjon. Tom streng hvis listen er tom.
    Pinnede oppføringer først (se _sorter_pin_forst). Hvis max_chars er satt og
    listen overskrider taket, kappes den ved oppføringsgrense (aldri midt i en
    linje) og en markørlinje med reelt antall vist/totalt legges rett under
    tittelen, slik at Kåre vet at det finnes mer i filen enn det som vises."""
    items = _sorter_pin_forst(_les(_KARE_PATH))
    if not items:
        return ""
    total = len(items)
    entry_lines = []
    for i in items:
        kontekst = f" → {i['kontekst']}" if i.get("kontekst") else ""
        pin = " 📌" if i.get("pinned") else ""
        entry_lines.append(f"- [{i['id']}] {i['dato']} — {i['tekst']}{kontekst}{pin}")

    if max_chars is None:
        shown_lines = entry_lines
    else:
        shown_lines = []
        used = len("# Min huskeliste")
        for line in entry_lines:
            used += len(line) + 1
            if used > max_chars and shown_lines:
                break
            shown_lines.append(line)

    header = ["# Min huskeliste"]
    if len(shown_lines) < total:
        header.append(t("list_kare_injection_capped", lang, shown=len(shown_lines), total=total))
    return "\n".join(header + shown_lines)
