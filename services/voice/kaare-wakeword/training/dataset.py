"""Dataset loader for wake word training.

Loads MFCC features for three classes:
  0: Positive class ("Kåre")
  1: Negative class (other words)
  2: Background (silence/noise)

Usage:
    dataset = WakeWordDataset(
        positive_mfcc="data/mfcc_positive.npy",
        negative_mfcc="data/mfcc_negative.npy",
        background_mfcc="data/mfcc_background.npy",
    )
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class WakeWordDataset:
    """Dataset for wake word classification."""

    def __init__(
        self,
        positive_mfcc: Path | str,
        negative_mfcc: Path | str,
        background_mfcc: Path | str,
        split: str = "train",
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
    ):
        """Initialize dataset with preprocessed MFCC features.

        Args:
            positive_mfcc: Path to positive MFCC .npy file
            negative_mfcc: Path to negative MFCC .npy file
            background_mfcc: Path to background MFCC .npy file
            split: One of "train", "val", "test"
            train_ratio/val_ratio/test_ratio: Dataset split ratios
        """
        self.split = split
        
        # Load all MFCCs
        pos_mfcc = np.load(positive_mfcc)
        neg_mfcc = np.load(negative_mfcc)
        bg_mfcc = np.load(background_mfcc)
        
        # Per-sample global normalization (mean/std across entire MFCC matrix)
        # This preserves relative spectral patterns between MFCC coefficients
        # which are critical for distinguishing different phonemes/sounds.
        for arr in (pos_mfcc, neg_mfcc, bg_mfcc):
            for i in range(len(arr)):
                m = arr[i].mean()
                s = arr[i].std() + 1e-8
                arr[i] = (arr[i] - m) / s
        
        # Assign labels: 0=positive, 1=negative, 2=background
        pos_data = [(mfcc, 0) for mfcc in pos_mfcc]
        neg_data = [(mfcc, 1) for mfcc in neg_mfcc]
        bg_data = [(mfcc, 2) for mfcc in bg_mfcc]
        
        # Combine all data
        all_data = pos_data + neg_data + bg_data
        
        # Shuffle with fixed seed for reproducibility
        np.random.seed(42)
        np.random.shuffle(all_data)
        
        # Split dataset
        n_total = len(all_data)
        n_train = int(n_total * train_ratio)
        n_val = int(n_total * val_ratio)
        
        if split == "train":
            self.data = all_data[:n_train]
        elif split == "val":
            self.data = all_data[n_train:n_train + n_val]
        else:  # test
            self.data = all_data[n_train + n_val:]
        
        print(f"{split.capitalize()} split: {len(self.data)} samples")

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> tuple[np.ndarray, int]:
        """Get a single sample.

        Returns:
            (mfcc, label) where mfcc shape is [n_mfcc, n_frames]
        """
        mfcc, label = self.data[idx]
        return mfcc.astype(np.float32), int(label)
