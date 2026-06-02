"""Satellite configuration constants.

All tunable parameters live here. Import from this module,
not from scattered magic numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SatelliteConfig:
    """Immutable satellite configuration."""

    # Audio input
    sample_rate: int = 16_000
    channels: int = 1
    dtype: str = "float32"
    audio_device: int = 0

    # Audio output
    output_device: int | None = None
    volume_boost: float = 3.0

    # Ring buffer
    ring_buffer_seconds: float = 3.0

    # Wake word
    wake_model_path: str = "models/wakeword.tflite"
    wake_confidence: float = 0.85
    wake_debounce_s: float = 2.0

    # VAD
    vad_threshold: float = 0.5
    vad_frame_ms: int = 32  # Silero=32ms (512 samples), WebRTC=30ms (480)
    vad_backend: str = "auto"  # "silero", "webrtc", or "auto"
    vad_aggressiveness: int = 2  # WebRTC aggressiveness (0-3)

    # End-of-utterance
    eou_silence_s: float = 1.5
    eou_hard_timeout_s: float = 15.0
    pre_roll_s: float = 0.5

    # Server connection
    server_url: str = "ws://localhost:8765"
    satellite_id: str = "satellite"

    # Satellite identity
    room: str = "unknown"
    http_port: int = 8080

    # Hardware
    no_leds: bool = False

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
