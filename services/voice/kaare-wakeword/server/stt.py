"""faster-whisper STT wrapper.

Wraps the faster-whisper library for speech-to-text transcription.

Supports NB-AiLab Norwegian models out of the box:
  --whisper-model nb-whisper       -> Necklace/faster-nb-whisper-large (ct2)
  --whisper-model nb-whisper-turbo -> NbAiLab/nb-whisper-large-distil-turbo-beta
  --whisper-model large            -> standard Whisper large-v3

The faster-whisper import is deferred to __init__ so the module can be
imported even when faster-whisper is not installed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

# Shorthand aliases for Norwegian models
_MODEL_ALIASES = {
    "nb-whisper": "Necklace/faster-nb-whisper-large",
    "nb-whisper-turbo": "NbAiLab/nb-whisper-large-distil-turbo-beta",
}


@dataclass
class STTResult:
    """Speech-to-text result."""
    text: str
    language: str
    confidence: float


class WhisperSTT:
    """faster-whisper speech-to-text.

    Args:
        model_size: Whisper model name, HuggingFace ID, or alias
                    (nb-whisper, nb-whisper-turbo, tiny, base, large, etc.).
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

        resolved = _MODEL_ALIASES.get(model_size, model_size)
        if resolved != model_size:
            log.info("Resolved model alias '%s' -> '%s'", model_size, resolved)

        self.language = language
        self._model = WhisperModel(
            resolved,
            device=device,
            compute_type=compute_type,
        )
        log.info("Whisper model loaded: %s (device=%s)", resolved, device)

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
            total_prob += seg.avg_logprob
            n_segments += 1

        text = " ".join(text_parts).strip()
        confidence = total_prob / max(n_segments, 1)
        language = info.language or "unknown"

        log.info("STT: [%s] (%.2f) %s", language, confidence, text[:80])
        return STTResult(text=text, language=language, confidence=confidence)
