import asyncio
import difflib
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import websockets
import yaml

from kaare_core import rooms
from kaare_core.config import get_local_tz
from kaare_core.config import get_service as _svc
from kaare_core.ha import action_truth as truth
from kaare_core.service_auth import service_headers
from kaare_core.tools.i18n import _T, get_lang, t
from kaare_core.voice import cast_client, channels, output_routing
from kaare_core.voice import volume as voice_volume

log = logging.getLogger(__name__)

_HA_TOKEN_PATH = Path("/kaare/configs/ha_token.env")
_NODES_PATH = Path("/kaare/configs/nodes.yaml")
_ALIASES_PATH = "/kaare/configs/aliases.yaml"

HA_GATEWAY_URL = _svc("internal", "ha_gateway")

HA_TOOLS = {
    "ha_read",
    "ha_control",
    "les_ha",
    "les_alias_lista",
    "les_ha_status",
    "styr_enhet",
}


def _domain_label(domain: str, lang: str) -> str:
    key = f"ha_domain_{domain}"

    return t(key, lang) if key in _T else domain


def get_ha_token() -> str:
    token = os.environ.get("HA_TOKEN", "")
    if token:
        return token
    if _HA_TOKEN_PATH.exists():
        for line in _HA_TOKEN_PATH.read_text().splitlines():
            if line.startswith("HA_TOKEN="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token
    raise ValueError("HA_TOKEN not found — check configs/ha_token.env")


def node_image_targets(source_node: str, display_ids: list[str] | None = None) -> list[str]:
    """Display nodes for «show it here» when Kåre names no target (P138): the input node's `image_out`
    (auto = itself if a display, else the displays in its place). [] = no node-specific answer."""
    if not source_node or not _NODES_PATH.exists():
        return []
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return []
    if display_ids is None:
        display_ids = [
            nid
            for nid, cfg in nodes.items()
            if isinstance(cfg, dict)
            and cfg.get("enabled", True)
            and output_routing.has_display(cfg)
        ]

    def place_of(nid: str) -> str:
        return rooms.node_place(nid)[0]

    return output_routing.image_targets(source_node, nodes, display_ids, place_of=place_of)


def node_entity(node_id: str) -> str | None:
    """HA entity_id of one node by its exact id."""
    if not node_id or not _NODES_PATH.exists():
        return None
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return None
    cfg = nodes.get(node_id)
    return str(cfg.get("entity_id") or "") or None if isinstance(cfg, dict) else None


def resolve_node_entity(client_hint: str) -> str | None:
    """Return HA entity_id for a node matching client_hint (fuzzy, case-insensitive)."""
    if not _NODES_PATH.exists():
        return None
    nodes = yaml.safe_load(_NODES_PATH.read_text()).get("nodes", {})
    needle = client_hint.lower().replace(" ", "_")
    for node_id, cfg in nodes.items():
        if not isinstance(cfg, dict) or not cfg.get("entity_id"):
            continue  # nothing to cast to (KTSB Direct, plain ESP32 …) — must not shadow a later match
        nid = node_id.lower().replace(" ", "_")
        room = (cfg.get("room") or "").lower().replace(" ", "_")
        if needle == nid:
            return cfg.get("entity_id")
        if needle in nid:
            return cfg.get("entity_id")
        if needle in room:
            return cfg.get("entity_id")
        if needle in cfg.get("description", "").lower():
            return cfg.get("entity_id")
    return None


def cast_nodes() -> dict[str, dict]:
    """Enabled nodes KTSB can reach over its own Cast: an address and an enabled `cast` channel (P139 phase 3)."""
    if not _NODES_PATH.exists():
        return {}
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return {}
    return {
        nid: cfg
        for nid, cfg in nodes.items()
        if isinstance(cfg, dict)
        and cfg.get("enabled", True)
        and str(cfg.get("host") or "").strip()
        and "cast" in channels.enabled_channels(cfg)
    }


def remote_nodes() -> dict[str, dict]:
    """Enabled nodes with an address and an enabled remote-control channel (P139 phase 5)."""
    if not _NODES_PATH.exists():
        return {}
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return {}
    return {
        nid: cfg
        for nid, cfg in nodes.items()
        if isinstance(cfg, dict)
        and cfg.get("enabled", True)
        and str(cfg.get("host") or "").strip()
        and _has_remote(cfg)
    }


_REMOTE_CHANNELS = ("atv_remote", "samsung_ws")


def _has_remote(cfg: dict) -> bool:
    return any(c in channels.enabled_channels(cfg) for c in _REMOTE_CHANNELS)


def _nodes_at(reg, kind: str, ident: str) -> list[tuple[str, dict]]:
    """Enabled KTSB nodes at a place (or on a floor). room_devices must show them next to HA's things — a TV that
    only KTSB knows was invisible, so Kåre never reached for `media tv_power` (FIX 2026-11-16)."""
    if not _NODES_PATH.exists():
        return []
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return []
    found = []
    for nid, cfg in nodes.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue
        place = rooms.node_place(str(nid), nodes_path=_NODES_PATH, reg=reg)[0]
        if not place:
            continue
        if kind == "place" and place != ident:
            continue
        if kind == "floor" and getattr(reg.places.get(place), "floor", "") != ident:
            continue
        found.append((str(nid), cfg))
    return found


def _node_line(nid: str, cfg: dict, reg, kind: str, lang: str) -> str:
    what = [t("ha_node_display", lang)] if cfg.get("has_display") else []
    if cfg.get("has_audio"):
        what.append(t("ha_node_audio", lang))
    parts = [f"- {cfg.get('description') or nid} — node:{nid} [{t('ha_node_kind', lang, what=', '.join(what))}]"]
    if kind == "floor":
        parts.append(reg.label_of_place(rooms.node_place(nid, nodes_path=_NODES_PATH, reg=reg)[0]))
    if str(cfg.get("host") or "").strip() and _has_remote(cfg):
        parts.append(t("ha_node_remote", lang, node=nid))
    # The node's own HA player, only as set in its config and only when HA has it — never a guess (FIX 2026-11-16:
    # Kåre searched 2 min / 315 s for the Nest Hub's entity and tried an invented one).
    eid = str(cfg.get("entity_id") or "").strip()
    if eid and eid in reg.ha_entities:
        parts.append(t("ha_node_ha_player", lang, entity=eid))
    return " — ".join(parts)


async def tv_own_state(entity_id: str, lang: str) -> str:
    """What the TV itself reports, for a media player a node with a remote channel owns. HA's Cast player says «off»
    when no Cast app runs although the TV is on (P139 phase 5 live test: Kåre was told the Thomson was off)."""
    node = next((nid for nid, cfg in remote_nodes().items() if str(cfg.get("entity_id") or "") == entity_id), "")
    if not node:
        return ""
    res = await cast_client.call("/tv/act", {"node": node, "action": "status", "origin": "requested"}, timeout=10)
    after = res.get("after") or {}
    if not res.get("ok") or after.get("on") is None:
        return ""
    state = ", ".join(f"{k} {after[k]}" for k in ("on", "app", "volume", "muted") if after.get(k) not in (None, ""))
    return "\n" + t("ha_status_tv_own", lang, node=node, state=state)


def screen_nodes() -> dict[str, dict]:
    """Enabled nodes with an address — every screen a Plex play can be tied to (P140 bug A)."""
    if not _NODES_PATH.exists():
        return {}
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return {}
    return {
        nid: cfg
        for nid, cfg in nodes.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True) and str(cfg.get("host") or "").strip()
    }


def node_for_address(address: str) -> str:
    """The node whose host is this IP (Plex's Player.address), "" if none."""
    address = (address or "").strip()
    if not address:
        return ""
    return next((nid for nid, cfg in screen_nodes().items() if str(cfg["host"]).strip() == address), "")


def screen_names() -> dict[str, str]:
    """Every name a screen node goes by → node id: the id, its HA player's id and HA name. Plex apps are often named
    like the HA player («Workshop TV 2» = media_player.workshop_tv_2), which ties older plays to the node."""
    names: dict[str, str] = {}
    try:
        reg = rooms.get_register()
    except Exception:
        reg = None  # no HA (or no snapshot yet): node ids and entity ids still work — HA only adds names
    for nid, cfg in screen_nodes().items():
        names[nid] = nid
        eid = str(cfg.get("entity_id") or "").strip()
        if eid:
            names[eid.split(".", 1)[-1]] = nid
            try:
                if reg is not None and (name := reg.name_of(eid)):
                    names[name] = nid
            except Exception:
                pass
    return names


def resolve_screen_nodes(text: str) -> list[tuple[str, dict]]:
    """The screen nodes the user's words name — one, or several to ask about (same rules as the Cast lookup)."""
    return _match_nodes(screen_nodes(), text, "") if (text or "").strip() else []


def resolve_remote_node(text: str, source_node: str = "") -> tuple[str, dict] | None:
    """A TV with a remote-control channel the user's words name (same rules as `resolve_cast_node`)."""
    return _resolve_node(remote_nodes(), text, source_node)


def resolve_cast_node(text: str, source_node: str = "") -> tuple[str, dict] | None:
    """A Cast node the user's words name — node id, its description, its place (P137 register), its HA player, or a
    near spelling; with no words, the screen where the user is (the source node's `image_out`). None when no Cast
    node matches — or when several do (`cast_choices` then names them, so the caller asks)."""
    return _resolve_node(cast_nodes(), text, source_node)


def cast_choices(text: str) -> list[tuple[str, dict]]:
    """The Cast nodes the words fit when they fit more than one (e.g. a room with a Nest Hub and a TV)."""
    found = _match_nodes(cast_nodes(), text, "")
    return found if len(found) > 1 else []


def remote_choices(text: str) -> list[tuple[str, dict]]:
    found = _match_nodes(remote_nodes(), text, "")
    return found if len(found) > 1 else []


def resolve_display_node(text: str, display_ids: list[str]) -> tuple[list[str], list[tuple[str, dict]]]:
    """Display nodes the words name, with the same rules as the Cast/remote lookup (id, description, place, HA player,
    near spelling, two screens in a room → choices). (targets, choices): one target, or choices to ask about."""
    if not _NODES_PATH.exists():
        return [], []
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return [], []
    found = _match_nodes({nid: nodes[nid] for nid in display_ids if isinstance(nodes.get(nid), dict)}, text, "")
    return ([found[0][0]], []) if len(found) == 1 else ([], found)


def choices_text(choices: list[tuple[str, dict]]) -> str:
    """«client=node:<id> (description)» for each — values that resolve to exactly one node, also when a node is
    named like its room (the Nest node «hovedsoverom», P139 live test)."""
    return "; ".join(f"client=node:{nid} ({cfg.get('description') or cfg.get('type') or nid})" for nid, cfg in choices)


def _resolve_node(nodes: dict[str, dict], text: str, source_node: str) -> tuple[str, dict] | None:
    found = _match_nodes(nodes, text, source_node)
    return found[0] if len(found) == 1 else None


_WORD = re.compile(r"[a-z0-9æøå]+")


def _words(text: str) -> set[str]:
    """Words that can tell nodes apart: three letters or more, and «tv» (the one short word that does)."""
    return {w for w in _WORD.findall((text or "").lower()) if len(w) >= 3 or w == "tv"}


def _vocab(nid: str, cfg: dict, reg) -> set[str]:
    eid = str(cfg.get("entity_id") or "")
    return _words(" ".join((nid.replace("_", " "), str(cfg.get("type") or "").replace("_", " "),
                            str(cfg.get("description") or ""), reg.name_of(eid) if eid else "")))


def _narrow(found: list[tuple[str, dict]], text: str, reg) -> list[tuple[str, dict]]:
    """Several nodes fit (a room with two screens): keep the one the other words name («TV-en», «Nest-en»); words
    every candidate shares (the room name) do not count. Still a tie → all of them, and the caller asks."""
    if len(found) < 2:
        return found
    vocabs = [_vocab(nid, cfg, reg) for nid, cfg in found]
    shared = set.intersection(*vocabs)
    words = _words(text) - shared
    scores = [len(words & v) for v in vocabs]
    best = max(scores)
    if best and scores.count(best) == 1:
        return [found[scores.index(best)]]
    return found


def _match_nodes(nodes: dict[str, dict], text: str, source_node: str) -> list[tuple[str, dict]]:
    if not nodes:
        return []
    text = (text or "").strip()
    if not text:
        return [(nid, nodes[nid]) for nid in node_image_targets(source_node, list(nodes))][:1]
    if text.lower().startswith("node:"):  # exactly this node — what «which one?» hands back (P139)
        nid = text[5:].strip()
        return [(nid, nodes[nid])] if nid in nodes else []
    key = text.lower()
    needle = key.replace(" ", "_")
    reg = rooms.get_register()
    place = reg.resolve(text) or reg.find_in_text(text)
    at_place = [(nid, cfg) for nid, cfg in nodes.items()
                if place and place[0] == "place" and rooms.node_place(nid, reg=reg)[0] == place[1]]
    exact = [(nid, cfg) for nid, cfg in nodes.items() if needle == nid.lower()]
    if exact and not at_place:
        return exact
    stages = [
        # node id, description, or the node's own HA player by id or name — one stage, so a word that names a kind of
        # device («nest») finds every node of that kind whichever field says it (P139 bug 2026-11-16: two Nests were
        # found by description, the third only by its HA id, and the search stopped before reading it). Kåre often
        # names a TV the way HA does («verksted» for media_player.workshop_tv_2 «Workshop TV 2», P139 phase 5).
        [(nid, cfg) for nid, cfg in nodes.items()
         if needle in nid.lower() or key in str(cfg.get("description") or "").lower()
         or ((eid := str(cfg.get("entity_id") or "")) and (
             key == eid.lower() or needle in eid.lower().split(".", 1)[-1] or key in reg.name_of(eid).lower()))],
        # the place (P137 register) — a room with two screens gives both
        at_place,
    ]
    for found in stages:
        if found:
            if at_place and found is not at_place:
                found = list(dict(found + at_place).items())
            return _narrow(found, text, reg)
    # a near spelling or form («Thomsen» for a node named after a Thomson TV, «hovedsoverommet» for hovedsoverom): words of five letters
    # or more, then the same narrowing as above
    long_words = {w for w in _words(text) if len(w) >= 5}
    near = [(nid, cfg) for nid, cfg in nodes.items()
            if any(difflib.SequenceMatcher(None, w, v).ratio() >= 0.8
                   for w in long_words for v in _vocab(nid, cfg, reg) if len(v) >= 5)]
    return _narrow(near, text, reg)


def resolve_screens(text: str) -> tuple[list[tuple[str, str]], str]:
    """HA media players a user's words name (P139): ([(entity_id, name)], other_room). Through a node, an entity id,
    an alias, or a place/floor from the register (P137) — every media player registered there that HA still has. A
    node without its own entity_id (a TV reached directly) resolves through its place. When the named room has no
    player, the same floor's players are returned with `other_room` = that room's name: the caller must ask before
    using one (it is in another room)."""
    text = (text or "").strip()
    if not text:
        return [], ""
    reg = rooms.get_register()
    eid = resolve_node_entity(text)
    if eid:
        return [(eid, reg.name_of(eid))], ""
    if text.startswith("media_player."):
        return [(text, reg.name_of(text))], ""
    key = text.lower()
    for alias, ref in ((_load_aliases().get("aliases") or {}).items()):
        if (
            str(alias).strip().lower() == key
            and str(ref).startswith("media_player.")
            and rooms.missing_in_ha(reg, str(ref)) != "gone"  # an alias left pointing at a removed player
        ):
            return [(str(ref), reg.name_of(str(ref)))], ""
    hit = reg.resolve(text) or reg.find_in_text(text)
    if not hit and _NODES_PATH.exists():
        # a node named by the user that has no entity itself → its place
        try:
            nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
        except Exception:
            nodes = {}
        needle = key.replace(" ", "_")
        for nid, cfg in nodes.items():
            label = str((cfg or {}).get("description") or "").lower() if isinstance(cfg, dict) else ""
            if needle in nid.lower() or (label and key in label):
                place = rooms.node_place(nid, reg=reg)[0]
                if place:
                    hit = ("place", place)
                    break
    if not hit:
        return [], ""
    kind, ident = hit

    def players(things: list) -> list[tuple[str, str]]:
        # Skip only refs HA does not have at all; «orphan» can be a device that was merely off at snapshot time.
        return [(th.ref, th.name) for th in things if th.source == "ha" and rooms.missing_in_ha(reg, th.ref) != "gone"]

    if kind == "floor":
        return players(reg.things_at(floor=ident, domain="media_player")), ""
    if kind != "place":
        return [], ""
    found = players(reg.things_at(place=ident, domain="media_player"))
    floor = reg.places[ident].floor if ident in reg.places else ""
    if not found and floor:  # no player in that room → the same floor (one room with zones, a TV seen from the next)
        return players(reg.things_at(floor=floor, domain="media_player")), _place_label(reg, ident)
    return found, ""


def _load_aliases() -> dict:
    try:
        with open(_ALIASES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ── P137: places from the register (rooms.yaml + HA areas/floors) ────────────

_LIGHT_ONLY_ACTIONS = {"set_level", "set_color_temp", "set_color"}
_MEDIA_ACTIONS = set(truth.MEDIA_ACTIONS)  # P139 phase 1 — media_player only
_VOICE_SETTINGS_PATH = Path("/kaare/configs/voice_settings.yaml")
# rid → (entity_id, ok, memory note) of a room/floor command; note = "" | "unconfirmed" | "not_found" (N26)
_LOCATION_RESULTS: dict[str, list[tuple[str, bool, str]]] = {}


def memory_note(outcome: str) -> str:
    """How STM should remember an HA outcome besides ok/failed (N26): sent-but-unconfirmed is not a failure."""
    if outcome == truth.NOT_CONFIRMED:
        return "unconfirmed"
    if outcome == truth.NOT_FOUND:
        return "not_found"
    return ""


def _gateway_timeout() -> float:
    """The gateway may wait up to its verification time for HA to report the new state (N26)."""
    try:
        raw = _svc("home_assistant", "verify_seconds")
        verify = float(2.0 if raw is None else raw)
    except Exception:
        verify = 2.0
    return 5.0 + max(0.0, min(10.0, verify)) + 2.0


# (rid, entity_id) → memory note of a single-entity ha_control call (N26)
_ENTITY_NOTES: dict[tuple[str, str], str] = {}


def pop_entity_note(rid: str, entity_id: str) -> str:
    """The memory note ("", "unconfirmed", "not_found") the gateway's answer gave for one entity in this request."""
    return _ENTITY_NOTES.pop((rid, entity_id), "") if rid else ""


def pop_location_results(rid: str) -> list[tuple[str, bool, str]]:
    """The entities a room/floor ha_control call switched in this request — so the router can record each one in
    STM/LTM like a single-entity call."""
    return _LOCATION_RESULTS.pop(rid, []) if rid else []


def _aliases_by_ref() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for text, ref in (_load_aliases().get("aliases") or {}).items():
        out.setdefault(str(ref), []).append(str(text))
    return out


def _place_label(reg: "rooms.Register", place_id: str) -> str:
    p = reg.places.get(place_id)
    if not p:
        return place_id.replace("_", " ")
    floor = reg.floors.get(p.floor)
    return f"{p.name} ({floor.name})" if floor else p.name


def _resolve_location(arguments: dict, reg: "rooms.Register") -> tuple[str, str, str, str]:
    """(kind, id, origin, unknown_text): place/floor named in the request → the source node's place → nothing.
    kind is 'place' | 'floor' | ''. origin is 'request' | 'node' | ''. unknown_text is set when the request named a
    place the register does not know (the caller reports it instead of falling back to the node)."""
    for key, want in (("floor", "floor"), ("room", "place")):
        text = str(arguments.get(key) or "").strip()
        if not text:
            continue
        hit = reg.resolve(text) or reg.find_in_text(text)
        if hit and (hit[0] == want or key == "room"):
            return (hit[0], hit[1], "request", "")
        return ("", "", "", text)
    node = str(arguments.get("_source_node") or "")
    if node:
        place, _note = rooms.node_place(node, reg=reg)
        if place:
            return ("place", place, "node", "")
    return ("", "", "", "")


def _known_places_text(reg: "rooms.Register", limit: int = 30) -> str:
    names = [p.name for p in reg.places.values() if not p.hidden]
    return ", ".join(names[:limit]) + (" …" if len(names) > limit else "")


def _format_places(reg: "rooms.Register", lang: str) -> str:
    visible = [p for p in reg.places.values() if not p.hidden]
    if not visible:
        return t("ha_no_places", lang)
    lines = [t("ha_places_header", lang)]
    lines.extend(f"- {_place_label(reg, p.id)} — {len(reg.things_at(place=p.id))}" for p in visible)
    if reg.floors:
        lines += ["", t("ha_floors_header", lang)]
        for f in reg.floors.values():
            names = [
                reg.places[pid].name
                for pid in reg.places_on_floor(f.id)
                if not reg.places[pid].hidden
            ]
            lines.append(f"- {f.name}: {', '.join(names) or '—'}")
    return "\n".join(lines)


def _unregistered_detail(reg: "rooms.Register", kind: str, ident: str, lang: str) -> str:
    ids = {ident} if kind == "place" else set(reg.places_on_floor(ident))
    counts: dict[str, int] = {}
    for eid, row in reg.ha_entities.items():
        if (
            row.get("area_id") in ids
            and eid not in reg.things
            and not row.get("category")
            and not row.get("hidden")
            and not row.get("orphan")
        ):
            counts[row.get("domain") or ""] = counts.get(row.get("domain") or "", 0) + 1
    total = sum(counts.values())
    if not total:
        return ""
    detail = ", ".join(f"{n} {d}" for d, n in sorted(counts.items(), key=lambda x: -x[1]))
    return t("ha_unregistered_hint", lang, count=total, detail=detail)


def _format_place_things(arguments: dict, lang: str) -> str:
    reg = rooms.get_register()
    kind, ident, origin, unknown = _resolve_location(arguments, reg)
    if unknown:
        return t("ha_place_unknown", lang, text=unknown, known=_known_places_text(reg))
    if not kind:
        return t("ha_location_needed", lang)
    things = reg.things_at(place=ident) if kind == "place" else reg.things_at(floor=ident)
    label = _place_label(reg, ident) if kind == "place" else reg.floors[ident].name
    aliases = _aliases_by_ref()
    nodes = _nodes_at(reg, kind, ident)
    lines = [
        t(
            "ha_place_header",
            lang,
            place=label,
            count=len(things) + len(nodes),
            origin=t("ha_origin_node", lang) if origin == "node" else "",
        )
    ]
    for th in things:
        parts = [
            f"- {th.name} — {th.ref} [{_domain_label(rooms.domain_of(th.ref), lang) or th.source}]"
        ]
        if kind == "floor":
            parts.append(reg.label_of_place(th.place))
        if aliases.get(th.ref):
            parts.append(
                t(
                    "ha_thing_aliases",
                    lang,
                    aliases=", ".join(f"«{a}»" for a in aliases[th.ref][:6]),
                )
            )
        if th.note:
            parts.append(t("ha_thing_note", lang, note=th.note))
        lines.append(" — ".join(parts))
    lines += [_node_line(nid, cfg, reg, kind, lang) for nid, cfg in nodes]
    hint = _unregistered_detail(reg, kind, ident, lang)
    if hint:
        lines += ["", hint]
    return "\n".join(lines)


async def _control_location(arguments: dict, action: str, params: dict, lang: str) -> str:
    """ha_control without entity_id: every registered HA thing of one domain at a place/floor, in parallel."""
    reg = rooms.get_register()
    kind, ident, _origin, unknown = _resolve_location(arguments, reg)
    if unknown:
        return t("ha_place_unknown", lang, text=unknown, known=_known_places_text(reg))
    if not kind:
        return t("ha_location_needed", lang)
    domain = str(arguments.get("domain") or "").strip().lower() or "light"
    if action in _LIGHT_ONLY_ACTIONS:
        domain = "light"
    elif action in _MEDIA_ACTIONS:
        domain = "media_player"
    things = (
        reg.things_at(place=ident, domain=domain)
        if kind == "place"
        else reg.things_at(floor=ident, domain=domain)
    )
    # Things the register still lists but HA no longer has would only fail (P139: chromecast2592 at kjellerstue).
    targets = [th for th in things if th.source == "ha" and rooms.missing_in_ha(reg, th.ref) != "gone"]
    label = _place_label(reg, ident) if kind == "place" else reg.floors[ident].name
    if not targets:
        msg = t("ha_place_none_of_type", lang, domain=domain, place=label)
        hint = _unregistered_detail(reg, kind, ident, lang)
        return f"{msg}\n{hint}" if hint else msg
    return await _apply_group(
        [(th.ref, th.name) for th in targets],
        action,
        params,
        domain,
        label,
        str(arguments.get("_rid") or ""),
        lang,
    )


async def _apply_group(
    targets: list[tuple[str, str]],
    action: str,
    params: dict,
    domain: str,
    label: str,
    rid: str,
    lang: str,
) -> str:
    """One action on several (entity_id, name) targets in parallel; each result is kept for STM like a single call."""
    results = await asyncio.gather(*(_apply_full(ref, action, params) for ref, _n in targets))
    if rid:
        _LOCATION_RESULTS.setdefault(rid, []).extend(
            (ref, good, memory_note(st)) for (ref, _n), (good, st, _m) in zip(targets, results, strict=True)
        )
    pairs = list(zip(targets, results, strict=True))

    def shown(name: str, media: dict) -> str:
        summary = _media_summary(media, lang) if action in _MEDIA_ACTIONS else ""
        return f"{name} ({summary})" if summary else name

    ok = [shown(name, m) for (_r, name), (good, _s, m) in pairs if good]
    unconfirmed = [
        name for (_r, name), (good, st, _m) in pairs if not good and st == truth.NOT_CONFIRMED
    ]
    bad = [
        f"{name} ({_outcome_word(st, lang)})"
        for (_r, name), (good, st, _m) in pairs
        if not good and st != truth.NOT_CONFIRMED
    ]
    out = []
    if ok:
        out.append(
            t(
                "ha_location_done",
                lang,
                action=t(f"ha_act_{action}", lang),
                count=len(ok),
                domain=domain,
                place=label,
                names=", ".join(ok),
            )
        )
    if unconfirmed:
        out.append(t("ha_location_unconfirmed", lang, names=", ".join(unconfirmed)))
    if bad:
        out.append(t("ha_location_failed", lang, names=", ".join(bad)))
    return "\n".join(out)


def _outcome_word(outcome: str, lang: str) -> str:
    """A short reason for a failed target; raw text (an exception) when it is not a known outcome."""
    key = f"ha_outcome_word_{outcome}"
    return t(key, lang) if key in _T else outcome


def _media_summary(media: dict | None, lang: str) -> str:
    """What HA reports about a player after the call: volume, mute, source — only what it reports."""
    media = media or {}
    parts = []
    level = media.get("volume_level")
    if isinstance(level, int | float):
        parts.append(t("ha_media_volume", lang, pct=round(float(level) * 100)))
    if media.get("is_volume_muted") is True:
        parts.append(t("ha_media_muted", lang))
    if media.get("source"):
        parts.append(t("ha_media_source", lang, source=media["source"]))
    if media.get("app_name"):
        parts.append(
            t("ha_media_app", lang, app=media["app_name"], title=media["media_title"])
            if media.get("media_title")
            else t("ha_media_app_no_title", lang, app=media["app_name"])
        )
    return ", ".join(parts)


def _voice_playback_mode() -> str:
    try:
        cfg = yaml.safe_load(_VOICE_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return str((cfg.get("bridge") or {}).get("playback_mode") or output_routing.LOCAL)
    except Exception:
        return output_routing.LOCAL


def node_audio_entities(node_id: str) -> tuple[list[tuple[str, str]], str]:
    """The HA media players a node's spoken reply plays on — «the sound» when the user names no device (P139 phase 1,
    BUG-log 2026-10-18: Atom → Kåre raised the workshop radio, not her Nest). Same rule as the voice bridge
    (`output_routing.audio_targets`). Returns ([(entity_id, name)], label of where the sound goes); the list is empty
    when the reply plays on a speaker HA does not control (the satellite itself, this machine)."""
    if not node_id or not _NODES_PATH.exists():
        return [], ""
    try:
        nodes = (yaml.safe_load(_NODES_PATH.read_text()) or {}).get("nodes") or {}
    except Exception:
        return [], ""
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return [], ""
    kind = (
        output_routing.INPUT_WYOMING
        if str(node.get("type") or "") == "wyoming"
        else output_routing.INPUT_BUTTON
    )

    def place_of(nid: str) -> str:
        return rooms.node_place(nid)[0]

    targets = output_routing.audio_targets(
        node_id, nodes, kind, _voice_playback_mode(), place_of=place_of
    )
    out: list[tuple[str, str]] = []
    labels = []
    for tid in targets:
        nid = node_id if tid == output_routing.SELF else tid
        labels.append(
            output_routing.label(nid, nodes) if tid != output_routing.LOCAL else output_routing.LOCAL
        )
        eid = str((nodes.get(nid) or {}).get("entity_id") or "")
        if eid.startswith("media_player."):
            out.append((eid, output_routing.label(nid, nodes)))
    return out, ", ".join(labels)


async def _control_node_audio(node_id: str, action: str, params: dict, rid: str, lang: str) -> str:
    """A media action with no device, room or floor named: the speaker the node's own replies play on."""
    targets, where = node_audio_entities(node_id)
    if not targets:
        if not where:
            return t("ha_media_target_needed", lang)
        if where == output_routing.LOCAL:
            where = t("ha_media_local_speaker", lang)
        return t("ha_media_node_not_ha", lang, where=where)
    label = t("ha_media_node_place", lang, node=where)
    return await _apply_group(targets, action, params, "media_player", label, rid, lang)


async def _apply(entity_id: str, action: str, params: dict) -> tuple[bool, str]:
    """One action through the gateway's explicit path. (done, outcome) — done only when HA's state shows it (N26)."""
    done, outcome, _media = await _apply_full(entity_id, action, params)
    return done, outcome


async def _apply_full(entity_id: str, action: str, params: dict) -> tuple[bool, str, dict]:
    """As `_apply`, plus what HA reports about a media player afterwards."""
    try:
        async with httpx.AsyncClient(timeout=_gateway_timeout()) as client:
            r = await client.post(
                f"{HA_GATEWAY_URL}/api/ha_apply",
                json={
                    "action": action,
                    "entity_id": entity_id,
                    "params": params,
                    "source": "kare-tool",
                },
                headers=service_headers(),
            )
            r.raise_for_status()
            data = r.json()
        outcome = str(data.get("outcome") or "")
        media = data.get("media") or {}
        if not outcome:  # a reply without an outcome (no state to check): its status decides, as before N26
            status = str(data.get("status", "unknown"))
            return (status == "ok", status, media)
        return (outcome in truth.DONE, outcome, media)
    except Exception as e:
        return (False, str(e), {})


async def _control_entity(
    entity_id: str,
    action: str,
    brightness_pct: int | None = None,
    color_temp_kelvin: int | None = None,
    rgb_color: list | None = None,
    lang: str = "nb",
    location: dict | None = None,
    volume_pct: int | None = None,
    source: str | None = None,
) -> str:
    if not action:
        return t("ha_entity_action_required", lang)

    params: dict[str, Any] = {}
    if action == "set_level" and brightness_pct is not None:
        params["level"] = int(brightness_pct)
    elif action == "set_color_temp" and color_temp_kelvin is not None:
        params["color_temp_kelvin"] = int(color_temp_kelvin)
    elif action == "set_color" and rgb_color is not None:
        params["rgb_color"] = rgb_color
    elif action == "set_volume":
        try:
            pct = int(float(str(volume_pct).strip().rstrip("%")))
        except (TypeError, ValueError):
            return t("ha_media_volume_needed", lang)
        # I64 seam: the user asked for this level — the seam passes a user's wish through unchanged
        pct = max(0, min(100, pct))
        level = voice_volume.target_volume(str((location or {}).get("_source_node") or ""), {}, pct / 100,
                                           voice_volume.PURPOSE_USER_REQUEST)
        params["volume_pct"] = pct if level is None else max(0, min(100, round(level * 100)))
    elif action == "select_source":
        if not str(source or "").strip():
            return t("ha_media_source_needed", lang)
        params["source"] = str(source).strip()

    loc = location or {}
    if not entity_id:
        if action in _MEDIA_ACTIONS and not (loc.get("room") or loc.get("floor")):
            # «Turn the sound up» with nothing named = the speaker this node's replies play on (P139 phase 1).
            return await _control_node_audio(
                str(loc.get("_source_node") or ""), action, params, str(loc.get("_rid") or ""), lang
            )
        # P137: room / floor / the source node's place — the code picks the entities.
        return await _control_location(loc, action, params, lang)
    if action in _MEDIA_ACTIONS and truth.domain_of(entity_id) != "media_player":
        return t("ha_media_not_player", lang, entity_id=entity_id)

    try:
        async with httpx.AsyncClient(timeout=_gateway_timeout()) as client:
            r = await client.post(
                f"{HA_GATEWAY_URL}/api/ha_apply",
                json={
                    "action": action,
                    "entity_id": entity_id,
                    "params": params,
                    "source": "kare-tool",
                },
                headers=service_headers(),
            )
            r.raise_for_status()
            data = r.json()
        outcome = str(data.get("outcome") or "")
        state = str(data.get("state") or "?")
        rid = str((location or {}).get("_rid") or "")
        if rid and memory_note(outcome):
            _ENTITY_NOTES[(rid, entity_id)] = memory_note(outcome)
        if action in _MEDIA_ACTIONS:
            return _media_text(entity_id, action, outcome, state, data.get("media") or {}, lang)
        summary = _media_summary(data.get("media"), lang) if data.get("media") else ""
        if summary:
            state = f"{state}; {summary}"
        # N26: say what HA's state shows. «OK:» = done (router records success); «SENT:» = accepted, not confirmed.
        if outcome == truth.ALREADY:
            return "OK: " + t("ha_outcome_already", lang, entity_id=entity_id, state=state)
        if outcome == truth.ACCEPTED:
            return "OK: " + t(
                "ha_outcome_accepted", lang, entity_id=entity_id, action=action, state=state
            )
        if outcome == truth.NOT_CONFIRMED:
            return "SENT: " + t(
                "ha_outcome_not_confirmed", lang, entity_id=entity_id, action=action, state=state
            )
        if outcome == truth.NOT_FOUND:
            return t("ha_outcome_not_found", lang, entity_id=entity_id)
        if outcome == truth.UNAVAILABLE:
            return t("ha_outcome_unavailable", lang, entity_id=entity_id)
        if data.get("status") == "ok":
            if action == "turn_on":
                msg = t("ha_turned_on", lang, entity_id=entity_id)
            elif action == "turn_off":
                msg = t("ha_turned_off", lang, entity_id=entity_id)
            elif action == "set_level":
                msg = t(
                    "ha_brightness_set", lang, entity_id=entity_id, brightness_pct=brightness_pct
                )
            elif action == "set_color_temp":
                msg = t(
                    "ha_color_temp_set",
                    lang,
                    entity_id=entity_id,
                    color_temp_kelvin=color_temp_kelvin,
                )
            elif action == "set_color":
                msg = t("ha_color_set", lang, entity_id=entity_id)
            else:
                msg = t("ha_action_done", lang, entity_id=entity_id, action=action)
            if data.get("media") is not None:  # a media player: say what HA shows now
                msg = f"{msg} ({state})"
            return f"OK: {msg}"
        return t("ha_status_response", lang, status=data.get("status", "ukjent"))
    except Exception as e:
        return t("ha_call_error", lang, error=e)


def _media_text(entity_id: str, action: str, outcome: str, state: str, media: dict, lang: str) -> str:
    """What Kåre is told after a media-player action (N26: «OK:» only when HA shows it, or nothing can be checked)."""
    act = t(f"ha_act_{action}", lang)
    summary = _media_summary(media, lang)
    now = f"{state}, {summary}" if summary else state
    if outcome == truth.CONFIRMED:
        return "OK: " + t("ha_media_confirmed", lang, entity_id=entity_id, action=act, now=now)
    if outcome == truth.ALREADY:
        return "OK: " + t("ha_media_already", lang, entity_id=entity_id, now=now)
    if outcome == truth.ACCEPTED:
        return "OK: " + t("ha_media_accepted", lang, entity_id=entity_id, action=act, state=state)
    if outcome == truth.NOT_CONFIRMED:
        return "SENT: " + t("ha_media_not_confirmed", lang, entity_id=entity_id, action=act, now=now)
    if outcome == truth.NOT_FOUND:
        return t("ha_outcome_not_found", lang, entity_id=entity_id)
    if outcome == truth.UNAVAILABLE:
        return t("ha_outcome_unavailable", lang, entity_id=entity_id)
    if outcome == truth.UNSUPPORTED:
        return t("ha_media_unsupported", lang, entity_id=entity_id, action=act)
    if outcome == truth.SOURCE_UNKNOWN:
        sources = ", ".join(str(x) for x in media.get("source_list") or []) or "—"
        return t("ha_media_source_unknown", lang, entity_id=entity_id, sources=sources)
    return t("ha_media_failed", lang, entity_id=entity_id, action=act, outcome=outcome or state)


def _read_ha_url() -> str:
    """Return the HA base URL from services.yaml, or empty string if not configured."""
    try:
        return (_svc("home_assistant", "url") or "").rstrip("/")
    except Exception:
        return ""


async def _ha_ws_call(ha_url: str, token: str, message: dict, timeout: float = 10.0) -> Any:
    """One command over Home Assistant's WebSocket API: auth → command → result. Long-term statistics
    (`recorder/statistics_during_period`) exist only here — there is no REST endpoint for them."""
    ws_url = (
        ha_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/websocket"
    )
    async with asyncio.timeout(timeout):
        async with websockets.connect(ws_url, open_timeout=timeout) as ws:
            if json.loads(await ws.recv()).get("type") != "auth_required":
                raise RuntimeError("unexpected HA WebSocket greeting")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            if json.loads(await ws.recv()).get("type") != "auth_ok":
                raise RuntimeError("HA WebSocket authentication failed")
            await ws.send(json.dumps({"id": 1, **message}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") == 1 and reply.get("type") == "result":
                    if not reply.get("success"):
                        raise RuntimeError((reply.get("error") or {}).get("message") or "HA error")
                    return reply.get("result")


def _parse_ha_time(value: Any) -> datetime:
    """HA statistics give epoch milliseconds; REST history gives ISO strings."""
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, UTC)
    return datetime.fromisoformat(str(value))


async def _ha_state_changes(
    ha_url: str, headers: dict, entity_id: str, start: datetime, end: datetime, days: int, lang: str
) -> str:
    """Raw state changes from HA's REST history — for entities without long-term statistics (switches,
    binary sensors, sensors without state_class). Only as far back as HA's recorder keeps history."""
    max_rows = 40
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{ha_url}/api/history/period/{start.isoformat()}",
                params={
                    "filter_entity_id": entity_id,
                    "end_time": end.isoformat(),
                    "minimal_response": "",
                    "no_attributes": "",
                },
                headers=headers,
            )
    except Exception as exc:
        return t("ha_history_error", lang, entity_id=entity_id, error=exc)
    if r.status_code != 200:
        return t("ha_history_error", lang, entity_id=entity_id, error=f"HTTP {r.status_code}")
    series = r.json()
    changes = series[0] if series and isinstance(series[0], list) else []
    if not changes:
        return t("ha_history_no_changes", lang, entity_id=entity_id, days=days)
    try:
        local_tz = get_local_tz()
    except Exception:
        local_tz = ZoneInfo("UTC")
    rows = []
    for ch in changes[-max_rows:]:
        try:
            when = (
                _parse_ha_time(ch.get("last_changed"))
                .astimezone(local_tz)
                .strftime("%Y-%m-%d %H:%M")
            )
        except Exception:
            when = str(ch.get("last_changed", "?"))[:16]
        rows.append(f"  {when} → {ch.get('state')}")
    lines = [t("ha_history_changes_header", lang, entity_id=entity_id, days=days), *rows]
    if len(changes) > max_rows:
        lines.append(t("ha_history_changes_truncated", lang, shown=max_rows, total=len(changes)))
    return "\n".join(lines)


async def _ha_sensor_history(entity_id: str, days: int, period: str, lang: str) -> str:
    """
    Long-term statistics for a HA entity (mean/min/max/change per day/week/month) via the recorder's
    WebSocket command `recorder/statistics_during_period`. Only entities with a state_class have them;
    for the rest the raw state changes from the REST history are returned instead.
    """
    ha_url = _read_ha_url()
    try:
        token = get_ha_token()
    except ValueError:
        return t("ha_history_not_configured", lang)
    if not ha_url:
        return t("ha_history_not_configured", lang)

    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    message = {
        "type": "recorder/statistics_during_period",
        "start_time": start.isoformat(),
        "end_time": now.isoformat(),
        "statistic_ids": [entity_id],
        "period": period,
        "types": ["mean", "min", "max", "change"],
    }

    async def _state() -> httpx.Response:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(f"{ha_url}/api/states/{entity_id}", headers=headers)

    stats, state_resp = await asyncio.gather(
        _ha_ws_call(ha_url, token, message), _state(), return_exceptions=True
    )

    unit = ""
    if not isinstance(state_resp, BaseException):
        if state_resp.status_code == 404:
            return t("ha_entity_not_found", lang, entity_id=entity_id)
        if state_resp.status_code == 200:
            unit_raw = state_resp.json().get("attributes", {}).get("unit_of_measurement", "")
            unit = f" {unit_raw}" if unit_raw else ""

    if isinstance(stats, BaseException):
        return t("ha_history_error", lang, entity_id=entity_id, error=stats)

    entries: list[dict] = (stats or {}).get(entity_id, [])
    if not entries:
        return await _ha_state_changes(ha_url, headers, entity_id, start, now, days, lang)

    # Resolve period label for header
    period_label = t(f"ha_history_period_{period}", lang)
    header = t("ha_history_header", lang, entity_id=entity_id, days=days, period=period_label)

    # Build per-period rows
    # Use local timezone for date display if available
    try:
        local_tz = get_local_tz()
    except Exception:
        local_tz = ZoneInfo("UTC")

    rows: list[str] = []
    all_means: list[float] = []
    all_mins: list[float] = []
    all_maxes: list[float] = []
    all_changes: list[float] = []

    for entry in entries:
        try:
            date_str = _parse_ha_time(entry["start"]).astimezone(local_tz).strftime("%Y-%m-%d")
        except Exception:
            date_str = str(entry.get("start", "?"))[:10]

        parts: list[str] = []
        if (v := entry.get("mean")) is not None:
            all_means.append(float(v))
            parts.append(
                f"snitt {float(v):.1f}{unit}"
                if lang == "nb"
                else f"avg {float(v):.1f}{unit}"
                if lang == "en"
                else f"Durchschn. {float(v):.1f}{unit}"
            )
        if (v := entry.get("min")) is not None:
            all_mins.append(float(v))
            parts.append(f"min {float(v):.1f}{unit}")
        if (v := entry.get("max")) is not None:
            all_maxes.append(float(v))
            parts.append(
                f"maks {float(v):.1f}{unit}" if lang == "nb" else f"max {float(v):.1f}{unit}"
            )
        if (v := entry.get("change")) is not None and v is not None:
            all_changes.append(float(v))
            parts.append(f"Δ {float(v):.1f}{unit}")
        rows.append(f"  {date_str}: {', '.join(parts)}" if parts else f"  {date_str}: —")

    # Build summary
    summary_parts: list[str] = []
    if all_maxes:
        summary_parts.append(
            t(
                "ha_history_summary",
                lang,
                max_val=max(all_maxes),
                mean_val=sum(all_means) / len(all_means) if all_means else 0,
                min_val=min(all_mins) if all_mins else 0,
                unit=unit,
            )
        )
    # Accumulated total from change values (useful for precipitation sensors that reset daily)
    if all_changes:
        total = sum(all_changes)
        summary_parts.append(t("ha_history_total", lang, total=total, unit=unit))

    lines = [header, *rows]
    if summary_parts:
        lines.append("")
        lines.extend(summary_parts)

    return "\n".join(lines)


async def _read_ha_status(entity_id: str, lang: str = "nb") -> str:
    if not entity_id:
        return t("ha_entity_id_required", lang)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{HA_GATEWAY_URL}/api/ha_status/{entity_id}", headers=service_headers()
            )
            if r.status_code == 404:
                return t("ha_entity_not_found", lang, entity_id=entity_id)
            r.raise_for_status()
            data = r.json()
        state = data.get("state", "ukjent")
        unit = data.get("unit", "")
        friendly = data.get("friendly", entity_id)
        value = f"{state} {unit}".strip()
        return f"{friendly}: {value}"
    except Exception as e:
        return t("ha_read_error", lang, entity_id=entity_id, error=e)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in ("ha_read", "les_ha"):
        action = arguments.get("action", "")
        if action == "room_list":
            return _format_places(rooms.get_register(), lang)
        if action == "room_devices":
            return _format_place_things(arguments, lang)
        if action == "status":
            entity_id = str(arguments.get("entity_id", ""))
            out = await _read_ha_status(entity_id, lang=lang)
            if entity_id.startswith("media_player."):
                out += await tv_own_state(entity_id, lang)
            return out
        return f"Unknown action for ha_read: '{action}'. Valid: room_list, room_devices, status."

    if name == "les_alias_lista":
        return (
            _format_place_things(arguments, lang)
            if arguments.get("room")
            else _format_places(rooms.get_register(), lang)
        )

    if name == "les_ha_status":
        return await _read_ha_status(arguments.get("entity_id", ""), lang=lang)

    if name in ("ha_control", "styr_enhet"):
        action = arguments.get("action", "")

        if action == "ha_history":
            entity_id = arguments.get("entity_id", "").strip()
            if not entity_id:
                return t("ha_history_no_entity", lang)
            days = max(1, min(int(arguments.get("history_days", 7)), 365))
            period = arguments.get("history_period", "day")
            if period not in ("day", "week", "month"):
                period = "day"
            return await _ha_sensor_history(entity_id, days, period, lang)

        if arguments.get("_block_ha_write"):
            return t("ha_blocked_external", lang)
        return await _control_entity(
            entity_id=arguments.get("entity_id", ""),
            action=action,
            brightness_pct=arguments.get("brightness_pct"),
            color_temp_kelvin=arguments.get("color_temp_kelvin"),
            rgb_color=arguments.get("rgb_color"),
            lang=lang,
            location={
                k: arguments.get(k) for k in ("room", "floor", "domain", "_source_node", "_rid")
            },
            volume_pct=arguments.get("volume_pct"),
            source=arguments.get("source"),
        )

    return f"[executor_ha] Unknown tool: {name}"
