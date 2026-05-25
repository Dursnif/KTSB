"""Export trained Keras model to TFLite for different targets.

Targets:
  mac   - Float32 TFLite, optimized with dynamic range quantization
  rpi   - Float32 TFLite, same as mac (CPU inference)
  coral - Full INT8 TFLite (input for edgetpu_compiler)
  vim3  - Full INT8 TFLite (input for KSNN conversion)
  all   - Export all targets at once

Usage:
    python -m training.export_tflite --target mac
    python -m training.export_tflite --target coral
    python -m training.export_tflite --target all
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import tensorflow as tf

from scripts.audio_config import DATA_DIR, MODELS_DIR


TARGETS = {
    "mac":   {"quantize": False, "output": "wakeword_mac.tflite"},
    "rpi":   {"quantize": False, "output": "wakeword_rpi.tflite"},
    "coral": {"quantize": True,  "output": "wakeword_coral_int8.tflite"},
    "vim3":  {"quantize": True,  "output": "wakeword_vim3_int8.tflite"},
}


def _normalize_mfcc(mfcc: np.ndarray) -> np.ndarray:
    """Per-sample global normalization matching WakeWordDataset."""
    out = np.empty_like(mfcc)
    for i in range(len(mfcc)):
        m = mfcc[i].mean()
        s = mfcc[i].std() + 1e-8
        out[i] = (mfcc[i] - m) / s
    return out


def _load_representative(num: int = 100) -> np.ndarray:
    """Load and normalize representative data for INT8 calibration."""
    n = num // 3
    parts = []
    for name in ["positive", "negative", "background"]:
        path = DATA_DIR / f"mfcc_{name}_all.npy"
        parts.append(_normalize_mfcc(np.load(path)[:n]))
    rep = np.concatenate(parts, axis=0).astype(np.float32)
    return rep[..., np.newaxis]  # add channel dim


def export_float32(model: tf.keras.Model, output_path: Path) -> None:
    """Export float32 TFLite with dynamic range optimization."""
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_model = converter.convert()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(tflite_model)
    print(f"  {output_path.name}: {len(tflite_model) / 1024:.1f} KB (float32)")


def export_int8(model: tf.keras.Model, output_path: Path) -> None:
    """Export full INT8 TFLite for hardware accelerators."""
    rep_data = _load_representative()

    def representative_dataset():
        for sample in rep_data:
            yield [sample[np.newaxis, ...]]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    converter.representative_dataset = representative_dataset

    tflite_model = converter.convert()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(tflite_model)
    print(f"  {output_path.name}: {len(tflite_model) / 1024:.1f} KB (int8)")


def export_target(model: tf.keras.Model, target: str) -> None:
    """Export model for a specific target."""
    cfg = TARGETS[target]
    output_path = MODELS_DIR / cfg["output"]
    if cfg["quantize"]:
        export_int8(model, output_path)
    else:
        export_float32(model, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export model to TFLite")
    parser.add_argument(
        "--model", type=Path,
        default=MODELS_DIR / "wakeword_model.keras",
        help="Path to trained .keras model",
    )
    parser.add_argument(
        "--target", required=True,
        choices=list(TARGETS.keys()) + ["all"],
        help="Export target: mac, rpi, coral, vim3, or all",
    )
    args = parser.parse_args()

    model = tf.keras.models.load_model(str(args.model))
    print(f"Loaded: {args.model}")

    targets = list(TARGETS.keys()) if args.target == "all" else [args.target]
    for t in targets:
        export_target(model, t)

    if "coral" in targets:
        print("\n  Coral neste steg (på Linux):")
        print(f"    edgetpu_compiler models/{TARGETS['coral']['output']}")
    if "vim3" in targets:
        print("\n  VIM3 neste steg (på VIM3):")
        print(f"    # Konverter til .nb med Khadas SDK, eller bruk TFLite direkte")


if __name__ == "__main__":
    main()
