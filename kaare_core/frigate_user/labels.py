"""Frigate's object labels in the user's words (run-queue J8, P144 R142).

Frigate names objects in English (`waste_bin`, `license_plate` …). A user says «søppeldunk», «Mülltonne» or «trash», and
the camera tool found nothing. This maps a word to the labels this install's cameras actually track (the camera cards,
Integrations → Frigate), through the display names below (nb/en/de — product vocabulary, like any translation; nothing
about this house). A label with no names here still matches by its own key. Never guesses outside the known labels.
"""

from __future__ import annotations

import re

from kaare_core.domain import frigate_store

# Display names of Frigate's standard and Frigate+ labels, per language; the first name is the one shown.
LABEL_NAMES: dict[str, dict[str, list[str]]] = {
    "person": {"nb": ["person", "menneske", "folk"], "en": ["person", "people", "human"], "de": ["Person", "Mensch"]},
    "car": {"nb": ["bil"], "en": ["car"], "de": ["Auto", "Wagen"]},
    "truck": {"nb": ["lastebil", "varebil"], "en": ["truck", "lorry"], "de": ["Lastwagen", "LKW"]},
    "bus": {"nb": ["buss"], "en": ["bus"], "de": ["Bus"]},  # codespell:ignore buss
    "motorcycle": {"nb": ["motorsykkel"], "en": ["motorcycle", "motorbike"], "de": ["Motorrad"]},
    "bicycle": {"nb": ["sykkel"], "en": ["bicycle", "bike"], "de": ["Fahrrad"]},
    "boat": {"nb": ["båt"], "en": ["boat"], "de": ["Boot"]},
    "license_plate": {"nb": ["skilt", "bilskilt", "registreringsnummer"], "en": ["license plate", "number plate"],
                      "de": ["Kennzeichen", "Nummernschild"]},
    "dog": {"nb": ["hund"], "en": ["dog"], "de": ["Hund"]},
    "cat": {"nb": ["katt"], "en": ["cat"], "de": ["Katze"]},
    "bird": {"nb": ["fugl"], "en": ["bird"], "de": ["Vogel"]},
    "horse": {"nb": ["hest"], "en": ["horse"], "de": ["Pferd"]},
    "deer": {"nb": ["hjort", "rådyr", "elg"], "en": ["deer", "moose"], "de": ["Reh", "Hirsch", "Elch"]},
    "bear": {"nb": ["bjørn"], "en": ["bear"], "de": ["Bär"]},
    "fox": {"nb": ["rev"], "en": ["fox"], "de": ["Fuchs"]},
    "rabbit": {"nb": ["kanin", "hare"], "en": ["rabbit", "hare"], "de": ["Kaninchen", "Hase"]},
    "squirrel": {"nb": ["ekorn"], "en": ["squirrel"], "de": ["Eichhörnchen"]},
    "raccoon": {"nb": ["vaskebjørn"], "en": ["raccoon"], "de": ["Waschbär"]},
    "package": {"nb": ["pakke"], "en": ["package", "parcel"], "de": ["Paket"]},  # codespell:ignore paket
    "waste_bin": {"nb": ["søppeldunk", "søppelkasse", "avfallsdunk"], "en": ["waste bin", "trash can", "bin", "garbage can"],
                  "de": ["Mülltonne", "Abfalleimer"]},
    "face": {"nb": ["ansikt"], "en": ["face"], "de": ["Gesicht"]},
}
_MIN_PART = 3  # a word part this long may match inside a name («dunk» → «søppeldunk»)
_WS = re.compile(r"[\s_\-]+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").strip().casefold())


def known_labels() -> list[str]:
    """Every label a camera card on this install lists (sorted). [] when there are no camera cards."""
    try:
        cams = frigate_store.camera_config().get("cameras") or {}
    except Exception:
        return []
    out: set[str] = set()
    for card in cams.values() if isinstance(cams, dict) else []:
        labels = (card or {}).get("labels") if isinstance(card, dict) else None
        if isinstance(labels, dict):
            out.update(str(k) for k in labels)
    return sorted(out)


def resolve(word: str, known: list[str] | None = None) -> list[str]:
    """The known labels a user's word means — exact label, then a display name in any language, then a word part of at
    least three letters inside a name. [] = no match (the caller lists the known labels)."""
    w = _norm(word)
    if not w:
        return []
    known = known_labels() if known is None else known
    exact = [lb for lb in known if _norm(lb) == w]
    if exact:
        return exact
    names = {lb: [_norm(lb), *(_norm(n) for langs in LABEL_NAMES.get(lb, {}).values() for n in langs)] for lb in known}
    hits = [lb for lb, ns in names.items() if w in ns]
    if hits:
        return hits
    if len(w) >= _MIN_PART:
        return [lb for lb, ns in names.items() if any(w in n or (len(n) >= _MIN_PART and n in w) for n in ns)]
    return []


def display(label: str, lang: str) -> str:
    names = LABEL_NAMES.get(label, {}).get(lang) or LABEL_NAMES.get(label, {}).get("en")
    return names[0] if names else label.replace("_", " ")
