"""Which playback destinations a spoken-announcement target means (DT04-S05f).

Moved out of services/voice/voice_bridge.py so it can be tested (the bridge loads speech models at import). Two changes
from the old bridge code: a node is matched by id OR display name with «-», «_», spaces and case treated alike
(«tv-a» = `tv_a` = «TV A»), and an unknown or disabled target is reported — it never silently
becomes the local speaker.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

LOCAL = "local"
UNKNOWN = "unknown"
DISABLED = "disabled"


def norm(s: str) -> str:
    """Lowercase; spaces, hyphens and underscores are the same."""
    return str(s or "").strip().lower().replace("-", "_").replace(" ", "_")


def has_audio(node_cfg: dict) -> bool:
    return node_cfg.get("has_audio", True)


def has_display(node_cfg: dict) -> bool:
    if "has_display" in node_cfg:
        return bool(node_cfg["has_display"])
    return bool(node_cfg.get("is_tv", False))  # backward-compat during migration


def _names(nid: str, ncfg: dict) -> set[str]:
    return {norm(n) for n in (nid, ncfg.get("description", ""), ncfg.get("name", "")) if n}


def _prefer_audio_only(nodes: dict, candidates: list[str]) -> str:
    audio = [n for n in candidates if has_audio(nodes[n])]
    audio_only = [n for n in audio if not has_display(nodes[n])]
    return (audio_only or audio or candidates)[0]


def _one(nodes: dict, nid: str) -> tuple[list[str], str]:
    return ([LOCAL] if nodes[nid].get("tts_target") == LOCAL else [nid]), ""


def resolve(
    target: str,
    nodes: dict,
    *,
    resolve_place: Callable[[str], str | None] | None = None,
    place_of: Callable[[str], str | None] | None = None,
) -> tuple[list[str], str]:
    """(destinations, reason). Destinations are node ids plus the sentinel «local» (this machine's speaker).
    reason is "" on success, UNKNOWN or DISABLED when nothing matches — then destinations is []."""
    t = str(target or "").strip().lower()
    if t in (LOCAL, "lokal", ""):
        return [LOCAL], ""

    enabled = {nid: c for nid, c in nodes.items() if c.get("enabled", True)}
    if t in ("all", "alle"):
        by_room: dict[str, list[str]] = {}
        roomless: list[str] = []
        for nid, ncfg in enabled.items():
            room = str(ncfg.get("room", "")).strip().lower()
            (by_room.setdefault(room, []) if room else roomless).append(nid)
        selected: list[str] = []
        for room_nodes in by_room.values():
            if len(room_nodes) == 1:
                if has_audio(nodes[room_nodes[0]]):
                    selected.append(room_nodes[0])
            else:
                audio = [n for n in room_nodes if has_audio(nodes[n])]
                audio_only = [n for n in audio if not has_display(nodes[n])]
                selected.extend(audio_only or audio)
        selected.extend(n for n in roomless if has_audio(nodes[n]))
        return [LOCAL, *selected], ""

    tn = norm(t)
    # Node by id or display name
    for nid, ncfg in nodes.items():
        if tn in _names(nid, ncfg):
            if not ncfg.get("enabled", True):
                return [], DISABLED
            return _one(nodes, nid)

    # Place through the register (P137), then the free-text room name
    room_nodes: list[str] = []
    if resolve_place is not None and place_of is not None:
        try:
            place = resolve_place(t)
            if place:
                room_nodes = [nid for nid in enabled if place_of(nid) == place]
        except Exception:
            room_nodes = []
    if not room_nodes:
        room_nodes = [nid for nid, c in enabled.items() if norm(c.get("room", "")) == tn]
    if room_nodes:
        return _one(nodes, _prefer_audio_only(nodes, room_nodes))

    # Substring — «kid» matches «bedroom_kid»
    sub = [nid for nid, c in enabled.items() if any(tn in n for n in _names(nid, c) | {norm(c.get("room", ""))})]
    if sub:
        return _one(nodes, _prefer_audio_only(nodes, sub))
    return [], UNKNOWN


def known_targets(nodes: dict) -> list[str]:
    """What Kåre may say instead: «id (display name)» for every enabled audio node, plus local and all."""
    out = []
    for nid, c in nodes.items():
        if c.get("enabled", True) and has_audio(c):
            name = str(c.get("description") or c.get("name") or "").strip()
            out.append(f"{nid} ({name})" if name else nid)
    return [*out, LOCAL, "all"]


# ── receipt after the outcome (DT04-S05f): «sent» only when playback really started ─────────────────────────────

PLAYED, PLAYING, FAILED = "played", "playing", "failed"
BUSY = "busy"  # outcome «busy:<app>» — another app plays on the target, nothing was sent (P-D1)


class TargetBusy(Exception):
    """Raised by a provider when another app plays on the target; no fallback may replace it (P-D1, 2026-11-16)."""

    def __init__(self, app: str):
        super().__init__(f"busy: {app} is playing")
        self.app = app

# The local speaker (aplay) blocks for the whole clip, so «still running after 3 s» means it started. A remote
# provider (Cast, ESP32, HA …) returns when the device accepted or refused — wait for that answer, up to a cap: a Cast
# speech in the house failed only after 9.4 s (2026-11-15), which a 3 s window would have called «playing».
CONFIRM_WINDOW_S = 3.0
REMOTE_CONFIRM_S = 15.0


async def confirm_start(
    jobs: dict, window: float = CONFIRM_WINDOW_S, remote_window: float = REMOTE_CONFIRM_S
) -> tuple[dict[str, str], list]:
    """Start one playback per target. The local speaker gets `window` seconds, remote targets up to `remote_window`.
    Finished with a truthy result → PLAYED; False/raised → FAILED; still running at its limit → PLAYING (started, no
    failure yet). Returns (outcome per target, tasks still running — the caller awaits or cancels them)."""
    tasks = {name: asyncio.ensure_future(coro) for name, coro in jobs.items()}
    if not tasks:
        return {}, []
    await asyncio.wait(tasks.values(), timeout=window)
    remote_open = [t for n, t in tasks.items() if n != LOCAL and not t.done()]
    if remote_open and remote_window > window:
        await asyncio.wait(remote_open, timeout=remote_window - window)
    pending = {t for t in tasks.values() if not t.done()}
    outcome: dict[str, str] = {}
    for name, task in tasks.items():
        if task in pending:
            outcome[name] = PLAYING
        elif not task.cancelled() and isinstance(task.exception(), TargetBusy):
            outcome[name] = f"{BUSY}:{task.exception().app}"
        elif task.cancelled() or task.exception() is not None:
            outcome[name] = FAILED
        else:
            res = task.result()
            outcome[name] = FAILED if res is False else PLAYED
    return outcome, list(pending)


def summary(outcome: dict[str, str]) -> str:
    """ok (all started) · partial · failed (none, or nothing to play)."""
    good = [v for v in outcome.values() if v in (PLAYED, PLAYING)]
    if outcome and len(good) == len(outcome):
        return "ok"
    return "partial" if good else "failed"
