"""Home Assistant registry sync (P137 phase 2): HA's floors, areas, labels, devices and entities → one snapshot file.

Writes `state/ha_registry.json`, which kaare_core/rooms.py reads (mtime cache) — no HA lookups in the request path.
Read-only towards HA, and every command used works with a normal (non-admin) user token: the registry `list`
commands and the `*_registry_updated` events are allowed for every user (HA core, verified 2026-10-17 — P137
Research). The template API is admin-only and not used.

Runs as a background task in the API process: one fetch at start, then a subscription to the five registry events;
any event triggers a fresh fetch after a short quiet period (several changes in a row → one fetch). On a lost
connection it retries with backoff; without HA configured it checks the config again every minute. `sync_now()`
is the GUI's «Synkroniser nå».

Only events that can change the snapshot trigger a fetch: an entity/device `update` whose `changes` touch none of
the fields below is ignored (HA sends these all day — e.g. a media player's `supported_features` flipping when it
switches source, a battery icon). Create, remove, rename and area/floor/label events always fetch. The file is only
rewritten when the snapshot's content (all but `fetched_at`) differs from what is on disk.

Snapshot rows per entity (disabled entities left out): name (HA's friendly name), area_id (the entity's own area,
else its device's — HA's rule), device_id, device_name, domain, labels, area_labels, exposed (to HA's Assist,
from the entity options), category (has an entity_category → diagnostic/config), hidden.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets

from kaare_core.config import get_service
from kaare_core.rooms import HA_CACHE_PATH
from kaare_core.tools.executor_ha import get_ha_token

_log = logging.getLogger("ha_registry_sync")

REGISTRY_EVENTS = (
    "area_registry_updated",
    "floor_registry_updated",
    "label_registry_updated",
    "device_registry_updated",
    "entity_registry_updated",
)
_DEBOUNCE_S = 3.0
_IDLE_RECHECK_S = 60.0
_BACKOFF_MAX_S = 300.0
_CMD_TIMEOUT_S = 30.0

# Registry fields build_snapshot() reads (plus has_entity_name / device disabled_by, which change the friendly name
# or the entity set indirectly). tests/test_p137_ha_registry_sync.py fails if the snapshot starts reading a field
# that is missing here.
ENTITY_FIELDS = frozenset({
    "entity_id", "device_id", "area_id", "name", "original_name", "has_entity_name", "options", "labels",
    "entity_category", "hidden_by", "disabled_by",
})
DEVICE_FIELDS = frozenset({"id", "area_id", "name", "name_by_user", "disabled_by"})
_UPDATE_FIELDS = {"entity_registry_updated": ENTITY_FIELDS, "device_registry_updated": DEVICE_FIELDS}

_status: dict[str, Any] = {"state": "idle", "last_sync": None, "error": "", "counts": {}}
_wake = asyncio.Event()
_last_content: dict[Path, dict] = {}  # per snapshot path: content (without fetched_at) as last written or found on disk


def request_resync() -> None:
    """Fetch again soon — called when the HA address or token is saved in the GUI (settings or onboarding), so the
    places appear without waiting for the next check."""
    _wake.set()


def status() -> dict:
    """For the GUI: state (idle | not_configured | connected | error), last sync time, last error, counts."""
    return dict(_status)


def _ha_config() -> tuple[str, str]:
    try:
        url = (get_service("home_assistant", "url") or "").rstrip("/")
    except Exception:
        url = ""
    try:
        token = get_ha_token()
    except Exception:
        token = ""
    return url, token


def _ws_url(ha_url: str) -> str:
    return ha_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/websocket"


class _Conn:
    """One authenticated HA WebSocket with numbered commands; event messages are queued for the caller."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 1
        self.events: asyncio.Queue = asyncio.Queue()

    async def call(self, msg_type: str, **fields) -> Any:
        mid = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"id": mid, "type": msg_type, **fields}))
        async with asyncio.timeout(_CMD_TIMEOUT_S):
            while True:
                reply = json.loads(await self.ws.recv())
                if reply.get("type") == "event":
                    self.events.put_nowait(reply)
                    continue
                if reply.get("id") == mid and reply.get("type") == "result":
                    if not reply.get("success"):
                        raise RuntimeError(f"{msg_type}: {(reply.get('error') or {}).get('message') or 'HA error'}")
                    return reply.get("result")


async def _connect(ha_url: str, token: str) -> _Conn:
    ws = await websockets.connect(_ws_url(ha_url), max_size=None, open_timeout=15, ping_interval=30)
    if json.loads(await ws.recv()).get("type") != "auth_required":
        await ws.close()
        raise RuntimeError("unexpected HA WebSocket greeting")
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    if json.loads(await ws.recv()).get("type") != "auth_ok":
        await ws.close()
        raise RuntimeError("HA WebSocket authentication failed — check the HA token")
    return _Conn(ws)


def _names_list(value: Any) -> list[str]:
    return [str(v) for v in (value or []) if v]


def build_snapshot(raw: dict[str, Any]) -> dict:
    """Pure: registry lists + states → the snapshot rooms.py reads."""
    areas_raw = raw.get("areas") or []
    devices = {d.get("id"): d for d in raw.get("devices") or [] if isinstance(d, dict)}
    friendly = {
        s.get("entity_id"): (s.get("attributes") or {}).get("friendly_name")
        for s in raw.get("states") or [] if isinstance(s, dict)
    }
    # A registry entry the integration no longer provides (e.g. after a ZHA firmware/quirk change) stays in HA as
    # an unavailable state with `restored: true` — a leftover, not a device.
    orphans = {
        s.get("entity_id") for s in raw.get("states") or []
        if isinstance(s, dict) and s.get("state") == "unavailable" and (s.get("attributes") or {}).get("restored")
    }
    area_labels = {a.get("area_id"): _names_list(a.get("labels")) for a in areas_raw}
    snap: dict[str, Any] = {
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ha_version": raw.get("version") or "",
        "floors": {
            f["floor_id"]: {"name": f.get("name") or f["floor_id"], "level": f.get("level"),
                            "aliases": _names_list(f.get("aliases"))}
            for f in raw.get("floors") or [] if f.get("floor_id")
        },
        "areas": {
            a["area_id"]: {"name": a.get("name") or a["area_id"], "floor_id": a.get("floor_id") or "",
                           "aliases": _names_list(a.get("aliases")), "labels": area_labels.get(a["area_id"], []),
                           "temperature_entity_id": a.get("temperature_entity_id") or "",
                           "humidity_entity_id": a.get("humidity_entity_id") or ""}
            for a in areas_raw if a.get("area_id")
        },
        "labels": {
            lb["label_id"]: {"name": lb.get("name") or lb["label_id"]}
            for lb in raw.get("labels") or [] if lb.get("label_id")
        },
        "entities": {},
    }
    disabled = {e.get("entity_id") for e in raw.get("entities") or [] if e.get("disabled_by")}
    for e in raw.get("entities") or []:
        eid = e.get("entity_id")
        if not eid or e.get("disabled_by"):
            continue
        dev = devices.get(e.get("device_id")) or {}
        area = e.get("area_id") or dev.get("area_id") or ""
        opts = e.get("options") or {}
        snap["entities"][eid] = {
            "name": friendly.get(eid) or e.get("name") or e.get("original_name") or eid,
            "area_id": area,
            "device_id": e.get("device_id") or "",
            "device_name": dev.get("name_by_user") or dev.get("name") or "",
            "domain": eid.split(".", 1)[0],
            "labels": _names_list(e.get("labels")),
            "area_labels": area_labels.get(area, []),
            "exposed": bool((opts.get("conversation") or {}).get("should_expose")),
            "category": bool(e.get("entity_category")),
            "hidden": bool(e.get("hidden_by")),
            "orphan": eid in orphans,
        }
    # Entities HA has no registry entry for (YAML-defined without unique_id) still have a state and a name — keep
    # them so their names show; they have no area (HA cannot place them either).
    for eid, name in friendly.items():
        if eid and eid not in snap["entities"] and eid not in disabled:
            snap["entities"][eid] = {
                "name": name or eid, "area_id": "", "device_id": "", "device_name": "",
                "domain": eid.split(".", 1)[0], "labels": [], "area_labels": [], "exposed": False,
                "category": False, "hidden": False, "orphan": False,
            }
    return snap


async def _fetch(conn: _Conn) -> dict:
    raw = {
        "version": (await conn.call("get_config") or {}).get("version"),
        "floors": await conn.call("config/floor_registry/list"),
        "areas": await conn.call("config/area_registry/list"),
        "labels": await conn.call("config/label_registry/list"),
        "devices": await conn.call("config/device_registry/list"),
        "entities": await conn.call("config/entity_registry/list"),
        "states": await conn.call("get_states"),
    }
    return build_snapshot(raw)


def is_relevant(event: dict) -> bool:
    """Whether a registry event can change the snapshot. Unknown shapes count as relevant (fetch rather than miss)."""
    fields = _UPDATE_FIELDS.get(event.get("event_type") or "")
    data = event.get("data") or {}
    if fields is None or data.get("action") != "update" or data.get("old_entity_id"):
        return True
    changes = data.get("changes")
    if not isinstance(changes, dict) or not changes:
        return True
    return not fields.isdisjoint(changes)


def _content(snap: dict) -> dict:
    return {k: v for k, v in snap.items() if k != "fetched_at"}


def store_snapshot(snap: dict, path: Path | None = None) -> bool:
    """Write the snapshot only when its content differs from the last one (on disk at start). True = written."""
    path = path or HA_CACHE_PATH
    content = _content(snap)
    if path not in _last_content:
        try:
            _last_content[path] = _content(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _last_content[path] = {}
    if content == _last_content[path]:
        return False
    write_snapshot(snap, path)
    _last_content[path] = content
    return True


def write_snapshot(snap: dict, path: Path = HA_CACHE_PATH) -> None:
    """Atomic write (tmp + rename) so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ha_registry.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o664)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _record(snap: dict) -> None:
    _status.update(
        state="connected", last_sync=snap.get("fetched_at"), error="",
        counts={k: len(snap.get(k) or {}) for k in ("floors", "areas", "labels", "entities")},
    )


async def sync_now() -> dict:
    """One fetch on its own connection (GUI «Synkroniser nå», onboarding). Returns status()."""
    url, token = _ha_config()
    if not url or not token:
        _status.update(state="not_configured", error="")
        return status()
    try:
        conn = await _connect(url, token)
        try:
            snap = await _fetch(conn)
        finally:
            await conn.ws.close()
        await asyncio.to_thread(store_snapshot, snap)
        _record(snap)
    except Exception as e:
        _status.update(state="error", error=str(e))
        _log.warning("[HA-REGISTRY] sync failed: %s", e)
    return status()


async def _run_once(url: str, token: str) -> None:
    conn = await _connect(url, token)
    try:
        for ev in REGISTRY_EVENTS:
            await conn.call("subscribe_events", event_type=ev)
        snap = await _fetch(conn)
        await asyncio.to_thread(store_snapshot, snap)
        _record(snap)
        _log.info("[HA-REGISTRY] synced: %s", _status["counts"])
        while True:
            # Wait for an event (or a config change from the GUI); then let a burst settle and fetch once.
            if conn.events.empty():
                recv = asyncio.ensure_future(conn.ws.recv())
                wake = asyncio.ensure_future(_wake.wait())
                done, _pending = await asyncio.wait({recv, wake}, return_when=asyncio.FIRST_COMPLETED)
                if wake in done:
                    recv.cancel()
                    _wake.clear()
                    if _ha_config() != (url, token):
                        return  # reconnect with the new config
                    # same config: fall through and fetch again (GUI asked for it)
                else:
                    wake.cancel()
                    msg = json.loads(recv.result())
                    if msg.get("type") != "event" or not is_relevant(msg.get("event") or {}):
                        continue
            elif not is_relevant(conn.events.get_nowait().get("event") or {}):
                continue
            await asyncio.sleep(_DEBOUNCE_S)
            while not conn.events.empty():
                conn.events.get_nowait()
            snap = await _fetch(conn)
            written = await asyncio.to_thread(store_snapshot, snap)
            _record(snap)
            if written:
                _log.info("[HA-REGISTRY] updated after HA change: %s", _status["counts"])
            else:
                _log.debug("[HA-REGISTRY] HA change, snapshot unchanged")
    finally:
        await conn.ws.close()


async def start_ha_registry_sync() -> None:
    """Background task (kaare_api lifespan). Never raises."""
    backoff = 5.0
    while True:
        url, token = _ha_config()
        if not url or not token:
            _status.update(state="not_configured", error="")
            try:
                await asyncio.wait_for(_wake.wait(), timeout=_IDLE_RECHECK_S)
            except TimeoutError:
                pass
            _wake.clear()
            continue
        try:
            await _run_once(url, token)
            backoff = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _status.update(state="error", error=str(e))
            _log.warning("[HA-REGISTRY] connection lost (%s) — retry in %.0f s", e, backoff)
            try:
                await asyncio.wait_for(_wake.wait(), timeout=backoff)
            except TimeoutError:
                pass
            _wake.clear()
            backoff = min(backoff * 2, _BACKOFF_MAX_S)
