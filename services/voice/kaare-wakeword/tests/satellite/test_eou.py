# tests/satellite/test_eou.py
"""Tests for end-of-utterance detection."""
from __future__ import annotations

import pytest

from satellite.eou import EOUDetector, EOUResult


class TestEOUDetector:
    def test_no_speech_stays_waiting(self):
        """Silence-only frames should not trigger EOU."""
        eou = EOUDetector(silence_timeout_frames=50, hard_timeout_frames=500)
        for _ in range(49):
            result = eou.update(speech_prob=0.1)
            assert result == EOUResult.LISTENING

    def test_speech_then_silence_triggers_eou(self):
        """Speech followed by enough silence triggers EOU."""
        eou = EOUDetector(silence_timeout_frames=5, hard_timeout_frames=500)
        # Speech
        for _ in range(10):
            result = eou.update(speech_prob=0.9)
            assert result == EOUResult.LISTENING
        # Silence
        for i in range(4):
            result = eou.update(speech_prob=0.1)
            assert result == EOUResult.LISTENING
        # 5th silence frame triggers EOU
        result = eou.update(speech_prob=0.1)
        assert result == EOUResult.END_OF_UTTERANCE

    def test_hard_timeout(self):
        """Continuous speech beyond hard timeout triggers timeout."""
        eou = EOUDetector(silence_timeout_frames=50, hard_timeout_frames=10)
        for i in range(9):
            result = eou.update(speech_prob=0.9)
            assert result == EOUResult.LISTENING
        result = eou.update(speech_prob=0.9)
        assert result == EOUResult.HARD_TIMEOUT

    def test_silence_counter_resets_on_speech(self):
        """Brief silence followed by speech resets the silence counter."""
        eou = EOUDetector(silence_timeout_frames=5, hard_timeout_frames=500)
        # Speech
        for _ in range(5):
            eou.update(speech_prob=0.9)
        # Brief silence (not enough to trigger)
        for _ in range(3):
            eou.update(speech_prob=0.1)
        # Speech again -- resets counter
        eou.update(speech_prob=0.9)
        # Need full 5 silence frames again
        for _ in range(4):
            result = eou.update(speech_prob=0.1)
            assert result == EOUResult.LISTENING
        result = eou.update(speech_prob=0.1)
        assert result == EOUResult.END_OF_UTTERANCE

    def test_reset_clears_state(self):
        """Reset allows reuse for next utterance."""
        eou = EOUDetector(silence_timeout_frames=5, hard_timeout_frames=500)
        for _ in range(10):
            eou.update(speech_prob=0.9)
        eou.reset()
        assert eou._speech_seen is False
        assert eou._silence_frames == 0
        assert eou._total_frames == 0

    def test_no_eou_without_speech(self):
        """Pure silence (no speech ever) should NOT trigger EOU."""
        eou = EOUDetector(silence_timeout_frames=5, hard_timeout_frames=500)
        for _ in range(100):
            result = eou.update(speech_prob=0.1)
            assert result == EOUResult.LISTENING
