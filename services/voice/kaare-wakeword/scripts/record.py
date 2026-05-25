"""Record wake word samples from microphone.

Usage:
    python -m scripts.record                    # record "kåre" (positive)
    python -m scripts.record --label unknown    # record other words (negative)
    python -m scripts.record --label background --duration 60  # ambient noise

Each recording is saved as a 16kHz mono WAV in data/<label>/.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from scripts.audio_config import (
    BACKGROUND_DIR,
    CLIP_DURATION_S,
    NEGATIVE_DIR,
    POSITIVE_DIR,
    SAMPLE_RATE,
)


def list_devices() -> None:
    """Print available audio input devices."""
    print("\nAvailable input devices:")
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            print(f"  [{idx}] {dev['name']}  (channels={dev['max_input_channels']})")
    print()


def record_clip(duration: float, device: int | None = None) -> np.ndarray:
    """Record a single audio clip and return as float32 array."""
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return audio.squeeze()


def compute_peak_db(audio: np.ndarray) -> float:
    """Return peak level in dBFS."""
    peak = np.max(np.abs(audio))
    if peak < 1e-10:
        return -100.0
    return 20.0 * np.log10(peak)


def get_output_dir(label: str) -> Path:
    """Return the output directory for a given label."""
    dirs = {
        "kåre": POSITIVE_DIR,
        "unknown": NEGATIVE_DIR,
        "background": BACKGROUND_DIR,
    }
    out = dirs.get(label)
    if out is None:
        out = NEGATIVE_DIR / label
    out.mkdir(parents=True, exist_ok=True)
    return out


def next_filename(directory: Path) -> Path:
    """Generate the next sequential filename in a directory."""
    max_idx = -1
    for f in directory.glob("*.wav"):
        m = re.search(r"(\d+)", f.stem)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    return directory / f"{max_idx + 1:04d}.wav"


# Child-friendly prompts to keep it fun
CHILD_PROMPTS = [
    "Si «KÅRE» høyt og tydelig!",
    "Rop «KÅRE» som om du kaller på han!",
    "Hvisk «Kåre» forsiktig...",
    "Si «Kåre» helt vanlig",
    "Lat som du roper «KÅRE!» ut vinduet",
    "Si «Kåre» med morsom stemme!",
    "Si «Kåre» som en robot!",
    "Si «Kåre» skikkelig sakte",
    "Si «Kåre» fort fort fort!",
    "Si «Kåre» som om du synger det",
    "Si «Hei Kåre!»",
    "Si «Kååååre!»",
    "Si «Kåre, kom hit!»",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Record wake word samples")
    parser.add_argument(
        "--label",
        default="kåre",
        help="Sample label: kåre, unknown, background (default: kåre)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help=f"Clip duration in seconds (default: {CLIP_DURATION_S} for words, 60 for background)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of clips to record. 0 = interactive loop (default: 0)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Audio input device index (use --list-devices to see options)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--child-friendly",
        action="store_true",
        help="Fun prompts for kids recording wake words",
    )
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    is_background = args.label == "background"
    duration = args.duration or (60.0 if is_background else CLIP_DURATION_S)
    out_dir = get_output_dir(args.label)

    print(f"Recording label: {args.label}")
    print(f"Duration: {duration}s")
    print(f"Output: {out_dir}/")
    print(f"Sample rate: {SAMPLE_RATE} Hz")
    list_devices()

    recorded = 0
    target = args.count if args.count > 0 else float("inf")

    try:
        while recorded < target:
            if args.child_friendly and not is_background:
                prompt = random.choice(CHILD_PROMPTS)
                print(f"\n{'='*50}")
                print(f"  {prompt}")
                print(f"{'='*50}")
                input("  Trykk ENTER når du er klar! ")
                print("  3...", flush=True)
                time.sleep(0.5)
                print("  2...", flush=True)
                time.sleep(0.5)
                print("  1...", flush=True)
                time.sleep(0.5)
                # Start recording with a pre-buffer to skip device init click
                pre_buf = 0.3
                audio_buf = sd.rec(
                    int((duration + pre_buf) * SAMPLE_RATE),
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    device=args.device,
                )
                time.sleep(pre_buf)
                print("  >>> SI DET NA! <<<", flush=True)
                sd.wait()
                # Trim the pre-buffer (removes init click)
                audio = audio_buf[int(pre_buf * SAMPLE_RATE):].squeeze()
            elif args.count == 0:
                prompt = "background noise" if is_background else f'"{args.label}"'
                input(f"\nPress ENTER to record {prompt} (Ctrl+C to stop)... ")
                audio = record_clip(duration, device=args.device)
            else:
                audio = record_clip(duration, device=args.device)
            peak = compute_peak_db(audio)
            print(f"  peak={peak:.1f} dBFS", end="")

            if peak < -40.0 and not is_background:
                print("  [TOO QUIET - skipped]")
                continue

            filepath = next_filename(out_dir)
            sf.write(str(filepath), audio, SAMPLE_RATE, subtype="PCM_16")
            recorded += 1
            print(f"  -> {filepath.name}  (total: {recorded})")

    except KeyboardInterrupt:
        print(f"\n\nDone. Recorded {recorded} clips in {out_dir}/")
        sys.exit(0)

    print(f"\nDone. Recorded {recorded} clips in {out_dir}/")


if __name__ == "__main__":
    main()
