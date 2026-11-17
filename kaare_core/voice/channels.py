"""Channels per node (P139 phase 2): one node = one physical device; a channel is a way KTSB can reach it.

A TV can answer on several at once — TvOverlay for a short notice, Cast for a full picture or speech, a remote-control
protocol for power/volume/apps, Home Assistant as a fallback. `nodes.yaml` keeps them under the node:

    channels:
      tvoverlay: {enabled: true, port: 5001, found_at: "2026-10-19T14:02:11+00:00", last_seen: "…"}
      cast:      {enabled: true, port: 8009, found_at: "…"}
      ha:        {enabled: true}            # uses the node's entity_id

The node type stays the «brand» (which drivers apply later). Channels are found by probing ports, never assumed from
the type — a stranger's TV may have Cast built in, or not (rule 24). A channel that does not answer now is kept: a
Google TV drops TvOverlay in standby but keeps Cast and the remote alive (P139 phase 0).

Pure rules here (catalogue, defaults for nodes saved before channels existed, merge, order per job) plus `probe`, the
one function with I/O. Shared by the API (display), the node dialog, N29 and the config migration.
"""

import asyncio
import contextlib
from datetime import UTC, datetime

# name → (ports to probe, jobs it can do, driver in KTSB today). Ports verified on this install's TVs in phase 0
# (Thomson Google TV: 8009/8443 Cast, 6466/6467 remote, 5001 TvOverlay; Samsung AU9075: 8001/8002 WebSocket).
CATALOGUE: dict[str, tuple[tuple[int, ...], frozenset[str], bool]] = {
    "tvoverlay": ((5001,), frozenset({"notice"}), True),
    "cast": ((8009,), frozenset({"picture"}), True),
    "dlna": ((), frozenset({"picture"}), True),  # found by SSDP, not a fixed port — assumed for its brands (phase 3)
    "ha": ((), frozenset({"notice", "picture"}), True),  # the node's entity_id
    "atv_remote": ((6466,), frozenset({"control"}), True),  # Android TV Remote v2 (tv_control, phase 5)
    "samsung_ws": ((8001, 8002), frozenset({"control"}), True),  # Samsung Tizen WebSocket/REST (tv_control)
    "lg_ssap": ((3000, 3001), frozenset({"control"}), False),
    "airplay": ((7000,), frozenset({"picture"}), False),
    "adb": ((5555,), frozenset({"control"}), False),
}
PORT_CHANNELS = tuple(name for name, (ports, _j, _d) in CATALOGUE.items() if ports)

# Which channel does a job first. A notice is text (+ a small picture): an overlay keeps what is on screen; a full
# picture replaces it. HA is always last — a fallback, and the reply must say it was used (N26).
JOB_ORDER: dict[str, tuple[str, ...]] = {
    "notice": ("tvoverlay", "cast", "dlna", "ha"),
    "picture": ("cast", "dlna", "tvoverlay", "ha"),
}

# What a node saved before P139 used, by type — the display registry's old «primary → fallback» map. Only for nodes
# without `channels` (the migration writes these once; until then the runtime reads them the same way).
_LEGACY_BY_TYPE: dict[str, tuple[str, ...]] = {
    "chromecast": ("cast",),
    "google_tv": ("tvoverlay",),
    "android_tv": ("tvoverlay",),
    "samsung_tv": ("dlna",),
    "lg_tv": ("dlna",),
    "apple_tv": ("dlna",),
    "projector": ("dlna",),
}
_PROBE_TIMEOUT_S = 1.5


_SCREEN_TYPES = frozenset({*_LEGACY_BY_TYPE, "fire_tv"})


def wants_channels(node: dict) -> bool:
    """Nodes that are screens or Cast devices (incl. an HA player the user gave an address — a Nest, a Google Home)
    and have an address to probe."""
    if not isinstance(node, dict) or not str(node.get("host") or "").strip():
        return False
    if str(node.get("type") or "") in ("chromecast", "ha_media_player"):
        return True
    if "has_display" in node:
        return bool(node["has_display"]) or str(node.get("type") or "") == "chromecast"
    return str(node.get("type") or "") in _SCREEN_TYPES or bool(node.get("is_tv"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def legacy_channels(node: dict) -> dict[str, dict]:
    """The channels a node without `channels` had through its type (and entity_id). Not marked as found."""
    out: dict[str, dict] = {}
    for name in _LEGACY_BY_TYPE.get(str(node.get("type") or ""), ()):
        entry: dict = {"enabled": True}
        if name == "tvoverlay" and node.get("tvoverlay_port"):
            entry["port"] = int(node["tvoverlay_port"])
        out[name] = entry
    if str(node.get("entity_id") or "").strip():
        out["ha"] = {"enabled": True}
    return out


def node_channels(node: dict) -> dict[str, dict]:
    """The node's channels: its own `channels` (known names only), else the legacy ones from its type. An `ha`
    channel needs an entity_id — without one it is left out even if listed."""
    raw = node.get("channels")
    chans = (
        {k: dict(v) for k, v in raw.items() if k in CATALOGUE and isinstance(v, dict)}
        if isinstance(raw, dict)
        else legacy_channels(node)
    )
    if not str(node.get("entity_id") or "").strip():
        chans.pop("ha", None)
    return chans


def enabled_channels(node: dict) -> dict[str, dict]:
    return {k: v for k, v in node_channels(node).items() if v.get("enabled", True)}


def port_of(name: str, entry: dict | None) -> int | None:
    ports = CATALOGUE[name][0]
    if not ports:
        return None
    try:
        return int((entry or {}).get("port") or ports[0])
    except (TypeError, ValueError):
        return ports[0]


def order_for(job: str, node: dict) -> list[str]:
    """Enabled channels with a driver that can do `job`, best first."""
    chans = enabled_channels(node)
    return [n for n in JOB_ORDER.get(job, JOB_ORDER["notice"]) if n in chans and CATALOGUE[n][2]]


def merge_found(node: dict, found: dict[str, int], at: str | None = None) -> dict[str, dict]:
    """Channels after a probe: newly found ones are added enabled (with found_at), known ones that answered get
    last_seen, known ones that did not answer are kept as they are (standby is not «gone»). The user's on/off is
    never changed."""
    at = at or _now()
    chans = node_channels(node)
    for name, port in found.items():
        entry = chans.get(name)
        if entry is None:
            entry = {"enabled": True, "found_at": at}
            chans[name] = entry
        entry.setdefault("found_at", at)
        entry["last_seen"] = at
        if port != CATALOGUE[name][0][0] or "port" in entry:
            entry["port"] = port
    if str(node.get("entity_id") or "").strip():
        chans.setdefault("ha", {"enabled": True})
    return chans


async def _open(host: str, port: int) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), _PROBE_TIMEOUT_S)
    except Exception:
        return False
    w.close()
    with contextlib.suppress(Exception):
        await w.wait_closed()
    return True


async def probe(host: str, ports: dict[str, tuple[int, ...]] | None = None) -> dict[str, int]:
    """{channel: port that answered} for every port channel, all ports in parallel (~1.5 s worst case)."""
    if not host:
        return {}
    plan = ports or {n: CATALOGUE[n][0] for n in PORT_CHANNELS}
    pairs = [(n, p) for n, ps in plan.items() for p in ps]
    results = await asyncio.gather(*(_open(host, p) for _n, p in pairs))
    found: dict[str, int] = {}
    for (name, port), ok in zip(pairs, results, strict=True):
        if ok and name not in found:
            found[name] = port
    return found
