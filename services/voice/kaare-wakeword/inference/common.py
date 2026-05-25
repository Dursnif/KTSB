"""Common audio processing for wake word inference.

Shared between Coral and VIM3 inference:
  - Audio capture
  - MFCC extraction
  - Confidence thresholding

Usage:
    from inference.common import WakeWordProcessor
    
    processor = WakeWordProcessor(confidence=0.85)
    detected = processor.process_audio_chunk(audio_chunk)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
import librosa
import sounddevice as sd

from scripts.audio_config import CLIP_SAMPLES, SAMPLE_RATE


# MFCC parameters (must match training)
N_MFCC = 40
N_FFT = 1024
HOP_LENGTH = 160
WIN_LENGTH = 400


def extract_mfcc(audio: np.ndarray) -> np.ndarray:
    """Extract MFCC features from audio chunk.

    Args:
        audio: Audio array at SAMPLE_RATE

    Returns:
        MFCC array of shape [n_mfcc, n_frames]
    """
    # Ensure mono
    if audio.ndim > 1:
        audio = audio[:, 0]
    
    # Pad or trim
    if len(audio) < CLIP_SAMPLES:
        audio = np.pad(audio, (0, CLIP_SAMPLES - len(audio)), mode="constant")
    elif len(audio) > CLIP_SAMPLES:
        audio = audio[:CLIP_SAMPLES]
    
    # Extract MFCC
    mfcc = librosa.feature.mfcc(
        y=audio,
        sr=SAMPLE_RATE,
        n_mfcc=N_MFCC,
        n_fft=N_FFT,
        hop_length=HOP_LENGTH,
        win_length=WIN_LENGTH,
    )
    
    # Normalize per-frame (axis=0 = across 40 MFCC coefficients),
    # matching dataset.py which does axis=1 on [N, 40, 151]
    mfcc = (mfcc - mfcc.mean(axis=0, keepdims=True)) / (
        mfcc.std(axis=0, keepdims=True) + 1e-8
    )
    
    return mfcc.astype(np.float32)


class WakeWordProcessor:
    """Wake word detector with debouncing.

    Handles:
      - Audio streaming
      - MFCC extraction
      - Confidence thresholding
      - Debounce (ignore repeated triggers)
    """

    def __init__(
        self,
        confidence: float = 0.85,
        debounce_seconds: float = 2.0,
    ):
        """Initialize wake word processor.

        Args:
            confidence: Minimum confidence score (0-1)
            debounce_seconds: Minimum time between triggers
        """
        self.confidence = confidence
        self.debounce_samples = int(debounce_seconds * SAMPLE_RATE)
        self.last_trigger_sample = -self.debounce_samples

    def should_trigger(self, confidence_score: float, current_sample: int) -> bool:
        """Check if wake word should trigger with debouncing.

        Args:
            confidence_score: Model confidence (0-1)
            current_sample: Current audio sample index

        Returns:
            True if should trigger
        """
        if confidence_score < self.confidence:
            return False
        
        samples_since_trigger = current_sample - self.last_trigger_sample
        return samples_since_trigger >= self.debounce_samples

    def record_trigger(self, current_sample: int) -> None:
        """Record that wake word triggered at this time."""
        self.last_trigger_sample = current_sample


def list_audio_devices() -> dict[int, str]:
    """List available audio input devices.

    Returns:
        Dict mapping device index to device name
    """
    devices = {}
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_input_channels'] > 0:
            devices[i] = dev['name']
    return devices
