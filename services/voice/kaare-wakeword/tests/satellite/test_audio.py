# tests/satellite/test_audio.py
"""Tests for ring buffer and audio capture."""
from __future__ import annotations

import numpy as np
import pytest

from satellite.audio import RingBuffer


class TestRingBuffer:
    def test_write_and_read_back(self):
        """Write samples, read them back."""
        buf = RingBuffer(max_samples=1000, sample_rate=16000)
        data = np.arange(500, dtype=np.float32)
        buf.write(data)
        result = buf.read_last(500)
        np.testing.assert_array_equal(result, data)

    def test_read_last_fewer_than_written(self):
        """Read fewer samples than were written."""
        buf = RingBuffer(max_samples=1000, sample_rate=16000)
        data = np.arange(500, dtype=np.float32)
        buf.write(data)
        result = buf.read_last(100)
        np.testing.assert_array_equal(result, data[400:])

    def test_wrap_around(self):
        """Buffer wraps around correctly when full."""
        buf = RingBuffer(max_samples=100, sample_rate=16000)
        # Write 150 samples into a 100-sample buffer
        data1 = np.arange(100, dtype=np.float32)
        buf.write(data1)
        data2 = np.arange(100, 150, dtype=np.float32)
        buf.write(data2)
        # Last 100 samples should be 50..149
        result = buf.read_last(100)
        expected = np.arange(50, 150, dtype=np.float32)
        np.testing.assert_array_equal(result, expected)

    def test_read_more_than_available_pads_with_zeros(self):
        """Reading more than available returns zero-padded result."""
        buf = RingBuffer(max_samples=1000, sample_rate=16000)
        data = np.ones(100, dtype=np.float32)
        buf.write(data)
        result = buf.read_last(200)
        assert len(result) == 200
        np.testing.assert_array_equal(result[:100], 0.0)
        np.testing.assert_array_equal(result[100:], 1.0)

    def test_samples_written_counter(self):
        """samples_written tracks total samples ever written."""
        buf = RingBuffer(max_samples=100, sample_rate=16000)
        buf.write(np.zeros(50, dtype=np.float32))
        buf.write(np.zeros(75, dtype=np.float32))
        assert buf.samples_written == 125
