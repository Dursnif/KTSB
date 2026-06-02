"""Augment wake word audio samples for robust training.

Creates variations:
  - Pitch shift (±2 semitones)
  - Time stretch (0.9x-1.1x speed)
  - Noise injection (background audio)
  - Gain variation (±3 dB)

Usage:
    python -m scripts.augment --source data/positive/ --output data/augmented/ --factor 3
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import soundfile as sf
import librosa

from scripts.audio_config import SAMPLE_RATE


def load_background(noise_dir: Path, max_samples: int = 10) -> list[np.ndarray]:
    """Load background noise samples from directory."""
    backgrounds = []
    if not noise_dir.exists():
        return backgrounds
    
    for wav_path in list(noise_dir.glob("*.wav"))[:max_samples]:
        audio, sr = sf.read(str(wav_path))
        if sr != SAMPLE_RATE:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
        if audio.ndim > 1:
            audio = audio[:, 0]
        backgrounds.append(audio)
    return backgrounds


def augment_audio(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    backgrounds: list[np.ndarray] | None = None,
    num_variants: int = 3,
) -> list[np.ndarray]:
    """Generate augmented variants of a single audio clip.

    Returns:
        List of augmented audio arrays (same length as input).
    """
    augmented = []
    
    for _ in range(num_variants):
        aug = audio.copy()
        
        # 1. Pitch shift (50% chance)
        if random.random() > 0.5:
            steps = random.uniform(-2, 2)
            try:
                aug = librosa.effects.pitch_shift(aug, sr=sample_rate, n_steps=steps)
            except Exception:
                pass  # librosa can be picky
        
        # 2. Time stretch (50% chance)
        if random.random() > 0.5:
            rate = random.uniform(0.9, 1.1)
            try:
                aug = librosa.effects.time_stretch(aug, rate=rate)
                # Trim/pad back to original length
                if len(aug) > len(audio):
                    aug = aug[:len(audio)]
                else:
                    aug = np.pad(aug, (0, len(audio) - len(aug)), mode="constant")
            except Exception:
                pass
        
        # 3. Gain variation (always)
        gain_db = random.uniform(-3, 3)
        gain_linear = 10 ** (gain_db / 20)
        aug = aug * gain_linear
        
        # 4. Noise injection (if backgrounds available, 50% chance)
        if backgrounds and random.random() > 0.5:
            bg = random.choice(backgrounds)
            bg_len = len(bg)
            if bg_len > len(aug):
                offset = random.randint(0, bg_len - len(aug))
                bg_clip = bg[offset:offset + len(aug)]
            else:
                bg_clip = bg
                bg_clip = np.pad(bg_clip, (0, len(aug) - len(bg_clip)), mode="constant")
            
            snr_db = random.uniform(10, 25)
            snr_linear = 10 ** (snr_db / 20)
            signal_power = np.mean(aug ** 2)
            noise_power = np.mean(bg_clip ** 2)
            scale = np.sqrt(signal_power / (noise_power * snr_linear))
            aug = aug + bg_clip * scale
        
        # Normalize to prevent clipping
        max_val = np.max(np.abs(aug))
        if max_val > 1.0:
            aug = aug / max_val
        
        augmented.append(aug)
    
    return augmented


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment wake word audio samples")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/positive/"),
        help="Source directory with audio files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/augmented/"),
        help="Output directory for augmented audio",
    )
    parser.add_argument(
        "--factor",
        type=int,
        default=3,
        help="Augmentation factor per file (default: 3)",
    )
    parser.add_argument(
        "--background",
        type=Path,
        default=Path("data/background/"),
        help="Directory with background noise samples",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    
    backgrounds = load_background(args.background)
    print(f"Loaded {len(backgrounds)} background noise samples")

    wav_files = list(args.source.glob("*.wav"))
    print(f"Found {len(wav_files)} source files in {args.source}/")

    total_generated = 0
    
    for wav_path in wav_files:
        try:
            audio, sr = sf.read(str(wav_path))
            if sr != SAMPLE_RATE:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
            if audio.ndim > 1:
                audio = audio[:, 0]
        except Exception as exc:
            print(f"  Skipping {wav_path.name}: {exc}")
            continue
        
        variants = augment_audio(audio, SAMPLE_RATE, backgrounds, args.factor)
        
        for i, variant in enumerate(variants):
            out_path = args.output / f"{wav_path.stem}_aug{i:02d}.wav"
            sf.write(str(out_path), variant, SAMPLE_RATE, subtype="PCM_16")
            total_generated += 1

        if total_generated % 100 == 0:
            print(f"  Generated {total_generated} samples...")

    print(f"\nDone. {total_generated} augmented samples in {args.output}/")


if __name__ == "__main__":
    main()
