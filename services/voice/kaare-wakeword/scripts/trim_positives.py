"""Trim positive wake word recordings to 1.5s centered on detected speech.

Uses smoothed energy envelope with adaptive threshold to find speech onset
and offset, then centers a 1.5s window on the speech region. No external
VAD dependencies needed — just numpy.

Usage:
    python scripts/trim_positives.py data/positive_review/ data/positive_trimmed/
"""
import sys
import wave
from pathlib import Path

import numpy as np

CLIP_DURATION = 1.5  # seconds
SAMPLE_RATE = 16000
CLIP_SAMPLES = int(CLIP_DURATION * SAMPLE_RATE)  # 24000

# Speech detection params
FRAME_MS = 20  # energy frame size
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)  # 320
SMOOTH_FRAMES = 5  # smoothing window for energy envelope
NOISE_PERCENTILE = 30  # percentile of energy treated as noise floor
SPEECH_FACTOR = 4.0  # energy must exceed noise floor by this factor


def read_wav_mono(path: Path) -> np.ndarray:
    """Read WAV file and return mono float32 array at 16kHz."""
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    if n_channels > 1:
        samples = samples[::n_channels]

    if rate != SAMPLE_RATE:
        ratio = rate / SAMPLE_RATE
        indices = np.arange(0, len(samples), ratio).astype(int)
        indices = indices[indices < len(samples)]
        samples = samples[indices]

    return samples


def find_speech_region(audio: np.ndarray) -> tuple[int, int] | None:
    """Find speech start/end using adaptive energy thresholding.

    1. Compute per-frame RMS energy
    2. Smooth with moving average
    3. Estimate noise floor from quietest frames
    4. Threshold = noise_floor * SPEECH_FACTOR
    5. Find first/last frame exceeding threshold

    Returns (start_sample, end_sample) or None if no speech.
    """
    # Compute per-frame RMS energy
    n_frames = len(audio) // FRAME_SAMPLES
    if n_frames < 3:
        return None

    energy = np.array([
        np.sqrt(np.mean(audio[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES] ** 2))
        for i in range(n_frames)
    ])

    # Smooth energy envelope
    kernel = np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES
    smoothed = np.convolve(energy, kernel, mode="same")

    # Adaptive threshold: noise floor + factor
    noise_floor = np.percentile(smoothed, NOISE_PERCENTILE)
    threshold = max(noise_floor * SPEECH_FACTOR, 0.005)  # min threshold to avoid silence-only

    # Find speech frames
    speech_mask = smoothed > threshold
    speech_indices = np.where(speech_mask)[0]

    if len(speech_indices) == 0:
        return None

    start_frame = speech_indices[0]
    end_frame = speech_indices[-1] + 1

    return (start_frame * FRAME_SAMPLES, end_frame * FRAME_SAMPLES)


def trim_around_region(audio: np.ndarray, start: int, end: int) -> np.ndarray:
    """Extract CLIP_SAMPLES centered on the speech region."""
    center = (start + end) // 2
    half = CLIP_SAMPLES // 2
    clip_start = max(0, center - half)
    clip_end = clip_start + CLIP_SAMPLES

    if clip_end > len(audio):
        clip_end = len(audio)
        clip_start = max(0, clip_end - CLIP_SAMPLES)

    clip = audio[clip_start:clip_end]

    if len(clip) < CLIP_SAMPLES:
        clip = np.pad(clip, (0, CLIP_SAMPLES - len(clip)))

    return clip


def write_wav_mono(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE):
    """Write mono float32 array as 16-bit WAV."""
    int16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16.tobytes())


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_dir> <output_dir>")
        sys.exit(1)

    in_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    wavs = sorted(in_dir.glob("*.wav"))
    if not wavs:
        print(f"No WAV files in {in_dir}")
        sys.exit(1)

    skipped = 0
    print(f"Processing {len(wavs)} files...")
    for wav_path in wavs:
        audio = read_wav_mono(wav_path)
        region = find_speech_region(audio)

        if region is None:
            print(f"  {wav_path.name}: NO SPEECH — skipping")
            skipped += 1
            continue

        start, end = region
        speech_ms = (end - start) * 1000 // SAMPLE_RATE
        center_ms = ((start + end) // 2) * 1000 // SAMPLE_RATE
        clip = trim_around_region(audio, start, end)

        out_path = out_dir / wav_path.name
        write_wav_mono(out_path, clip)

        print(f"  {wav_path.name}: speech {start*1000//SAMPLE_RATE}-{end*1000//SAMPLE_RATE}ms ({speech_ms}ms) center={center_ms}ms")

    written = len(wavs) - skipped
    print(f"\nDone! {written} trimmed, {skipped} skipped -> {out_dir}")


if __name__ == "__main__":
    main()
