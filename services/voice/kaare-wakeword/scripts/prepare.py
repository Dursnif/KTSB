"""Full preprocessing pipeline: augment + MFCC + combine.

Runs the complete pipeline from raw WAV files to training-ready .npy files:
  1. Augment each class (positive, negative, background)
  2. Extract MFCC from raw + augmented WAV
  3. Combine into *_all.npy files ready for training

Usage:
    python -m scripts.prepare
    python -m scripts.prepare --factor 5          # More augmentation
    python -m scripts.prepare --skip-augment      # Only MFCC + combine
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

from scripts.audio_config import DATA_DIR


CLASSES = [
    ("positive", DATA_DIR / "positive", DATA_DIR / "augmented"),
    ("negative", DATA_DIR / "negative", DATA_DIR / "augmented_negative"),
    ("background", DATA_DIR / "background", DATA_DIR / "augmented_background"),
]


def run(cmd: list[str]) -> None:
    print(f"\n  >> {' '.join(cmd)}")
    subprocess.check_call(cmd)


def count_wavs(d: Path) -> int:
    return len(list(d.glob("*.wav"))) if d.exists() else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Full preprocessing pipeline")
    parser.add_argument(
        "--factor", type=int, default=3,
        help="Augmentation factor per sample (default: 3)",
    )
    parser.add_argument(
        "--skip-augment", action="store_true",
        help="Skip augmentation, only run MFCC + combine",
    )
    args = parser.parse_args()

    py = [sys.executable]

    # Step 1: Count raw data
    print("=== Raw data ===")
    for name, raw_dir, _ in CLASSES:
        n = count_wavs(raw_dir)
        print(f"  {name}: {n} WAV files")

    # Step 2: Augment
    if not args.skip_augment:
        print(f"\n=== Augmenting (factor={args.factor}) ===")
        for name, raw_dir, aug_dir in CLASSES:
            if count_wavs(raw_dir) == 0:
                print(f"  Skipping {name} (no WAV files)")
                continue
            run(py + [
                "-m", "scripts.augment",
                "--source", str(raw_dir),
                "--output", str(aug_dir),
                "--factor", str(args.factor),
            ])

    # Step 3: MFCC extraction
    print("\n=== Extracting MFCC ===")
    mfcc_files: dict[str, list[Path]] = {}

    for name, raw_dir, aug_dir in CLASSES:
        mfcc_files[name] = []

        # Raw
        raw_npy = DATA_DIR / f"mfcc_{name}_raw.npy"
        if count_wavs(raw_dir) > 0:
            run(py + [
                "-m", "scripts.preprocess",
                "--input", str(raw_dir),
                "--output", str(raw_npy),
            ])
            mfcc_files[name].append(raw_npy)

        # Augmented
        aug_npy = DATA_DIR / f"mfcc_{name}_aug.npy"
        if count_wavs(aug_dir) > 0:
            run(py + [
                "-m", "scripts.preprocess",
                "--input", str(aug_dir),
                "--output", str(aug_npy),
            ])
            mfcc_files[name].append(aug_npy)

    # Step 4: Combine
    print("\n=== Combining ===")
    for name, files in mfcc_files.items():
        if not files:
            print(f"  Skipping {name} (no MFCC files)")
            continue

        arrays = [np.load(f) for f in files]
        combined = np.concatenate(arrays)
        out_path = DATA_DIR / f"mfcc_{name}_all.npy"
        np.save(out_path, combined)
        print(f"  {name}: {len(combined)} samples -> {out_path}")

    print("\n=== Done! Ready to train ===")
    print("  uv run python -m training.train \\")
    print("      --positive data/mfcc_positive_all.npy \\")
    print("      --negative data/mfcc_negative_all.npy \\")
    print("      --background data/mfcc_background_all.npy")


if __name__ == "__main__":
    main()
