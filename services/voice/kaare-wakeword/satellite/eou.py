# satellite/eou.py
"""End-of-utterance detection.

Consumes VAD speech probabilities and determines when the user has
finished speaking. Uses two signals:

1. Silence timeout: speech_seen + N consecutive low-probability frames
2. Hard timeout: total frames exceed limit (prevents infinite listening)

Does NOT trigger on pure silence -- requires speech_seen=True first.
"""
from __future__ import annotations

from enum import Enum, auto


class EOUResult(Enum):
    """Result of each EOU update."""
    LISTENING = auto()
    END_OF_UTTERANCE = auto()
    HARD_TIMEOUT = auto()


class EOUDetector:
    """End-of-utterance detector driven by VAD probabilities.

    Feed VAD probabilities via update(). Returns EOUResult indicating
    whether the user is still speaking, has finished, or timed out.

    Args:
        silence_timeout_frames: Consecutive silent frames to trigger EOU.
        hard_timeout_frames: Maximum total frames before hard timeout.
        speech_threshold: VAD probability above which counts as speech.
    """

    def __init__(
        self,
        silence_timeout_frames: int = 50,
        hard_timeout_frames: int = 500,
        speech_threshold: float = 0.5,
    ):
        self._silence_timeout = silence_timeout_frames
        self._hard_timeout = hard_timeout_frames
        self._threshold = speech_threshold
        self._speech_seen = False
        self._silence_frames = 0
        self._total_frames = 0

    def update(self, speech_prob: float) -> EOUResult:
        """Process one VAD frame.

        Args:
            speech_prob: Speech probability [0.0, 1.0] from VAD.

        Returns:
            EOUResult indicating current state.
        """
        self._total_frames += 1

        if self._total_frames >= self._hard_timeout:
            return EOUResult.HARD_TIMEOUT

        if speech_prob > self._threshold:
            self._speech_seen = True
            self._silence_frames = 0
            return EOUResult.LISTENING

        # Below threshold
        if self._speech_seen:
            self._silence_frames += 1
            if self._silence_frames >= self._silence_timeout:
                return EOUResult.END_OF_UTTERANCE

        return EOUResult.LISTENING

    def reset(self) -> None:
        """Reset state for next utterance."""
        self._speech_seen = False
        self._silence_frames = 0
        self._total_frames = 0
