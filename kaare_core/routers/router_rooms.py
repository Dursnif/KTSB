"""Rom og aliaser → Rom (P137 phase 5): the place register for the admin GUI.

GET    /api/settings/rooms           effective register (places, floors, things, settings, HA sync status, hints)
PUT    /api/settings/rooms           save rooms.yaml (refused when a word would name two places/floors)
POST   /api/settings/rooms/sync      «Synkroniser nå» — fetch HA's registries now
POST   /api/settings/rooms/link      make a KTSB place part of an HA place (similar-name hint)
GET    /api/settings/rooms/ha_tree   HA places → devices → entities, for the picker (Velg selv)

Admin only, like /api/settings/aliases. KTSB never writes to HA from here.
"""

from __future__ import annotations

import copy
import logging

from fastapi import APIRouter, Depends, HTTPException

from kaare_core import config_store, rooms
from kaare_core import ha_registry_sync as ha_sync
from kaare_core.users.auth import require_admin as _require_admin

router = APIRouter()
_log = logging.getLogger("router_rooms")

_SETTINGS_DEFAULTS = {"import_mode": rooms.DEFAULT_IMPORT_MODE, "import_label": "", "import_domains": []}


def _read_rooms() -> dict:
    data = config_store.read_yaml(rooms.ROOMS_PATH) or {}
    return data if isinstance(data, dict) else {}


def _view(data: dict) -> dict:
    reg = rooms.build(data, rooms.read_ha_cache(rooms.HA_CACHE_PATH))
    ov_places = data.get("places") if isinstance(data.get("places"), dict) else {}
    explicit = set((data.get("things") or {}).keys())
    settings = {**_SETTINGS_DEFAULTS, **(data.get("settings") or {})}
    return {
        "settings": settings,
        "import_modes": list(rooms.IMPORT_MODES),
        "ha": _ha_status(reg),
        "floors": [
            {"id": f.id, "name": f.name, "source": f.source, "level": f.level, "synonyms": list(f.synonyms),
             "ha_aliases": list(f.ha_aliases)}
            for f in reg.floors.values()
        ],
        "places": [
            {"id": p.id, "name": p.name, "source": p.source, "floor": p.floor, "synonyms": list(p.synonyms),
             "ha_aliases": list(p.ha_aliases), "hidden": p.hidden,
             "template_example": bool((ov_places.get(p.id) or {}).get("template_example")),
             "things": len(reg.things_at(place=p.id)), "unregistered": reg.unregistered_at(place=p.id)}
            for p in reg.places.values()
        ],
        "things": [
            {"ref": t.ref, "name": t.name, "source": t.source, "place": t.place, "note": t.note,
             "domain": rooms.domain_of(t.ref), "explicit": t.ref in explicit,
             "in_ha": t.ref in reg.ha_entities, "missing": rooms.missing_in_ha(reg, t.ref)}
            for t in reg.things.values()
        ],
        "similar": [{"manual": m, "ha": h} for m, h in rooms.similar_places(reg)],
        "collisions": rooms.collisions(reg),
        # Place, name and source of every alias target — the Aliaser tab groups by these (P137 phase 7).
        "refs": {
            ref: {"place": reg.place_of(ref), "name": reg.name_of(ref), "registered": ref in reg.things,
                  "in_ha": ref in reg.ha_entities, "missing": rooms.missing_in_ha(reg, ref),
                  "ha_place": (reg.ha_entities.get(ref) or {}).get("area_id") or ""}
            for ref in _alias_targets()
        },
        "nodes": _node_places(reg),
        "raw": data,
    }


def _node_places(reg: rooms.Register) -> dict:
    """node id → the place it gets (its `place`, else its free-text room resolved) — the Nodes page shows nodes
    without one."""
    try:
        nodes = (config_store.read_yaml(rooms.NODES_PATH) or {}).get("nodes") or {}
    except Exception:
        return {}
    out = {}
    for nid, node in nodes.items() if isinstance(nodes, dict) else []:
        place, note = rooms.node_place(str(nid), rooms.NODES_PATH, reg)
        out[str(nid)] = {"place": place, "note": note, "room": str((node or {}).get("room") or "")}
    return out


def _ha_status(reg: rooms.Register) -> dict:
    """Sync status; counts from the snapshot on disk when this process has not synced yet (e.g. right after start)."""
    st = {**ha_sync.status(), "connected": reg.ha_connected}
    if not st.get("counts") and reg.ha_connected:
        st["counts"] = {"floors": len(reg.floors), "areas": sum(1 for p in reg.places.values() if p.source == "ha"),
                        "entities": len(reg.ha_entities)}
    return st


def _alias_targets() -> list[str]:
    try:
        aliases = (config_store.read_yaml(rooms.cs.CONFIG_DIR / "aliases.yaml") or {}).get("aliases") or {}
    except Exception:
        return []
    return sorted({str(v) for v in aliases.values() if v})


@router.get("/api/settings/rooms")
def api_get_rooms(_u=Depends(_require_admin)):
    return _view(_read_rooms())


def _clean(payload: dict) -> dict:
    """Accept only the four sections, with the shapes rooms.yaml uses."""
    out: dict = {}
    s = payload.get("settings") or {}
    if not isinstance(s, dict):
        raise HTTPException(400, "settings must be a mapping")
    mode = s.get("import_mode", rooms.DEFAULT_IMPORT_MODE)
    if mode not in rooms.IMPORT_MODES:
        raise HTTPException(400, f"import_mode must be one of {', '.join(rooms.IMPORT_MODES)}")
    domains = s.get("import_domains") or []
    if not isinstance(domains, list):
        raise HTTPException(400, "import_domains must be a list")
    out["settings"] = {"import_mode": mode, "import_label": str(s.get("import_label") or "").strip(),
                       "import_domains": [str(d).strip() for d in domains if str(d).strip()]}
    for section, fields in (("places", ("name", "floor", "synonyms", "hidden", "template_example")),
                            ("floors", ("name", "level", "synonyms")),
                            ("things", ("place", "note", "name"))):
        raw = payload.get(section) or {}
        if not isinstance(raw, dict):
            raise HTTPException(400, f"{section} must be a mapping")
        clean: dict = {}
        for key, val in raw.items():
            key = str(key).strip()
            if not key or not isinstance(val, dict):
                continue
            entry = {f: val[f] for f in fields if f in val and val[f] not in (None, "", [])}
            if "synonyms" in entry:
                entry["synonyms"] = [str(x).strip() for x in entry["synonyms"] if str(x).strip()]
            if section == "things" and rooms.source_of(key) != "ha" and not entry.get("place"):
                raise HTTPException(400, f"{key}: a thing outside Home Assistant needs a place")
            clean[key] = entry
        out[section] = clean
    return out


@router.put("/api/settings/rooms")
def api_put_rooms(payload: dict, _u=Depends(_require_admin)):
    data = _clean(payload or {})
    clashes = rooms.collisions(rooms.build(data, rooms.read_ha_cache(rooms.HA_CACHE_PATH)))
    if clashes:
        raise HTTPException(409, {"collisions": clashes})
    current = config_store.load_for_update(rooms.ROOMS_PATH)
    for section, value in data.items():
        current[section] = value
    config_store.write_yaml(rooms.ROOMS_PATH, current, "gui:put_rooms")
    return _view(_read_rooms())


@router.post("/api/settings/rooms/sync")
async def api_sync_rooms(_u=Depends(_require_admin)):
    st = await ha_sync.sync_now()
    return {"ha": st, **_view(_read_rooms())}


@router.post("/api/settings/rooms/link")
def api_link_place(payload: dict, _u=Depends(_require_admin)):
    manual, ha = str(payload.get("manual") or ""), str(payload.get("ha") or "")
    data = _read_rooms()
    reg = rooms.build(data, rooms.read_ha_cache(rooms.HA_CACHE_PATH))
    if manual not in reg.places or reg.places[manual].source != "manual":
        raise HTTPException(400, "manual must be a KTSB place")
    if ha not in reg.places or reg.places[ha].source != "ha":
        raise HTTPException(400, "ha must be a Home Assistant place")
    linked = rooms.link_place(copy.deepcopy(data), manual, ha)
    current = config_store.load_for_update(rooms.ROOMS_PATH)
    current["places"] = linked.get("places") or {}
    current["things"] = linked.get("things") or {}
    config_store.write_yaml(rooms.ROOMS_PATH, current, f"gui:link_place {manual}->{ha}")
    return _view(_read_rooms())


@router.get("/api/settings/rooms/ha_tree")
def api_ha_tree(_u=Depends(_require_admin)):
    """HA's areas with their devices and entities (disabled ones are not in the snapshot). Each entity carries
    `user_facing` (no diagnostic/config category, not hidden) so the GUI can hide the rest by default."""
    ha = rooms.read_ha_cache(rooms.HA_CACHE_PATH)
    areas = ha.get("areas") or {}
    tree: dict[str, dict] = {aid: {"id": aid, "name": a.get("name") or aid, "floor": a.get("floor_id") or "",
                                   "devices": {}} for aid, a in areas.items()}
    unplaced: dict = {"id": "", "name": "", "floor": "", "devices": {}}
    for eid, row in (ha.get("entities") or {}).items():
        node = tree.get(row.get("area_id") or "") or unplaced
        dev_key = row.get("device_id") or ""
        dev = node["devices"].setdefault(dev_key, {"id": dev_key, "name": row.get("device_name") or "",
                                                   "entities": []})
        dev["entities"].append({
            "ref": eid, "name": row.get("name") or eid, "domain": row.get("domain") or "",
            "user_facing": not row.get("category") and not row.get("hidden") and not row.get("orphan"),
            "orphan": bool(row.get("orphan")),
            "exposed": bool(row.get("exposed")), "labels": row.get("labels") or [],
        })
    def _out(node: dict) -> dict:
        return {**node, "devices": sorted(node["devices"].values(), key=lambda d: (d["name"] == "", d["name"]))}
    return {
        "areas": [_out(n) for n in tree.values()],
        "unplaced": _out(unplaced),
        "labels": [{"id": k, "name": v.get("name") or k} for k, v in (ha.get("labels") or {}).items()],
        "fetched_at": ha.get("fetched_at") or "",
    }
