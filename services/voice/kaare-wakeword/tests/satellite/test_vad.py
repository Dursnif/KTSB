"""Tests for Silero VAD wrapper."""
from __future__ import annotations

import numpy as np
import pytest

from satellite.vad import SileroVAD


@pytest.fixture
def vad():
    """Create a VAD instance (downloads model on first run)."""
    return SileroVAD(threshold=0.5)


class TestSileroVAD:
    def test_silence_returns_low_probability(self, vad):
        """Silent audio should give low speech probability."""
        silence = np.zeros(512, dtype=np.float32)  # 32ms at 16kHz (Silero requirement)
        prob = vad.process_frame(silence)
        assert 0.0 <= prob <= 1.0
        assert prob < 0.5

    def test_noise_returns_float(self, vad):
        """Random noise should return a valid float probability."""
        noise = np.random.randn(512).astype(np.float32) * 0.01
        prob = vad.process_frame(noise)
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_reset_clears_state(self, vad):
        """Reset should not raise and should allow re-processing."""
        silence = np.zeros(512, dtype=np.float32)
        vad.process_frame(silence)
        vad.reset()
        prob = vad.process_frame(silence)
        assert 0.0 <= prob <= 1.0

    def test_wrong_frame_size_raises(self, vad):
        """Frames not exactly 512 samples should raise ValueError."""
        wrong_size = np.zeros(100, dtype=np.float32)
        with pytest.raises(ValueError, match="frame"):
            vad.process_frame(wrong_size)
