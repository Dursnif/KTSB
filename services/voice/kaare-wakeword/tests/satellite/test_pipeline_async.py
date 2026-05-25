"""Tests for satellite pipeline async methods."""
from __future__ import annotations

import asyncio

import pytest

from satellite.config import SatelliteConfig
from satellite.pipeline import SatellitePipeline, PipelineState


def test_pipeline_has_run_methods():
    """Pipeline should have run() and run_for() async methods."""
    config = SatelliteConfig()
    pipeline = SatellitePipeline(config)
    assert hasattr(pipeline, 'run')
    assert hasattr(pipeline, 'run_for')
    assert asyncio.iscoroutinefunction(pipeline.run)
    assert asyncio.iscoroutinefunction(pipeline.run_for)


def test_pipeline_has_wake_model_path():
    """Config should include wake_model_path."""
    config = SatelliteConfig()
    assert config.wake_model_path == "models/wakeword.tflite"
