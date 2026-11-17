"""Voice nodes started by Home Assistant (P138 phase 4): a press on any HA button starts a node's microphone.

A node in nodes.yaml may carry a `trigger:` block, set in the GUI (Nodes → Start from Home Assistant):

    trigger:
      kind: ha_event            # an HA event, e.g. zha_event from a Zigbee scene switch
      event_type: zha_event
      match: {device_ieee: "a4:c1:…", command: toggle}   # every key must equal the event's data
    trigger:
      kind: entity              # an entity changing, e.g. event.* (Z2M, Hue, Matter, newer ZHA) or sensor.*_action
      entity_id: event.hall_button
      to: single                # optional: the new state, or an event entity's event_type, must equal this

On a match the voice bridge's `/button/{node}` is called with the service token — the same pipeline, ducking and
route-log events as the node's own button. A node fires at most once per `_DEBOUNCE_S` (a scene switch sends
several events for one press).

Its own HA WebSocket (the registry sync's connection treats every event as a registry change). Entity triggers use
`subscribe_entities` (filtered by HA, allowed for every user token); event triggers use `subscribe_events`, which
HA allows for non-admin tokens only for a short allow-list — `zha_event` needs an admin token. A refused
subscription is reported per node in status() so the GUI can say so and offer the entity or webhook route.

`learn()` listens for the next press (GUI «Lær»): button-type events and changes of button-like entities, and
returns a ready trigger block.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from kaare_core import ha_registry_sync as hrs
from kaare_core.config import get_service
from kaare_core.rooms import HA_CACHE_PATH, NODES_PATH, read_ha_cache
from kaare_core.service_auth import service_headers

_log = logging.getLogger("ha_node_triggers")

KIND_EVENT = "ha_event"
KIND_ENTITY = "entity"
_DEBOUNCE_S = 2.0
_CONFIG_POLL_S = 5.0
_BACKOFF_MAX_S = 300.0
_LEARN_TIMEOUT_S = 20.0

# Integrations that report button presses as their own HA event (not an entity). Learn listens to these.
BUTTON_EVENTS = (
    "zha_event",
    "deconz_event",
    "hue_event",
    "zwave_js_value_notification",
    "zwave_js_notification",
    "lutron_caseta_button_event",
    "shelly.click",
    "homematic.keypress",
    "esphome.button_pressed",
)
# Keys from an event's data that identify «this button, this press» — the rest (timestamps, args) varies per press.
MATCH_KEYS = (
    "device_ieee",
    "device_id",
    "unique_id",
    "endpoint_id",
    "command",
    "type",
    "subtype",
    "event",
    "button",
    "click_type",
    "action",
    "property_key_name",
    "value",
)
# Entity domains a press shows up in (learn): event entities, and the classic sensor.*_action pattern.
_LEARN_DOMAINS = ("event", "button", "input_button")

_status: dict[str, Any] = {"state": "idle", "error": "", "nodes": {}}
_wake = asyncio.Event()
_last_fired: dict[str, float] = {}


def request_reload() -> None:
    """Re-read the triggers now (Nodes saved in the GUI)."""
    _wake.set()


def status() -> dict:
    """For the GUI: state (idle | not_configured | connected | error), error, and per node: ok / refused reason."""
    return {**_status, "nodes": dict(_status["nodes"])}


def _read_nodes(path: Path = NODES_PATH) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        nodes = data.get("nodes") or {}
        return nodes if isinstance(nodes, dict) else {}
    except Exception:
        return {}


def node_triggers(nodes: dict) -> dict[str, dict]:
    """Enabled nodes with a usable trigger block → {node_id: trigger}."""
    out: dict[str, dict] = {}
    for nid, cfg in nodes.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue
        trig = cfg.get("trigger")
        if not isinstance(trig, dict):
            continue
        kind = trig.get("kind")
        if (kind == KIND_EVENT and str(trig.get("event_type") or "").strip()) or (kind == KIND_ENTITY and str(trig.get("entity_id") or "").strip()):
            out[nid] = trig
    return out


def event_matches(trigger: dict, event_type: str, data: dict) -> bool:
    if trigger.get("kind") != KIND_EVENT or str(trigger.get("event_type")) != event_type:
        return False
    match = trigger.get("match") or {}
    if not isinstance(match, dict):
        return False
    return all(str(data.get(k)) == str(v) for k, v in match.items())


def entity_matches(trigger: dict, entity_id: str, new_state: str, attrs: dict) -> bool:
    """A change of the trigger's entity. `to` (optional) must equal the new state or, for an event entity, its
    event_type. An event entity's state is the time of the last press, so «any change» is a press."""
    if trigger.get("kind") != KIND_ENTITY or str(trigger.get("entity_id")) != entity_id:
        return False
    if new_state in ("unavailable", "unknown", ""):
        return False
    want = str(trigger.get("to") or "").strip()
    if not want:
        return True
    return want in (new_state, str((attrs or {}).get("event_type") or ""))


def _debounced(node_id: str, now: float | None = None) -> bool:
    """True if the node fired too recently (one press = several events on some switches)."""
    now = time.monotonic() if now is None else now
    last = _last_fired.get(node_id)
    if last is not None and now - last < _DEBOUNCE_S:
        return True
    _last_fired[node_id] = now
    return False


async def fire(node_id: str) -> bool:
    """Start the node's microphone through the voice bridge — same path as the node's own button."""
    if _debounced(node_id):
        return False
    try:
        url = f"{get_service('internal', 'voice_bridge')}/button/{node_id}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=service_headers())
        ok = resp.status_code < 300
        _log.info("[HA-TRIGGER] %s → voice bridge %s", node_id, resp.status_code)
        return ok
    except Exception as exc:
        _log.warning("[HA-TRIGGER] could not start %s: %s", node_id, exc)
        return False


def _entity_changes(msg: dict) -> list[tuple[str, str, dict]]:
    """subscribe_entities event → [(entity_id, new state, changed attributes)]. Initial `a` rows are the current
    states, not presses, and are skipped."""
    ev = msg.get("event") or {}
    out: list[tuple[str, str, dict]] = []
    for eid, diff in (ev.get("c") or {}).items():
        plus = (diff or {}).get("+") or {}
        if "s" in plus:
            out.append((eid, str(plus.get("s") or ""), plus.get("a") or {}))
    return out


async def _subscribe(conn, triggers: dict[str, dict]) -> tuple[dict[int, str], dict[str, str]]:
    """Subscribe what the triggers need. Returns ({subscription id: event_type or 'entities'}, {node: refusal})."""
    subs: dict[int, str] = {}
    refused: dict[str, str] = {}
    event_types = sorted(
        {str(t["event_type"]) for t in triggers.values() if t.get("kind") == KIND_EVENT}
    )
    for et in event_types:
        sid = conn.next_id
        try:
            await conn.call("subscribe_events", event_type=et)
            subs[sid] = et
        except Exception as exc:
            reason = "admin_required" if "unauthorized" in str(exc).lower() else str(exc)
            for nid, t in triggers.items():
                if t.get("kind") == KIND_EVENT and t.get("event_type") == et:
                    refused[nid] = reason
            _log.warning("[HA-TRIGGER] subscribe %s refused: %s", et, exc)
    entity_ids = sorted(
        {str(t["entity_id"]) for t in triggers.values() if t.get("kind") == KIND_ENTITY}
    )
    if entity_ids:
        sid = conn.next_id
        try:
            await conn.call("subscribe_entities", entity_ids=entity_ids)
            subs[sid] = "entities"
        except Exception as exc:
            for nid, t in triggers.items():
                if t.get("kind") == KIND_ENTITY:
                    refused[nid] = str(exc)
            _log.warning("[HA-TRIGGER] subscribe_entities refused: %s", exc)
    return subs, refused


async def handle_message(msg: dict, subs: dict[int, str], triggers: dict[str, dict]) -> list[str]:
    """One WebSocket event → the nodes it starts (fired)."""
    kind = subs.get(msg.get("id"))
    hits: list[str] = []
    if kind == "entities":
        for eid, state, attrs in _entity_changes(msg):
            hits += [n for n, t in triggers.items() if entity_matches(t, eid, state, attrs)]
    elif kind:
        ev = msg.get("event") or {}
        et, data = str(ev.get("event_type") or ""), ev.get("data") or {}
        hits += [n for n, t in triggers.items() if event_matches(t, et, data)]
    return [nid for nid in dict.fromkeys(hits) if await fire(nid)]


def _signature(path: Path = NODES_PATH) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


async def _run_once(url: str, token: str, triggers: dict[str, dict]) -> None:
    conn = await hrs._connect(url, token)
    try:
        subs, refused = await _subscribe(conn, triggers)
        _status.update(
            state="connected",
            error="",
            nodes={
                nid: {"ok": nid not in refused, "reason": refused.get(nid, "")} for nid in triggers
            },
        )
        _log.info(
            "[HA-TRIGGER] listening for %d node(s), refused: %s", len(triggers), refused or "none"
        )
        sig = _signature()
        while True:
            while not conn.events.empty():
                await handle_message(conn.events.get_nowait(), subs, triggers)
            recv = asyncio.ensure_future(conn.ws.recv())
            wake = asyncio.ensure_future(_wake.wait())
            done, _ = await asyncio.wait(
                {recv, wake}, timeout=_CONFIG_POLL_S, return_when=asyncio.FIRST_COMPLETED
            )
            if recv in done:
                wake.cancel()
                msg = json.loads(recv.result())
                if msg.get("type") == "event":
                    await handle_message(msg, subs, triggers)
                continue
            recv.cancel()
            wake.cancel()
            if _wake.is_set() or _signature() != sig or hrs._ha_config() != (url, token):
                _wake.clear()
                return  # reload: triggers or HA config changed
    finally:
        await conn.ws.close()


async def start_ha_node_triggers() -> None:
    """Background task (kaare_api lifespan). Idle while no node has a trigger. Never raises."""
    backoff = 5.0
    while True:
        triggers = node_triggers(_read_nodes())
        url, token = hrs._ha_config()
        if not triggers or not url or not token:
            _status.update(state="idle" if not triggers else "not_configured", error="", nodes={})
            sig = _signature()
            # Wait for a GUI save (wake) or a hand edit of nodes.yaml (mtime, checked every few seconds).
            while _signature() == sig:
                try:
                    await asyncio.wait_for(_wake.wait(), timeout=_CONFIG_POLL_S)
                    break
                except TimeoutError:
                    continue
            _wake.clear()
            continue
        try:
            await _run_once(url, token, triggers)
            backoff = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _status.update(state="error", error=str(exc))
            _log.warning("[HA-TRIGGER] connection lost (%s) — retry in %.0f s", exc, backoff)
            try:
                await asyncio.wait_for(_wake.wait(), timeout=backoff)
            except TimeoutError:
                pass
            _wake.clear()
            backoff = min(backoff * 2, _BACKOFF_MAX_S)


# ── Learn (GUI «Lær») ─────────────────────────────────────────────────────────


def _learn_entity_ids(snapshot: dict) -> list[str]:
    ents = (snapshot or {}).get("entities") or {}
    return sorted(
        eid
        for eid, row in ents.items()
        if (
            row.get("domain") in _LEARN_DOMAINS
            or (row.get("domain") == "sensor" and eid.endswith("_action"))
        )
        and not row.get("orphan")
    )


def candidate_from_event(event_type: str, data: dict, snapshot: dict | None = None) -> dict:
    match = {k: data[k] for k in MATCH_KEYS if k in data and data[k] not in (None, "", [], {})}
    label = event_type
    dev = str(data.get("device_id") or "")
    if dev and snapshot:
        for row in (snapshot.get("entities") or {}).values():
            if row.get("device_id") == dev and row.get("device_name"):
                label = f"{row['device_name']} ({event_type})"
                break
    return {"kind": KIND_EVENT, "event_type": event_type, "match": match, "label": label}


def candidate_from_entity(
    entity_id: str, state: str, attrs: dict, snapshot: dict | None = None
) -> dict:
    row = ((snapshot or {}).get("entities") or {}).get(entity_id) or {}
    to = str((attrs or {}).get("event_type") or "") if entity_id.startswith("event.") else state
    if entity_id.split(".", 1)[0] in ("button", "input_button"):
        to = ""  # a button entity's state is the press time — any change is a press
    return {
        "kind": KIND_ENTITY,
        "entity_id": entity_id,
        "to": to,
        "label": row.get("name") or entity_id,
    }


async def learn(timeout: float = _LEARN_TIMEOUT_S) -> dict:
    """Wait for the next button press anywhere in HA and return a trigger block for it.

    {"ok": True, "trigger": {...}, "admin": bool} — or {"ok": False, "reason": "timeout" | "not_configured" | …}.
    `admin` = HA allowed the button-event subscriptions (False: only entity presses can be learnt; the GUI says so).
    """
    url, token = hrs._ha_config()
    if not url or not token:
        return {"ok": False, "reason": "not_configured"}
    snapshot = read_ha_cache(HA_CACHE_PATH)
    conn = await hrs._connect(url, token)
    try:
        subs: dict[int, str] = {}
        admin = True
        for et in BUTTON_EVENTS:
            sid = conn.next_id
            try:
                await conn.call("subscribe_events", event_type=et)
                subs[sid] = et
            except Exception:
                admin = False
                break  # non-admin: every one of these is refused the same way
        entity_ids = _learn_entity_ids(snapshot)
        if entity_ids:
            sid = conn.next_id
            await conn.call("subscribe_entities", entity_ids=entity_ids)
            subs[sid] = "entities"
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            if conn.events.empty():
                try:
                    msg = json.loads(await asyncio.wait_for(conn.ws.recv(), timeout=left))
                except TimeoutError:
                    break
            else:
                msg = conn.events.get_nowait()
            if msg.get("type") != "event":
                continue
            kind = subs.get(msg.get("id"))
            if kind == "entities":
                for eid, state, attrs in _entity_changes(msg):
                    if state not in ("unavailable", "unknown", ""):
                        cand = candidate_from_entity(eid, state, attrs, snapshot)
                        return {"ok": True, "trigger": cand, "admin": admin}
            elif kind:
                ev = msg.get("event") or {}
                cand = candidate_from_event(
                    str(ev.get("event_type") or kind), ev.get("data") or {}, snapshot
                )
                return {"ok": True, "trigger": cand, "admin": admin}
        return {"ok": False, "reason": "timeout", "admin": admin}
    finally:
        await conn.ws.close()
