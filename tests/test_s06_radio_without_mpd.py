"""Radio on an install without MPD (Docker: no `mpc`/`mpd`) says so plainly (DT04 R5a, item 4)."""

import asyncio

import pytest

from kaare_core.tools import executor_media as em


@pytest.mark.parametrize("args", [
    {"action": "radio_status"},
    {"action": "radio_stop"},
    {"action": "radio_volume", "volume": 30},
])
def test_radio_without_mpc_says_mpd_is_not_installed(monkeypatch, args):
    monkeypatch.setattr(em.shutil, "which", lambda name: None)
    out = asyncio.run(em.dispatch("media", {**args, "_user_id": "global"}))
    assert "not installed" in out or "ikke installert" in out or "nicht installiert" in out, out
    assert "Errno" not in out
