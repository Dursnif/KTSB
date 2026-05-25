# Voice Satellite Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a full voice satellite pipeline (wake word -> VAD -> stream -> STT -> NLU -> TTS -> playback) that prototypes on Mac and deploys to BPi M2+ satellites.

**Architecture:** Single audio stream feeds a ring buffer consumed by wake word detector and Silero VAD. A state machine (IDLE -> LISTENING -> PROCESSING -> RESPONDING) orchestrates the pipeline. Satellite communicates with server via async WebSocket. Server runs faster-whisper STT, Ollama/Claude NLU, and Piper TTS.

**Tech Stack:** Python 3.11+, uv (package manager), sounddevice, Silero VAD (ONNX), websockets, faster-whisper, Piper TTS, Ollama, anthropic SDK, Home Assistant REST API. Existing: TFLite wake word models, librosa MFCC extraction.

---

## Task 1: Project Setup -- Dependencies & Package Structure

**Files:**
- Modify: `pyproject.toml`
- Create: `satellite/__init__.py`
- Create: `satellite/config.py`
- Create: `server/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/satellite/__init__.py`
- Create: `tests/server/__init__.py`

**Step 1: Add satellite and server dependencies to pyproject.toml**

Add new dependency groups and package entries. Keep existing deps intact.

```toml
# Add to [project.dependencies]:
    "onnxruntime>=1.17",
    "websockets>=12.0",

# Add new optional dependency groups:
[project.optional-dependencies]
# ... keep existing ...
satellite = ["onnxruntime>=1.17", "websockets>=12.0"]
server = [
    "faster-whisper>=1.0",
    "websockets>=12.0",
    "anthropic>=0.40",
    "requests>=2.31",
]
tts = ["piper-tts>=1.2"]
# ... keep existing coral, vim3, dev ...

# Update [tool.setuptools]:
[tool.setuptools]
packages = ["scripts", "training", "inference", "satellite", "server"]

# Add new entry points:
[project.scripts]
# ... keep existing ...
kaare-satellite = "satellite.pipeline:main"
kaare-server = "server.server:main"
```

**Step 2: Create satellite package**

```python
# satellite/__init__.py
"""Voice satellite client -- wake word, VAD, streaming."""
```

**Step 3: Create satellite config**

```python
# satellite/config.py
"""Satellite configuration constants.

All tunable parameters live here. Import from this module,
not from scattered magic numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SatelliteConfig:
    """Immutable satellite configuration."""

    # Audio
    sample_rate: int = 16_000
    channels: int = 1
    dtype: str = "float32"

    # Ring buffer
    ring_buffer_seconds: float = 3.0

    # Wake word
    wake_confidence: float = 0.85
    wake_debounce_s: float = 2.0

    # VAD (Silero)
    vad_threshold: float = 0.5
    vad_frame_ms: int = 30  # Silero requires 30ms frames

    # End-of-utterance
    eou_silence_s: float = 1.5
    eou_hard_timeout_s: float = 15.0
    pre_roll_s: float = 0.5

    # Server connection
    server_url: str = "ws://localhost:8765"
    satellite_id: str = "mac-prototype"

    @property
    def ring_buffer_samples(self) -> int:
        return int(self.ring_buffer_seconds * self.sample_rate)

    @property
    def vad_frame_samples(self) -> int:
        return int(self.vad_frame_ms * self.sample_rate / 1000)

    @property
    def pre_roll_samples(self) -> int:
        return int(self.pre_roll_s * self.sample_rate)

    @property
    def eou_silence_frames(self) -> int:
        """Number of VAD frames of silence needed to trigger EOU."""
        return int(self.eou_silence_s * 1000 / self.vad_frame_ms)

    @property
    def eou_hard_timeout_frames(self) -> int:
        """Max VAD frames before hard timeout."""
        return int(self.eou_hard_timeout_s * 1000 / self.vad_frame_ms)
```

**Step 4: Create server package**

```python
# server/__init__.py
"""Voice satellite server -- STT, NLU, TTS."""
```

**Step 5: Create test directories**

```python
# tests/__init__.py
# tests/satellite/__init__.py
# tests/server/__init__.py
# (all empty)
```

**Step 6: Install with uv**

Run: `uv sync --extra satellite --extra server --extra dev`
Expected: All dependencies install successfully.

**Step 7: Commit**

```bash
git add satellite/ server/ tests/ pyproject.toml
git commit -m "feat: scaffold satellite and server packages with config"
```

---

## Task 2: Ring Buffer -- `satellite/audio.py`

**Files:**
- Create: `satellite/audio.py`
- Create: `tests/satellite/test_audio.py`

**Step 1: Write the failing test**

```python
# tests/satellite/test_audio.py
"""Tests for ring buffer and audio capture."""
from __future__ import annotations

import numpy as np
import pytest

from satellite.audio import RingBuffer


class TestRingBuffer:
    def test_write_and_read_back(self):
        """Write samples, read them back."""
        buf = RingBuffer(max_samples=1000, sample_rate=16000)
        data = np.arange(500, dtype=np.float32)
        buf.write(data)
        result = buf.read_last(500)
        np.testing.assert_array_equal(result, data)

    def test_read_last_fewer_than_written(self):
        """Read fewer samples than were written."""
        buf = RingBuffer(max_samples=1000, sample_rate=16000)
        data = np.arange(500, dtype=np.float32)
        buf.write(data)
        result = buf.read_last(100)
        np.testing.assert_array_equal(result, data[400:])

    def test_wrap_around(self):
        """Buffer wraps around correctly when full."""
        buf = RingBuffer(max_samples=100, sample_rate=16000)
        # Write 150 samples into a 100-sample buffer
        data1 = np.arange(100, dtype=np.float32)
        buf.write(data1)
        data2 = np.arange(100, 150, dtype=np.float32)
        buf.write(data2)
        # Last 100 samples should be 50..149
        result = buf.read_last(100)
        expected = np.arange(50, 150, dtype=np.float32)
        np.testing.assert_array_equal(result, expected)

    def test_read_more_than_available_pads_with_zeros(self):
        """Reading more than available returns zero-padded result."""
        buf = RingBuffer(max_samples=1000, sample_rate=16000)
        data = np.ones(100, dtype=np.float32)
        buf.write(data)
        result = buf.read_last(200)
        assert len(result) == 200
        np.testing.assert_array_equal(result[:100], 0.0)
        np.testing.assert_array_equal(result[100:], 1.0)

    def test_samples_written_counter(self):
        """samples_written tracks total samples ever written."""
        buf = RingBuffer(max_samples=100, sample_rate=16000)
        buf.write(np.zeros(50, dtype=np.float32))
        buf.write(np.zeros(75, dtype=np.float32))
        assert buf.samples_written == 125
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/satellite/test_audio.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'satellite.audio'`

**Step 3: Write RingBuffer implementation**

```python
# satellite/audio.py
"""Audio capture and ring buffer.

The ring buffer stores the most recent N seconds of audio. Two consumers
(wake word detector and VAD) read from it independently. The buffer is
lock-free for a single writer / multiple readers pattern because only
the write pointer advances and readers only look at committed data.
"""
from __future__ import annotations

import numpy as np


class RingBuffer:
    """Fixed-size circular buffer for audio samples.

    Stores float32 mono audio. Supports reading the last N samples
    even when the buffer has wrapped around.

    Args:
        max_samples: Buffer capacity in samples.
        sample_rate: Audio sample rate (for time-based reads).
    """

    def __init__(self, max_samples: int, sample_rate: int = 16_000):
        self._buf = np.zeros(max_samples, dtype=np.float32)
        self._max = max_samples
        self._write_pos = 0
        self._samples_written = 0
        self.sample_rate = sample_rate

    @property
    def samples_written(self) -> int:
        return self._samples_written

    def write(self, data: np.ndarray) -> None:
        """Append samples to the buffer.

        If data is longer than the buffer, only the last max_samples
        are kept.
        """
        n = len(data)
        if n >= self._max:
            # Only keep the tail
            self._buf[:] = data[-self._max:]
            self._write_pos = 0
            self._samples_written += n
            return

        end = self._write_pos + n
        if end <= self._max:
            self._buf[self._write_pos:end] = data
        else:
            first = self._max - self._write_pos
            self._buf[self._write_pos:] = data[:first]
            self._buf[:n - first] = data[first:]
        self._write_pos = end % self._max
        self._samples_written += n

    def read_last(self, n_samples: int) -> np.ndarray:
        """Read the most recent n_samples from the buffer.

        If fewer than n_samples have been written, the result is
        zero-padded on the left (oldest side).
        """
        available = min(self._samples_written, self._max)
        to_read = min(n_samples, available)

        result = np.zeros(n_samples, dtype=np.float32)
        start = (self._write_pos - to_read) % self._max

        if start + to_read <= self._max:
            result[n_samples - to_read:] = self._buf[start:start + to_read]
        else:
            first = self._max - start
            result[n_samples - to_read:n_samples - to_read + first] = self._buf[start:]
            result[n_samples - to_read + first:] = self._buf[:to_read - first]

        return result
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/satellite/test_audio.py -v`
Expected: All 5 tests PASS.

**Step 5: Commit**

```bash
git add satellite/audio.py tests/satellite/test_audio.py
git commit -m "feat(satellite): ring buffer for audio capture"
```

---

## Task 3: Silero VAD Wrapper -- `satellite/vad.py`

**Files:**
- Create: `satellite/vad.py`
- Create: `tests/satellite/test_vad.py`

**Step 1: Write the failing test**

```python
# tests/satellite/test_vad.py
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
        silence = np.zeros(480, dtype=np.float32)  # 30ms at 16kHz
        prob = vad.process_frame(silence)
        assert 0.0 <= prob <= 1.0
        assert prob < 0.5

    def test_noise_returns_float(self, vad):
        """Random noise should return a valid float probability."""
        noise = np.random.randn(480).astype(np.float32) * 0.01
        prob = vad.process_frame(noise)
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_reset_clears_state(self, vad):
        """Reset should not raise and should allow re-processing."""
        silence = np.zeros(480, dtype=np.float32)
        vad.process_frame(silence)
        vad.reset()
        prob = vad.process_frame(silence)
        assert 0.0 <= prob <= 1.0

    def test_wrong_frame_size_raises(self, vad):
        """Frames not exactly 30ms should raise ValueError."""
        wrong_size = np.zeros(100, dtype=np.float32)
        with pytest.raises(ValueError, match="frame"):
            vad.process_frame(wrong_size)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/satellite/test_vad.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'satellite.vad'`

**Step 3: Write Silero VAD wrapper**

```python
# satellite/vad.py
"""Silero VAD wrapper.

Uses the Silero VAD ONNX model via torch.hub for speech probability
per 30ms audio frame. The model is stateful (LSTM hidden state) and
must be reset between utterances.

Download happens automatically on first use (~1.5MB model).
"""
from __future__ import annotations

import numpy as np
import torch


class SileroVAD:
    """Silero Voice Activity Detection.

    Wraps the Silero VAD model from torch.hub. Processes 30ms frames
    (480 samples at 16kHz) and returns speech probability [0.0, 1.0].

    Args:
        threshold: Speech probability threshold.
        sample_rate: Audio sample rate (must be 16000).
    """

    FRAME_SAMPLES = 480  # 30ms at 16kHz

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16_000):
        if sample_rate != 16_000:
            raise ValueError("Silero VAD requires 16kHz audio")
        self.threshold = threshold
        self.sample_rate = sample_rate

        self._model, self._utils = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            trust_repo=True,
        )
        self._model.eval()

    def process_frame(self, frame: np.ndarray) -> float:
        """Process a single 30ms audio frame.

        Args:
            frame: float32 array of exactly 480 samples (30ms at 16kHz).

        Returns:
            Speech probability [0.0, 1.0].

        Raises:
            ValueError: If frame is not exactly 480 samples.
        """
        if len(frame) != self.FRAME_SAMPLES:
            raise ValueError(
                f"VAD frame must be exactly {self.FRAME_SAMPLES} samples "
                f"(30ms at 16kHz), got {len(frame)}"
            )

        tensor = torch.from_numpy(frame).float()
        with torch.no_grad():
            prob = self._model(tensor, self.sample_rate)
        return float(prob.item())

    def reset(self) -> None:
        """Reset internal LSTM state between utterances."""
        self._model.reset_states()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/satellite/test_vad.py -v`
Expected: All 4 tests PASS (first run downloads ~1.5MB model).

**Step 5: Commit**

```bash
git add satellite/vad.py tests/satellite/test_vad.py
git commit -m "feat(satellite): Silero VAD wrapper with 30ms frame processing"
```

---

## Task 4: End-of-Utterance Detector -- `satellite/eou.py`

**Files:**
- Create: `satellite/eou.py`
- Create: `tests/satellite/test_eou.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/satellite/test_eou.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'satellite.eou'`

**Step 3: Write EOUDetector implementation**

```python
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/satellite/test_eou.py -v`
Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
git add satellite/eou.py tests/satellite/test_eou.py
git commit -m "feat(satellite): end-of-utterance detector with silence + hard timeout"
```

---

## Task 5: State Machine -- `satellite/pipeline.py`

**Files:**
- Create: `satellite/pipeline.py`
- Create: `tests/satellite/test_pipeline.py`

**Step 1: Write the failing test**

Test the state machine transitions in isolation (mock audio, VAD, wake word).

```python
# tests/satellite/test_pipeline.py
"""Tests for satellite pipeline state machine."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from satellite.pipeline import PipelineState, SatellitePipeline
from satellite.config import SatelliteConfig


class TestPipelineStateMachine:
    def test_initial_state_is_idle(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        assert pipeline.state == PipelineState.IDLE

    def test_wake_word_transitions_to_listening(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word()
        assert pipeline.state == PipelineState.LISTENING

    def test_eou_transitions_to_processing(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word()
        pipeline._on_end_of_utterance(reason="eou")
        assert pipeline.state == PipelineState.PROCESSING

    def test_idle_ignores_eou(self):
        """EOU in IDLE state should be ignored."""
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_end_of_utterance(reason="eou")
        assert pipeline.state == PipelineState.IDLE

    def test_response_complete_returns_to_idle(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word()
        pipeline._on_end_of_utterance(reason="eou")
        pipeline._on_response_complete()
        assert pipeline.state == PipelineState.IDLE

    def test_hard_timeout_returns_to_idle(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word()
        pipeline._on_end_of_utterance(reason="timeout")
        assert pipeline.state == PipelineState.PROCESSING
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/satellite/test_pipeline.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'satellite.pipeline'`

**Step 3: Write pipeline state machine**

```python
# satellite/pipeline.py
"""Main satellite pipeline.

Orchestrates audio capture, wake word detection, VAD, end-of-utterance,
and server streaming into a single async loop.

State Machine:
    IDLE -> LISTENING (wake word)
    LISTENING -> PROCESSING (EOU / timeout)
    LISTENING -> IDLE (hard timeout without speech)
    PROCESSING -> RESPONDING (TTS audio received)
    PROCESSING -> IDLE (error / timeout)
    RESPONDING -> IDLE (playback complete)
    RESPONDING -> LISTENING (barge-in wake word)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from enum import Enum, auto
from pathlib import Path

import numpy as np
import sounddevice as sd

from satellite.audio import RingBuffer
from satellite.config import SatelliteConfig
from satellite.eou import EOUDetector, EOUResult
from satellite.vad import SileroVAD
from inference.common import WakeWordProcessor, extract_mfcc
from scripts.audio_config import CLIP_SAMPLES, SAMPLE_RATE

log = logging.getLogger(__name__)


class PipelineState(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    RESPONDING = auto()


class SatellitePipeline:
    """Voice satellite pipeline state machine.

    Combines wake word detection, VAD, EOU detection, and server
    communication into a coherent pipeline.
    """

    def __init__(self, config: SatelliteConfig):
        self.config = config
        self.state = PipelineState.IDLE

        # Audio
        self.ring_buffer = RingBuffer(
            max_samples=config.ring_buffer_samples,
            sample_rate=config.sample_rate,
        )

        # VAD + EOU (created lazily or on start)
        self._vad: SileroVAD | None = None
        self._eou: EOUDetector | None = None

        # Wake word
        self._wake_processor = WakeWordProcessor(
            confidence=config.wake_confidence,
            debounce_seconds=config.wake_debounce_s,
        )

        # Utterance audio (collected during LISTENING)
        self._utterance_chunks: list[np.ndarray] = []

        # Stop signal
        self._stop_event = asyncio.Event()

    def _on_wake_word(self) -> None:
        """Handle wake word detection."""
        if self.state in (PipelineState.IDLE, PipelineState.RESPONDING):
            log.info("Wake word detected -- transitioning to LISTENING")
            self.state = PipelineState.LISTENING
            self._utterance_chunks = []

            # Grab pre-roll from ring buffer
            pre_roll = self.ring_buffer.read_last(self.config.pre_roll_samples)
            self._utterance_chunks.append(pre_roll)

            # Reset EOU for new utterance
            if self._eou:
                self._eou.reset()
            if self._vad:
                self._vad.reset()

    def _on_end_of_utterance(self, reason: str = "eou") -> None:
        """Handle end of utterance (silence timeout or hard timeout)."""
        if self.state != PipelineState.LISTENING:
            return
        log.info("End of utterance (reason=%s) -- transitioning to PROCESSING", reason)
        self.state = PipelineState.PROCESSING

    def _on_response_complete(self) -> None:
        """Handle response playback complete."""
        if self.state in (PipelineState.PROCESSING, PipelineState.RESPONDING):
            log.info("Response complete -- transitioning to IDLE")
            self.state = PipelineState.IDLE


def main() -> None:
    """Entry point for satellite pipeline."""
    parser = argparse.ArgumentParser(description="Voice satellite pipeline")
    parser.add_argument("--server", default="ws://localhost:8765", help="Server WebSocket URL")
    parser.add_argument("--device", type=int, default=0, help="Audio input device index")
    parser.add_argument("--model", type=Path, default=Path("models/wakeword_mac.tflite"), help="Wake word model path")
    parser.add_argument("--satellite-id", default="mac-prototype", help="Satellite identifier")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        from inference.common import list_audio_devices
        for idx, name in list_audio_devices().items():
            print(f"  {idx}: {name}")
        return

    config = SatelliteConfig(
        server_url=args.server,
        satellite_id=args.satellite_id,
    )
    pipeline = SatellitePipeline(config)
    print(f"Satellite '{config.satellite_id}' ready. State: {pipeline.state.name}")
    print("Full async loop will be implemented in Task 14.")


if __name__ == "__main__":
    main()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/satellite/test_pipeline.py -v`
Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
git add satellite/pipeline.py tests/satellite/test_pipeline.py
git commit -m "feat(satellite): pipeline state machine with wake/EOU/response transitions"
```

---

## Task 6: WebSocket Streaming Client -- `satellite/streaming.py`

**Files:**
- Create: `satellite/streaming.py`
- Create: `tests/satellite/test_streaming.py`

**Step 1: Write the failing test**

```python
# tests/satellite/test_streaming.py
"""Tests for WebSocket streaming client."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from satellite.streaming import SatelliteClient, AudioStartMsg, AudioChunkMsg, AudioEndMsg


class TestMessageSerialization:
    def test_audio_start_serializes(self):
        msg = AudioStartMsg(
            satellite_id="test-sat",
            sample_rate=16000,
            channels=1,
            format="float32",
            pre_roll_ms=500,
        )
        data = msg.to_json()
        parsed = json.loads(data)
        assert parsed["type"] == "audio_start"
        assert parsed["satellite_id"] == "test-sat"
        assert parsed["sample_rate"] == 16000

    def test_audio_chunk_serializes(self):
        audio = np.zeros(480, dtype=np.float32)
        msg = AudioChunkMsg(
            payload=audio.tobytes(),
            sequence=0,
            vad_probability=0.1,
        )
        data = msg.to_json()
        parsed = json.loads(data)
        assert parsed["type"] == "audio_chunk"
        assert parsed["sequence"] == 0

    def test_audio_end_serializes(self):
        msg = AudioEndMsg(reason="eou")
        data = msg.to_json()
        parsed = json.loads(data)
        assert parsed["type"] == "audio_end"
        assert parsed["reason"] == "eou"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/satellite/test_streaming.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'satellite.streaming'`

**Step 3: Write streaming client**

```python
# satellite/streaming.py
"""WebSocket streaming client for satellite-to-server communication.

Handles the WebSocket protocol: sending audio chunks to the server
and receiving transcript/TTS responses back.

Message protocol (satellite -> server):
    audio_start: Begin a new utterance stream.
    audio_chunk: PCM audio data with VAD probability.
    audio_end:   End of utterance (reason: eou, timeout, cancel).

Message protocol (server -> satellite):
    transcript:     STT result (partial or final).
    intent:         NLU result (action + entities).
    audio_response: TTS audio to play back.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class AudioStartMsg:
    satellite_id: str
    sample_rate: int
    channels: int
    format: str
    pre_roll_ms: int

    def to_json(self) -> str:
        return json.dumps({
            "type": "audio_start",
            "satellite_id": self.satellite_id,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "format": self.format,
            "pre_roll_ms": self.pre_roll_ms,
        })


@dataclass
class AudioChunkMsg:
    payload: bytes
    sequence: int
    vad_probability: float

    def to_json(self) -> str:
        return json.dumps({
            "type": "audio_chunk",
            "payload": base64.b64encode(self.payload).decode("ascii"),
            "sequence": self.sequence,
            "vad_probability": self.vad_probability,
        })


@dataclass
class AudioEndMsg:
    reason: str  # "eou", "timeout", "cancel"

    def to_json(self) -> str:
        return json.dumps({
            "type": "audio_end",
            "reason": self.reason,
        })


class SatelliteClient:
    """Async WebSocket client for satellite streaming.

    Manages the connection lifecycle and provides methods for
    sending audio and receiving server responses.

    Args:
        server_url: WebSocket server URL.
        on_transcript: Callback for transcript messages.
        on_audio_response: Callback for TTS audio responses.
        on_intent: Callback for intent messages.
    """

    def __init__(
        self,
        server_url: str,
        on_transcript: Callable[[dict], Coroutine] | None = None,
        on_audio_response: Callable[[dict], Coroutine] | None = None,
        on_intent: Callable[[dict], Coroutine] | None = None,
    ):
        self.server_url = server_url
        self._ws = None
        self._on_transcript = on_transcript
        self._on_audio_response = on_audio_response
        self._on_intent = on_intent

    async def connect(self) -> None:
        """Connect to the server."""
        import websockets
        self._ws = await websockets.connect(self.server_url)
        log.info("Connected to server: %s", self.server_url)

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: AudioStartMsg | AudioChunkMsg | AudioEndMsg) -> None:
        """Send a message to the server."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(msg.to_json())

    async def receive_loop(self) -> None:
        """Listen for server messages and dispatch to callbacks."""
        if not self._ws:
            raise RuntimeError("Not connected")
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Invalid JSON from server: %s", raw[:100])
                continue
            msg_type = msg.get("type")
            if msg_type == "transcript" and self._on_transcript:
                await self._on_transcript(msg)
            elif msg_type == "audio_response" and self._on_audio_response:
                await self._on_audio_response(msg)
            elif msg_type == "intent" and self._on_intent:
                await self._on_intent(msg)
            else:
                log.debug("Unknown message type: %s", msg_type)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/satellite/test_streaming.py -v`
Expected: All 3 tests PASS.

**Step 5: Commit**

```bash
git add satellite/streaming.py tests/satellite/test_streaming.py
git commit -m "feat(satellite): WebSocket streaming client with message protocol"
```

---

## Task 7: WebSocket Server -- `server/server.py`

**Files:**
- Create: `server/server.py`
- Create: `server/config.py`
- Create: `tests/server/test_server.py`

**Step 1: Write the failing test**

```python
# tests/server/test_server.py
"""Tests for WebSocket server."""
from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from server.server import VoiceServer
from server.config import ServerConfig


@pytest.fixture
async def server():
    """Start a test server on an ephemeral port."""
    config = ServerConfig(host="127.0.0.1", port=0)
    srv = VoiceServer(config)
    task = asyncio.create_task(srv.start())
    # Wait for server to bind
    await asyncio.sleep(0.2)
    yield srv
    srv.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestVoiceServer:
    @pytest.mark.asyncio
    async def test_server_accepts_connection(self, server):
        """Server should accept a WebSocket connection."""
        url = f"ws://{server.config.host}:{server.port}"
        async with websockets.connect(url) as ws:
            assert ws.open

    @pytest.mark.asyncio
    async def test_audio_start_acknowledged(self, server):
        """Server should log audio_start without crashing."""
        url = f"ws://{server.config.host}:{server.port}"
        async with websockets.connect(url) as ws:
            msg = json.dumps({
                "type": "audio_start",
                "satellite_id": "test",
                "sample_rate": 16000,
                "channels": 1,
                "format": "float32",
                "pre_roll_ms": 500,
            })
            await ws.send(msg)
            # Server shouldn't close the connection
            await asyncio.sleep(0.1)
            assert ws.open
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/server/test_server.py -v`
Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Write server config**

```python
# server/config.py
"""Server configuration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerConfig:
    """Voice server configuration."""

    host: str = "0.0.0.0"
    port: int = 8765

    # STT
    whisper_model: str = "base"
    whisper_language: str | None = None  # None = auto-detect

    # NLU
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # TTS
    piper_voice: str = "en_US-lessac-medium"

    # Home Assistant
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
```

**Step 4: Write server**

```python
# server/server.py
"""WebSocket voice server.

Accepts audio streams from satellites and orchestrates the
STT -> NLU -> TTS pipeline. Each satellite connection gets its own
handler coroutine.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from server.config import ServerConfig

log = logging.getLogger(__name__)


class VoiceServer:
    """Async WebSocket server for voice satellites.

    Each connected satellite gets a dedicated handler that accumulates
    audio, runs STT on end-of-utterance, processes the transcript
    through NLU, and sends back TTS audio.

    Args:
        config: Server configuration.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self._server = None
        self._port: int | None = None
        self._stop_event = asyncio.Event()

    @property
    def port(self) -> int:
        """Actual port the server is listening on."""
        if self._port is not None:
            return self._port
        return self.config.port

    async def _handle_client(self, websocket) -> None:
        """Handle a single satellite connection."""
        satellite_id = "unknown"
        audio_chunks: list[bytes] = []

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Invalid JSON from client")
                    continue

                msg_type = msg.get("type")

                if msg_type == "audio_start":
                    satellite_id = msg.get("satellite_id", "unknown")
                    audio_chunks = []
                    log.info("Audio stream started from %s", satellite_id)

                elif msg_type == "audio_chunk":
                    payload = base64.b64decode(msg["payload"])
                    audio_chunks.append(payload)

                elif msg_type == "audio_end":
                    reason = msg.get("reason", "unknown")
                    log.info(
                        "Audio stream ended from %s (reason=%s, chunks=%d)",
                        satellite_id, reason, len(audio_chunks),
                    )
                    if audio_chunks and reason != "cancel":
                        # Concatenate all audio
                        all_audio = b"".join(audio_chunks)
                        audio_array = np.frombuffer(all_audio, dtype=np.float32)
                        log.info(
                            "Received %.1fs of audio from %s",
                            len(audio_array) / 16000, satellite_id,
                        )
                        # Placeholder response (replaced in Task 13)
                        await websocket.send(json.dumps({
                            "type": "transcript",
                            "text": f"[placeholder] received {len(audio_array)} samples",
                            "is_final": True,
                            "confidence": 0.0,
                            "language": "en",
                        }))
                    audio_chunks = []

        except Exception:
            log.exception("Error handling satellite %s", satellite_id)

    async def start(self) -> None:
        """Start the server."""
        import websockets

        self._server = await websockets.serve(
            self._handle_client,
            self.config.host,
            self.config.port,
        )
        # Record actual port (useful when port=0)
        for sock in self._server.sockets:
            addr = sock.getsockname()
            self._port = addr[1]
            break

        log.info("Voice server listening on %s:%d", self.config.host, self.port)
        await self._stop_event.wait()

    def stop(self) -> None:
        """Signal the server to stop."""
        self._stop_event.set()
        if self._server:
            self._server.close()


def main() -> None:
    """Entry point for voice server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Voice satellite server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--whisper-model", default="base")
    args = parser.parse_args()

    config = ServerConfig(
        host=args.host,
        port=args.port,
        whisper_model=args.whisper_model,
    )
    server = VoiceServer(config)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/server/test_server.py -v`
Expected: All 2 tests PASS.

**Step 6: Commit**

```bash
git add server/server.py server/config.py tests/server/test_server.py
git commit -m "feat(server): WebSocket server accepting satellite audio streams"
```

---

## Task 8: Integration Test -- Satellite-to-Server Round-Trip

**Files:**
- Create: `tests/satellite/test_pipeline_integration.py`

**Step 1: Write the integration test**

```python
# tests/satellite/test_pipeline_integration.py
"""Integration test: satellite -> server round-trip."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest
import websockets

from server.server import VoiceServer
from server.config import ServerConfig
from satellite.config import SatelliteConfig
from satellite.streaming import SatelliteClient, AudioStartMsg, AudioChunkMsg, AudioEndMsg


@pytest.mark.asyncio
async def test_satellite_to_server_roundtrip():
    """Send audio chunks to server and get a transcript back."""
    # Start server
    server_config = ServerConfig(host="127.0.0.1", port=0)
    server = VoiceServer(server_config)
    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.3)

    try:
        server_url = f"ws://127.0.0.1:{server.port}"
        transcript_received = asyncio.Event()
        transcript_text = ""

        async def on_transcript(msg):
            nonlocal transcript_text
            transcript_text = msg.get("text", "")
            transcript_received.set()

        client = SatelliteClient(server_url, on_transcript=on_transcript)
        await client.connect()

        # Start receive loop in background
        recv_task = asyncio.create_task(client.receive_loop())

        # Send audio
        await client.send(AudioStartMsg(
            satellite_id="test",
            sample_rate=16000,
            channels=1,
            format="float32",
            pre_roll_ms=500,
        ))

        # Send a 1-second chunk of silence
        silence = np.zeros(16000, dtype=np.float32)
        await client.send(AudioChunkMsg(
            payload=silence.tobytes(),
            sequence=0,
            vad_probability=0.0,
        ))

        await client.send(AudioEndMsg(reason="eou"))

        # Wait for transcript
        await asyncio.wait_for(transcript_received.wait(), timeout=2.0)
        assert "16000" in transcript_text  # placeholder includes sample count

        await client.disconnect()
        recv_task.cancel()
    finally:
        server.stop()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
```

**Step 2: Run test**

Run: `uv run pytest tests/satellite/test_pipeline_integration.py -v`
Expected: PASS -- validates end-to-end WebSocket flow.

**Step 3: Commit**

```bash
git add tests/satellite/test_pipeline_integration.py
git commit -m "test: satellite-to-server WebSocket round-trip integration test"
```

---

## Task 9: STT -- `server/stt.py` (faster-whisper)

**Files:**
- Create: `server/stt.py`
- Create: `tests/server/test_stt.py`

**Step 1: Write the failing test**

```python
# tests/server/test_stt.py
"""Tests for faster-whisper STT wrapper."""
from __future__ import annotations

import numpy as np
import pytest

from server.stt import WhisperSTT


@pytest.fixture
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/server/test_stt.py -v`
Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Write STT wrapper**

```python
# server/stt.py
"""faster-whisper STT wrapper.

Wraps the faster-whisper library for speech-to-text transcription.
Supports language auto-detection (Norwegian + English).

The 'tiny' model is used for development/testing. Use 'base' or
'small' for production quality.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class STTResult:
    """Speech-to-text result."""
    text: str
    language: str
    confidence: float


class WhisperSTT:
    """faster-whisper speech-to-text.

    Args:
        model_size: Whisper model size (tiny, base, small, medium, large-v3).
        language: Force language (None = auto-detect).
        device: Compute device (cpu, cuda, auto).
        compute_type: Quantization (int8, float16, float32, auto).
    """

    def __init__(
        self,
        model_size: str = "base",
        language: str | None = None,
        device: str = "auto",
        compute_type: str = "auto",
    ):
        from faster_whisper import WhisperModel

        self.language = language
        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        log.info("Whisper model loaded: %s (device=%s)", model_size, device)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> STTResult:
        """Transcribe audio to text.

        Args:
            audio: float32 mono audio array.
            sample_rate: Audio sample rate (must be 16kHz for Whisper).

        Returns:
            STTResult with text, detected language, and confidence.
        """
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
        )

        text_parts = []
        total_prob = 0.0
        n_segments = 0
        for seg in segments:
            text_parts.append(seg.text)
            total_prob += seg.avg_log_prob
            n_segments += 1

        text = " ".join(text_parts).strip()
        confidence = total_prob / max(n_segments, 1)
        language = info.language or "unknown"

        log.info("STT: [%s] (%.2f) %s", language, confidence, text[:80])
        return STTResult(text=text, language=language, confidence=confidence)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/server/test_stt.py -v`
Expected: All 2 tests PASS (first run downloads ~75MB tiny model).

**Step 5: Commit**

```bash
git add server/stt.py tests/server/test_stt.py
git commit -m "feat(server): faster-whisper STT wrapper with language auto-detect"
```

---

## Task 10: NLU -- `server/nlu.py` (Ollama + Claude fallback)

**Files:**
- Create: `server/nlu.py`
- Create: `tests/server/test_nlu.py`

**Step 1: Write the failing test**

```python
# tests/server/test_nlu.py
"""Tests for NLU module."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from server.nlu import NLUEngine, NLUResult


class TestNLUResult:
    def test_result_fields(self):
        result = NLUResult(
            action="light_on",
            entities={"room": "kitchen"},
            response_text="Turning on the kitchen light.",
            confidence=0.9,
            source="ollama",
        )
        assert result.action == "light_on"
        assert result.entities["room"] == "kitchen"
        assert result.source == "ollama"

    def test_low_confidence_result(self):
        result = NLUResult(
            action="unknown",
            entities={},
            response_text="I didn't understand that.",
            confidence=0.2,
            source="ollama",
        )
        assert result.confidence < 0.5


class TestNLUEngine:
    def test_system_prompt_includes_ha_entities(self):
        """System prompt should mention available HA entities."""
        engine = NLUEngine(
            ollama_url="http://localhost:11434",
            ollama_model="llama3.2",
            ha_entities=["light.kitchen", "switch.tv"],
        )
        assert "light.kitchen" in engine._system_prompt
        assert "switch.tv" in engine._system_prompt
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/server/test_nlu.py -v`
Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Write NLU engine**

```python
# server/nlu.py
"""Natural Language Understanding.

Two-tier strategy:
1. Ollama (local, fast, free) -- try first
2. Claude API (cloud, accurate, costs money) -- fallback when user approves

The NLU prompt asks the LLM to classify the intent and extract entities
for Home Assistant actions.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are a voice assistant for a smart home. The user speaks Norwegian or English.
Respond in the same language the user spoke.

Available Home Assistant entities:
{entities}

For smart home commands, respond with JSON:
{{"action": "ha_call_service", "domain": "light", "service": "turn_on", \
"entity_id": "light.kitchen", "response": "Skrur pa lyset pa kjoekkenet."}}

For general questions, respond with JSON:
{{"action": "answer", "response": "Your helpful answer here."}}

For things you cannot do, respond with JSON:
{{"action": "unknown", "response": "Beklager, det kan jeg ikke hjelpe med."}}

Always respond with valid JSON only, no other text.
"""


@dataclass
class NLUResult:
    """NLU processing result."""
    action: str
    entities: dict
    response_text: str
    confidence: float
    source: str  # "ollama" or "claude"


class NLUEngine:
    """Two-tier NLU engine: Ollama local -> Claude cloud fallback.

    Args:
        ollama_url: Ollama API base URL.
        ollama_model: Ollama model name.
        claude_api_key: Anthropic API key (optional).
        ha_entities: List of Home Assistant entity IDs.
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "llama3.2",
        claude_api_key: str | None = None,
        ha_entities: list[str] | None = None,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.claude_api_key = claude_api_key
        self._ha_entities = ha_entities or []
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            entities="\n".join(f"- {e}" for e in self._ha_entities) or "None configured"
        )

    def process_local(self, transcript: str, language: str = "en") -> NLUResult:
        """Process transcript with Ollama (local LLM).

        Args:
            transcript: User's spoken text.
            language: Detected language code.

        Returns:
            NLUResult from local processing.
        """
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": transcript,
                    "system": self._system_prompt,
                    "stream": False,
                },
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")

            try:
                parsed = json.loads(raw)
                return NLUResult(
                    action=parsed.get("action", "unknown"),
                    entities={
                        k: v for k, v in parsed.items()
                        if k not in ("action", "response")
                    },
                    response_text=parsed.get("response", ""),
                    confidence=0.8,
                    source="ollama",
                )
            except json.JSONDecodeError:
                return NLUResult(
                    action="answer",
                    entities={},
                    response_text=raw,
                    confidence=0.5,
                    source="ollama",
                )

        except requests.RequestException as exc:
            log.warning("Ollama request failed: %s", exc)
            return NLUResult(
                action="error",
                entities={},
                response_text="Beklager, jeg klarte ikke a behandle forespoerselen.",
                confidence=0.0,
                source="ollama",
            )

    async def process_cloud(self, transcript: str, language: str = "en") -> NLUResult:
        """Process transcript with Claude API (cloud fallback).

        Only called when user approves sky-escalation.

        Args:
            transcript: User's spoken text.
            language: Detected language code.

        Returns:
            NLUResult from Claude processing.
        """
        if not self.claude_api_key:
            return NLUResult(
                action="error",
                entities={},
                response_text="Cloud processing not configured.",
                confidence=0.0,
                source="claude",
            )

        import anthropic

        client = anthropic.Anthropic(api_key=self.claude_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            system=self._system_prompt,
            messages=[{"role": "user", "content": transcript}],
        )

        raw = message.content[0].text
        try:
            parsed = json.loads(raw)
            return NLUResult(
                action=parsed.get("action", "unknown"),
                entities={
                    k: v for k, v in parsed.items()
                    if k not in ("action", "response")
                },
                response_text=parsed.get("response", ""),
                confidence=0.95,
                source="claude",
            )
        except json.JSONDecodeError:
            return NLUResult(
                action="answer",
                entities={},
                response_text=raw,
                confidence=0.7,
                source="claude",
            )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/server/test_nlu.py -v`
Expected: All 3 tests PASS (no network calls -- only tests structure and prompt).

**Step 5: Commit**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "feat(server): NLU engine with Ollama local + Claude cloud fallback"
```

---

## Task 11: TTS -- `server/tts.py` (Piper)

**Files:**
- Create: `server/tts.py`
- Create: `tests/server/test_tts.py`

**Step 1: Write the failing test**

```python
# tests/server/test_tts.py
"""Tests for Piper TTS wrapper."""
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
        """Synthesize should return audio bytes."""
        tts = PiperTTS(voice="en_US-lessac-medium")
        audio_bytes = tts.synthesize("Hello world")
        assert isinstance(audio_bytes, bytes)
        assert len(audio_bytes) > 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/server/test_tts.py -v`
Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Write TTS wrapper**

```python
# server/tts.py
"""Piper TTS wrapper.

Piper is a fast, local neural TTS engine. It runs on CPU and produces
natural-sounding speech. Voices are downloaded automatically.

Norwegian voices:
  - nb_NO-talessynth-medium (Bokmal)
English voices:
  - en_US-lessac-medium (default)
"""
from __future__ import annotations

import logging
import subprocess

import numpy as np

log = logging.getLogger(__name__)


class PiperTTS:
    """Piper text-to-speech.

    Uses piper-tts CLI or Python binding to synthesize speech.

    Args:
        voice: Piper voice name or path.
        sample_rate: Output sample rate (Piper default is 22050).
    """

    def __init__(self, voice: str = "en_US-lessac-medium", sample_rate: int = 22050):
        self.voice = voice
        self.sample_rate = sample_rate

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to raw PCM audio bytes (int16).

        Args:
            text: Text to speak.

        Returns:
            Raw PCM audio bytes (int16, mono, at self.sample_rate).
        """
        try:
            result = subprocess.run(
                [
                    "piper",
                    "--model", self.voice,
                    "--output-raw",
                ],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                log.error("Piper failed: %s", result.stderr.decode()[:200])
                return b""
            return result.stdout

        except FileNotFoundError:
            log.warning("piper not found in PATH, using espeak fallback")
            return self._espeak_fallback(text)

        except subprocess.TimeoutExpired:
            log.error("Piper timed out synthesizing: %s", text[:50])
            return b""

    def _espeak_fallback(self, text: str) -> bytes:
        """Fallback to espeak for development/testing."""
        try:
            result = subprocess.run(
                ["espeak-ng", "--stdout", text],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Last resort: return silence
        duration_s = max(0.5, len(text) * 0.06)
        n_samples = int(duration_s * self.sample_rate)
        return np.zeros(n_samples, dtype=np.int16).tobytes()

    def synthesize_to_float32(self, text: str, target_rate: int = 16000) -> np.ndarray:
        """Synthesize and convert to float32 at target sample rate.

        Args:
            text: Text to speak.
            target_rate: Desired output sample rate.

        Returns:
            float32 numpy array.
        """
        raw = self.synthesize(text)
        if not raw:
            return np.zeros(target_rate, dtype=np.float32)

        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        # Resample if needed
        if target_rate != self.sample_rate:
            import scipy.signal
            n_out = int(len(audio) * target_rate / self.sample_rate)
            audio = scipy.signal.resample(audio, n_out)

        return audio
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/server/test_tts.py -v`
Expected: All 2 tests PASS (may use espeak fallback or silence on Mac without piper).

**Step 5: Commit**

```bash
git add server/tts.py tests/server/test_tts.py
git commit -m "feat(server): Piper TTS wrapper with espeak fallback"
```

---

## Task 12: Home Assistant API -- `server/ha.py`

**Files:**
- Create: `server/ha.py`
- Create: `tests/server/test_ha.py`

**Step 1: Write the failing test**

```python
# tests/server/test_ha.py
"""Tests for Home Assistant API client."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.ha import HomeAssistantClient


class TestHomeAssistantClient:
    def test_init_with_url_and_token(self):
        client = HomeAssistantClient(
            url="http://ha.local:8123",
            token="test-token",
        )
        assert client.url == "http://ha.local:8123"

    def test_headers_include_bearer_token(self):
        client = HomeAssistantClient(
            url="http://ha.local:8123",
            token="my-token",
        )
        headers = client._headers()
        assert headers["Authorization"] == "Bearer my-token"
        assert "application/json" in headers["Content-Type"]

    @patch("server.ha.requests.get")
    def test_list_entities_returns_entity_ids(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "switch.tv", "state": "off"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = HomeAssistantClient(url="http://ha.local:8123", token="t")
        entities = client.list_entities()
        assert "light.kitchen" in entities
        assert "switch.tv" in entities

    @patch("server.ha.requests.post")
    def test_call_service(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        client = HomeAssistantClient(url="http://ha.local:8123", token="t")
        client.call_service("light", "turn_on", "light.kitchen")
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "light/turn_on" in call_url
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/server/test_ha.py -v`
Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Write HA client**

```python
# server/ha.py
"""Home Assistant REST API client.

Provides methods to list entities and call services via the
Home Assistant REST API. Used by the NLU engine to execute
smart home actions.

API docs: https://developers.home-assistant.io/docs/api/rest/
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class HomeAssistantClient:
    """Home Assistant REST API client.

    Args:
        url: HA base URL (e.g. http://homeassistant.local:8123).
        token: Long-lived access token.
    """

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def list_entities(self) -> list[str]:
        """List all entity IDs from Home Assistant.

        Returns:
            List of entity ID strings.
        """
        resp = requests.get(
            f"{self.url}/api/states",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return [e["entity_id"] for e in resp.json()]

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: dict | None = None,
    ) -> None:
        """Call a Home Assistant service.

        Args:
            domain: Service domain (e.g. "light").
            service: Service name (e.g. "turn_on").
            entity_id: Target entity ID.
            data: Additional service data.
        """
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)

        resp = requests.post(
            f"{self.url}/api/services/{domain}/{service}",
            headers=self._headers(),
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        log.info("HA service called: %s.%s -> %s", domain, service, entity_id)

    def get_state(self, entity_id: str) -> dict:
        """Get current state of an entity.

        Args:
            entity_id: Entity ID to query.

        Returns:
            State dict with 'state', 'attributes', etc.
        """
        resp = requests.get(
            f"{self.url}/api/states/{entity_id}",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/server/test_ha.py -v`
Expected: All 4 tests PASS.

**Step 5: Commit**

```bash
git add server/ha.py tests/server/test_ha.py
git commit -m "feat(server): Home Assistant REST API client"
```

---

## Task 13: Wire Server Pipeline -- STT -> NLU -> TTS

**Files:**
- Modify: `server/server.py` (add ServerPipeline class, integrate into audio_end handler)
- Create: `tests/server/test_server_pipeline.py`

**Step 1: Write failing test**

```python
# tests/server/test_server_pipeline.py
"""Test that server pipeline calls STT -> NLU -> TTS in order."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from server.server import ServerPipeline
from server.config import ServerConfig


class TestServerPipeline:
    def test_pipeline_initializes(self):
        config = ServerConfig(whisper_model="tiny")
        with patch("server.server.WhisperSTT"), \
             patch("server.server.NLUEngine"), \
             patch("server.server.PiperTTS"):
            pipeline = ServerPipeline(config)
            assert pipeline is not None

    @patch("server.server.PiperTTS")
    @patch("server.server.NLUEngine")
    @patch("server.server.WhisperSTT")
    def test_process_audio_calls_stt(self, mock_stt_cls, mock_nlu_cls, mock_tts_cls):
        config = ServerConfig()
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = MagicMock(
            text="turn on the light", language="en", confidence=0.9
        )
        mock_stt_cls.return_value = mock_stt

        mock_nlu = MagicMock()
        mock_nlu.process_local.return_value = MagicMock(
            action="ha_call_service", entities={}, response_text="OK",
            confidence=0.8, source="ollama"
        )
        mock_nlu_cls.return_value = mock_nlu

        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b"\x00" * 100
        mock_tts_cls.return_value = mock_tts

        pipeline = ServerPipeline(config)

        audio = np.zeros(16000, dtype=np.float32)
        result = pipeline.process(audio)

        mock_stt.transcribe.assert_called_once()
        mock_nlu.process_local.assert_called_once()
        assert result is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/server/test_server_pipeline.py -v`
Expected: FAIL -- `ImportError: cannot import name 'ServerPipeline'`

**Step 3: Add ServerPipeline to server.py**

Add the following to `server/server.py` (add imports at top, class before VoiceServer):

```python
# Add at top of server/server.py:
from server.stt import WhisperSTT, STTResult
from server.nlu import NLUEngine, NLUResult
from server.tts import PiperTTS


@dataclass
class PipelineResult:
    """Result of processing an utterance."""
    transcript: STTResult
    nlu: NLUResult
    tts_audio: bytes


class ServerPipeline:
    """STT -> NLU -> TTS pipeline.

    Orchestrates the three server-side components into a single
    call: audio in, response audio out.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self._stt = WhisperSTT(
            model_size=config.whisper_model,
            language=config.whisper_language,
        )
        self._nlu = NLUEngine(
            ollama_url=config.ollama_url,
            ollama_model=config.ollama_model,
        )
        self._tts = PiperTTS(voice=config.piper_voice)

    def process(self, audio: np.ndarray) -> PipelineResult:
        """Process an utterance through the full pipeline.

        Args:
            audio: float32 mono audio from satellite.

        Returns:
            PipelineResult with transcript, NLU result, and TTS audio.
        """
        # STT
        transcript = self._stt.transcribe(audio)
        log.info("Transcript: %s", transcript.text)

        # NLU
        nlu_result = self._nlu.process_local(transcript.text, transcript.language)
        log.info("NLU: action=%s", nlu_result.action)

        # TTS
        tts_audio = self._tts.synthesize(nlu_result.response_text)

        return PipelineResult(
            transcript=transcript,
            nlu=nlu_result,
            tts_audio=tts_audio,
        )
```

Then update VoiceServer._handle_client to use ServerPipeline instead of the placeholder transcript.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/server/test_server_pipeline.py -v`
Expected: All 2 tests PASS.

**Step 5: Commit**

```bash
git add server/server.py tests/server/test_server_pipeline.py
git commit -m "feat(server): wire STT -> NLU -> TTS into ServerPipeline"
```

---

## Task 14: Full Satellite Async Loop

**Files:**
- Modify: `satellite/pipeline.py` (implement `run()` with real audio + VAD + wake word)
- Create: `tests/satellite/test_pipeline_smoke.py`

**Step 1: Write failing smoke test**

```python
# tests/satellite/test_pipeline_smoke.py
"""Smoke test: pipeline starts and stops cleanly."""
from __future__ import annotations

import asyncio

import pytest

from satellite.config import SatelliteConfig
from satellite.pipeline import SatellitePipeline, PipelineState


@pytest.mark.asyncio
async def test_pipeline_starts_and_stops():
    """Pipeline should start, run briefly, and stop without errors."""
    config = SatelliteConfig()
    pipeline = SatellitePipeline(config)

    # Run for 0.5 seconds then cancel
    task = asyncio.create_task(pipeline.run_for(duration_s=0.5))
    await asyncio.wait_for(task, timeout=3.0)
    assert pipeline.state == PipelineState.IDLE
```

**Step 2: Implement `run()` and `run_for()` in pipeline.py**

Add to `SatellitePipeline`:

```python
async def run(self) -> None:
    """Main async loop: capture audio, detect wake word, run VAD/EOU."""
    import tensorflow as tf

    self._vad = SileroVAD(threshold=self.config.vad_threshold)
    self._eou = EOUDetector(
        silence_timeout_frames=self.config.eou_silence_frames,
        hard_timeout_frames=self.config.eou_hard_timeout_frames,
        speech_threshold=self.config.vad_threshold,
    )

    # Load wake word model
    interpreter = tf.lite.Interpreter(
        model_path=str(self.config.wake_model_path)
    )
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    # Audio stream via sounddevice
    audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def audio_callback(indata, frames, time_info, status):
        if not status:
            loop.call_soon_threadsafe(audio_queue.put_nowait, indata[:, 0].copy())

    stream = sd.InputStream(
        samplerate=self.config.sample_rate,
        channels=self.config.channels,
        blocksize=self.config.vad_frame_samples,
        callback=audio_callback,
        dtype=np.float32,
    )

    log.info("Starting satellite pipeline (state=%s)", self.state.name)

    with stream:
        wake_accumulator = np.zeros(0, dtype=np.float32)
        sample_count = 0

        while not self._stop_event.is_set():
            try:
                frame = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            self.ring_buffer.write(frame)
            sample_count += len(frame)

            if self.state == PipelineState.IDLE:
                # Accumulate for wake word (needs CLIP_SAMPLES)
                wake_accumulator = np.concatenate([wake_accumulator, frame])
                if len(wake_accumulator) >= CLIP_SAMPLES:
                    chunk = wake_accumulator[:CLIP_SAMPLES]
                    wake_accumulator = wake_accumulator[CLIP_SAMPLES:]

                    mfcc = extract_mfcc(chunk)
                    input_data = mfcc[np.newaxis, ..., np.newaxis]
                    interpreter.set_tensor(input_details[0]['index'], input_data)
                    interpreter.invoke()
                    output = interpreter.get_tensor(output_details[0]['index'])
                    exp_logits = np.exp(output - np.max(output))
                    probs = exp_logits / exp_logits.sum(axis=-1, keepdims=True)
                    score = float(probs[0, 0])

                    if self._wake_processor.should_trigger(score, sample_count):
                        self._wake_processor.record_trigger(sample_count)
                        self._on_wake_word()

            elif self.state == PipelineState.LISTENING:
                # Run VAD on each frame
                prob = self._vad.process_frame(frame)
                result = self._eou.update(prob)
                self._utterance_chunks.append(frame)

                if result == EOUResult.END_OF_UTTERANCE:
                    self._on_end_of_utterance(reason="eou")
                elif result == EOUResult.HARD_TIMEOUT:
                    self._on_end_of_utterance(reason="timeout")

            elif self.state == PipelineState.PROCESSING:
                # Send audio to server (async)
                utterance = np.concatenate(self._utterance_chunks)
                log.info(
                    "Sending %.1fs utterance to server",
                    len(utterance) / self.config.sample_rate,
                )
                # Server send handled in Task 15 when wiring to WebSocket
                self._on_response_complete()

    log.info("Pipeline stopped")

async def run_for(self, duration_s: float) -> None:
    """Run pipeline for a fixed duration (for testing)."""
    self._stop_event = asyncio.Event()
    task = asyncio.create_task(self.run())
    await asyncio.sleep(duration_s)
    self._stop_event.set()
    await task
```

Also add `wake_model_path` to `SatelliteConfig`:

```python
# In satellite/config.py, add to SatelliteConfig:
    wake_model_path: str = "models/wakeword_mac.tflite"
```

**Step 3: Run test to verify it passes**

Run: `uv run pytest tests/satellite/test_pipeline_smoke.py -v`
Expected: PASS (needs a microphone device -- may need to skip in CI).

**Step 4: Commit**

```bash
git add satellite/pipeline.py satellite/config.py tests/satellite/test_pipeline_smoke.py
git commit -m "feat(satellite): full async pipeline loop with audio, wake word, VAD, EOU"
```

---

## Task 15: End-to-End Demo -- Mac Prototype

**Files:**
- No new files -- wire existing code together

**Step 1: Test the full stack manually**

Terminal 1 -- Start the server:
```bash
uv run kaare-server --whisper-model tiny --port 8765
```
Expected: `Voice server listening on 0.0.0.0:8765`

Terminal 2 -- Start the satellite:
```bash
uv run kaare-satellite --model models/wakeword_mac.tflite --device 0 --server ws://localhost:8765
```
Expected: `Satellite 'mac-prototype' ready. State: IDLE`

**Step 2: Speak "Kare" to trigger wake word**

Expected behavior:
1. Wake word detected -> state changes to LISTENING
2. Speak a command (e.g. "turn on the light")
3. 1.5s silence -> EOU detected -> state changes to PROCESSING
4. Audio sent to server via WebSocket
5. Server runs STT -> NLU -> TTS
6. Transcript and response displayed in server terminal

**Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "feat: end-to-end voice satellite Mac prototype"
```

---

## Summary

| Task | Component | Tests | Estimated Lines |
|------|-----------|-------|-----------------|
| 1 | Project setup | -- | ~80 |
| 2 | Ring buffer | 5 | ~80 |
| 3 | Silero VAD | 4 | ~50 |
| 4 | EOU detector | 6 | ~60 |
| 5 | State machine | 6 | ~100 |
| 6 | WS client | 3 | ~120 |
| 7 | WS server | 2 | ~100 |
| 8 | Integration test | 1 | ~50 |
| 9 | STT (whisper) | 2 | ~60 |
| 10 | NLU (Ollama+Claude) | 3 | ~120 |
| 11 | TTS (Piper) | 2 | ~80 |
| 12 | HA client | 4 | ~70 |
| 13 | Server pipeline | 2 | ~50 |
| 14 | Async loop | 1 | ~100 |
| 15 | E2E demo | manual | -- |
| **Total** | | **41 tests** | **~1120** |

**Dependencies** (to add via `uv add`):
- `onnxruntime` -- Silero VAD ONNX model
- `websockets` -- async WebSocket client/server
- `faster-whisper` -- STT
- `piper-tts` -- TTS (optional group)
- `anthropic` -- Claude API fallback
- `requests` -- HA API + Ollama
- `pytest-asyncio` -- async test support

Tasks 1-4 can be tested on Mac with terminal output only (no server needed).
Tasks 5-8 give end-to-end wake -> transcription.
Tasks 9-12 add intelligence and actions.
Tasks 13-15 wire everything into a working demo.
