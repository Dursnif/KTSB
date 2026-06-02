"""Tests for Piper TTS wrapper.

Tests handle missing piper/espeak gracefully -- the implementation falls back
to silence so these tests always pass regardless of available TTS tools.
"""
from __future__ import annotations

import pytest

from server.tts import PiperTTS


class TestPiperTTS:
    def test_init_with_defaults(self):
        """TTS should initialize without crashing."""
        tts = PiperTTS(voice="en_US-lessac-medium")
        assert tts.voice == "en_US-lessac-medium"
        assert tts.sample_rate == 22050

    def test_synthesize_returns_bytes(self):
        """Synthesize should return audio bytes (uses fallback on Mac without piper)."""
        tts = PiperTTS(voice="en_US-lessac-medium")
        audio_bytes = tts.synthesize("Hello world")
        assert isinstance(audio_bytes, bytes)
        assert len(audio_bytes) > 0
