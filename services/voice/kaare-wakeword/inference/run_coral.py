"""Wake word inference on Google Coral Edge TPU.

Runs wake word detection using TFLite runtime with Edge TPU delegate:
  - Continuously listens for audio
  - Detects "Kåre" wake word
  - Supports real-time streaming

Usage:
    python -m inference.run_coral \
        --model models/wakeword_edgetpu.tflite \
        --device 0
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
import sounddevice as sd

# TFLite runtime — try standalone packages first, then TF fallback
try:
    from ai_edge_litert.interpreter import Interpreter as _TFLiteInterpreter
except ImportError:
    try:
        import tflite_runtime.interpreter as _tflite_mod
        _TFLiteInterpreter = _tflite_mod.Interpreter
    except ImportError:
        import tensorflow as tf
        _TFLiteInterpreter = tf.lite.Interpreter

from inference.common import WakeWordProcessor, extract_mfcc, list_audio_devices
from scripts.audio_config import CLIP_SAMPLES, SAMPLE_RATE


# MFCC parameters (must match training)
N_MFCC = 40
N_FRAMES = 150  # Approximate for 1.5s audio


class AudioStreamer:
    """Continuous audio capture in background thread."""

    def __init__(self, device: int = 0, sample_rate: int = SAMPLE_RATE):
        self.device = device
        self.sample_rate = sample_rate
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=10)
        self.stream: sd.InputStream | None = None
        self.running = False
        self.native_sr = 44100  # Actual hardware sample rate

    def audio_callback(self, indata: np.ndarray, frames: int, time_info: dict, status: int) -> None:
        """Callback for audio streaming."""
        if status:
            return
        try:
            mono = indata[:, 0].copy()
            if self.native_sr != self.sample_rate:
                mono = resample_poly(mono, self.sample_rate, self.native_sr).astype(np.float32)
            self.queue.put_nowait(mono)
        except queue.Full:
            pass  # Drop frames if queue full

    def start(self) -> None:
        """Start audio streaming."""
        dev_info = sd.query_devices(self.device)
        channels = max(dev_info['max_input_channels'], 2)
        self.native_sr = int(dev_info['default_samplerate'])
        native_blocksize = int(self.native_sr * CLIP_SAMPLES / (self.sample_rate * 10))
        self.stream = sd.InputStream(
            samplerate=self.native_sr,
            channels=channels,
            blocksize=native_blocksize,
            device=self.device,
            callback=self.audio_callback,
            dtype=np.float32,
        )
        self.stream.start()
        self.running = True
        print(f"Audio streaming started on device {self.device}")

    def stop(self) -> None:
        """Stop audio streaming."""
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        print("Audio streaming stopped")

    def get_chunk(self, size: int = CLIP_SAMPLES) -> np.ndarray:
        """Get audio chunk (blocking until full).

        Returns:
            Audio array of requested size
        """
        chunk = np.zeros(size, dtype=np.float32)
        offset = 0

        while offset < size:
            data = self.queue.get()  # block until data arrives
            n_copy = min(len(data), size - offset)
            chunk[offset:offset + n_copy] = data[:n_copy]
            offset += n_copy

        return chunk


def run_inference(
    model_path: Path,
    audio_device: int = 0,
    confidence: float = 0.85,
) -> None:
    """Run wake word inference on Edge TPU.

    Args:
        model_path: Path to Edge TPU compiled TFLite model
        audio_device: Audio input device index
        confidence: Minimum confidence score
    """
    # Load TFLite model
    interpreter = _TFLiteInterpreter(model_path=str(model_path))
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    input_shape = input_details[0]['shape']
    print(f"Model input shape: {input_shape}")

    # Initialize processor
    processor = WakeWordProcessor(confidence=confidence)

    # Start audio streaming
    streamer = AudioStreamer(device=audio_device)
    streamer.start()

    try:
        sample_count = 0
        print("Listening for 'Kåre'... (Ctrl+C to stop)")

        while True:
            # Get audio chunk
            audio_chunk = streamer.get_chunk(CLIP_SAMPLES)
            sample_count += CLIP_SAMPLES

            # Extract MFCC
            mfcc = extract_mfcc(audio_chunk)

            # Add batch and channel dimensions
            input_data = mfcc[np.newaxis, ..., np.newaxis]

            # Run inference
            interpreter.set_tensor(input_details[0]['index'], input_data)
            interpreter.invoke()
            output = interpreter.get_tensor(output_details[0]['index'])

            # Dequantize if INT8 output
            out_detail = output_details[0]
            if out_detail['dtype'] == np.int8:
                scale, zero_point = out_detail['quantization']
                logits = (output.astype(np.float32) - zero_point) * scale
            else:
                logits = output.astype(np.float32)

            # Softmax to get probabilities (model outputs raw logits)
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / exp_logits.sum(axis=-1, keepdims=True)
            positive_score = float(probs[0, 0])

            # Live score display
            label = ["kare", "neg", "bg"][int(np.argmax(probs[0]))]
            bar = "#" * int(positive_score * 20)
            print(f"\r  [{bar:<20}] {positive_score:.2f} ({label})  ", end="", flush=True)

            # Check for detection
            if processor.should_trigger(positive_score, sample_count):
                print(f"\n  >> Wake word detected! Confidence: {positive_score:.2f}")
                processor.record_trigger(sample_count)
                # TODO: Send MQTT event, trigger Home Assistant, etc.

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        streamer.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run wake word inference on Coral")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/wakeword_edgetpu.tflite"),
        help="Path to Edge TPU compiled model",
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

    run_inference(args.model, args.device, args.confidence)


if __name__ == "__main__":
    main()
