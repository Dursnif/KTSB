"""Self-contained wake word MFCC extraction and trigger logic.

Inlined from inference/common.py and scripts/audio_config.py so the
satellite package has zero cross-package imports (needed for RPi
deployment where only satellite/ is installed).
"""
from __future__ import annotations

import numpy as np
import sounddevice as sd

# Audio constants (must match training pipeline)
SAMPLE_RATE = 16_000
CLIP_DURATION_S = 1.5
CLIP_SAMPLES = int(SAMPLE_RATE * CLIP_DURATION_S)  # 24_000

# MFCC parameters (must match training — uses N_FFT=1024 from inference)
N_MFCC = 40
N_FFT = 1024
HOP_LENGTH = 160
WIN_LENGTH = 400


def extract_mfcc(audio: np.ndarray) -> np.ndarray:
    """Extract MFCC features from audio chunk.

    Args:
        audio: float32 mono audio at SAMPLE_RATE.

    Returns:
        MFCC array of shape [n_mfcc, n_frames], normalized per-frame.
    """
    import librosa

    if audio.ndim > 1:
        audio = audio[:, 0]

    if len(audio) < CLIP_SAMPLES:
        audio = np.pad(audio, (0, CLIP_SAMPLES - len(audio)), mode="constant")
    elif len(audio) > CLIP_SAMPLES:
        audio = audio[:CLIP_SAMPLES]

    mfcc = librosa.feature.mfcc(
        y=audio,
        sr=SAMPLE_RATE,
        n_mfcc=N_MFCC,
        n_fft=N_FFT,
        hop_length=HOP_LENGTH,
        win_length=WIN_LENGTH,
    )

    # Per-frame normalization (axis=0 = across MFCC coefficients)
    mfcc = (mfcc - mfcc.mean(axis=0, keepdims=True)) / (
        mfcc.std(axis=0, keepdims=True) + 1e-8
    )
    return mfcc.astype(np.float32)


class WakeWordProcessor:
    """Wake word trigger with confidence thresholding and debounce."""

    def __init__(
        self,
        confidence: float = 0.85,
        debounce_seconds: float = 2.0,
    ):
        self.confidence = confidence
        self.debounce_samples = int(debounce_seconds * SAMPLE_RATE)
        self.last_trigger_sample = -self.debounce_samples

    def should_trigger(self, confidence_score: float, current_sample: int) -> bool:
        if confidence_score < self.confidence:
            return False
        return (current_sample - self.last_trigger_sample) >= self.debounce_samples

    def record_trigger(self, current_sample: int) -> None:
        self.last_trigger_sample = current_sample


def list_audio_devices() -> dict[int, str]:
    """List available audio input devices."""
    devices = {}
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices[i] = dev["name"]
    return devices
