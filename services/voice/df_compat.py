"""DeepFilterNet ↔ current torchaudio compatibility (P145, 2026-10-27).

DeepFilterNet 0.5.6 (its last release) does `from torchaudio.backend.common import AudioMetaData` at import time.
torchaudio removed `torchaudio.backend` in 2.3, so a clean install of DeepFilterNet cannot be imported any more.
The house used to work only because that line had been hand-edited inside site-packages — invisible to every
requirements/lock file and lost on any reinstall. Calling `ensure()` before importing `df` registers a stand-in
module instead, so DeepFilterNet imports on any torchaudio version. `AudioMetaData` is only a type hint and the
return value of `df.io.load_audio`, which KTSB never calls (it passes tensors to `df.enhance.enhance`).
"""

import sys
import types
from dataclasses import dataclass


@dataclass
class AudioMetaData:
    """Same fields as torchaudio's removed AudioMetaData."""

    sample_rate: int = 0
    num_frames: int = 0
    num_channels: int = 0
    bits_per_sample: int = 0
    encoding: str = ""


def ensure() -> None:
    """Make `torchaudio.backend.common.AudioMetaData` importable. No-op when torchaudio still has it."""
    if sys.modules.get("torchaudio.backend.common") is not None:
        return
    try:
        # PLC0415: optional dependency — only present where DeepFilterNet is installed (rule 24)
        import torchaudio.backend.common  # noqa: F401, PLC0415

        return
    except ImportError:
        pass
    backend = sys.modules.get("torchaudio.backend") or types.ModuleType("torchaudio.backend")
    common = types.ModuleType("torchaudio.backend.common")
    common.AudioMetaData = AudioMetaData
    backend.common = common
    sys.modules["torchaudio.backend"] = backend
    sys.modules["torchaudio.backend.common"] = common
