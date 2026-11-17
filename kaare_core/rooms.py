"""Place register (P137): the one answer to «which places exist» and «what is where», for every consumer.

Two inputs, both read with an mtime cache (no lookups in the request path):
- `configs/rooms.yaml` — written by the GUI (Rom og aliaser → Rom): KTSB's own places and floors, KTSB synonyms and
  «hidden» on top of HA places, the things the user registered (any source), their location notes, import mode.
- `state/ha_registry.json` — a snapshot of Home Assistant's area/floor/label/device/entity registries, written by
  the HA sync (kaare_core/ha_registry_sync.py). Missing when HA is not connected; everything still works.

Rules (P137 decisions 6–21):
- Nothing has to come from HA. A place is `ha` (an HA area) or `manual`; a thing is `ha`, `mqtt`, `node`, `frigate`
  or `manual`. A bare `domain.name` reference means an HA entity; other sources use a prefix (`mqtt:…`, `node:…`).
- HA names are shown as they are and count as aliases; KTSB only adds synonyms, aliases and a location note.
- A thing from HA takes its place from HA (entity area, else its device's area); other things use their KTSB place.
- A word that names two places resolves to nothing (and is logged once) — never «last one wins».
- A manual place whose id is exactly an HA area id is that HA place (its KTSB synonyms stay); similar names are only
  offered for linking in the GUI, never joined here.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kaare_core import config_store as cs

_log = logging.getLogger(__name__)

ROOMS_PATH = cs.CONFIG_DIR / "rooms.yaml"
NODES_PATH = cs.CONFIG_DIR / "nodes.yaml"
HA_CACHE_PATH = Path("/kaare/state/ha_registry.json")

IMPORT_MODES = ("rooms_only", "pick", "assist", "label", "all")
DEFAULT_IMPORT_MODE = "rooms_only"
THING_SOURCES = ("ha", "mqtt", "node", "frigate", "manual")
_PREFIXED_SOURCES = ("mqtt", "node", "frigate", "manual")
_HA_REF = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


# ── text normalisation ────────────────────────────────────────────────────────


def norm(text: str) -> str:
    """Lower case, `_` as space, single spaces, trimmed — the form every lookup compares."""
    return re.sub(r"\s+", " ", str(text or "").replace("_", " ")).strip().casefold()


def fold(text: str) -> str:
    """norm() without diacritics (kjøkken ≈ kjokken) — only for «similar name» hints, never for resolving."""
    t = norm(text).replace("ø", "o").replace("æ", "ae").replace("å", "a")
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def source_of(ref: str) -> str:
    """Source of a thing reference: `mqtt:…` → mqtt, `light.x` → ha, anything else → manual."""
    head, sep, _rest = str(ref).partition(":")
    if sep and head in _PREFIXED_SOURCES:
        return head
    return "ha" if _HA_REF.match(str(ref)) else "manual"


def domain_of(ref: str) -> str:
    return str(ref).split(".", 1)[0] if source_of(ref) == "ha" else ""


# ── data model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Floor:
    id: str
    name: str
    source: str  # ha | manual
    level: int | None = None
    synonyms: tuple[str, ...] = ()  # KTSB synonyms
    ha_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    source: str  # ha | manual
    floor: str = ""
    synonyms: tuple[str, ...] = ()  # KTSB synonyms
    ha_aliases: tuple[str, ...] = ()
    hidden: bool = False


@dataclass(frozen=True)
class Thing:
    ref: str
    source: str
    name: str
    place: str  # place id, "" = none
    note: str = ""  # location description inside the place
    registered: bool = True  # chosen by the user (explicitly or through the import mode)


@dataclass
class Register:
    floors: dict[str, Floor] = field(default_factory=dict)
    places: dict[str, Place] = field(default_factory=dict)  # in display order
    things: dict[str, Thing] = field(default_factory=dict)  # registered things only
    ha_entities: dict[str, dict] = field(default_factory=dict)  # snapshot rows (all non-disabled HA entities)
    import_mode: str = DEFAULT_IMPORT_MODE
    ha_connected: bool = False
    place_terms: dict[str, str] = field(default_factory=dict)  # norm(term) → place id
    floor_terms: dict[str, str] = field(default_factory=dict)  # norm(term) → floor id
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # norm(term) → ids it would name

    # ── lookups ──

    def resolve(self, text: str) -> tuple[str, str] | None:
        """('place', id) or ('floor', id) for a word/phrase that names exactly one of them; else None."""
        t = norm(text)
        if not t or t in self.ambiguous:
            return None
        if t in self.place_terms:
            return ("place", self.place_terms[t])
        if t in self.floor_terms:
            return ("floor", self.floor_terms[t])
        return None

    def resolve_place(self, text: str) -> str | None:
        hit = self.resolve(text)
        return hit[1] if hit and hit[0] == "place" else None

    def find_in_text(self, text: str) -> tuple[str, str] | None:
        """The longest place/floor term found as whole words in a sentence («skru av lyset i stua» → stue)."""
        padded = f" {norm(re.sub(r'[^\w\s]', ' ', str(text or '')))} "
        best: tuple[int, tuple[str, str]] | None = None
        for term, pid in self.place_terms.items():
            if f" {term} " in padded and (best is None or len(term) > best[0]):
                best = (len(term), ("place", pid))
        for term, fid in self.floor_terms.items():
            if f" {term} " in padded and (best is None or len(term) > best[0]):
                best = (len(term), ("floor", fid))
        if best and any(f" {a} " in padded and len(a) >= best[0] for a in self.ambiguous):
            return None  # an equally long or longer ambiguous term is in the sentence — do not guess
        return best[1] if best else None

    def place_of(self, ref: str) -> str:
        thing = self.things.get(ref)
        if thing:
            return thing.place
        row = self.ha_entities.get(ref)
        return (row or {}).get("area_id") or ""

    def places_on_floor(self, floor_id: str) -> list[str]:
        return [p.id for p in self.places.values() if p.floor == floor_id]

    def things_at(self, place: str = "", floor: str = "", domain: str = "") -> list[Thing]:
        """Registered things at a place, or on every place of a floor; optional HA domain filter."""
        ids = {place} if place else set(self.places_on_floor(floor)) if floor else set()
        return [
            t for t in self.things.values()
            if t.place in ids and (not domain or domain_of(t.ref) == domain)
        ]

    def unregistered_at(self, place: str = "", floor: str = "", domain: str = "") -> int:
        """How many user-facing HA entities HA has there that the user has not registered."""
        ids = {place} if place else set(self.places_on_floor(floor)) if floor else set()
        return sum(
            1 for eid, row in self.ha_entities.items()
            if row.get("area_id") in ids and eid not in self.things and _user_facing(row)
            and (not domain or domain_of(eid) == domain)
        )

    def name_of(self, ref: str) -> str:
        thing = self.things.get(ref)
        if thing:
            return thing.name
        return (self.ha_entities.get(ref) or {}).get("name") or ref

    def label_of_place(self, place_id: str) -> str:
        p = self.places.get(place_id)
        return p.name if p else place_id.replace("_", " ")


def _user_facing(row: dict) -> bool:
    return not row.get("category") and not row.get("hidden") and not row.get("orphan")


def missing_in_ha(reg: Register, ref: str) -> str:
    """'' when fine; 'gone' when an HA reference names nothing HA has; 'orphan' when HA only keeps a leftover of it
    (no longer provided by its integration). Non-HA references are never missing in HA."""
    if source_of(ref) != "ha" or not reg.ha_connected:
        return ""
    row = reg.ha_entities.get(ref)
    if row is None:
        return "gone"
    return "orphan" if row.get("orphan") else ""


# ── building ──────────────────────────────────────────────────────────────────


def _str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple | set):
        return ()
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return tuple(out)


def _imported(mode: str, settings: dict, row: dict) -> bool:
    """Whether an HA entity counts as registered through the import mode (not by an explicit choice)."""
    if not row.get("area_id") or not _user_facing(row):
        return False
    if mode == "assist":
        return bool(row.get("exposed"))
    if mode == "label":
        label = str(settings.get("import_label") or "").strip()
        return bool(label) and (label in (row.get("labels") or []) or label in (row.get("area_labels") or []))
    if mode == "all":
        domains = set(_str_list(settings.get("import_domains")))
        return not domains or row.get("domain") in domains
    return False  # rooms_only, pick: only what the user ticked


def build(rooms: dict, ha: dict) -> Register:
    """Pure: the effective register from rooms.yaml data and the HA snapshot (either may be empty)."""
    rooms = rooms if isinstance(rooms, dict) else {}
    ha = ha if isinstance(ha, dict) else {}
    settings = rooms.get("settings") if isinstance(rooms.get("settings"), dict) else {}
    mode = settings.get("import_mode") if settings.get("import_mode") in IMPORT_MODES else DEFAULT_IMPORT_MODE
    reg = Register(import_mode=mode, ha_connected=bool(ha.get("areas") or ha.get("entities")))
    ov_places = rooms.get("places") if isinstance(rooms.get("places"), dict) else {}
    ov_floors = rooms.get("floors") if isinstance(rooms.get("floors"), dict) else {}

    # Floors: HA first (HA order), then KTSB's own.
    for fid, f in (ha.get("floors") or {}).items():
        ov = ov_floors.get(fid) if isinstance(ov_floors.get(fid), dict) else {}
        reg.floors[fid] = Floor(
            id=fid, name=str(f.get("name") or fid), source="ha", level=f.get("level"),
            synonyms=_str_list(ov.get("synonyms")), ha_aliases=_str_list(f.get("aliases")),
        )
    for fid, ov in ov_floors.items():
        if fid in reg.floors or not isinstance(ov, dict):
            continue
        lvl = ov.get("level")
        reg.floors[str(fid)] = Floor(
            id=str(fid), name=str(ov.get("name") or fid), source="manual",
            level=lvl if isinstance(lvl, int) else None, synonyms=_str_list(ov.get("synonyms")),
        )

    # Places: HA areas first (HA order) with KTSB's overlay, then KTSB's own places.
    ha_areas = ha.get("areas") or {}
    for aid, a in ha_areas.items():
        ov = ov_places.get(aid) if isinstance(ov_places.get(aid), dict) else {}
        reg.places[aid] = Place(
            id=aid, name=str(a.get("name") or aid), source="ha", floor=str(a.get("floor_id") or ""),
            synonyms=_str_list(ov.get("synonyms")), ha_aliases=_str_list(a.get("aliases")),
            hidden=bool(ov.get("hidden")),
        )
    for pid, ov in ov_places.items():
        pid = str(pid)
        if pid in reg.places or not isinstance(ov, dict):
            continue
        reg.places[pid] = Place(
            id=pid, name=str(ov.get("name") or pid.replace("_", " ")), source="manual",
            floor=str(ov.get("floor") or ""), synonyms=_str_list(ov.get("synonyms")), hidden=bool(ov.get("hidden")),
        )

    # Things: explicit registrations, then what the import mode brings in from HA.
    reg.ha_entities = {str(k): v for k, v in (ha.get("entities") or {}).items() if isinstance(v, dict)}
    for ref, t in (rooms.get("things") or {}).items():
        ref = str(ref)
        t = t if isinstance(t, dict) else {}
        src = source_of(ref)
        row = reg.ha_entities.get(ref) if src == "ha" else None
        place = (row or {}).get("area_id") or str(t.get("place") or "")  # HA wins for HA things (decision 18)
        reg.things[ref] = Thing(
            ref=ref, source=src, name=str((row or {}).get("name") or t.get("name") or ref), place=place,
            note=str(t.get("note") or ""),
        )
    for eid, row in reg.ha_entities.items():
        if eid not in reg.things and _imported(mode, settings, row):
            reg.things[eid] = Thing(ref=eid, source="ha", name=str(row.get("name") or eid), place=row["area_id"])

    _index_terms(reg)
    return reg


def _index_terms(reg: Register) -> None:
    """Places and floors are indexed separately: a word may name one place *and* one floor (HA's «Loft» area on
    the «Loft» floor) — resolve() then prefers the place. Only two places, or two floors, sharing a word is
    ambiguous."""
    for items, target in (
        ([p for p in reg.places.values() if not p.hidden], reg.place_terms),
        (list(reg.floors.values()), reg.floor_terms),
    ):
        claims: dict[str, set[str]] = {}
        for item in items:
            for term in (item.id, item.name, *item.synonyms, *item.ha_aliases):
                t = norm(term)
                if t:
                    claims.setdefault(t, set()).add(item.id)
        for term, owners in claims.items():
            if len(owners) == 1:
                target[term] = next(iter(owners))
            else:
                reg.ambiguous[term] = sorted(owners)
                target.pop(term, None)


def collisions(reg: Register) -> dict[str, list[str]]:
    """Terms that name more than one place/floor — the GUI refuses to save these (P137 P6)."""
    return dict(reg.ambiguous)


def similar_places(reg: Register) -> list[tuple[str, str]]:
    """(manual place id, HA place id) pairs with the same name apart from case/diacritics — offered for linking."""
    ha = {fold(p.name): p.id for p in reg.places.values() if p.source == "ha"}
    ha.update({fold(p.id): p.id for p in reg.places.values() if p.source == "ha"})
    out = []
    for p in reg.places.values():
        if p.source != "manual":
            continue
        hit = ha.get(fold(p.name)) or ha.get(fold(p.id))
        if hit:
            out.append((p.id, hit))
    return out


# ── cached access ─────────────────────────────────────────────────────────────

_cache: dict[str, Any] = {"sig": None, "reg": None}
_cache_lock = threading.Lock()
_warned: set[str] = set()


def _sig(path: Path) -> tuple | None:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_ino, st.st_size)
    except OSError:
        return None


def read_ha_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        _log.warning("[ROOMS] %s unreadable — HA places ignored", path, exc_info=True)
        return {}


def get_register(rooms_path: Path = ROOMS_PATH, ha_path: Path = HA_CACHE_PATH) -> Register:
    """The effective register, rebuilt only when rooms.yaml or the HA snapshot changed."""
    sig = (str(rooms_path), _sig(rooms_path), str(ha_path), _sig(ha_path))
    with _cache_lock:
        if _cache["sig"] == sig and _cache["reg"] is not None:
            return _cache["reg"]
    try:
        rooms = cs.read_yaml_cached(rooms_path)
    except Exception:
        _log.warning("[ROOMS] %s unreadable — using no KTSB places", rooms_path, exc_info=True)
        rooms = {}
    reg = build(rooms, read_ha_cache(ha_path))
    for term, ids in reg.ambiguous.items():
        if term not in _warned:
            _warned.add(term)
            _log.warning("[ROOMS] '%s' names %s — it resolves to nothing until one is renamed", term, ids)
    with _cache_lock:
        _cache["sig"], _cache["reg"] = sig, reg
    return reg


def node_place(node_id: str, nodes_path: Path = NODES_PATH, reg: Register | None = None) -> tuple[str, str]:
    """(place id, location note) of a node: its `place` field, else its legacy free-text `room` resolved through
    the register. ("", "") when the node has none or the text names no single place."""
    reg = reg or get_register()
    try:
        nodes = (cs.read_yaml_cached(nodes_path, copy_result=False) or {}).get("nodes") or {}
    except Exception:
        return ("", "")
    node = nodes.get(node_id) if isinstance(nodes, dict) else None
    if not isinstance(node, dict):
        return ("", "")
    note = str(node.get("location_note") or "")
    place = str(node.get("place") or "")
    if place and place in reg.places:
        return (place, note)
    legacy = str(node.get("room") or "")
    return (reg.resolve_place(legacy) or "", note)


def link_place(rooms_data: dict, manual_id: str, ha_id: str) -> dict:
    """Pure: make KTSB's own place `manual_id` part of the HA place `ha_id` (GUI «koble til HA-rom»). Its synonyms
    (and its own name, when it differs from the id) move onto the HA place, things placed there move with it, and
    the manual entry goes. Returns the changed copy; the input is not touched."""
    data = json.loads(json.dumps(rooms_data or {}))
    places = data.setdefault("places", {})
    src = places.pop(manual_id, None)
    if not isinstance(src, dict):
        return data
    dst = places.setdefault(ha_id, {})
    if not isinstance(dst, dict):
        dst = places[ha_id] = {}
    merged = list(_str_list(dst.get("synonyms")))
    for term in [*_str_list(src.get("synonyms")), str(src.get("name") or ""), manual_id.replace("_", " ")]:
        if term.strip() and norm(term) not in {norm(m) for m in merged}:
            merged.append(term)
    dst["synonyms"] = merged
    for thing in (data.get("things") or {}).values():
        if isinstance(thing, dict) and thing.get("place") == manual_id:
            thing["place"] = ha_id
    return data
