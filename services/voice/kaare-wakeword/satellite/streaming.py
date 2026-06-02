"""WebSocket streaming client for satellite-to-server communication.

Handles the WebSocket protocol: sending audio chunks to the server
and receiving transcript/TTS responses back.

Message protocol (satellite -> server):
    audio_start: Begin a new utterance stream.
    audio_chunk: PCM audio data with VAD probability.
    audio_end:   End of utterance (reason: eou, timeout, cancel).

Message protocol (server -> satellite):
    transcript:     STT result (partial or final).
    intent:         NLU result (action + entities).
    audio_response: TTS audio to play back.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class AudioStartMsg:
    satellite_id: str
    sample_rate: int
    channels: int
    format: str
    pre_roll_ms: int

    def to_json(self) -> str:
        return json.dumps({
            "type": "audio_start",
            "satellite_id": self.satellite_id,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "format": self.format,
            "pre_roll_ms": self.pre_roll_ms,
        })


@dataclass
class AudioChunkMsg:
    payload: bytes
    sequence: int
    vad_probability: float

    def to_json(self) -> str:
        return json.dumps({
            "type": "audio_chunk",
            "payload": base64.b64encode(self.payload).decode("ascii"),
            "sequence": self.sequence,
            "vad_probability": self.vad_probability,
        })


@dataclass
class AudioEndMsg:
    reason: str  # "eou", "timeout", "cancel"

    def to_json(self) -> str:
        return json.dumps({
            "type": "audio_end",
            "reason": self.reason,
        })


class SatelliteClient:
    """Async WebSocket client for satellite streaming.

    Manages the connection lifecycle and provides methods for
    sending audio and receiving server responses.

    Args:
        server_url: WebSocket server URL.
        on_transcript: Callback for transcript messages.
        on_audio_response: Callback for TTS audio responses.
        on_intent: Callback for intent messages.
    """

    def __init__(
        self,
        server_url: str,
        on_transcript: Callable[[dict], Coroutine] | None = None,
        on_audio_response: Callable[[dict], Coroutine] | None = None,
        on_intent: Callable[[dict], Coroutine] | None = None,
        on_listen: Callable[[dict], Coroutine] | None = None,
        on_done: Callable[[dict], Coroutine] | None = None,
        on_wake_result: Callable[[dict], Coroutine] | None = None,
    ):
        self.server_url = server_url
        self._ws = None
        self._on_transcript = on_transcript
        self._on_audio_response = on_audio_response
        self._on_intent = on_intent
        self._on_listen = on_listen
        self._on_done = on_done
        self._on_wake_result = on_wake_result

    @property
    def connected(self) -> bool:
        if self._ws is None:
            return False
        from websockets.protocol import State
        return self._ws.state == State.OPEN

    async def connect(self) -> None:
        """Connect to the server."""
        import websockets
        self._ws = await websockets.connect(
            self.server_url,
            ping_interval=60,
            ping_timeout=120,
            max_size=10 * 1024 * 1024,  # 10MB — TTS audio can be large
        )
        log.info("Connected to server: %s", self.server_url)

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: AudioStartMsg | AudioChunkMsg | AudioEndMsg) -> None:
        """Send a typed message to the server."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(msg.to_json())

    async def send_json(self, data: dict) -> None:
        """Send a raw JSON dict to the server."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(data))

    async def receive_loop(self) -> None:
        """Listen for server messages and dispatch to callbacks."""
        if not self._ws:
            raise RuntimeError("Not connected")
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Invalid JSON from server: %s", raw[:100])
                continue
            msg_type = msg.get("type")
            if msg_type == "transcript" and self._on_transcript:
                await self._on_transcript(msg)
            elif msg_type == "audio_response" and self._on_audio_response:
                await self._on_audio_response(msg)
            elif msg_type == "intent" and self._on_intent:
                await self._on_intent(msg)
            elif msg_type == "listen" and self._on_listen:
                await self._on_listen(msg)
            elif msg_type == "done" and self._on_done:
                await self._on_done(msg)
            elif msg_type == "wake_result" and self._on_wake_result:
                await self._on_wake_result(msg)
            else:
                log.debug("Unknown message type: %s", msg_type)
