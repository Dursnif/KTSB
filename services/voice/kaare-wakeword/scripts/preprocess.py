"""Preprocess audio to MFCC features for keyword spotting.

Converts WAV files to MFCC (Mel-Frequency Cepstral Coefficients)
using standard keyword spotting parameters:
  - Sample rate: 16 kHz
  - MFCC dimension: 40
  - Window: 25 ms, Hop: 10 ms
  - Frame count: ~150 frames (1.5s audio)

Usage:
    python -m scripts.preprocess --input data/positive/ --output data/mfcc_positive.npy
"""

from __future__ import annotations

import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import soundfile as sf
import librosa

from scripts.audio_config import CLIP_SAMPLES, SAMPLE_RATE


# MFCC parameters (standard for keyword spotting)
N_MFCC = 40
N_FFT = 1024
HOP_LENGTH = 160  # 10ms at 16kHz
WIN_LENGTH = 400   # 25ms at 16kHz


def extract_mfcc(wav_path: Path) -> np.ndarray | None:
    """Extract MFCC features from a WAV file.

    Returns:
        MFCC array of shape [n_mfcc, n_frames] or None if error.
    """
    try:
        audio, sr = sf.read(str(wav_path))
        
        # Resample if needed
        if sr != SAMPLE_RATE:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
        
        # Convert to mono
        if audio.ndim > 1:
            audio = audio[:, 0]
        
        # Pad or trim to exact length
        if len(audio) < CLIP_SAMPLES:
            audio = np.pad(audio, (0, CLIP_SAMPLES - len(audio)), mode="constant")
        elif len(audio) > CLIP_SAMPLES:
            # Random offset for variety
            max_offset = len(audio) - CLIP_SAMPLES
            offset = np.random.randint(0, max_offset + 1)
            audio = audio[offset:offset + CLIP_SAMPLES]
        
        # Extract MFCC
        mfcc = librosa.feature.mfcc(
            y=audio,
            sr=SAMPLE_RATE,
            n_mfcc=N_MFCC,
            n_fft=N_FFT,
            hop_length=HOP_LENGTH,
            win_length=WIN_LENGTH,
        )
        
        return mfcc.astype(np.float32)
    
    except Exception:
        return None


def preprocess_directory(
    input_dir: Path,
    output_path: Path,
    num_workers: int = 4,
) -> None:
    """Preprocess all WAV files in a directory to MFCC features.

    Args:
        input_dir: Directory with WAV files
        output_path: Path to save .npy file with MFCC features
        num_workers: Number of parallel processes
    """
    wav_files = list(input_dir.glob("*.wav"))
    print(f"Found {len(wav_files)} files in {input_dir}/")

    mfccs = []
    
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_file = {
            executor.submit(extract_mfcc, wav): wav for wav in wav_files
        }
        
        completed = 0
        for future in as_completed(future_to_file):
            wav_path = future_to_file[future]
            try:
                mfcc = future.result()
                if mfcc is not None:
                    mfccs.append(mfcc)
            except Exception as exc:
                print(f"  Error processing {wav_path.name}: {exc}")
            
            completed += 1
            if completed % 50 == 0:
                print(f"  Processed {completed}/{len(wav_files)}...")
    
    # Stack all MFCCs: [n_samples, n_mfcc, n_frames]
    if mfccs:
        mfcc_array = np.stack(mfccs)
        np.save(output_path, mfcc_array)
        print(f"Saved {len(mfccs)} MFCC features to {output_path}")
    else:
        print("No valid MFCCs extracted!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess audio to MFCC features")
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input directory with WAV files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output path for .npy file",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel processes (default: 4)",
    )
    args = parser.parse_args()

    preprocess_directory(args.input, args.output, args.workers)


if __name__ == "__main__":
    main()
