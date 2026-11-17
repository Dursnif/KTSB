"""Silence before a spoken reply on network speakers (N30, 2026-10-18).

Cast speakers (Nest) open their audio path late and eat the start of a reply: ~0.3–0.5 s from cold, ~1 s when the
new stream replaces one that was playing. Tested live on a Nest Hub: 0.4 s of silence first fixed the cold case;
waking the speaker with a silent clip beforehand made it worse. So the voice bridge prepends silence — more when the
speaker was playing — and each node can change both values in the node dialog (`lead_in_ms`,
`lead_in_playing_ms`).
"""

import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

from kaare_core.voice import channels

# Node types that play our wav as a network stream and were measured to clip. Others default to 0 until tested
# (the user can still set a value per node).
CLIPPING_TYPES = {"ha_media_player", "chromecast"}
DEFAULT_COLD_MS = 400
DEFAULT_PLAYING_MS = 1200
MAX_MS = 3000

# HA media_player states where a stream is (or was just) running on the device.
PLAYING_STATES = {"playing", "paused", "buffering"}


def _ms(value, default: int) -> int:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(MAX_MS, ms))


def uses_own_cast(node_cfg: dict) -> bool:
    """A node KTSB reaches over its own Cast: an address plus an enabled `cast` channel, or a Cast-type node."""
    if not str(node_cfg.get("host") or "").strip():
        return False
    return node_cfg.get("type") == "chromecast" or "cast" in channels.enabled_channels(node_cfg)


async def playing_source(node_cfg: dict, cast_status: Callable[[str], Awaitable[dict]],
                         ha_state: Callable[[str], Awaitable[str]]) -> tuple[bool, str]:
    """(was a stream running on the speaker?, where that answer came from: «cast» / «ha» / «none»). KTSB's own Cast
    first, HA only as the fallback (P139 D7) — so a Cast node without HA gets the same answer. The source goes into the
    bridge's log line so the path can be verified (kaare-d7 2026-11-16). `cast_status(host)` → cast_sender.status's
    dict; `ha_state(entity)` → HA state."""
    if uses_own_cast(node_cfg):
        res = await cast_status(str(node_cfg["host"]).strip())
        if res.get("ok"):
            return str((res.get("after") or {}).get("state") or "").lower() in PLAYING_STATES, "cast"
    entity = str(node_cfg.get("entity_id") or "")
    if node_cfg.get("type") == "ha_media_player" and entity:
        return (await ha_state(entity)) in PLAYING_STATES, "ha"
    return False, "none"


async def was_playing(node_cfg: dict, cast_status: Callable[[str], Awaitable[dict]],
                      ha_state: Callable[[str], Awaitable[str]]) -> bool:
    """Was a stream running on the speaker? (see `playing_source`)"""
    return (await playing_source(node_cfg, cast_status, ha_state))[0]


def lead_in_ms(node_cfg: dict, was_playing: bool) -> int:
    """Milliseconds of silence to put before the reply on this node."""
    clips = node_cfg.get("type") in CLIPPING_TYPES
    if was_playing:
        return _ms(node_cfg.get("lead_in_playing_ms"), DEFAULT_PLAYING_MS if clips else 0)
    return _ms(node_cfg.get("lead_in_ms"), DEFAULT_COLD_MS if clips else 0)


def pad_wav(src: Path, dst: Path, ms: int) -> Path:
    """Write `src` to `dst` with `ms` of silence in front, same format. Returns `dst`."""
    with wave.open(str(src), "rb") as r:
        params = r.getparams()
        frames = r.readframes(r.getnframes())
    silence = b"\x00" * (int(params.framerate * ms / 1000) * params.sampwidth * params.nchannels)
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(silence + frames)
    return dst
