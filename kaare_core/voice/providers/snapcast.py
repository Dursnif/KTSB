"""Snapcast provider — injiserer TTS-lyd via TCP stream source.

Krever at snapserver er konfigurert med en TCP-kilde som matcher Pipers output:
  source = tcp://0.0.0.0:<port>?name=kare&sampleformat=<rate>:16:<ch>&codec=pcm

Eksempel (Piper default 22050 Hz, mono):
  source = tcp://0.0.0.0:4953?name=kare&sampleformat=22050:16:1&codec=pcm

Port i nodes.yaml (api_port) må matche porten i snapserver.conf.
"""

import asyncio
import logging
import wave
from pathlib import Path

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)

_SEND_CHUNK = 4096  # bytes per write


class SnapcastProvider(BaseVoiceProvider):
    def __init__(self, config: dict, media_base_url: str):
        self._config = config
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        host = node_config.get("host", "")
        port = int(node_config.get("api_port", 4953))
        if not host:
            log.error("Snapcast provider: mangler host i node-config")
            return

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5.0
            )
        except (OSError, asyncio.TimeoutError) as exc:
            log.error(
                "Snapcast: kan ikke koble til %s:%s — %s. "
                "Sjekk at snapserver.conf har TCP-kilde på denne porten.",
                host, port, exc,
            )
            return

        try:
            with wave.open(str(wav_path), "rb") as wf:
                rate = wf.getframerate()
                width = wf.getsampwidth()
                channels = wf.getnchannels()
                log.info(
                    "Snapcast %s:%s <- %s (%dHz %dbit %dch) — "
                    "snapserver.conf må ha sampleformat=%d:%d:%d",
                    host, port, wav_path.name,
                    rate, width * 8, channels,
                    rate, width * 8, channels,
                )
                frames_per_chunk = _SEND_CHUNK // (width * channels)
                while True:
                    frames = wf.readframes(frames_per_chunk)
                    if not frames:
                        break
                    writer.write(frames)
                    await writer.drain()
        except Exception as exc:
            log.error("Snapcast: feil ved sending til %s:%s — %s", host, port, exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
