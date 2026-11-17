"""Vehicles — the same path unknown people already have, replicated for licence plates (P150 6b, 2026-11-14:
«anonym id på ikke kjente … samme som ukjente mennesker i frigate.. en måte/en vei» → «menneske og frigate er bygget.. du
skal replikere»).

  people (faces.py, built)                     vehicles (this module)
  Frigate's face library                       verified plates in frigate.db, one `vehicle-<hex>` per plate
  frigate_faces.yaml: class per face           frigate_vehicles.yaml: class + household name per vehicle id
  «Ansiktsklassifisering» (Frigate tab)        the «Hvem / Hva / Hvor» card's vehicle part (Frigate tab)
  unclassified names dropped after N days      unknown plate text dropped after N days — the id stays

A known plate keeps the name Frigate gives it (its known plates, e.g. «Tilhenger»). An unknown plate gets an id only when
it is verified (`verified_plate`): Frigate's LPR and the blind LLM read agree, or a human confirmed. The blind read is
R153 (P144, main session); until it exists nothing counts as verified — no id from noise («ta vare på kun det
som er verifisert skilt.. ikke alt støy»). The plate text never leaves the store; the GUI and Kåre see the id, the
picture and the household name only.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from kaare_core import config_store
from kaare_core.frigate_user import faces, pseudonyms, store

log = logging.getLogger(__name__)

CONFIG_PATH = config_store.CONFIG_DIR / "frigate_vehicles.yaml"
CLASSES = faces.CLASSES  # household / safe_guest / not_welcome — one set for people and vehicles
UNCLASSIFIED = faces.UNCLASSIFIED
MAX_NAME_LEN = 100
DEFAULT_QUESTION_DAYS = 3  # an unknown vehicle seen on this many household days becomes a question
_ID_RE = re.compile(rf"^{re.escape(pseudonyms.VEHICLE_PREFIX)}[0-9a-f]{{6}}$")


def _path() -> Path:
    return Path(CONFIG_PATH)


def _data() -> dict:
    try:
        data = config_store.read_yaml_cached(_path(), copy_result=False)
    except Exception as e:
        log.warning("frigate vehicles: could not read %s: %s", _path().name, e)
        return {}
    return data if isinstance(data, dict) else {}


def _vehicles() -> dict:
    v = _data().get("vehicles")
    return v if isinstance(v, dict) else {}


def _entry(vid: str) -> dict:
    e = _vehicles().get(vid)
    return e if isinstance(e, dict) else {}


# ── verification + id ─────────────────────────────────────────────────────────

def verified_plate(event) -> bool:
    """Is this plate read verified? Until the blind LLM read (R153) exists: never — so no plate gets an id yet."""
    return False


def id_for(plate: str | None, *, plate_known: bool | None, event) -> str | None:
    """The `vehicle-<hex>` of an unknown, verified plate; None for a known plate (it has Frigate's name), no plate, or an
    unverified read (noise)."""
    if not plate or plate_known:
        return None
    try:
        return pseudonyms.vehicle_id(plate) if verified_plate(event) else None
    except Exception as e:
        log.warning("frigate vehicles: no id (%s)", e)
        return None


# ── classification + household name (like faces) ──────────────────────────────

def classify(vid: str) -> str:
    cls = _entry(vid).get("class")
    return cls if cls in CLASSES else UNCLASSIFIED


def name_of(vid: str) -> str:
    n = _entry(vid).get("name")
    return n if isinstance(n, str) else ""


def skipped(vid: str) -> bool:
    return _entry(vid).get("skipped") is True


def validate(vid, cls, name) -> tuple[str, str, str]:
    if not isinstance(vid, str) or not _ID_RE.match(vid):
        raise ValueError("not a vehicle id")
    if cls not in (*CLASSES, UNCLASSIFIED):
        raise ValueError(f"class must be one of {', '.join((*CLASSES, UNCLASSIFIED))}")
    if not isinstance(name, str):
        raise ValueError("name must be text")
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        raise ValueError(f"name is at most {MAX_NAME_LEN} characters")
    return vid, cls, name


def set_vehicle(vid, cls, name) -> None:
    """Store one vehicle's class + household name; an entry with neither is removed (back to an open question)."""
    vid, cls, name = validate(vid, cls, name)

    def _apply(data):
        if not isinstance(data.get("vehicles"), dict):
            data["vehicles"] = {}
        if cls == UNCLASSIFIED and not name:
            data["vehicles"].pop(vid, None)
            return
        entry: dict = {}
        if cls != UNCLASSIFIED:
            entry["class"] = cls
        if name:
            entry["name"] = name
        data["vehicles"][vid] = entry

    config_store.update_yaml(_path(), _apply, "frigate vehicle classification")


def skip(vid) -> None:
    """«Hopp over»: the question goes away; the vehicle stays listed (like the other question cards' «skip»)."""
    if not isinstance(vid, str) or not _ID_RE.match(vid):
        raise ValueError("not a vehicle id")

    def _apply(data):
        if not isinstance(data.get("vehicles"), dict):
            data["vehicles"] = {}
        data["vehicles"][vid] = {**(data["vehicles"].get(vid) or {}), "skipped": True}

    config_store.update_yaml(_path(), _apply, "frigate vehicle question skipped")


def question_days() -> int:
    v = _data().get("question_days", DEFAULT_QUESTION_DAYS)
    return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 365 else DEFAULT_QUESTION_DAYS


def set_question_days(days) -> None:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("question_days must be a whole number 1–365")

    def _apply(data):
        data["question_days"] = days

    config_store.update_yaml(_path(), _apply, "frigate vehicle question days")


# ── the list and the questions ────────────────────────────────────────────────

def rows() -> list[dict]:
    """Every vehicle id the store has, with class, household name and counts — never a plate text."""
    return [{**v, "class": classify(v["vehicle_id"]), "name": name_of(v["vehicle_id"]),
             "skipped": skipped(v["vehicle_id"])} for v in store.vehicle_library()]


def questions() -> list[dict]:
    """Open «Hvilket kjøretøy er dette?» questions: unknown vehicles seen on at least `question_days` household days,
    not yet classified, named or skipped. Newest first."""
    need = question_days()
    out = [r for r in rows() if r["days_seen"] >= need and r["class"] == UNCLASSIFIED and not r["name"]
           and not r["skipped"]]
    return sorted(out, key=lambda r: r["last_seen"], reverse=True)
