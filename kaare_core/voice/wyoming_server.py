"""Wyoming TCP server — tar imot satellitter, kjører STT → Kåre API → TTS → audio ut."""

import asyncio
import logging
import os
import wave
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.asr import Transcript
from wyoming.event import async_read_event, async_write_event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info

log = logging.getLogger(__name__)

# Active satellite connections: peer_ip → (reader, writer, write_lock)
_active_connections: dict[str, tuple[asyncio.StreamReader, asyncio.StreamWriter, asyncio.Lock]] = {}

# --- Optional research tap: button-triggered multi-source wake-word recording ---
# Set KAARE_WYOMING_RECORD_DIR to enable. When enabled, every AudioStart/AudioStop
# round (button press/release) is saved as one clip per source (the satellite's own
# mic, plus any local mics configured via KAARE_WYOMING_RECORD_LOCAL_MICS) instead
# of running the normal STT->Kåre->TTS pipeline. Unset (default): zero behavior
# change. Origin: built for P90 Phase 6 wake-word training data collection — kept
# as a generic opt-in capability for anyone training a custom wake word, not tied
# to any one household's hardware.
_RECORD_DIR_ENV = "KAARE_WYOMING_RECORD_DIR"
_RECORD_PERSON_ENV = "KAARE_WYOMING_RECORD_PERSON"
_RECORD_LOCAL_MICS_ENV = "KAARE_WYOMING_RECORD_LOCAL_MICS"


def _parse_local_mics(raw: str) -> dict[str, str]:
    """Parse "label=alsa_dev;label2=alsa_dev2" (";"-separated pairs since ALSA
    device names like "plughw:2,0" already contain a comma). Unset/empty env
    var → no local mics, only the satellite's own audio is recorded."""
    mics: dict[str, str] = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        label, _, alsa_dev = pair.partition("=")
        if label.strip() and alsa_dev.strip():
            mics[label.strip()] = alsa_dev.strip()
    return mics


_RECORD_LOCAL_MICS = _parse_local_mics(os.environ.get(_RECORD_LOCAL_MICS_ENV, ""))
_RECORD_MAX_SEC = int(os.environ.get("KAARE_WYOMING_RECORD_MAX_SEC", "10"))  # safety cap per round; stopped earlier by AudioStop in practice
_RECORD_LEAD_TRIM_SEC = 2.0  # skip this much audio right after AudioStart — the
# firmware's own button-press click sound plays through the device speaker and
# bleeds into the mic before it decays, on all 3 sources (device + local mics)
_RECORD_ACK_WAV_PATH = Path("/tmp/kaare_wyoming_record_ack.wav")


def _get_record_ack_wav() -> Path:
    """Short beep sent back after each recording round so the firmware doesn't
    time out waiting for a real STT->Kåre->TTS reply (which recording mode skips).
    Generated once, reused — no numpy dependency, stdlib array/wave only."""
    if _RECORD_ACK_WAV_PATH.exists():
        return _RECORD_ACK_WAV_PATH
    import array
    import math

    rate = 16000
    duration_s = 0.15
    freq = 880.0
    amplitude = 6000
    n_samples = int(rate * duration_s)
    samples = array.array(
        "h",
        (int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n_samples)),
    )
    with wave.open(str(_RECORD_ACK_WAV_PATH), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return _RECORD_ACK_WAV_PATH

SttCallback = Callable[[bytes, int], Awaitable[str]]
KaareCallback = Callable[[str, str], Awaitable[str]]
TtsCallback = Callable[[str], Path]


class WyomingServer:
    def __init__(
        self,
        host: str,
        port: int,
        stt_fn: SttCallback,
        kaare_fn: KaareCallback,
        tts_fn: TtsCallback,
    ):
        self._host = host
        self._port = port
        self._stt = stt_fn
        self._kaare = kaare_fn
        self._tts = tts_fn
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        log.info("Wyoming server aktiv på %s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            log.info("Wyoming server stoppet")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", ("unknown", 0))
        peer_host = str(peer[0])
        log.info("Wyoming: satellitt tilkoblet fra %s", peer_host)

        lock = asyncio.Lock()
        _active_connections[peer_host] = (reader, writer, lock)

        audio_buffer = bytearray()
        audio_rate = 16000
        audio_width = 2

        record_dir = os.environ.get(_RECORD_DIR_ENV)
        record_person = os.environ.get(_RECORD_PERSON_ENV, "user")
        local_recorders: dict[str, asyncio.subprocess.Process] = {}
        local_recorders_task: Optional[asyncio.Task] = None
        record_session_id = ""
        record_start_monotonic = 0.0

        try:
            while True:
                event = await async_read_event(reader)
                if event is None:
                    break

                if Describe.is_type(event.type):
                    info = Info(
                        asr=[AsrProgram(
                            name="kare-whisper",
                            description="Kåre Whisper STT",
                            attribution=Attribution(name="Kåre", url=""),
                            installed=True,
                            models=[AsrModel(
                                name="nb-whisper",
                                description="NbAiLab nb-whisper (Norwegian)",
                                attribution=Attribution(name="NbAiLab", url=""),
                                installed=True,
                                languages=["no"],
                            )],
                        )]
                    )
                    await async_write_event(info.event(), writer)
                    log.debug("Wyoming: describe → info sendt til %s", peer_host)

                elif AudioStart.is_type(event.type):
                    fmt = AudioStart.from_event(event)
                    audio_rate = fmt.rate
                    audio_width = fmt.width
                    audio_buffer = bytearray()
                    log.debug("Wyoming: audio-start %dHz/%dbit fra %s", audio_rate, audio_width * 8, peer_host)

                    if record_dir:
                        record_session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                        record_start_monotonic = asyncio.get_event_loop().time()
                        local_recorders = {}
                        local_recorders_task = asyncio.create_task(
                            _start_local_recorders_delayed(record_session_id, record_dir, record_person, local_recorders)
                        )
                    else:
                        local_recorders_task = None

                elif AudioChunk.is_type(event.type):
                    if record_dir:
                        elapsed = asyncio.get_event_loop().time() - record_start_monotonic
                        if elapsed >= _RECORD_LEAD_TRIM_SEC:
                            audio_buffer.extend(AudioChunk.from_event(event).audio)
                    else:
                        audio_buffer.extend(AudioChunk.from_event(event).audio)

                elif AudioStop.is_type(event.type):
                    if record_dir:
                        if local_recorders_task and not local_recorders_task.done():
                            local_recorders_task.cancel()
                        await _stop_local_recorders(local_recorders)
                        local_recorders = {}
                        if audio_buffer:
                            _save_recorded_clip(
                                record_dir, "voicepe", record_person, record_session_id,
                                bytes(audio_buffer), audio_rate, audio_width,
                            )
                        log.info(
                            "Wyoming: opptaksmodus — lagret runde %s (%d bytes fra %s)",
                            record_session_id, len(audio_buffer), peer_host,
                        )
                        # Firmware waits ~30s for a real reply and errors out if none
                        # comes — recording mode skips STT/Kåre/TTS, so send a short
                        # placeholder transcript + ack beep instead, same event shape
                        # the normal pipeline would send. (Tried LED-only/no-audio —
                        # the firmware's LED phase logic got stuck expecting a full
                        # reply cycle, so back to the beep. The bleed-into-recording
                        # problem this caused turned out to be the button-press click
                        # sound at the *start* of the round, not this beep — fixed by
                        # trimming the first _RECORD_LEAD_TRIM_SEC instead.)
                        await async_write_event(Transcript(text="[recording saved]").event(), writer)
                        async with lock:
                            await _send_wav(writer, _get_record_ack_wav())
                    elif audio_buffer:
                        asyncio.create_task(
                            self._pipeline(bytes(audio_buffer), audio_rate, peer_host, writer, lock)
                        )
                    audio_buffer = bytearray()

        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:
            log.error("Wyoming: feil fra %s: %s", peer_host, exc)
        finally:
            _active_connections.pop(peer_host, None)
            try:
                writer.close()
            except Exception:
                pass
            log.info("Wyoming: satellitt %s frakoblet", peer_host)

    async def _pipeline(
        self,
        audio_bytes: bytes,
        rate: int,
        peer_host: str,
        writer: asyncio.StreamWriter,
        lock: asyncio.Lock,
    ) -> None:
        try:
            log.info("Wyoming: pipeline for %s (%d bytes @ %dHz)", peer_host, len(audio_bytes), rate)

            text = await self._stt(audio_bytes, rate)
            if not text.strip():
                log.info("Wyoming: STT ga tom tekst fra %s", peer_host)
                return

            log.info("Wyoming: STT '%s' fra %s", text, peer_host)
            await async_write_event(Transcript(text=text).event(), writer)

            answer = await self._kaare(text, "")
            if not answer.strip():
                return

            log.info("Wyoming: svar '%s...' til %s", answer[:60], peer_host)

            loop = asyncio.get_event_loop()
            wav_path = await loop.run_in_executor(None, self._tts, answer)

            async with lock:
                await _send_wav(writer, wav_path)

            await asyncio.sleep(3)
            wav_path.unlink(missing_ok=True)

        except Exception as exc:
            log.error("Wyoming: pipeline feil for %s: %s", peer_host, exc)


def _record_out_path(record_dir: str, source: str, person: str, session_id: str) -> Path:
    out_dir = Path(record_dir) / source / person
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{person}_{session_id}.wav"


async def _start_local_recorders(
    session_id: str, record_dir: str, person: str
) -> dict[str, asyncio.subprocess.Process]:
    """P90 Phase 6 research tap: start arecord on both ai-pc mics for one button-press round."""
    procs: dict[str, asyncio.subprocess.Process] = {}
    for label, alsa_dev in _RECORD_LOCAL_MICS.items():
        out_path = _record_out_path(record_dir, label, person, session_id)
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-D", alsa_dev, "-f", "S16_LE", "-r", "16000", "-c", "1",
            "-d", str(_RECORD_MAX_SEC), str(out_path),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        procs[label] = proc
    return procs


async def _start_local_recorders_delayed(
    session_id: str, record_dir: str, person: str, out: dict[str, asyncio.subprocess.Process]
) -> None:
    """Waits _RECORD_LEAD_TRIM_SEC (so the button-press click sound decays before
    the local mics start), then starts them and fills `out` in place. Runs as a
    background task so it never blocks the event-read loop; cancelled from
    AudioStop if a round ends before this delay elapses."""
    await asyncio.sleep(_RECORD_LEAD_TRIM_SEC)
    procs = await _start_local_recorders(session_id, record_dir, person)
    out.update(procs)


async def _stop_local_recorders(procs: dict[str, asyncio.subprocess.Process]) -> None:
    """Graceful stop (SIGINT) — arecord only finalizes the WAV header on a clean exit."""
    for label, proc in procs.items():
        try:
            proc.send_signal(2)  # SIGINT
        except ProcessLookupError:
            continue
    for label, proc in procs.items():
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            log.warning("Wyoming: opptaksmodus — %s-mic svarte ikke på SIGINT, dreper", label)
            proc.kill()


def _save_recorded_clip(
    record_dir: str, source: str, person: str, session_id: str,
    audio_bytes: bytes, rate: int, width: int,
) -> None:
    out_path = _record_out_path(record_dir, source, person, session_id)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(audio_bytes)


async def _send_wav(writer: asyncio.StreamWriter, wav_path: Path) -> None:
    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        chunk_frames = rate // 4  # 250ms per chunk

        await async_write_event(AudioStart(rate=rate, width=width, channels=channels).event(), writer)

        while True:
            frames = wf.readframes(chunk_frames)
            if not frames:
                break
            await async_write_event(
                AudioChunk(rate=rate, width=width, channels=channels, audio=frames).event(),
                writer,
            )

        await async_write_event(AudioStop().event(), writer)
    log.info("Wyoming: WAV sendt (%s)", wav_path.name)


async def speak_to_satellite(host: str, wav_path: Path) -> bool:
    """Push TTS audio til en allerede-tilkoblet Wyoming-satellitt.

    Returnerer True hvis satellitten var tilkoblet og lyd ble sendt.
    Brukes av WyomingProvider.speak() når announce-verktøyet aktiveres.
    """
    conn = _active_connections.get(host)
    if not conn:
        return False

    _, writer, lock = conn
    try:
        async with lock:
            await _send_wav(writer, wav_path)
        return True
    except Exception as exc:
        log.error("Wyoming: speak push feil til %s: %s", host, exc)
        _active_connections.pop(host, None)
        return False
