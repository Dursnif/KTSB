"""
Kåres listeystem — tre separate lister med ulik semantikk:
  handle   : felles handleliste for husstanden (state/lister/handleliste.json)
  huske    : per-bruker huskeliste (state/lister/brukere/{user_id}_huske.json)
  kare     : Kåres egne oppfølgingspunkter (state/lister/kare_huske.json)

Alle lister er JSON-arrays. Atomisk skriving via tmp-fil + rename.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

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

def handle_legg_til(tekst: str, mengde: str = "", enhet: str = "", lagt_til_av: str = "") -> str:
    if not tekst.strip():
        return "Feil: tekst kan ikke være tom."
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
    return f"Lagt til på handlelisten: {tekst}{mengde_str} [{item['id']}]"


def handle_les(kun_uhandlet: bool = True) -> str:
    items = _les(_HANDLE_PATH)
    if not items:
        return "Handlelisten er tom."
    vis = [i for i in items if not i.get("kjøpt")] if kun_uhandlet else items
    if not vis:
        return "Alt er allerede kjøpt. Bruk tøm_kjøpte for å rydde listen."
    lines = [f"Handleliste ({len(vis)} vare{'r' if len(vis) != 1 else ''}):\n"]
    for i in vis:
        mengde = f" — {i['mengde']} {i.get('enhet', '')}".rstrip() if i.get("mengde") else ""
        av = f" (lagt til av {i['lagt_til_av']})" if i.get("lagt_til_av") else ""
        lines.append(f"  [{i['id']}] {i['tekst']}{mengde}{av}")
    return "\n".join(lines)


def handle_merk_kjøpt(notat_id: str) -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_HANDLE_PATH)
    for item in items:
        if item.get("id") == notat_id.strip():
            item["kjøpt"] = True
            _skriv(_HANDLE_PATH, items)
            return f"Merket som kjøpt: {item['tekst']}"
    return f"Fant ingen vare med id '{notat_id}'."


def handle_slett(notat_id: str) -> str:
    if not notat_id.strip():
        return "Feil: id mangler."
    items = _les(_HANDLE_PATH)
    ny = [i for i in items if i.get("id") != notat_id.strip()]
    if len(ny) == len(items):
        return f"Fant ingen vare med id '{notat_id}'."
    _skriv(_HANDLE_PATH, ny)
    return f"Vare slettet fra handlelisten."


def handle_tøm_kjøpte() -> str:
    items = _les(_HANDLE_PATH)
    ny = [i for i in items if not i.get("kjøpt")]
    antall = len(items) - len(ny)
    if antall == 0:
        return "Ingen kjøpte varer å rydde."
    _skriv(_HANDLE_PATH, ny)
    return f"Fjernet {antall} kjøpte vare(r) fra handlelisten."


def handle_tøm() -> str:
    antall = len(_les(_HANDLE_PATH))
    _skriv(_HANDLE_PATH, [])
    return f"Handlelisten tømt ({antall} vare(r) slettet)."


# ─────────────────────────────────────────────
# BRUKER-HUSKELISTE
# ─────────────────────────────────────────────

def huske_husk(tekst: str, user_id: str, påminn_ved_login: bool = False) -> str:
    if not tekst.strip():
        return "Feil: tekst kan ikke være tom."
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
    påminn_str = " (påminnelse ved innlogging)" if påminn_ved_login else ""
    return f"Notert på huskelisten din: {tekst}{påminn_str} [{item['id']}]"


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


def huske_tøm(user_id: str) -> str:
    if not user_id or user_id == "global":
        return "Feil: ingen innlogget bruker."
    path = _huske_path(user_id)
    antall = len(_les(path))
    _skriv(path, [])
    return f"Huskelisten tømt ({antall} punkt(er) slettet)."


def huske_hent_påminnelser(user_id: str) -> list[str]:
    """Returnerer tekst for alle items med påminn_ved_login=True — brukes av login-flow."""
    if not user_id or user_id == "global":
        return []
    items = _les(_huske_path(user_id))
    return [i["tekst"] for i in items if i.get("påminn_ved_login") and not i.get("ferdig")]


# ─────────────────────────────────────────────
# KÅRES HUSKELISTE
# ─────────────────────────────────────────────

def kare_husk(tekst: str, kontekst: str = "") -> str:
    if not tekst.strip():
        return "Feil: tekst kan ikke være tom."
    items = _les(_KARE_PATH)
    item = {
        "id": _ny_id(),
        "tekst": tekst.strip(),
        "dato": _dato(),
    }
    if kontekst.strip():
        item["kontekst"] = kontekst.strip()
    items.append(item)
    _skriv(_KARE_PATH, items)
    kontekst_str = f" (kontekst: {kontekst})" if kontekst else ""
    return f"Notert på min egen huskeliste: {tekst}{kontekst_str} [{item['id']}]"


def kare_les() -> str:
    items = _les(_KARE_PATH)
    if not items:
        return "Min huskeliste er tom."
    lines = [f"Min huskeliste ({len(items)} punkt{'er' if len(items) != 1 else ''}):\n"]
    for i in items:
        kontekst = f" → {i['kontekst']}" if i.get("kontekst") else ""
        lines.append(f"  [{i['id']}] {i['dato']} — {i['tekst']}{kontekst}")
    return "\n".join(lines)


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


def kare_tøm() -> str:
    antall = len(_les(_KARE_PATH))
    _skriv(_KARE_PATH, [])
    return f"Min huskeliste tømt ({antall} punkt(er) slettet)."


def kare_les_for_injeksjon() -> str:
    """Kompakt tekst for system-prompt-injeksjon. Tom streng hvis listen er tom."""
    items = _les(_KARE_PATH)
    if not items:
        return ""
    lines = ["# Min huskeliste"]
    for i in items:
        kontekst = f" → {i['kontekst']}" if i.get("kontekst") else ""
        lines.append(f"- [{i['id']}] {i['dato']} — {i['tekst']}{kontekst}")
    return "\n".join(lines)
