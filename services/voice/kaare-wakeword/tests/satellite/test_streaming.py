"""Tests for WebSocket streaming client."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from satellite.streaming import SatelliteClient, AudioStartMsg, AudioChunkMsg, AudioEndMsg


class TestMessageSerialization:
    def test_audio_start_serializes(self):
        msg = AudioStartMsg(
            satellite_id="test-sat",
            sample_rate=16000,
            channels=1,
            format="float32",
            pre_roll_ms=500,
        )
        data = msg.to_json()
        parsed = json.loads(data)
        assert parsed["type"] == "audio_start"
        assert parsed["satellite_id"] == "test-sat"
        assert parsed["sample_rate"] == 16000

    def test_audio_chunk_serializes(self):
        audio = np.zeros(480, dtype=np.float32)
        msg = AudioChunkMsg(
            payload=audio.tobytes(),
            sequence=0,
            vad_probability=0.1,
        )
        data = msg.to_json()
        parsed = json.loads(data)
        assert parsed["type"] == "audio_chunk"
        assert parsed["sequence"] == 0

    def test_audio_end_serializes(self):
        msg = AudioEndMsg(reason="eou")
        data = msg.to_json()
        parsed = json.loads(data)
        assert parsed["type"] == "audio_end"
        assert parsed["reason"] == "eou"
