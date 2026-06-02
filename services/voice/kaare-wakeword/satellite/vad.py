"""Voice Activity Detection backends.

Two backends with the same interface:

- SileroVAD: ONNX LSTM model, 512-sample frames (32ms), continuous [0,1]
  Requires onnxruntime. Best accuracy.

- WebRTCVAD: Classical signal processing, 480-sample frames (30ms), binary
  Requires webrtcvad-wheels. No ML dependencies — ideal for RPi.

Use create_vad() to auto-select the best available backend.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class VADBackend(Protocol):
    """Common interface for VAD backends."""

    FRAME_SAMPLES: int

    def reset(self) -> None: ...
    def process_frame(self, frame: np.ndarray) -> float: ...


# --------------- Silero VAD (ONNX) ---------------

_HUB_CACHE_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "torch", "hub",
    "snakers4_silero-vad_master",
)
_ONNX_MODEL_RELATIVE = os.path.join(
    "src", "silero_vad", "data", "silero_vad.onnx"
)


def _ensure_model_downloaded() -> str:
    """Return path to the Silero VAD ONNX model, downloading if needed."""
    model_path = os.path.join(_HUB_CACHE_DIR, _ONNX_MODEL_RELATIVE)
    if os.path.exists(model_path):
        return model_path

    try:
        import torch
        torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            trust_repo=True,
            force_reload=False,
        )
    except Exception:
        pass

    if not os.path.exists(model_path):
        raise RuntimeError(
            f"Silero VAD ONNX model not found at {model_path}. "
            "Try: python -c \"import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)\""
        )
    return model_path


class SileroVAD:
    """Silero Voice Activity Detection via ONNX runtime.

    Processes 32ms frames (512 samples at 16kHz) and returns speech
    probability [0.0, 1.0]. Stateful — call reset() between utterances.

    Args:
        threshold: Speech probability threshold (informational).
        sample_rate: Audio sample rate. Must be 16000.
        model_path: Explicit path to silero_vad.onnx. If None, auto-downloads.
    """

    FRAME_SAMPLES = 512  # 32ms at 16kHz

    _CONTEXT_SIZE = 64

    def __init__(
        self,
        threshold: float = 0.5,
        sample_rate: int = 16_000,
        model_path: str | None = None,
    ):
        if sample_rate != 16_000:
            raise ValueError("Silero VAD requires 16kHz audio")
        self.threshold = threshold
        self.sample_rate = sample_rate

        import onnxruntime

        resolved = model_path or _ensure_model_downloaded()
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            resolved,
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        self.reset()

    def reset(self) -> None:
        """Reset internal LSTM state between utterances."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._CONTEXT_SIZE), dtype=np.float32)

    def process_frame(self, frame: np.ndarray) -> float:
        """Process a single 32ms audio frame (512 samples).

        Returns speech probability [0.0, 1.0].
        """
        if len(frame) != self.FRAME_SAMPLES:
            raise ValueError(
                f"VAD frame must be exactly {self.FRAME_SAMPLES} samples "
                f"(32ms at 16kHz), got {len(frame)}"
            )

        x = frame.reshape(1, self.FRAME_SAMPLES).astype(np.float32)
        x_with_context = np.concatenate([self._context, x], axis=1)

        sr = np.array(self.sample_rate, dtype=np.int64)
        ort_inputs = {
            "input": x_with_context,
            "state": self._state,
            "sr": sr,
        }
        output, new_state = self._session.run(None, ort_inputs)

        self._context = x_with_context[:, -self._CONTEXT_SIZE:]
        self._state = new_state

        return float(output[0, 0])


# --------------- WebRTC VAD ---------------

class WebRTCVAD:
    """WebRTC VAD wrapper — same interface as SileroVAD.

    Uses classical signal processing (no ML). Returns binary 1.0 or 0.0.
    Frame size is 480 samples (30ms at 16kHz).

    Args:
        threshold: Unused (kept for interface compatibility).
        sample_rate: Audio sample rate. Must be 16000.
        aggressiveness: WebRTC aggressiveness 0-3 (higher = more aggressive
            filtering of non-speech). Default 2.
    """

    FRAME_SAMPLES = 480  # 30ms at 16kHz

    def __init__(
        self,
        threshold: float = 0.5,
        sample_rate: int = 16_000,
        aggressiveness: int = 2,
    ):
        if sample_rate != 16_000:
            raise ValueError("WebRTC VAD requires 16kHz audio")
        self.threshold = threshold
        self.sample_rate = sample_rate

        import webrtcvad
        self._vad = webrtcvad.Vad(aggressiveness)

    def reset(self) -> None:
        """No-op — WebRTC VAD is stateless."""
        pass

    def process_frame(self, frame: np.ndarray) -> float:
        """Process a single 30ms audio frame (480 samples).

        Returns 1.0 (speech) or 0.0 (silence).
        """
        if len(frame) != self.FRAME_SAMPLES:
            raise ValueError(
                f"WebRTC VAD frame must be exactly {self.FRAME_SAMPLES} samples "
                f"(30ms at 16kHz), got {len(frame)}"
            )

        # WebRTC VAD expects int16 PCM bytes
        pcm = (frame * 32768).astype(np.int16).tobytes()
        is_speech = self._vad.is_speech(pcm, self.sample_rate)
        return 1.0 if is_speech else 0.0


# --------------- Factory ---------------

def create_vad(
    backend: str = "auto",
    threshold: float = 0.5,
    aggressiveness: int = 2,
    model_path: str | None = None,
) -> SileroVAD | WebRTCVAD:
    """Create a VAD backend.

    Args:
        backend: "silero", "webrtc", or "auto" (try Silero first).
        threshold: Speech probability threshold.
        aggressiveness: WebRTC aggressiveness (0-3), ignored for Silero.
        model_path: Explicit Silero ONNX model path (optional).

    Returns:
        VAD instance with FRAME_SAMPLES, reset(), and process_frame().
    """
    if backend == "silero":
        return SileroVAD(threshold=threshold, model_path=model_path)

    if backend == "webrtc":
        return WebRTCVAD(threshold=threshold, aggressiveness=aggressiveness)

    # auto: try Silero, fall back to WebRTC
    try:
        vad = SileroVAD(threshold=threshold, model_path=model_path)
        log.info("VAD backend: Silero (ONNX)")
        return vad
    except Exception as exc:
        log.info("Silero VAD unavailable (%s), falling back to WebRTC", exc)

    try:
        vad = WebRTCVAD(threshold=threshold, aggressiveness=aggressiveness)
        log.info("VAD backend: WebRTC (aggressiveness=%d)", aggressiveness)
        return vad
    except ImportError:
        raise RuntimeError(
            "No VAD backend available. Install onnxruntime (Silero) "
            "or webrtcvad-wheels (WebRTC)."
        )
