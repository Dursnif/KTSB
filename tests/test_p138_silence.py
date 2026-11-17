"""P138 — adaptive end of speech, shared by Wyoming and the KTSB Direct mic (feed_level with float levels).

Levels mimic the workshop's analog mic measured 2026-10-17 (boost 1/3, power save off): noise 0.004–0.0105,
quiet speech peaking ~0.05 — under the old fixed 0.05 threshold almost all of it counted as silence.
"""

import asyncio
import random

import pytest
from fastapi import HTTPException

import kaare_core.routers.router_settings as rs
from kaare_core.voice.silence import SilenceDetector

DUR = 2048 / 44100  # one local-mic chunk


def _run(det, levels):
    for i, lv in enumerate(levels):
        if det.feed_level(lv, DUR):
            return i * DUR
    return None


def _noise(rng, sec):
    return [rng.uniform(0.004, 0.0105) for _ in range(int(sec / DUR))]


def _speech(rng, sec):
    # words with short gaps at noise level
    out = []
    for _ in range(int(sec / 0.5)):
        out += [rng.uniform(0.015, 0.055) for _ in range(int(0.35 / DUR))]
        out += [rng.uniform(0.004, 0.0105) for _ in range(int(0.15 / DUR))]
    return out


def test_quiet_mic_ends_after_silence_not_at_cap():
    rng = random.Random(1)
    levels = _noise(rng, 0.6) + _speech(rng, 3.0) + _noise(rng, 8.0)
    det = SilenceDetector(1.5, 44100, 4)
    end = _run(det, levels)
    last_word = max(i for i, lv in enumerate(levels) if lv > 0.0105) * DUR
    assert det.reason == "silence"
    assert 1.4 < end - last_word < 1.7  # silence_sec after the last word, not the 10 s cap
    assert 0.005 < det.floor < 0.009  # median of the noise, not its quietest chunk


def test_word_gaps_do_not_end_the_round():
    rng = random.Random(2)
    det = SilenceDetector(1.5, 44100, 4)
    assert _run(det, _noise(rng, 0.5) + _speech(rng, 6.0)) is None


def test_nothing_said_ends_as_no_speech():
    rng = random.Random(3)
    det = SilenceDetector(1.5, 44100, 4, no_speech_sec=6.0)
    end = _run(det, _noise(rng, 10.0))
    assert det.reason == "no_speech" and 5.9 < end < 6.2


def test_loud_mic_also_works():
    rng = random.Random(4)
    levels = [rng.uniform(0.02, 0.04) for _ in range(20)] + [
        rng.uniform(0.2, 0.5) for _ in range(40)
    ]
    levels += [rng.uniform(0.02, 0.04) for _ in range(60)]
    det = SilenceDetector(1.0, 44100, 4)
    assert _run(det, levels) is not None and det.reason == "silence"


# ── GUI setting ───────────────────────────────────────────────────────────────


@pytest.fixture
def voice_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "get_service", lambda *a, **k: "http://127.0.0.1:9")
    p = tmp_path / "voice_settings.yaml"
    p.write_text("stt:\n  silence_threshold_seconds: 1.5\n")
    monkeypatch.setattr(rs, "_VOICE_SETTINGS_PATH", p)
    return p


def test_end_of_speech_defaults_to_auto_and_saves(voice_file):
    assert rs.api_get_voice_pipeline(None)["end_of_speech"] == "auto"
    out = asyncio.run(rs.api_put_voice_pipeline({"end_of_speech": "fixed"}, None))
    assert out["end_of_speech"] == "fixed" and "end_of_speech: fixed" in voice_file.read_text()


@pytest.mark.parametrize("bad", ["vad", 1, None, True])
def test_end_of_speech_rejects_other_values(voice_file, bad):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.api_put_voice_pipeline({"end_of_speech": bad}, None))
    assert exc.value.status_code == 400


def test_quiet_pdm_mic_with_start_pop_hears_speech():
    """Atom Echo, 2026-10-18: floor ~0.0005, a 0.86 pop in the first chunk, speech 0.003–0.011 → was «no_speech»."""
    rng = random.Random(5)
    dur = 512 / 16000  # 32 ms satellite chunks
    levels = [0.86] + [rng.uniform(0.0003, 0.0008) for _ in range(15)]
    levels += [rng.uniform(0.003, 0.011) for _ in range(40)]  # «hvor mye er klokka»
    levels += [rng.uniform(0.0003, 0.0008) for _ in range(80)]
    det = SilenceDetector(1.5, 16000, 2)
    end = None
    for i, lv in enumerate(levels):
        if det.feed_level(lv, dur):
            end = i
            break
    assert det.reason == "silence" and end is not None
    assert det.floor < 0.001 and det.peak < 0.02  # the pop was not measured


def _atom_round(rng, lead_quiet: int):
    """Atom Echo levels (32 ms chunks): optional quiet lead, speech straight away, then quiet."""
    quiet = [rng.uniform(0.0003, 0.0008) for _ in range(lead_quiet)]
    speech = [rng.uniform(0.003, 0.011) for _ in range(45)]
    tail = [rng.uniform(0.0003, 0.0008) for _ in range(200)]
    return [0.0005] * 5 + quiet + speech + tail  # 5 warm-up chunks


def _feed(det, levels, dur=512 / 16000):
    for i, lv in enumerate(levels):
        if det.feed_level(lv, dur):
            return i * dur
    return None


def test_speech_right_after_press_is_found_late_not_lost():
    """2026-10-18, userc's room: talking at once made the voice the «floor» → no_speech every other press."""
    det = SilenceDetector(1.5, 16000, 2)
    end = _feed(det, _atom_round(random.Random(6), lead_quiet=0))
    assert det.reason in SilenceDetector.SPEECH_REASONS and end is not None


def test_prior_floor_ends_soon_after_the_words():
    det = SilenceDetector(1.5, 16000, 2, prior_floor=0.0006)
    end = _feed(det, _atom_round(random.Random(7), lead_quiet=0))
    assert det.reason == "silence"
    assert end < 0.16 + 45 * 0.032 + 2.0  # ~1.5 s after the last word, not the 6 s no-speech mark


def test_real_silence_is_still_no_speech():
    det = SilenceDetector(1.5, 16000, 2, prior_floor=0.0006)
    rng = random.Random(8)
    _feed(det, [rng.uniform(0.0003, 0.0008) for _ in range(250)])
    assert det.reason == "no_speech"
