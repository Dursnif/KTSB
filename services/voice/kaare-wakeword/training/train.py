"""Train wake word model with QAT for Edge TPU deployment.

Training pipeline:
  1. Load preprocessed MFCC features
  2. Split into train/val/test
  3. Train DS-CNN with QAT
  4. Save best model checkpoint

Usage:
    python -m training.train \
        --positive data/mfcc_positive.npy \
        --negative data/mfcc_negative.npy \
        --background data/mfcc_background.npy \
        --output models/wakeword_model.keras
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import tensorflow as tf

from scripts.audio_config import SAMPLE_RATE
from training.dataset import WakeWordDataset
from training.model import build_wakeword_model, apply_qat


def train_model(
    positive_mfcc: Path,
    negative_mfcc: Path,
    background_mfcc: Path,
    output_path: Path,
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 0.001,
) -> None:
    """Train wake word model with QAT.

    Args:
        positive_mfcc: Path to positive MFCC .npy
        negative_mfcc: Path to negative MFCC .npy
        background_mfcc: Path to background MFCC .npy
        output_path: Path to save trained model
        epochs: Number of training epochs
        batch_size: Training batch size
        learning_rate: Initial learning rate
    """
    # Load datasets
    train_dataset = WakeWordDataset(
        positive_mfcc=str(positive_mfcc),
        negative_mfcc=str(negative_mfcc),
        background_mfcc=str(background_mfcc),
        split="train",
    )
    val_dataset = WakeWordDataset(
        positive_mfcc=str(positive_mfcc),
        negative_mfcc=str(negative_mfcc),
        background_mfcc=str(background_mfcc),
        split="val",
    )

    # Convert to TensorFlow datasets
    def to_tf_dataset(wake_dataset):
        """Convert WakeWordDataset to tf.data.Dataset."""
        # Add channel dim only: (n_mfcc, n_frames) → (n_mfcc, n_frames, 1)
        mfccs = np.stack([mfcc for mfcc, _ in wake_dataset.data])[..., np.newaxis]
        labels = np.array([label for _, label in wake_dataset.data])
        return (
            tf.data.Dataset.from_tensor_slices((mfccs, labels))
            .shuffle(1000)
            .batch(batch_size)
            .prefetch(tf.data.AUTOTUNE)
        )

    train_ds = to_tf_dataset(train_dataset)
    val_ds = to_tf_dataset(val_dataset)

    # Detect actual frame count from data
    sample_mfcc = train_dataset.data[0][0]
    num_mfcc, num_frames = sample_mfcc.shape

    # Build model
    model = build_wakeword_model(num_mfcc=num_mfcc, num_frames=num_frames, num_classes=3)
    qat_model = apply_qat(model)

    # Callbacks
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=10,
            restore_best_weights=True,
        ),
        tf.keras.callbacks.ModelCheckpoint(
            filepath=str(output_path),
            monitor="val_accuracy",
            save_best_only=True,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=5,
        ),
    ]

    # Train
    print(f"Training for up to {epochs} epochs...")
    history = qat_model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=epochs,
        callbacks=callbacks,
        verbose=1,
    )

    # Evaluate on test set
    test_dataset = WakeWordDataset(
        positive_mfcc=str(positive_mfcc),
        negative_mfcc=str(negative_mfcc),
        background_mfcc=str(background_mfcc),
        split="test",
    )
    test_ds = to_tf_dataset(test_dataset)
    test_loss, test_acc = qat_model.evaluate(test_ds, verbose=0)
    print(f"\nTest accuracy: {test_acc:.4f}, Test loss: {test_loss:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train wake word model with QAT")
    parser.add_argument(
        "--positive",
        type=Path,
        required=True,
        help="Path to positive MFCC .npy",
    )
    parser.add_argument(
        "--negative",
        type=Path,
        required=True,
        help="Path to negative MFCC .npy",
    )
    parser.add_argument(
        "--background",
        type=Path,
        required=True,
        help="Path to background MFCC .npy",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/wakeword_model.keras"),
        help="Output model path (default: models/wakeword_model.keras)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
        help="Training epochs (default: 50)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Batch size (default: 32)",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=0.001,
        help="Learning rate (default: 0.001)",
    )
    args = parser.parse_args()

    train_model(
        args.positive,
        args.negative,
        args.background,
        args.output,
        args.epochs,
        args.batch_size,
        args.learning_rate,
    )


if __name__ == "__main__":
    main()
