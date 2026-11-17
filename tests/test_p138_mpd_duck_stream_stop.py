"""BUG-log 2026-10-22 [→P138] — ducking paused MPD, and a paused radio stream keeps one CPU core at 100 %.

Measured 2026-11-16 with a private MPD (null output, port 6611, the house's radio stream): playing ~1.5 % CPU;
paused → 100 % of one core after ~20 s and staying there; stopped → 0 %; playing at 20 % volume → ~1.5 %.
Fix: a stream is stopped instead of paused (a live stream has no position to keep, and `mpc play` picks it up live
again); a local file is still paused (keeps its position). What the user hears is unchanged: silence while Kåre speaks.
"""

import ast
from pathlib import Path

import pytest

from kaare_core.voice import ducking

BRIDGE = Path("/kaare/services/voice/voice_bridge.py")


@pytest.mark.parametrize("current", [
    "https://radio.example/stream",
    "http://192.0.2.5:8000/live.mp3",
    "icecast://radio.example/a",
    "  https://radio.example/stream\n",
])
def test_a_stream_is_stopped_not_paused(current):
    assert ducking.mpd_quiet_command(current) == "stop"


@pytest.mark.parametrize("current", ["Music/album/track.flac", "track.mp3", "", None])
def test_a_local_file_or_nothing_is_paused(current):
    assert ducking.mpd_quiet_command(current) == "pause"


def _bridge_fn(name):
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_the_bridge_asks_the_helper_with_the_current_file():
    src = ast.unparse(_bridge_fn("_mpd_pause"))
    assert "mpd_quiet_command" in src and "%file%" in src and "current" in src
    assert "_mpc('pause')" not in src  # never an unconditional pause again


def test_resume_plays_again_for_both():
    assert "_mpc('play')" in ast.unparse(_bridge_fn("_mpd_resume"))
