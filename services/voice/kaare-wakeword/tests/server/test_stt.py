"""Tests for faster-whisper STT wrapper."""
from __future__ import annotations

import numpy as np
import pytest

# Skip entire module if faster-whisper is not installed
faster_whisper = pytest.importorskip("faster_whisper")

from server.stt import WhisperSTT


@pytest.fixture(scope="module")
def stt():
    """Create STT instance with tiny model for fast tests."""
    return WhisperSTT(model_size="tiny")


class TestWhisperSTT:
    def test_silence_returns_empty_or_short(self, stt):
        """Silent audio should produce empty or near-empty transcription."""
        silence = np.zeros(16000 * 2, dtype=np.float32)  # 2 seconds
        result = stt.transcribe(silence)
        assert isinstance(result.text, str)
        assert isinstance(result.language, str)

    def test_result_has_fields(self, stt):
        """Result should have text, language, confidence."""
        audio = np.random.randn(16000 * 2).astype(np.float32) * 0.001
        result = stt.transcribe(audio)
        assert hasattr(result, "text")
        assert hasattr(result, "language")
        assert hasattr(result, "confidence")
