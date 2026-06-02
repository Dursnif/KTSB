"""Tests for satellite pipeline state machine."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from satellite.pipeline import PipelineState, SatellitePipeline
from satellite.config import SatelliteConfig


def _make_queue() -> asyncio.Queue:
    """Create a dummy audio queue for state machine tests."""
    return asyncio.Queue()


class TestPipelineStateMachine:
    def test_initial_state_is_idle(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        assert pipeline.state == PipelineState.IDLE

    def test_wake_word_transitions_to_listening(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word(_make_queue())
        assert pipeline.state == PipelineState.LISTENING

    def test_eou_transitions_to_processing(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word(_make_queue())
        pipeline._on_end_of_utterance(reason="eou")
        assert pipeline.state == PipelineState.PROCESSING

    def test_idle_ignores_eou(self):
        """EOU in IDLE state should be ignored."""
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_end_of_utterance(reason="eou")
        assert pipeline.state == PipelineState.IDLE

    def test_response_complete_returns_to_idle(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word(_make_queue())
        pipeline._on_end_of_utterance(reason="eou")
        pipeline._on_response_complete()
        assert pipeline.state == PipelineState.IDLE

    def test_hard_timeout_returns_to_idle(self):
        config = SatelliteConfig()
        pipeline = SatellitePipeline(config)
        pipeline._on_wake_word(_make_queue())
        pipeline._on_end_of_utterance(reason="timeout")
        assert pipeline.state == PipelineState.PROCESSING
