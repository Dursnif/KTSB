"""
Speech-to-text models for faster-whisper (CTranslate2, CPU) — presets per language and where the
GUI downloads them (P126).

One STT model serves the household language (kare_language, P101). Norwegian gets the National
Library's NB-Whisper (far better on Bokmål at every size); every other language uses OpenAI's
multilingual Whisper. Sizes follow the same small / medium / large idea as semantic memory.

Models are stored in the Hugging Face cache layout under <models_dir>/voice/faster-whisper; the
voice service passes that folder as download_root, so a model fetched from the GUI is found there
and nothing is downloaded again on the next start.

A user may also type any Hugging Face repo (or its URL); it must be a CTranslate2 conversion
(model.bin + config.json + vocabulary/tokenizer at the repo root), which is checked before download.
"""

import os
import re
from pathlib import Path

from kaare_core.config import get_models_dir

# faster-whisper's own download filter (faster_whisper/utils.py download_model).
FW_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

# Short names faster-whisper resolves itself (older configs store these).
FW_SHORT_NAMES = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

_NORWEGIAN = {"nb", "nn", "no"}

PRESETS: dict[str, list[dict]] = {
    "nb": [
        {"repo": "NbAiLab/nb-whisper-small", "tier": "small", "size_mb": 967},
        {"repo": "NbAiLab/nb-whisper-medium", "tier": "medium", "size_mb": 3056},
        {"repo": "NbAiLab/nb-whisper-large", "tier": "large", "size_mb": 6175},
    ],
    # CPU benchmark 2026-10-07 (P126): turbo is as accurate as large-v3 with half the RAM, but not
    # faster on a CPU (the encoder dominates there) — so it is «large», and medium stays medium.
    "default": [
        {"repo": "Systran/faster-whisper-small", "tier": "small", "size_mb": 484},
        {"repo": "Systran/faster-whisper-medium", "tier": "medium", "size_mb": 1528},
        {"repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "tier": "large", "size_mb": 1618},
    ],
}

_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def presets_for(lang: str) -> list[dict]:
    return PRESETS["nb" if lang in _NORWEGIAN else "default"]


def resolve_model(configured: str, lang: str) -> str:
    """The faster-whisper model to run: the configured one, or — when none is set (the shipped
    default) — the smallest preset for the household language (rule 24: lightest tier).
    A local folder (absolute path to a CTranslate2 model) is used as it is."""
    configured = (configured or "").strip()
    if configured.startswith("/"):
        return configured
    return normalize_repo(configured) or presets_for(lang)[0]["repo"]


def cpu_threads(configured: int | None) -> int:
    """Threads for faster-whisper. 0/None = auto: half the CPU cores, at least 2, at most 8.

    CPU benchmark 2026-10-07 (P126, 20-core Core Ultra 7 265 = 8 P + 12 E cores): 2→4 threads halves
    the time, 4→8 saves another 11–23 %, 12–16 is slower again (E-cores, contention). Half the cores
    also leaves room for the rest of KTSB. The hardware check (I33) may set an explicit value."""
    if configured and int(configured) > 0:
        return int(configured)
    return max(2, min(8, (os.cpu_count() or 4) // 2))


def cache_dir() -> Path:
    """download_root for faster-whisper: <models_dir>/voice/faster-whisper."""
    return get_models_dir() / "voice" / "faster-whisper"


def normalize_repo(text: str) -> str | None:
    """A repo id from what the user typed: `org/name`, a huggingface.co URL, or a faster-whisper
    short name. None if it is none of these (no other hosts — nothing to check the files against)."""
    text = (text or "").strip()
    if text in FW_SHORT_NAMES:
        return FW_SHORT_NAMES[text]
    m = re.match(r"^https?://(?:www\.)?huggingface\.co/([^/?#]+/[^/?#]+)", text)
    if m:
        text = m.group(1)
    return text if _REPO_RE.match(text) else None


def is_ct2_layout(filenames: set[str]) -> bool:
    """True if a repo's root holds a CTranslate2 Whisper model faster-whisper can load."""
    return (
        "model.bin" in filenames
        and "config.json" in filenames
        and any(f.startswith("vocabulary.") or f == "tokenizer.json" for f in filenames)
    )
