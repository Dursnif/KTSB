"""Integration test: satellite -> server round-trip."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from server.server import VoiceServer
from server.config import ServerConfig
from satellite.streaming import SatelliteClient, AudioStartMsg, AudioChunkMsg, AudioEndMsg


@pytest.mark.asyncio
async def test_satellite_to_server_roundtrip():
    """Send audio chunks to server and get a transcript back."""
    # Start server
    server_config = ServerConfig(host="127.0.0.1", port=0)
    server = VoiceServer(server_config)
    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.3)

    try:
        server_url = f"ws://127.0.0.1:{server.port}"
        transcript_received = asyncio.Event()
        transcript_text = ""

        async def on_transcript(msg):
            nonlocal transcript_text
            transcript_text = msg.get("text", "")
            transcript_received.set()

        client = SatelliteClient(server_url, on_transcript=on_transcript)
        await client.connect()

        # Start receive loop in background
        recv_task = asyncio.create_task(client.receive_loop())

        # Send audio
        await client.send(AudioStartMsg(
            satellite_id="test",
            sample_rate=16000,
            channels=1,
            format="float32",
            pre_roll_ms=500,
        ))

        # Send a 1-second chunk of silence
        silence = np.zeros(16000, dtype=np.float32)
        await client.send(AudioChunkMsg(
            payload=silence.tobytes(),
            sequence=0,
            vad_probability=0.0,
        ))

        await client.send(AudioEndMsg(reason="eou"))

        # Wait for transcript
        await asyncio.wait_for(transcript_received.wait(), timeout=2.0)
        assert "16000" in transcript_text  # placeholder includes sample count

        await client.disconnect()
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass
    finally:
        server.stop()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
