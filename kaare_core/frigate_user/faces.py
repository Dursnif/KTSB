"""Face classification + attribute profiles for the Frigate user (P144-s05, N19 point 6).

Each face in Frigate's face library gets one class, set by an admin in Integrations → Frigate:
  household   — name in all tiers under consent (s02), counts as home;
  safe_guest  — name in all tiers, no unknown-person alert;
  not_welcome — name in all tiers, always alerts (worded as Frigate's claim, never an automatic action).
A face nobody has classified is «unclassified»: treated as unknown, its name kept ``unclassified_name_days``.
People Frigate does not know at all are «unknown» — they never reach this module.

The attribute profile (beard, glasses, age band, headwear) is what the shadow pipe (s06) compares a single image's
fields with to find contradictions in Frigate's claim. «unknown» is always allowed.

Stored in ``configs/frigate_faces.yaml`` (personal, gitignored; written only through config_store). Household faces
link to a KTSB user in users.db (s02) — that link is not copied here.

Readers never raise: a missing or broken file, an odd name or a value outside the fixed sets reads as
``unclassified`` / ``unknown``. Writers raise ValueError on anything outside the sets (fail closed).
"""

import logging
from pathlib import Path

from kaare_core import config_store

log = logging.getLogger(__name__)

CONFIG_PATH = config_store.CONFIG_DIR / "frigate_faces.yaml"

CLASSES = ("household", "safe_guest", "not_welcome")
UNCLASSIFIED = "unclassified"
UNKNOWN = "unknown"
ATTRIBUTES: dict[str, tuple[str, ...]] = {
    # Beard type (2026-10-26). "yes" = legacy «some beard, type not chosen», still accepted.
    "beard": ("no", "moustache", "circle_beard", "full", "yes", UNKNOWN),
    "glasses": ("yes", "sometimes", "no", UNKNOWN),
    "age_band": ("child", "teen", "adult", "senior", UNKNOWN),
    "headwear_often": ("yes", "no", UNKNOWN),
}
DEFAULT_NAME_DAYS = 7
MAX_NAME_DAYS = 90
MAX_NAME_LEN = 100
# Frigate's own folder of not-yet-assigned training images — not a person.
FRIGATE_TRAIN_FOLDER = "train"


def _path() -> Path:
    return Path(CONFIG_PATH)


def _data() -> dict:
    try:
        data = config_store.read_yaml_cached(_path(), copy_result=False)
    except Exception as e:
        log.warning("frigate faces: could not read %s: %s", _path().name, e)
        return {}
    return data if isinstance(data, dict) else {}


def _faces() -> dict:
    faces = _data().get("faces")
    return faces if isinstance(faces, dict) else {}


def _entry(face_name) -> dict:
    if not isinstance(face_name, str) or not face_name.strip():
        return {}
    wanted = face_name.strip().casefold()
    for name, entry in _faces().items():
        if isinstance(name, str) and name.casefold() == wanted:
            return entry if isinstance(entry, dict) else {}
    return {}


def _norm_value(value) -> str:
    # A hand-edited `beard: yes` is a YAML boolean to PyYAML.
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return value.strip().lower() if isinstance(value, str) else ""


def classify(face_name) -> str:
    """One of CLASSES, or ``unclassified``. Never raises."""
    try:
        cls = _norm_value(_entry(face_name).get("class"))
        return cls if cls in CLASSES else UNCLASSIFIED
    except Exception:
        return UNCLASSIFIED


def attribute_profile(face_name) -> dict:
    """All four attribute fields, ``unknown`` where not set or not valid. Never raises."""
    profile = dict.fromkeys(ATTRIBUTES, UNKNOWN)
    try:
        stored = _entry(face_name).get("attributes")
        if isinstance(stored, dict):
            for field, allowed in ATTRIBUTES.items():
                v = _norm_value(stored.get(field))
                if v in allowed:
                    profile[field] = v
    except Exception:
        pass
    return profile


def unclassified_faces(library) -> list[str]:
    """Names from Frigate's face library that have no class (Frigate's «train» folder skipped). Never raises."""
    try:
        return [
            n for n in library
            if isinstance(n, str) and n != FRIGATE_TRAIN_FOLDER and classify(n) == UNCLASSIFIED
        ]
    except Exception:
        return []


def unclassified_name_days() -> int:
    """How long an unclassified face's name is kept (days, 0–90). Never raises."""
    v = _data().get("unclassified_name_days")
    if isinstance(v, bool) or not isinstance(v, int):
        return DEFAULT_NAME_DAYS
    return max(0, min(MAX_NAME_DAYS, v))


def configured_faces() -> list[str]:
    """Face names that have an entry in the config (for listing them while Frigate is down)."""
    return [n for n in _faces() if isinstance(n, str)]


# ── writers (admin API only) ──────────────────────────────────────────────────


def validate_name(name) -> str:
    if not isinstance(name, str):
        raise ValueError("face name must be text")
    n = name.strip()
    if not n or len(n) > MAX_NAME_LEN or n in (".", "..") or "/" in n or "\\" in n or n == FRIGATE_TRAIN_FOLDER:
        raise ValueError("invalid face name")
    return n


def validate(cls, attributes) -> tuple[str, dict]:
    """(class, attributes without `unknown` fields). ValueError on anything outside the fixed sets."""
    if cls not in (*CLASSES, UNCLASSIFIED):
        raise ValueError(f"class must be one of {', '.join((*CLASSES, UNCLASSIFIED))}")
    if not isinstance(attributes, dict):
        raise ValueError("attributes must be a mapping")
    out = {}
    for field, value in attributes.items():
        if field not in ATTRIBUTES:
            raise ValueError(f"unknown attribute: {field}")
        if not isinstance(value, str) or value not in ATTRIBUTES[field]:
            raise ValueError(f"{field} must be one of {', '.join(ATTRIBUTES[field])}")
        if value != UNKNOWN:
            out[field] = value
    return cls, out


def set_face(name, cls, attributes) -> None:
    """Store one face's class + attributes. An entry with no class and no known attribute is removed."""
    name = validate_name(name)
    cls, attrs = validate(cls, attributes)

    def _apply(data):
        if not isinstance(data.get("faces"), dict):
            data["faces"] = {}
        faces = data["faces"]
        # One entry per face, matched like the readers (case-insensitive); keep the existing key's spelling.
        key = next((k for k in faces if isinstance(k, str) and k.casefold() == name.casefold()), name)
        if cls == UNCLASSIFIED and not attrs:
            faces.pop(key, None)
            return
        entry = {}
        if cls != UNCLASSIFIED:
            entry["class"] = cls
        if attrs:
            entry["attributes"] = attrs
        faces[key] = entry

    config_store.update_yaml(_path(), _apply, "frigate face classification")


def set_unclassified_name_days(days) -> None:
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_NAME_DAYS:
        raise ValueError(f"unclassified_name_days must be a whole number 0–{MAX_NAME_DAYS}")

    def _apply(data):
        data["unclassified_name_days"] = days

    config_store.update_yaml(_path(), _apply, "frigate unclassified name days")
