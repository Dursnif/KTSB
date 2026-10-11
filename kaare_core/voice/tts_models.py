"""
Piper text-to-speech voices — presets per language and where they are stored (P126).

Voices come from the Hugging Face repo rhasspy/piper-voices and are kept in the same folder
layout under <models_dir>/voice/piper (GUI: Settings → LLM/Model → Model storage), which the API
(GUI download) and the voice service (start-up) both see — in Docker through the ./models volume.

A fresh install has no voice at all; the voice service downloads DEFAULT_VOICES[language] for the
household language on start when no configured voice exists (rule 24 — works from the defaults).
"""

from pathlib import Path

from huggingface_hub import hf_hub_download

from kaare_core.config import get_models_dir

PIPER_REPO = "rhasspy/piper-voices"

PIPER_PRESETS: dict[str, list[dict]] = {
    "nb": [
        {
            "model": "no_NO-talesyntese-medium",
            "tier": "medium",
            "tier_label": "Medium",
            "size_mb": 63,
            "gender": "female",
        },
    ],
    "en": [
        {
            "model": "en_US-lessac-low",
            "tier": "low",
            "tier_label": "Small",
            "size_mb": 63,
            "gender": "female",
        },
        {
            "model": "en_US-lessac-medium",
            "tier": "medium",
            "tier_label": "Medium",
            "size_mb": 63,
            "gender": "female",
        },
        {
            "model": "en_US-lessac-high",
            "tier": "high",
            "tier_label": "Large",
            "size_mb": 114,
            "gender": "female",
        },
        {
            "model": "en_US-ryan-high",
            "tier": "high",
            "tier_label": "Large",
            "size_mb": 121,
            "gender": "male",
        },
        {
            "model": "en_US-libritts-high",
            "tier": "high",
            "tier_label": "Large+",
            "size_mb": 137,
            "gender": "mixed",
        },
    ],
    "de": [
        {
            "model": "de_DE-eva_k-x_low",
            "tier": "x_low",
            "tier_label": "Tiny",
            "size_mb": 21,
            "gender": "female",
        },
        {
            "model": "de_DE-kerstin-low",
            "tier": "low",
            "tier_label": "Small",
            "size_mb": 63,
            "gender": "female",
        },
        {
            "model": "de_DE-thorsten-low",
            "tier": "low",
            "tier_label": "Small",
            "size_mb": 63,
            "gender": "male",
        },
        {
            "model": "de_DE-thorsten-medium",
            "tier": "medium",
            "tier_label": "Medium",
            "size_mb": 63,
            "gender": "male",
        },
        {
            "model": "de_DE-thorsten-high",
            "tier": "high",
            "tier_label": "Large",
            "size_mb": 114,
            "gender": "male",
        },
    ],
}

# Voice used when the household language has none configured (downloaded on voice start).
DEFAULT_VOICES = {
    "nb": "no_NO-talesyntese-medium",
    "en": "en_US-lessac-medium",
    "de": "de_DE-thorsten-medium",
}

_TIERS = {"x_low", "low", "medium", "high"}


def piper_dir() -> Path:
    return get_models_dir() / "voice" / "piper"


def piper_local_path(model_name: str) -> Path:
    """Local path of a voice: the repo's own layout {lang}/{lang_CODE}/{speaker}/{tier}/{model}.onnx."""
    lang_code, _, remainder = model_name.partition("-")  # no_NO, talesyntese-medium
    lang_prefix = lang_code.split("_")[0].lower()  # no
    speaker, _, tier = remainder.rpartition("-")
    if tier not in _TIERS:
        speaker, tier = remainder, "medium"
    return piper_dir() / lang_prefix / lang_code / speaker / tier / f"{model_name}.onnx"


def piper_hf_paths(model_name: str) -> tuple[str, str]:
    """The two files of a voice in the repo (onnx + json), relative to its root."""
    rel = piper_local_path(model_name).relative_to(piper_dir())
    return str(rel), str(rel) + ".json"


def download_voice(model_name: str) -> Path:
    """Fetch a voice (blocking). Returns the local .onnx path."""
    target = piper_dir()
    target.mkdir(parents=True, exist_ok=True)
    for rel in piper_hf_paths(model_name):
        hf_hub_download(repo_id=PIPER_REPO, filename=rel, local_dir=str(target))
    return piper_local_path(model_name)


def tts_lang(speech_lang: str) -> str:
    """Map the household speech language to a preset key (Norwegian variants → nb)."""
    return "nb" if speech_lang in {"nb", "nn", "no"} else speech_lang
