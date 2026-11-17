"""P139 / P-D1 — speech or a picture never replaces an app that is playing on the target (kaare-d7, 2026-11-16).

Seen 2026-11-16 20:54 (rid-1794858834810): speech to a screen playing YouTube — own Cast not confirmed after ~8 s,
then the HA fallback's `play_media` replaced YouTube, and nothing brought it back. Now: when the Cast status shows
another app running, KTSB's own Cast gives up at once (no LOAD), the HA / DLNA fallbacks are not tried, and Kåre is
told «X is playing on Y — not sent». An overlay channel (TvOverlay) may still show a picture: it does not replace what
plays. KTSB's own receivers (Default Media Receiver, DashCast) and an idle device (photo frame) are not «busy».
"""

import asyncio
import types

import pytest

from adapters.display import registry as reg
from kaare_core.tools import executor as ex
from kaare_core.voice import cast_sender as cs
from kaare_core.voice import speak_target as st
from kaare_core.voice.base import BaseVoiceProvider
from kaare_core.voice.providers.chromecast import CastFirstProvider, ChromecastProvider

YOUTUBE = {"app": "YouTube", "app_id": "233637DE", "standby": False}


class FakeCast:
    def __init__(self, snap):
        self.calls: list = []
        self.socket_client = types.SimpleNamespace(is_connected=True)
        self.status = types.SimpleNamespace(
            is_stand_by=snap.get("standby"), is_active_input=None, display_name=snap.get("app"),
            app_id=snap.get("app_id"), volume_level=0.3, volume_muted=False,
        )
        self.media_controller = types.SimpleNamespace(
            play_media=lambda url, mime, title=None: self.calls.append(("play_media", url)),
            status=types.SimpleNamespace(player_state=None, title=None, content_id=None),
        )

    def wait(self, timeout=None):
        pass


@pytest.fixture
def device(monkeypatch):
    def make(snap):
        cast = FakeCast(snap)
        monkeypatch.setattr(cs, "_casts", {"10.0.0.4": cast})
        monkeypatch.setattr(cs, "_POLL_S", 0.01)
        return cast

    return make


# ── what counts as «another app playing» ─────────────────────────────────────

@pytest.mark.parametrize("snap,busy", [
    (YOUTUBE, "YouTube"),
    ({"app": "Plex", "app_id": "9AC194DC"}, "Plex"),
    ({"app": None, "app_id": None}, None),
    ({"app": "Backdrop", "app_id": "E8C28D3C"}, None),  # Nest Hub photo frame
    ({"app": "Default Media Receiver", "app_id": "CC1AD845"}, None),  # KTSB's own speech/picture/radio
    ({"app": "DashCast", "app_id": "84912283"}, None),  # KTSB's own screen page
])
def test_other_app(snap, busy):
    assert cs.other_app(snap) == busy


# ── cast_sender: no LOAD into a playing app ─────────────────────────────────

def test_speech_gives_up_at_once_when_another_app_plays(device):
    cast = device(YOUTUBE)
    out = cs.play_audio("10.0.0.4", "http://bridge/audio/a.wav")
    assert not out["ok"] and out["busy"] == "YouTube" and cast.calls == []


def test_picture_gives_up_at_once_when_another_app_plays(device):
    cast = device(YOUTUBE)
    out = cs.show_picture("10.0.0.4", "http://bridge/media/a.png")
    assert not out["ok"] and out["busy"] == "YouTube" and cast.calls == []


def test_an_idle_device_still_gets_the_speech(device):
    cast = device({"app": "Backdrop", "app_id": "E8C28D3C"})
    cs.play_audio("10.0.0.4", "http://bridge/audio/a.wav")
    assert cast.calls == [("play_media", "http://bridge/audio/a.wav")]


# ── providers: the HA fallback is not tried for a busy target ────────────────

class FakeHA(BaseVoiceProvider):
    def __init__(self):
        self.spoke = False

    async def speak(self, wav_path, node_config):
        self.spoke = True
        return True

    async def is_available(self, node_config):
        return True

    async def set_volume(self, volume, node_config):
        pass


def test_cast_first_never_falls_back_to_ha_over_a_playing_app(monkeypatch, tmp_path):
    async def run(fn, *args, **kw):
        return {"ok": False, "busy": "YouTube", "error": "busy: YouTube"}

    monkeypatch.setattr(cs, "run", run)
    ha = FakeHA()
    provider = CastFirstProvider(ChromecastProvider({}, "http://bridge/audio"), ha)
    with pytest.raises(st.TargetBusy) as err:
        asyncio.run(provider.speak(tmp_path / "a.wav", {"host": "10.0.0.4"}))
    assert err.value.app == "YouTube" and not ha.spoke


def test_cast_first_still_falls_back_when_cast_just_failed(monkeypatch, tmp_path):
    async def run(fn, *args, **kw):
        return {"ok": False, "error": "unreachable: timeout"}

    monkeypatch.setattr(cs, "run", run)
    ha = FakeHA()
    provider = CastFirstProvider(ChromecastProvider({}, "http://bridge/audio"), ha)
    assert asyncio.run(provider.speak(tmp_path / "a.wav", {"host": "10.0.0.4"})) is True and ha.spoke


def test_the_outcome_names_the_app():
    async def busy():
        raise st.TargetBusy("YouTube")

    async def ok():
        return True

    outcome, pending = asyncio.run(st.confirm_start({"tv_a": busy(), "nest": ok()}))
    assert outcome == {"tv_a": "busy:YouTube", "nest": st.PLAYED} and not pending
    assert st.summary(outcome) == "partial"


# ── display registry: after «busy», only an overlay may still try ────────────

class Chan:
    def __init__(self, name, result):
        self.name, self.result, self.called = name, result, False

    async def send(self, cfg, **kw):
        self.called = True
        return self.result


def _registry(monkeypatch, chans):
    r = reg.DisplayProviderRegistry.__new__(reg.DisplayProviderRegistry)
    r._by_channel = {c.name: c for c in chans}
    monkeypatch.setattr(reg.channels, "order_for", lambda job, node: [c.name for c in chans])
    monkeypatch.setattr(reg.channels, "node_channels", lambda node: {})
    monkeypatch.setattr(reg.channels, "port_of", lambda name, entry: 5001)
    return r


def test_busy_cast_skips_dlna_and_ha_but_tries_the_overlay(monkeypatch):
    cast = Chan("cast", {"ok": False, "busy": "YouTube", "error": "busy: YouTube"})
    dlna, overlay, ha = Chan("dlna", {"ok": True}), Chan("tvoverlay", {"ok": True}), Chan("ha", {"ok": True})
    r = _registry(monkeypatch, [cast, dlna, overlay, ha])
    out = asyncio.run(r.send("tv_a", {}, job="picture", text="x"))
    assert out["ok"] and out["channel"] == "tvoverlay" and not dlna.called and not ha.called


def test_busy_without_an_overlay_fails_and_says_busy(monkeypatch):
    cast = Chan("cast", {"ok": False, "busy": "YouTube", "error": "busy: YouTube"})
    ha = Chan("ha", {"ok": True})
    r = _registry(monkeypatch, [cast, ha])
    out = asyncio.run(r.send("nest", {}, job="picture", text="x"))
    assert not out["ok"] and out["busy"] == "YouTube" and not ha.called


# ── what Kåre is told ─────────────────────────────────────────────────────────

def test_kare_is_told_what_plays_where():
    text = ex.busy_note("tv_a", "busy:YouTube", "nb")
    assert "YouTube" in text and "tv_a" in text
    assert ex.busy_note("tv_a", "failed", "nb") == "tv_a"
