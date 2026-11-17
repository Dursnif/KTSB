"""Turn down what is playing on the reply's output while someone talks to Kåre (P138, 2026-10-17).

From the button press (or a Wyoming satellite starting to stream) the devices the answer will be played on are
quieted: the local MPD goes silent (`mpd_quiet_command`: a stream is stopped, a file paused), a Home Assistant media
player that is playing gets its volume lowered. Before the
answer is spoken there, the volume goes back up (a Cast device in announce mode pauses and resumes its own media
around the announcement); when the cycle ends, everything is restored — MPD resumes, volumes are back — whatever
happened in between (empty transcript, error, timeout).

The I/O is injected (`DuckIO`) so the voice bridge supplies the real HA/MPD calls and tests supply fakes.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

LOCAL = "local"


def mpd_quiet_command(current_file: str | None) -> str:
    """The mpc command that silences MPD for a while: `stop` for a stream, `pause` for a file.

    A paused network stream keeps one CPU core at 100 % in MPD (measured 2026-11-16, BUG-log 2026-10-22 [→P138]);
    stopped it uses nothing. A live stream has no position worth keeping, and `mpc play` picks it up live again. A
    local file is paused so it continues where it was.
    """
    return "stop" if "://" in (current_file or "") else "pause"


@dataclass
class DuckIO:
    """The calls a duck session needs. `ha_state` returns (state, volume_level or None)."""

    ha_state: Callable[[str], Awaitable[tuple[str, float | None]]]
    ha_set_volume: Callable[[str, float], Awaitable[None]]
    mpd_state: Callable[[], Awaitable[str]]
    mpd_pause: Callable[[], Awaitable[None]]
    mpd_resume: Callable[[], Awaitable[None]]


@dataclass
class DuckSession:
    io: DuckIO
    volume: float = 0.2
    mpd_paused: bool = False
    # entity_id → volume to restore
    lowered: dict[str, float] = field(default_factory=dict)

    async def start(self, targets: list[str], nodes: dict) -> None:
        """Quiet every output target that is playing. Never raises."""
        for target in targets:
            try:
                if target == LOCAL:
                    if await self.io.mpd_state() == "playing":
                        await self.io.mpd_pause()
                        self.mpd_paused = True
                    continue
                node = nodes.get(target) or {}
                entity = str(node.get("entity_id") or "")
                if node.get("type") != "ha_media_player" or not entity or entity in self.lowered:
                    continue
                state, vol = await self.io.ha_state(entity)
                if state == "playing" and vol is not None and vol > self.volume:
                    await self.io.ha_set_volume(entity, self.volume)
                    self.lowered[entity] = vol
            except Exception as exc:
                log.warning("Duck: could not quiet %s: %s", target, exc)
        if self.mpd_paused or self.lowered:
            log.info("Duck: quieted mpd=%s ha=%s", self.mpd_paused, list(self.lowered))

    async def restore_volumes(self) -> None:
        """Volumes back up — before the answer is spoken on those devices. MPD stays paused."""
        for entity, vol in list(self.lowered.items()):
            try:
                await self.io.ha_set_volume(entity, vol)
            except Exception as exc:
                log.warning("Duck: could not restore volume on %s: %s", entity, exc)
            self.lowered.pop(entity, None)

    async def end(self) -> None:
        """Everything back as it was. Safe to call more than once. Never raises."""
        await self.restore_volumes()
        if self.mpd_paused:
            try:
                await self.io.mpd_resume()
            except Exception as exc:
                log.warning("Duck: could not resume MPD: %s", exc)
            self.mpd_paused = False
