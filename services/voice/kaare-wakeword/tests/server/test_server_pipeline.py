"""Test that server pipeline calls STT -> NLU -> TTS in order."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from server.server import ServerPipeline
from server.config import ServerConfig


class TestServerPipeline:
    def test_pipeline_initializes(self):
        config = ServerConfig(whisper_model="tiny")
        with patch("server.server.WhisperSTT"), \
             patch("server.server.NLUEngine"), \
             patch("server.server.PiperTTS"):
            pipeline = ServerPipeline(config)
            assert pipeline is not None

    @patch("server.server.PiperTTS")
    @patch("server.server.NLUEngine")
    @patch("server.server.WhisperSTT")
    def test_process_audio_calls_stt(self, mock_stt_cls, mock_nlu_cls, mock_tts_cls):
        config = ServerConfig()
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = MagicMock(
            text="turn on the light", language="en", confidence=0.9
        )
        mock_stt_cls.return_value = mock_stt

        mock_nlu = MagicMock()
        mock_nlu.process_local.return_value = MagicMock(
            action="ha_call_service", entities={}, response_text="OK",
            confidence=0.8, source="ollama"
        )
        mock_nlu_cls.return_value = mock_nlu

        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b"\x00" * 100
        mock_tts_cls.return_value = mock_tts

        pipeline = ServerPipeline(config)

        audio = np.zeros(16000, dtype=np.float32)
        result = pipeline.process(audio)

        mock_stt.transcribe.assert_called_once()
        mock_nlu.process_local.assert_called_once()
        assert result is not None
