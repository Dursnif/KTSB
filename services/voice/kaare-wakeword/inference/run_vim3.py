"""Wake word inference on Khadas VIM3 NPU.

Runs wake word detection using KSNN or TFLite with Verisilicon delegate:
  - Same logic as Coral inference
  - Different backend (VIM3 NPU)

Usage:
    python -m inference.run_vim3 \
        --model models/wakeword_vim3.nb \
        --device 0
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

# Try VIM3 NPU libraries
try:
    import ksnn
    KSNN_AVAILABLE = True
except ImportError:
    KSNN_AVAILABLE = False
    print("Warning: KSNN not available, using TFLite fallback")

from inference.common import WakeWordProcessor, extract_mfcc, list_audio_devices
from scripts.audio_config import CLIP_SAMPLES, SAMPLE_RATE


# Reuse AudioStreamer from run_coral.py
class AudioStreamer:
    """Continuous audio capture in background thread."""

    def __init__(self, device: int = 0, sample_rate: int = SAMPLE_RATE):
        self.device = device
        self.sample_rate = sample_rate
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=10)
        self.stream: sd.InputStream | None = None
        self.running = False

    def audio_callback(self, indata: np.ndarray, frames: int, time_info: dict, status: int) -> None:
        if status:
            return
        try:
            self.queue.put_nowait(indata[:, 0])
        except queue.Full:
            pass

    def start(self) -> None:
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            blocksize=int(CLIP_SAMPLES / 10),
            device=self.device,
            callback=self.audio_callback,
            dtype=np.float32,
        )
        self.stream.start()
        self.running = True
        print(f"Audio streaming started on device {self.device}")

    def stop(self) -> None:
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        print("Audio streaming stopped")

    def get_chunk(self, size: int = CLIP_SAMPLES) -> np.ndarray:
        chunk = np.zeros(size, dtype=np.float32)
        offset = 0

        while offset < size:
            try:
                data = self.queue.get(timeout=0.1)
                n_copy = min(len(data), size - offset)
                chunk[offset:offset + n_copy] = data[:n_copy]
                offset += n_copy
            except queue.Empty:
                break

        return chunk


def run_vim3_inference(
    model_path: Path,
    lib_path: Path | None,
    audio_device: int = 0,
    confidence: float = 0.85,
) -> None:
    """Run wake word inference on VIM3 NPU.

    Args:
        model_path: Path to VIM3 .nb model or TFLite model
        lib_path: Path to KSNN library (.so file)
        audio_device: Audio input device index
        confidence: Minimum confidence score
    """
    # Try KSNN first (native VIM3 NPU)
    if KSNN_AVAILABLE and model_path.suffix == ".nb":
        print(f"Loading VIM3 NPU model: {model_path}")
        if lib_path is None:
            # Try to find lib in same directory as model
            lib_path = model_path.parent / "libnn_model_uint8.so"
        
        model = ksnn.Model(str(model_path), lib_path=str(lib_path))
    else:
        # Fallback to TFLite (may use Verisilicon delegate)
        print("Using TFLite fallback for VIM3")
        try:
            import tflite_runtime.interpreter as tflite
            model = tflite.Interpreter(model_path=str(model_path))
            model.allocate_tensors()
            input_details = model.get_input_details()
            output_details = model.get_output_details()
        except ImportError:
            import tensorflow as tf
            model = tf.lite.Interpreter(model_path=str(model_path))
            model.allocate_tensors()
            input_details = model.get_input_details()
            output_details = model.get_output_details()

    processor = WakeWordProcessor(confidence=confidence)
    streamer = AudioStreamer(device=audio_device)
    streamer.start()

    try:
        sample_count = 0
        print("Listening for 'Kåre' on VIM3 NPU... (Ctrl+C to stop)")

        while True:
            audio_chunk = streamer.get_chunk(CLIP_SAMPLES)
            sample_count += CLIP_SAMPLES

            mfcc = extract_mfcc(audio_chunk)
            input_data = mfcc[np.newaxis, ..., np.newaxis]

            if KSNN_AVAILABLE and model_path.suffix == ".nb":
                # KSNN inference
                output = model.inference(input_data.astype(np.uint8))
                positive_score = float(output[0][0])
            else:
                # TFLite inference
                model.set_tensor(input_details[0]['index'], input_data)
                model.invoke()
                output = model.get_tensor(output_details[0]['index'])
                positive_score = float(output[0, 0])

            if processor.should_trigger(positive_score, sample_count):
                print(f"  ✓ Wake word detected! Confidence: {positive_score:.2f}")
                processor.record_trigger(sample_count)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        streamer.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run wake word inference on VIM3")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/wakeword_vim3.nb"),
        help="Path to VIM3 model (.nb) or TFLite model",
    )
    parser.add_argument(
        "--lib",
        type=Path,
        default=None,
        help="Path to KSNN library (.so file)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=0,
        help="Audio input device index",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.85,
        help="Confidence threshold (default: 0.85)",
    )
    args = parser.parse_args()

    if args.list_devices:
        devices = list_audio_devices()
        print("Available audio devices:")
        for idx, name in devices.items():
            print(f"  {idx}: {name}")
        return

    run_vim3_inference(args.model, args.lib, args.device, args.confidence)


if __name__ == "__main__":
    main()
