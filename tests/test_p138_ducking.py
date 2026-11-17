"""P138 — quiet the reply's output devices from the press until the answer is done, then restore them."""

import asyncio

from kaare_core.voice.ducking import LOCAL, DuckIO, DuckSession


class Fake:
    def __init__(self, states: dict, mpd: str = "stopped"):
        self.states = states  # entity → (state, volume)
        self.mpd = mpd
        self.calls: list = []

    def io(self) -> DuckIO:
        async def ha_state(e):
            return self.states.get(e, ("off", None))

        async def ha_set_volume(e, v):
            self.calls.append(("vol", e, v))
            st, _ = self.states.get(e, ("off", None))
            self.states[e] = (st, v)

        async def mpd_state():
            return self.mpd

        async def mpd_pause():
            self.calls.append(("mpd", "pause"))
            self.mpd = "paused"

        async def mpd_resume():
            self.calls.append(("mpd", "play"))
            self.mpd = "playing"

        return DuckIO(ha_state, ha_set_volume, mpd_state, mpd_pause, mpd_resume)


NODES = {
    "nest": {"type": "ha_media_player", "entity_id": "media_player.nest"},
    "quiet": {"type": "ha_media_player", "entity_id": "media_player.quiet"},
    "pe": {"type": "esp32", "host": "10.0.0.6"},
}


def run(coro):
    return asyncio.run(coro)


def test_playing_ha_player_turned_down_then_restored_before_tts():
    f = Fake({"media_player.nest": ("playing", 0.6)})
    d = DuckSession(io=f.io(), volume=0.2)
    run(d.start(["nest"], NODES))
    assert f.states["media_player.nest"] == ("playing", 0.2)
    run(d.restore_volumes())
    assert f.states["media_player.nest"] == ("playing", 0.6)
    run(d.end())
    assert f.calls == [("vol", "media_player.nest", 0.2), ("vol", "media_player.nest", 0.6)]


def test_idle_or_already_quiet_players_untouched():
    f = Fake({"media_player.nest": ("idle", 0.6), "media_player.quiet": ("playing", 0.1)})
    d = DuckSession(io=f.io(), volume=0.2)
    run(d.start(["nest", "quiet", "pe"], NODES))
    run(d.end())
    assert f.calls == []


def test_local_mpd_paused_and_resumed_only_at_end():
    f = Fake({}, mpd="playing")
    d = DuckSession(io=f.io())
    run(d.start([LOCAL], NODES))
    assert f.mpd == "paused"
    run(d.restore_volumes())
    assert f.mpd == "paused"  # stays paused while Kåre speaks
    run(d.end())
    assert f.mpd == "playing"
    run(d.end())  # second end is a no-op
    assert f.calls == [("mpd", "pause"), ("mpd", "play")]


def test_stopped_mpd_not_resumed():
    f = Fake({}, mpd="stopped")
    d = DuckSession(io=f.io())
    run(d.start([LOCAL], NODES))
    run(d.end())
    assert f.calls == []


def test_errors_never_raise():
    async def boom(*a):
        raise RuntimeError("HA down")

    io = DuckIO(boom, boom, boom, boom, boom)
    d = DuckSession(io=io)
    run(d.start([LOCAL, "nest"], NODES))
    run(d.end())
