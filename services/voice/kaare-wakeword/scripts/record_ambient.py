"""Record ambient audio (TV, radio, conversation) and split into clips.

Records continuously, then chops into 1.5s clips for negative training data.
Much faster than recording word-by-word.

Usage:
    python -m scripts.record_ambient --duration 60 --device 1      # 1 min TV
    python -m scripts.record_ambient --duration 300 --device 1     # 5 min
    python -m scripts.record_ambient --list-devices
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from scripts.audio_config import CLIP_DURATION_S, CLIP_SAMPLES, SAMPLE_RATE
from scripts.record import list_devices


def record_and_split(
    duration_s: float,
    output_dir: Path,
    device: int = 0,
    prefix: str = "amb",
    min_peak: float = 0.005,
) -> int:
    """Record continuously, then split into clips.

    Args:
        duration_s: Total recording duration in seconds.
        output_dir: Where to save clips.
        device: Audio input device index.
        prefix: Filename prefix for clips.
        min_peak: Minimum peak amplitude to keep a clip (skip silence).

    Returns:
        Number of clips saved.
    """
    total_samples = int(duration_s * SAMPLE_RATE)

    print(f"Tar opp {duration_s:.0f} sekunder...")
    print("(Trykk Ctrl+C for å stoppe tidlig)\n")

    try:
        audio = sd.rec(
            total_samples,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=device,
        )
        sd.wait()
    except KeyboardInterrupt:
        sd.stop()
        # Keep what we got so far
        audio = audio[: sd.get_stream().read_available] if sd.get_stream() else audio
        print("\nStoppet tidlig.")

    audio = audio.squeeze()
    actual_duration = len(audio) / SAMPLE_RATE
    print(f"Tok opp {actual_duration:.1f}s ({len(audio)} samples)")

    # Find highest existing index to avoid overwriting
    max_idx = -1
    for f in output_dir.glob("*.wav"):
        m = re.search(r"(\d+)", f.stem)
        if m:
            max_idx = max(max_idx, int(m.group(1)))

    # Split into clips
    num_clips = len(audio) // CLIP_SAMPLES
    saved = 0
    skipped_silent = 0

    for i in range(num_clips):
        start = i * CLIP_SAMPLES
        clip = audio[start : start + CLIP_SAMPLES]

        # Skip silent clips
        peak = np.max(np.abs(clip))
        if peak < min_peak:
            skipped_silent += 1
            continue

        idx = max_idx + 1 + saved
        out_path = output_dir / f"{prefix}_{idx:04d}.wav"
        sf.write(str(out_path), clip, SAMPLE_RATE, subtype="PCM_16")
        saved += 1

    return saved, skipped_silent, num_clips


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record ambient audio and split into training clips"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60,
        help="Recording duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=0,
        help="Audio input device index (use --list-devices to see options)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/negative/"),
        help="Output directory (default: data/negative/)",
    )
    parser.add_argument(
        "--min-peak",
        type=float,
        default=0.005,
        help="Minimum peak amplitude to keep a clip (default: 0.005)",
    )
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    args.output.mkdir(parents=True, exist_ok=True)

    clips_per_minute = 60 / CLIP_DURATION_S
    expected = int(args.duration / CLIP_DURATION_S)

    print("=" * 60)
    print(f"Ambient opptak → {args.output}/")
    print(f"Varighet: {args.duration:.0f}s → ~{expected} clips à {CLIP_DURATION_S}s")
    print(f"Device: {args.device}")
    print("=" * 60)
    print("\nTrykk ENTER for å starte...")
    input()

    saved, skipped, total = record_and_split(
        args.duration, args.output, args.device, min_peak=args.min_peak
    )

    print("\n" + "=" * 60)
    print(f"Ferdig!")
    print(f"  Totalt clips:    {total}")
    print(f"  Lagret:          {saved}")
    print(f"  Hoppet (stille): {skipped}")
    print(f"  Output:          {args.output}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
