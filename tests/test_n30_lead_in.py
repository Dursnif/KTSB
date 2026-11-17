"""N30: silence before a spoken reply on network speakers."""

import wave

from kaare_core.voice.lead_in import (
    DEFAULT_COLD_MS,
    DEFAULT_PLAYING_MS,
    MAX_MS,
    lead_in_ms,
    pad_wav,
)


def _write_wav(path, frames: bytes, rate=22050, width=2, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)


def test_defaults_for_cast_types():
    for node_type in ("ha_media_player", "chromecast"):
        node = {"type": node_type}
        assert lead_in_ms(node, was_playing=False) == DEFAULT_COLD_MS
        assert lead_in_ms(node, was_playing=True) == DEFAULT_PLAYING_MS


def test_untested_types_default_to_zero():
    for node_type in ("esp32", "wyoming", "snapcast", "airplay", "dlna", "direct"):
        node = {"type": node_type}
        assert lead_in_ms(node, was_playing=False) == 0
        assert lead_in_ms(node, was_playing=True) == 0


def test_node_values_win_and_are_clamped():
    node = {"type": "ha_media_player", "lead_in_ms": 250, "lead_in_playing_ms": 99999}
    assert lead_in_ms(node, was_playing=False) == 250
    assert lead_in_ms(node, was_playing=True) == MAX_MS
    assert lead_in_ms({"type": "esp32", "lead_in_ms": -5}, was_playing=False) == 0
    assert lead_in_ms({"type": "esp32", "lead_in_ms": 300}, was_playing=False) == 300


def test_bad_value_falls_back_to_default():
    assert lead_in_ms({"type": "chromecast", "lead_in_ms": "abc"}, was_playing=False) == DEFAULT_COLD_MS
    assert lead_in_ms({"type": "chromecast", "lead_in_ms": None}, was_playing=False) == DEFAULT_COLD_MS


def test_pad_wav_prepends_silence_and_keeps_format(tmp_path):
    src = tmp_path / "a.wav"
    audio = b"\x01\x02" * 1000
    _write_wav(src, audio, rate=16000)
    dst = pad_wav(src, tmp_path / "b.wav", 400)
    with wave.open(str(dst), "rb") as r:
        assert (r.getframerate(), r.getsampwidth(), r.getnchannels()) == (16000, 2, 1)
        data = r.readframes(r.getnframes())
    silence = int(16000 * 0.4) * 2
    assert data[:silence] == b"\x00" * silence
    assert data[silence:] == audio


def test_pad_wav_stereo(tmp_path):
    src = tmp_path / "s.wav"
    _write_wav(src, b"\x05\x00\x06\x00" * 10, rate=8000, channels=2)
    dst = pad_wav(src, tmp_path / "t.wav", 100)
    with wave.open(str(dst), "rb") as r:
        assert r.getnframes() == 10 + 800
