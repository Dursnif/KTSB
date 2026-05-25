"""Wyoming TCP server — tar imot satellitter, kjører STT → Kåre API → TTS → audio ut."""

import asyncio
import logging
import wave
from pathlib import Path
from typing import Awaitable, Callable, Optional

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.asr import Transcript
from wyoming.event import async_read_event, async_write_event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info

log = logging.getLogger(__name__)

# Active satellite connections: peer_ip → (reader, writer, write_lock)
_active_connections: dict[str, tuple[asyncio.StreamReader, asyncio.StreamWriter, asyncio.Lock]] = {}

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
                    await async_write_event(writer, info.event())
                    log.debug("Wyoming: describe → info sendt til %s", peer_host)

                elif AudioStart.is_type(event.type):
                    fmt = AudioStart.from_event(event)
                    audio_rate = fmt.rate
                    audio_width = fmt.width
                    audio_buffer = bytearray()
                    log.debug("Wyoming: audio-start %dHz/%dbit fra %s", audio_rate, audio_width * 8, peer_host)

                elif AudioChunk.is_type(event.type):
                    audio_buffer.extend(AudioChunk.from_event(event).audio)

                elif AudioStop.is_type(event.type):
                    if audio_buffer:
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
            await async_write_event(writer, Transcript(text=text).event())

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


async def _send_wav(writer: asyncio.StreamWriter, wav_path: Path) -> None:
    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        chunk_frames = rate // 4  # 250ms per chunk

        await async_write_event(writer, AudioStart(rate=rate, width=width, channels=channels).event())

        while True:
            frames = wf.readframes(chunk_frames)
            if not frames:
                break
            await async_write_event(
                writer,
                AudioChunk(rate=rate, width=width, channels=channels, audio=frames).event(),
            )

        await async_write_event(writer, AudioStop().event())
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
