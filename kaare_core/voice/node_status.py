"""Is a node reachable right now? (N29) — the Nodes page's WiFi icon.

`plan_probe` (pure) picks one check per node from what its config already holds; `node_statuses` runs them all in
parallel with a short timeout and keeps the answer for a few seconds, so a page polling every 30 s costs one round of
cheap checks. Nothing runs when nobody has the page open.
"""

import asyncio
import contextlib
import time

import httpx

from kaare_core.config import get_service
from kaare_core.service_auth import service_headers
from kaare_core.tools.executor_ha import get_ha_token
from kaare_core.voice import channels
from kaare_core.voice.direct_node import is_direct, with_direct

ONLINE = "online"
OFFLINE = "offline"
UNKNOWN = "unknown"  # nothing to check with (no entity, host or port)

_TIMEOUT_S = 1.5
_CACHE_S = 20.0
_cache: dict = {"at": 0.0, "key": None, "data": None}

# Ports a device of this type listens on when the node config names none. Wyoming: an ESPHome satellite answers on
# its API port, a wyoming-satellite (Raspberry Pi) on 10700 — the node's own api_port is Kåre's server port.
_TYPE_PORTS: dict[str, tuple[int, ...]] = {
    "wyoming": (6053, 10700),
    "esp32": (6053,),
    "chromecast": (8009,),
    # Android/Google TV: KTSB talks to them through TvOverlay (5001) and Cast (8009) — ADB (5555) is rarely on.
    "google_tv": (8009, 5001),
    "android_tv": (5001, 8009),
    "fire_tv": (5555,),
    "airplay": (7000,),
    "apple_tv": (7000,),
    "samsung_tv": (8001, 8002),
    "lg_tv": (3000, 3001),
}


_API_PORT_TYPES = frozenset({"esp32", "snapcast", "dlna"})


def plan_probe(node: dict) -> tuple:
    """("direct",) | ("channels", host) | ("ha", entity_id) | ("tcp", host, (ports…)) | ("ping", host) | ("none",)."""
    if is_direct(node):
        return ("direct",)
    if channels.wants_channels(node):
        return ("channels", str(node.get("host")).strip())  # P139 phase 2: every channel port, per-channel answer
    ntype = str(node.get("type") or "")
    entity = str(node.get("entity_id") or "").strip()
    host = str(node.get("host") or "").strip()
    if entity and (ntype == "ha_media_player" or not host):
        return ("ha", entity)
    if host:
        ports = _TYPE_PORTS.get(ntype)
        # api_port is the device's own service port only for these; on TVs the GUI fills in ADB (5555), which KTSB
        # never uses, and on Wyoming nodes it is Kåre's server port.
        if ntype in _API_PORT_TYPES and node.get("api_port"):
            ports = (int(node["api_port"]),)
        elif ntype in ("android_tv", "google_tv") and node.get("tvoverlay_port"):
            ports = tuple(dict.fromkeys((int(node["tvoverlay_port"]), *(ports or ()))))
        if ports:
            return ("tcp", host, tuple(ports))
        return ("ping", host)
    return ("none",)


async def _tcp(host: str, ports: tuple[int, ...]) -> tuple[str, str]:
    async def one(port: int) -> bool:
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), _TIMEOUT_S)
        except Exception:
            return False
        w.close()
        with contextlib.suppress(Exception):
            await w.wait_closed()
        return True

    results = await asyncio.gather(*(one(p) for p in ports))
    good = [p for p, ok in zip(ports, results, strict=True) if ok]
    if good:
        return ONLINE, f"{host}:{good[0]}"
    return OFFLINE, f"{host}:{'/'.join(str(p) for p in ports)}"


async def _channels(node: dict, host: str) -> tuple[str, str, dict[str, bool]]:
    """A screen node: online if any channel port answers (a Google TV in standby keeps Cast + remote, drops
    TvOverlay). Returns the answer per channel: the node's own channels (answered or not) plus any port channel
    that answered but is not listed yet (the dialog can add it)."""
    entries = channels.node_channels(node)
    plan = {
        n: ((channels.port_of(n, entries.get(n)),) if n in entries else channels.CATALOGUE[n][0])
        for n in channels.PORT_CHANNELS
    }
    found = await channels.probe(host, plan)
    per = {n: n in found for n in entries if channels.CATALOGUE[n][0]}
    per.update(dict.fromkeys(found, True))
    detail = ", ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in per.items()) or host
    return (ONLINE if found else OFFLINE), detail, per


async def _ping(host: str) -> tuple[str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            "1",
            host,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        code = await asyncio.wait_for(proc.wait(), _TIMEOUT_S + 1)
    except Exception:
        return UNKNOWN, f"ping {host}"
    return (ONLINE if code == 0 else OFFLINE), f"ping {host}"


async def _ha(
    client: httpx.AsyncClient, ha_url: str, headers: dict, entity: str
) -> tuple[str, str]:
    if not ha_url or not headers:
        return UNKNOWN, entity
    try:
        r = await client.get(f"{ha_url}/api/states/{entity}", headers=headers)
    except Exception:
        return UNKNOWN, f"{entity} (HA)"
    if r.status_code == 404:
        return OFFLINE, f"{entity}: not in HA"
    if r.status_code >= 400:
        return UNKNOWN, f"{entity} (HA {r.status_code})"
    state = (r.json() or {}).get("state")
    return (OFFLINE if state in ("unavailable", None) else ONLINE), f"{entity}: {state}"


async def _direct(client: httpx.AsyncClient) -> tuple[str, str]:
    try:
        r = await client.get(
            f"{get_service('internal', 'voice_bridge')}/audio_devices",
            params={"rescan": "false"},
            headers=service_headers(),
        )
        inputs = r.json().get("inputs") or []
    except Exception:
        return OFFLINE, "voice bridge"
    return (ONLINE if inputs else OFFLINE), f"{len(inputs)} mic(s)"


async def node_statuses(nodes: dict) -> dict[str, dict]:
    """{node_id: {"status", "via", "detail", "checked_at"}} for every node; cached for a few seconds."""
    nodes = with_direct(nodes)
    key = repr(
        sorted((k, sorted((v or {}).items())) for k, v in nodes.items() if isinstance(v, dict))
    )
    now = time.time()
    if _cache["data"] is not None and _cache["key"] == key and now - _cache["at"] < _CACHE_S:
        return _cache["data"]
    try:
        ha_url = (get_service("home_assistant", "url") or "").rstrip("/")
    except Exception:
        ha_url = ""
    try:
        headers = {"Authorization": f"Bearer {get_ha_token()}"}
    except Exception:
        headers = {}

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:

        async def check(node: dict) -> tuple:
            plan = plan_probe(node)
            if plan[0] == "channels":
                status, detail, per = await _channels(node, plan[1])
                if status == OFFLINE:
                    # No channel port answered — a projector/DLNA screen may have none; the device can still be up.
                    ping_status, _p = await _ping(plan[1])
                    if ping_status == ONLINE:
                        return ONLINE, f"{detail} · ping ✓", "channels", per
                if status == OFFLINE and str(node.get("entity_id") or "").strip():
                    ha_status, ha_detail = await _ha(client, ha_url, headers, str(node["entity_id"]))
                    per["ha"] = ha_status == ONLINE
                    if ha_status == ONLINE:
                        return ONLINE, f"{detail}, ha ✓ ({ha_detail})", "channels", per
                return status, detail, "channels", per
            if plan[0] == "direct":
                return (*await _direct(client), "direct")
            if plan[0] == "ha":
                return (*await _ha(client, ha_url, headers, plan[1]), "ha")
            if plan[0] == "tcp":
                return (*await _tcp(plan[1], plan[2]), "tcp")
            if plan[0] == "ping":
                return (*await _ping(plan[1]), "ping")
            return UNKNOWN, "", "none"

        ids = [k for k, v in nodes.items() if isinstance(v, dict)]
        results = await asyncio.gather(*(check(nodes[k]) for k in ids), return_exceptions=True)
    out: dict[str, dict] = {}
    for nid, res in zip(ids, results, strict=True):
        if isinstance(res, BaseException):
            out[nid] = {"status": UNKNOWN, "via": "", "detail": str(res)[:120], "checked_at": now}
        else:
            status, detail, via = res[:3]
            out[nid] = {"status": status, "via": via, "detail": detail, "checked_at": now}
            if len(res) > 3:
                out[nid]["channels"] = res[3]
    _cache.update(at=now, key=key, data=out)
    return out
