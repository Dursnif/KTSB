"""Shared audio configuration for the entire pipeline.

Every script imports from here to guarantee identical preprocessing
between training and inference. Change values here, not elsewhere.
"""

from dataclasses import dataclass
from pathlib import Path

# --- Project paths -----------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
POSITIVE_DIR = DATA_DIR / "positive"
NEGATIVE_DIR = DATA_DIR / "negative"
BACKGROUND_DIR = DATA_DIR / "background"
AUGMENTED_DIR = DATA_DIR / "augmented"
MODELS_DIR = PROJECT_ROOT / "models"
PREPROCESSED_DIR = DATA_DIR / "preprocessed"

# --- Audio constants ---------------------------------------------------------

SAMPLE_RATE = 16_000          # Hz - standard for keyword spotting
CLIP_DURATION_S = 1.5         # seconds per sample
CLIP_SAMPLES = int(SAMPLE_RATE * CLIP_DURATION_S)  # 24000 samples

# --- Mel / MFCC --------------------------------------------------------------

N_MFCC = 40                   # number of MFCC coefficients
N_MELS = 80                   # mel filter banks
N_FFT = 512                   # FFT window size
HOP_LENGTH = 160              # 10 ms hop at 16kHz
WIN_LENGTH = 400              # 25 ms window at 16kHz

# Computed: number of time frames per clip
# floor((CLIP_SAMPLES - WIN_LENGTH) / HOP_LENGTH) + 1
N_FRAMES = (CLIP_SAMPLES - WIN_LENGTH) // HOP_LENGTH + 1  # 148

# --- Model input shape -------------------------------------------------------

# DS-CNN expects (N_FRAMES, N_MFCC, 1) per sample
INPUT_SHAPE = (N_FRAMES, N_MFCC, 1)

# --- Labels ------------------------------------------------------------------

LABELS = ["kåre", "unknown", "background"]
NUM_CLASSES = len(LABELS)
LABEL_TO_IDX = {label: idx for idx, label in enumerate(LABELS)}
IDX_TO_LABEL = {idx: label for idx, label in enumerate(LABELS)}

# --- Inference ---------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.85   # minimum confidence for positive detection
DEBOUNCE_S = 2.0              # seconds between triggers
INFERENCE_HOP_S = 0.1         # run inference every 100ms (sliding window)


@dataclass(frozen=True)
class AudioConfig:
    """Immutable snapshot of audio configuration for serialization."""

    sample_rate: int = SAMPLE_RATE
    clip_duration_s: float = CLIP_DURATION_S
    n_mfcc: int = N_MFCC
    n_mels: int = N_MELS
    n_fft: int = N_FFT
    hop_length: int = HOP_LENGTH
    win_length: int = WIN_LENGTH
    n_frames: int = N_FRAMES
    num_classes: int = NUM_CLASSES
