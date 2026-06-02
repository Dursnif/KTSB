"""Main satellite pipeline.

Orchestrates audio capture, wake word detection, VAD, end-of-utterance,
and server streaming into a single async loop.

State Machine:
    IDLE -> LISTENING (wake word)
    LISTENING -> PROCESSING (EOU / timeout)
    LISTENING -> IDLE (hard timeout without speech)
    PROCESSING -> RESPONDING (TTS audio received)
    PROCESSING -> IDLE (error / timeout)
    RESPONDING -> IDLE (playback complete)
    RESPONDING -> LISTENING (barge-in wake word)
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import sys
import time
from enum import Enum, auto
from pathlib import Path

import numpy as np

from satellite.audio import RingBuffer
from satellite.config import SatelliteConfig
from satellite.eou import EOUDetector, EOUResult
from satellite.leds import create_leds
from satellite.streaming import SatelliteClient, AudioStartMsg, AudioChunkMsg, AudioEndMsg
from satellite.vad import create_vad
from satellite.wakeword import WakeWordProcessor, extract_mfcc, CLIP_SAMPLES, SAMPLE_RATE

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

log = logging.getLogger(__name__)


class PipelineState(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    RESPONDING = auto()


def _run_wake_inference(
    chunk: np.ndarray,
    interpreter,
    input_details: list,
    output_details: list,
) -> float:
    """Run wake word MFCC + TFLite inference (CPU-bound, runs in thread)."""
    mfcc = extract_mfcc(chunk)
    input_data = mfcc[np.newaxis, ..., np.newaxis]
    interpreter.set_tensor(input_details[0]['index'], input_data)
    interpreter.invoke()
    output = interpreter.get_tensor(output_details[0]['index'])
    exp_logits = np.exp(output - np.max(output))
    probs = exp_logits / exp_logits.sum(axis=-1, keepdims=True)
    return float(probs[0, 0])


class SatellitePipeline:
    """Voice satellite pipeline state machine.

    Combines wake word detection, VAD, EOU detection, and server
    communication into a coherent pipeline.
    """

    def __init__(self, config: SatelliteConfig):
        self.config = config
        self.state = PipelineState.IDLE

        # Audio
        self.ring_buffer = RingBuffer(
            max_samples=config.ring_buffer_samples,
            sample_rate=config.sample_rate,
        )

        # VAD + EOU (created lazily on run())
        self._vad = None
        self._eou: EOUDetector | None = None

        # Wake word
        self._wake_processor = WakeWordProcessor(
            confidence=config.wake_confidence,
            debounce_seconds=config.wake_debounce_s,
        )

        # LEDs
        self._leds = None

        # Utterance audio (collected during LISTENING)
        self._utterance_chunks: list[np.ndarray] = []
        self._speech_detected: bool = False  # VAD saw speech during this utterance

        # Grace period: ignore EOU for first N frames after wake word
        self._listening_frames = 0
        self._grace_frames = 0  # Set in run() from config
        self._followup_timeout_s: float = 0  # 0 = no followup timeout
        self._response_wait_start: float = 0

        # Server client
        self._client: SatelliteClient | None = None
        self._last_transcript: str = ""
        self._last_response_audio: bytes | None = None

        # Playback tracking (prevents IDLE transition while audio still playing)
        self._playback_end_time: float = 0

        # Deferred listen request (stored when listen arrives during playback)
        self._pending_listen: dict | None = None

        # Wake word STT verification (None=pending, True=confirmed, False=rejected)
        self._wake_verified: bool | None = None

        # Stop signal
        self._stop_event = asyncio.Event()

    def _set_state(self, new_state: PipelineState) -> None:
        """Transition state and update LEDs."""
        self.state = new_state
        if self._leds is None:
            return
        if new_state == PipelineState.IDLE:
            self._leds.off()
        elif new_state == PipelineState.LISTENING:
            self._leds.listening()
        elif new_state == PipelineState.PROCESSING:
            self._leds.processing()
        elif new_state == PipelineState.RESPONDING:
            self._leds.responding()

    def _on_wake_word(self, audio_queue: asyncio.Queue) -> None:
        """Handle wake word detection."""
        if self.state in (PipelineState.IDLE, PipelineState.RESPONDING):
            log.info("Wake word detected -- transitioning to LISTENING")
            self._set_state(PipelineState.LISTENING)
            self._utterance_chunks = []
            self._listening_frames = 0
            self._speech_detected = False
            self._wake_verified = None  # pending verification

            # Drain stale frames from queue (buffered during inference)
            drained = 0
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if drained:
                log.debug("Drained %d stale frames from audio queue", drained)

            # Grab pre-roll from ring buffer
            pre_roll = self.ring_buffer.read_last(self.config.pre_roll_samples)
            self._utterance_chunks.append(pre_roll)

            # Send wake word audio to server for STT verification (async, parallel with listening)
            wake_clip = self.ring_buffer.read_last(CLIP_SAMPLES)
            if self._client and self._client.connected:
                import base64
                asyncio.ensure_future(self._client.send_json({
                    "type": "verify_wake",
                    "satellite_id": self.config.satellite_id,
                    "audio": base64.b64encode(wake_clip.astype(np.float32).tobytes()).decode("ascii"),
                    "sample_rate": self.config.sample_rate,
                }))
                log.info("Sent wake word audio (%.1fs) for STT verification", len(wake_clip) / self.config.sample_rate)
            else:
                # Can't verify without connection — assume valid
                self._wake_verified = True

            # Reset EOU for new utterance
            if self._eou:
                self._eou.reset()
            if self._vad:
                self._vad.reset()

    async def _on_wake_result(self, msg: dict) -> None:
        """Handle wake word verification result from server."""
        confirmed = msg.get("confirmed", False)
        transcript = msg.get("transcript", "")
        self._wake_verified = confirmed
        if confirmed:
            log.info("Wake word verified by STT: '%s'", transcript)
        else:
            log.info("Wake word REJECTED by STT: '%s' — will drop utterance", transcript)

    def _on_end_of_utterance(self, reason: str = "eou") -> None:
        """Handle end of utterance (silence timeout or hard timeout)."""
        if self.state != PipelineState.LISTENING:
            return
        duration = sum(len(c) for c in self._utterance_chunks) / self.config.sample_rate
        log.info(
            "End of utterance (reason=%s, frames=%d, duration=%.1fs) -- transitioning to PROCESSING",
            reason, self._listening_frames, duration,
        )
        self._set_state(PipelineState.PROCESSING)

    def _on_response_complete(self) -> None:
        """Handle response playback complete."""
        if self.state in (PipelineState.PROCESSING, PipelineState.RESPONDING):
            log.info("Response complete -- transitioning to IDLE")
            self._set_state(PipelineState.IDLE)

    async def _on_transcript(self, msg: dict) -> None:
        """Handle transcript from server."""
        self._last_transcript = msg.get("text", "")
        log.info("Transcript: %s", self._last_transcript)

    async def _on_intent(self, msg: dict) -> None:
        """Handle intent from server."""
        log.info("Intent: %s -> %s", msg.get("action"), msg.get("response_text", "")[:80])

    async def _on_audio_response(self, msg: dict) -> None:
        """Handle TTS audio from server."""
        import base64
        self._last_response_audio = base64.b64decode(msg["payload"])
        rate = msg.get("sample_rate", 22050)
        is_last = msg.get("is_last", True)
        log.info("TTS audio: %d bytes @ %dHz (is_last=%s)", len(self._last_response_audio), rate, is_last)

        # Skip tiny/empty audio (silence fallback for empty responses)
        if len(self._last_response_audio) < 1000:
            log.debug("Skipping tiny audio (%d bytes)", len(self._last_response_audio))
            if is_last:
                await self._wait_for_playback_and_complete()
            return

        self._set_state(PipelineState.RESPONDING)

        try:
            import sounddevice as sd
            audio_i16 = np.frombuffer(self._last_response_audio, dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32768.0

            # Software volume boost
            if self.config.volume_boost != 1.0:
                audio_f32 = audio_f32 * self.config.volume_boost
                audio_f32 = np.clip(audio_f32, -1.0, 1.0)

            # Resample if device expects a different rate
            play_rate = rate
            output_dev = self.config.output_device
            if output_dev is not None:
                dev_info = sd.query_devices(output_dev)
                dev_rate = int(dev_info["default_samplerate"])
                if dev_rate != rate:
                    try:
                        import soxr
                        audio_f32 = soxr.resample(audio_f32, rate, dev_rate)
                        play_rate = dev_rate
                        log.debug("Resampled %dHz -> %dHz", rate, dev_rate)
                    except ImportError:
                        log.debug("soxr not available, playing at original rate")

            # Stereo conversion for ReSpeaker HAT (2-channel output)
            if output_dev is not None:
                try:
                    dev_info = sd.query_devices(output_dev)
                    out_channels = dev_info["max_output_channels"]
                    if out_channels >= 2 and audio_f32.ndim == 1:
                        audio_f32 = np.column_stack([audio_f32, audio_f32])
                except Exception:
                    pass

            duration_s = len(audio_f32) / play_rate if audio_f32.ndim == 1 else len(audio_f32) / play_rate
            log.info("Playing %.1fs of TTS audio (rate=%d, boost=%.1fx)", duration_s, play_rate, self.config.volume_boost)
            sd.play(audio_f32, samplerate=play_rate, device=output_dev, blocking=False)

            # Track when this playback will finish (with BT latency buffer)
            self._playback_end_time = time.monotonic() + duration_s + 0.5

            if is_last:
                await self._wait_for_playback_and_complete()
        except Exception as e:
            log.warning("Could not play audio: %s", e)
            if is_last:
                self._playback_end_time = 0
                self._on_response_complete()

    async def _wait_for_playback_and_complete(self) -> None:
        """Wait until all audio has finished playing, then transition."""
        remaining = self._playback_end_time - time.monotonic()
        if remaining > 0:
            log.info("Waiting %.1fs for playback to finish", remaining)
            await asyncio.sleep(remaining)
        self._playback_end_time = 0

        # Check for deferred follow-up listen request
        if self._pending_listen is not None:
            pending = self._pending_listen
            self._pending_listen = None
            self._start_followup_listening(pending)
        else:
            self._on_response_complete()

    async def _on_listen(self, msg: dict) -> None:
        """Handle listen request from server (conversational follow-up)."""
        # If audio is still playing, defer listen until playback completes.
        # Otherwise TTS echo gets picked up as "speech" and consumes the window.
        if self._playback_end_time > time.monotonic():
            self._pending_listen = msg
            log.info("Listen request deferred until playback completes")
            return
        self._start_followup_listening(msg)

    def _start_followup_listening(self, msg: dict) -> None:
        """Begin follow-up listening (after playback is done)."""
        timeout_s = msg.get("timeout_s", 10)
        log.info("Follow-up listening (timeout=%ds)", timeout_s)
        self._set_state(PipelineState.LISTENING)
        self._utterance_chunks = []
        self._listening_frames = 0
        self._speech_detected = False
        self._followup_timeout_s = timeout_s
        if self._eou:
            self._eou.reset()
        if self._vad:
            self._vad.reset()

    async def _on_done(self, msg: dict) -> None:
        """Handle done message from server."""
        log.info("Server done processing")
        # If we're still waiting for a response, go back to idle
        if self.state == PipelineState.PROCESSING:
            self._on_response_complete()

    async def _ensure_connected(self, retries: int = 3, delay: float = 2.0) -> bool:
        """Ensure WebSocket is connected, retrying if needed."""
        if self._client and self._client.connected:
            return True
        for attempt in range(1, retries + 1):
            log.info("Connecting to server %s (attempt %d/%d)...",
                     self.config.server_url, attempt, retries)
            try:
                await self._client.connect()
                self._recv_task = asyncio.create_task(self._client.receive_loop())

                # Register satellite with server
                register_msg = json.dumps({
                    "type": "register",
                    "satellite_id": self.config.satellite_id,
                    "room": self.config.room,
                    "http_port": self.config.http_port,
                })
                await self._client._ws.send(register_msg)
                log.info("Registered as '%s' in room '%s'",
                         self.config.satellite_id, self.config.room)
                return True
            except Exception as exc:
                log.warning("Connection failed: %s", exc)
                if attempt < retries:
                    await asyncio.sleep(delay)
        log.error("Could not connect after %d attempts", retries)
        return False

    async def _send_utterance(self, audio: np.ndarray) -> None:
        """Send utterance to server and wait for response."""
        if not await self._ensure_connected():
            log.warning("Skipping utterance — no server connection")
            return

        try:
            await self._client.send(AudioStartMsg(
                satellite_id=self.config.satellite_id,
                sample_rate=self.config.sample_rate,
                channels=self.config.channels,
                format=self.config.dtype,
                pre_roll_ms=int(self.config.pre_roll_s * 1000),
            ))
            await self._client.send(AudioChunkMsg(
                payload=audio.tobytes(),
                sequence=0,
                vad_probability=1.0,
            ))
            await self._client.send(AudioEndMsg(reason="eou"))
            await asyncio.sleep(0.1)
        except Exception:
            log.exception("Failed to send utterance to server")

    async def run(self) -> None:
        """Main async loop: capture audio, detect wake word, run VAD/EOU."""
        import sounddevice as sd

        # Create VAD via factory (auto-selects Silero or WebRTC)
        self._vad = create_vad(
            backend=self.config.vad_backend,
            threshold=self.config.vad_threshold,
            aggressiveness=self.config.vad_aggressiveness,
        )

        # Adjust frame size to match VAD backend
        vad_frame_samples = self._vad.FRAME_SAMPLES
        vad_frame_ms = int(vad_frame_samples * 1000 / self.config.sample_rate)
        log.info("VAD frame: %d samples (%dms)", vad_frame_samples, vad_frame_ms)

        self._eou = EOUDetector(
            silence_timeout_frames=int(self.config.eou_silence_s * 1000 / vad_frame_ms),
            hard_timeout_frames=int(self.config.eou_hard_timeout_s * 1000 / vad_frame_ms),
            speech_threshold=self.config.vad_threshold,
        )

        # Grace period: 0.5s before EOU can fire after wake word
        self._grace_frames = int(0.5 * 1000 / vad_frame_ms)
        log.info("EOU grace period: %d frames (%.1fs)", self._grace_frames, 0.5)

        # LEDs
        self._leds = create_leds(no_leds=self.config.no_leds)

        # Load wake word model
        interpreter = _TFLiteInterpreter(
            model_path=str(self.config.wake_model_path)
        )
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()

        # Query actual device channels (same fix as run_coral.py)
        dev_info = sd.query_devices(self.config.audio_device)
        channels = dev_info['max_input_channels']
        log.info(
            "Audio device %d: %s (%d channels)",
            self.config.audio_device, dev_info['name'], channels,
        )

        # Audio stream via sounddevice
        audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def audio_callback(indata, frames, time_info, status):
            if not status:
                loop.call_soon_threadsafe(audio_queue.put_nowait, indata[:, 0].copy())

        stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=channels,
            blocksize=vad_frame_samples,
            device=self.config.audio_device,
            callback=audio_callback,
            dtype=np.float32,
        )

        # Server client (connects lazily on first utterance via _ensure_connected)
        self._client = SatelliteClient(
            server_url=self.config.server_url,
            on_transcript=self._on_transcript,
            on_audio_response=self._on_audio_response,
            on_intent=self._on_intent,
            on_listen=self._on_listen,
            on_done=self._on_done,
            on_wake_result=self._on_wake_result,
        )
        self._recv_task = None

        log.info("Starting satellite pipeline (state=%s) -- server=%s",
                 self.state.name, self.config.server_url)

        # Connect to server at startup (needed for verify_wake)
        await self._ensure_connected()

        try:
            with stream:
                wake_accumulator = np.zeros(0, dtype=np.float32)
                sample_count = 0

                while not self._stop_event.is_set():
                    try:
                        frame = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue

                    self.ring_buffer.write(frame)
                    sample_count += len(frame)

                    if self.state == PipelineState.IDLE:
                        # Accumulate for wake word (needs CLIP_SAMPLES)
                        wake_accumulator = np.concatenate([wake_accumulator, frame])
                        if len(wake_accumulator) >= CLIP_SAMPLES:
                            chunk = wake_accumulator[:CLIP_SAMPLES]
                            wake_accumulator = wake_accumulator[CLIP_SAMPLES:]

                            # Run MFCC + inference in thread to avoid blocking event loop
                            score = await asyncio.to_thread(
                                _run_wake_inference,
                                chunk, interpreter, input_details, output_details,
                            )

                            if score > 0.3:
                                log.info("Wake score: %.3f (threshold=%.2f)", score, self.config.wake_confidence)

                            if self._wake_processor.should_trigger(score, sample_count):
                                self._wake_processor.record_trigger(sample_count)
                                self._on_wake_word(audio_queue)

                    elif self.state == PipelineState.LISTENING:
                        # Run VAD on each frame
                        prob = self._vad.process_frame(frame)
                        self._utterance_chunks.append(frame)
                        if prob > self.config.vad_threshold:
                            self._speech_detected = True
                        self._listening_frames += 1

                        # Followup timeout: if server asked us to listen but
                        # no speech detected, go back to IDLE
                        if self._followup_timeout_s > 0:
                            elapsed = self._listening_frames * vad_frame_ms / 1000
                            if elapsed > self._followup_timeout_s and prob < self.config.vad_threshold:
                                log.info("Follow-up timeout (%.1fs) -- back to IDLE", elapsed)
                                self._followup_timeout_s = 0
                                self._set_state(PipelineState.IDLE)
                                continue

                        # Grace period: don't check EOU until user has time to speak
                        if self._listening_frames <= self._grace_frames:
                            # Still in grace period — feed EOU but ignore result
                            self._eou.update(prob)
                            continue

                        result = self._eou.update(prob)

                        if result == EOUResult.END_OF_UTTERANCE:
                            self._followup_timeout_s = 0
                            self._on_end_of_utterance(reason="eou")
                        elif result == EOUResult.HARD_TIMEOUT:
                            self._followup_timeout_s = 0
                            self._on_end_of_utterance(reason="timeout")

                    elif self.state == PipelineState.PROCESSING:
                        # Skip if VAD never detected speech (false wake)
                        if not self._speech_detected:
                            duration = sum(len(c) for c in self._utterance_chunks) / self.config.sample_rate
                            log.info("No speech detected in %.1fs utterance — false wake, back to IDLE", duration)
                            self._set_state(PipelineState.IDLE)
                            continue

                        # Skip if wake word STT verification failed
                        if self._wake_verified is False:
                            log.info("Wake word not verified by STT — false wake, back to IDLE")
                            self._set_state(PipelineState.IDLE)
                            continue

                        # Send audio to server, then wait for response
                        utterance = np.concatenate(self._utterance_chunks)
                        duration = len(utterance) / self.config.sample_rate
                        log.info("Sending %.1fs utterance to server", duration)
                        await self._send_utterance(utterance)
                        self._set_state(PipelineState.RESPONDING)
                        self._response_wait_start = time.monotonic()

                    elif self.state == PipelineState.RESPONDING:
                        # Waiting for server response — timeout after 120s
                        elapsed = time.monotonic() - self._response_wait_start
                        if elapsed > 120:
                            log.warning("Response timeout (%.0fs) -- back to IDLE", elapsed)
                            self._set_state(PipelineState.IDLE)
        finally:
            if self._leds:
                self._leds.close()
            if self._recv_task:
                self._recv_task.cancel()
            if self._client:
                await self._client.disconnect()
            log.info("Pipeline stopped")

    async def run_for(self, duration_s: float) -> None:
        """Run pipeline for a fixed duration (for testing)."""
        self._stop_event = asyncio.Event()
        task = asyncio.create_task(self.run())
        await asyncio.sleep(duration_s)
        self._stop_event.set()
        await task


def main() -> None:
    """Entry point for satellite pipeline."""
    parser = argparse.ArgumentParser(description="Voice satellite pipeline")
    parser.add_argument("--server", default="ws://localhost:8765", help="Server WebSocket URL")
    parser.add_argument("--device", type=int, default=0, help="Audio input device index")
    parser.add_argument("--output-device", type=int, default=None, help="Audio output device index")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/wakeword.tflite"),
        help="Wake word model path",
    )
    parser.add_argument("--satellite-id", default="satellite", help="Satellite identifier")
    parser.add_argument("--room", default="unknown", help="Room name for satellite registry")
    parser.add_argument("--volume-boost", type=float, default=3.0, help="Software volume multiplier")
    parser.add_argument("--no-leds", action="store_true", help="Disable LED control")
    parser.add_argument(
        "--vad-backend", default="auto",
        choices=["auto", "silero", "webrtc"],
        help="VAD backend",
    )
    parser.add_argument("--wake-confidence", type=float, default=0.85, help="Wake word confidence threshold")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP push API port")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        from satellite.wakeword import list_audio_devices
        for idx, name in list_audio_devices().items():
            print(f"  {idx}: {name}")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    config = SatelliteConfig(
        server_url=args.server,
        satellite_id=args.satellite_id,
        wake_model_path=str(args.model),
        audio_device=args.device,
        output_device=args.output_device,
        volume_boost=args.volume_boost,
        wake_confidence=args.wake_confidence,
        no_leds=args.no_leds,
        vad_backend=args.vad_backend,
        room=args.room,
        http_port=args.http_port,
    )
    pipeline = SatellitePipeline(config)
    log.info("Satellite '%s' (room=%s) connecting to %s",
             config.satellite_id, config.room, config.server_url)
    try:
        asyncio.run(pipeline.run())
    except KeyboardInterrupt:
        log.info("Satellite stopped by user")


if __name__ == "__main__":
    main()
