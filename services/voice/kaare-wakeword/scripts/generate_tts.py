"""Generate synthetic wake word samples using local TTS.

Supports two engines (no network required):
  - piper-tts: Higher quality Norwegian voices (requires piper-tts package)
  - espeak-ng:  Robotic but consistent pronunciation (requires espeak-ng binary)

Usage:
    python -m scripts.generate_tts                        # default: piper
    python -m scripts.generate_tts --engine espeak        # use espeak-ng
    python -m scripts.generate_tts --count 500            # generate 500 samples
    python -m scripts.generate_tts --phrases "hei kåre" "kåre" "ok kåre"
"""

from __future__ import annotations

import argparse
import io
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from scripts.audio_config import CLIP_SAMPLES, POSITIVE_DIR, SAMPLE_RATE

# Default phrases to synthesize. Multiple variants improve robustness.
DEFAULT_PHRASES = [
    "kåre",
    "hei kåre",
    "ok kåre",
]

# Pitch and speed variations for espeak-ng
ESPEAK_SPEEDS = [130, 150, 170, 190]  # words per minute
ESPEAK_PITCHES = [40, 50, 60, 70]     # pitch (0-99)


def _check_espeak() -> bool:
    """Return True if espeak-ng is available."""
    try:
        subprocess.run(
            ["espeak-ng", "--version"],
            capture_output=True,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _check_piper() -> bool:
    """Return True if piper-tts is importable."""
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def generate_espeak(
    phrase: str,
    speed: int = 150,
    pitch: int = 50,
) -> np.ndarray:
    """Generate audio for a phrase using espeak-ng.

    Returns float32 array at SAMPLE_RATE.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        subprocess.run(
            [
                "espeak-ng",
                "-v", "nb",           # Norwegian Bokmål
                "-s", str(speed),     # words per minute
                "-p", str(pitch),     # pitch
                "-w", tmp.name,       # output WAV
                phrase,
            ],
            capture_output=True,
            check=True,
        )
        audio, sr = sf.read(tmp.name, dtype="float32")

    # Resample if needed
    if sr != SAMPLE_RATE:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)

    return audio


def generate_piper(
    phrase: str,
    model: str = "no_NO-talesyntese-medium",
) -> np.ndarray:
    """Generate audio for a phrase using piper-tts.

    The model is downloaded on first use and cached locally.
    Returns float32 array at SAMPLE_RATE.
    """
    # piper outputs raw PCM via stdout when using --output-raw
    result = subprocess.run(
        [
            "piper",
            "--model", model,
            "--output-raw",
        ],
        input=phrase.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    # piper raw output is 16-bit signed PCM at the model's sample rate (usually 22050)
    raw = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    piper_sr = 22050  # piper default

    if piper_sr != SAMPLE_RATE:
        import librosa
        raw = librosa.resample(raw, orig_sr=piper_sr, target_sr=SAMPLE_RATE)

    return raw


def pad_or_trim(audio: np.ndarray, target_length: int = CLIP_SAMPLES) -> np.ndarray:
    """Pad with silence or trim to exact target length."""
    if len(audio) >= target_length:
        # Random offset so the word isn't always at the start
        max_offset = len(audio) - target_length
        offset = random.randint(0, max_offset)
        return audio[offset : offset + target_length]

    # Pad: center the audio with silence on both sides
    pad_total = target_length - len(audio)
    pad_left = random.randint(0, pad_total)
    pad_right = pad_total - pad_left
    return np.pad(audio, (pad_left, pad_right), mode="constant")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic wake word samples")
    parser.add_argument(
        "--engine",
        choices=["piper", "espeak"],
        default="espeak",
        help="TTS engine to use (default: espeak)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=200,
        help="Total samples to generate (default: 200)",
    )
    parser.add_argument(
        "--phrases",
        nargs="+",
        default=DEFAULT_PHRASES,
        help="Phrases to synthesize",
    )
    parser.add_argument(
        "--piper-model",
        default="no_NO-talesyntese-medium",
        help="Piper model name for Norwegian (default: no_NO-talesyntese-medium)",
    )
    args = parser.parse_args()

    # Check engine availability
    if args.engine == "espeak" and not _check_espeak():
        print("espeak-ng not found. Install: brew install espeak-ng (macOS) or apt install espeak-ng")
        sys.exit(1)
    if args.engine == "piper" and not _check_piper():
        print("piper-tts not found. Install: pip install piper-tts")
        sys.exit(1)

    out_dir = POSITIVE_DIR / "synthetic"
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = len(list(out_dir.glob("*.wav")))
    print(f"Engine: {args.engine}")
    print(f"Phrases: {args.phrases}")
    print(f"Target: {args.count} samples -> {out_dir}/")
    print(f"Existing: {existing} samples")

    generated = 0
    idx = existing

    while generated < args.count:
        phrase = random.choice(args.phrases)

        try:
            if args.engine == "espeak":
                speed = random.choice(ESPEAK_SPEEDS)
                pitch = random.choice(ESPEAK_PITCHES)
                audio = generate_espeak(phrase, speed=speed, pitch=pitch)
            else:
                audio = generate_piper(phrase, model=args.piper_model)
        except subprocess.CalledProcessError as exc:
            print(f"  TTS failed for '{phrase}': {exc.stderr[:200] if exc.stderr else exc}")
            continue

        audio = pad_or_trim(audio)

        filepath = out_dir / f"synth_{idx:04d}.wav"
        sf.write(str(filepath), audio, SAMPLE_RATE, subtype="PCM_16")
        idx += 1
        generated += 1

        if generated % 50 == 0 or generated == args.count:
            print(f"  {generated}/{args.count} generated")

    print(f"\nDone. {generated} synthetic samples in {out_dir}/")


if __name__ == "__main__":
    main()
