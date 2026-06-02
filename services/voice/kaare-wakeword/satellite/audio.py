# satellite/audio.py
"""Audio capture and ring buffer.

The ring buffer stores the most recent N seconds of audio. Two consumers
(wake word detector and VAD) read from it independently. The buffer is
lock-free for a single writer / multiple readers pattern because only
the write pointer advances and readers only look at committed data.
"""
from __future__ import annotations

import numpy as np


class RingBuffer:
    """Fixed-size circular buffer for audio samples.

    Stores float32 mono audio. Supports reading the last N samples
    even when the buffer has wrapped around.

    Args:
        max_samples: Buffer capacity in samples.
        sample_rate: Audio sample rate (for time-based reads).
    """

    def __init__(self, max_samples: int, sample_rate: int = 16_000):
        self._buf = np.zeros(max_samples, dtype=np.float32)
        self._max = max_samples
        self._write_pos = 0
        self._samples_written = 0
        self.sample_rate = sample_rate

    @property
    def samples_written(self) -> int:
        return self._samples_written

    def write(self, data: np.ndarray) -> None:
        """Append samples to the buffer.

        If data is longer than the buffer, only the last max_samples
        are kept.
        """
        n = len(data)
        if n >= self._max:
            # Only keep the tail
            self._buf[:] = data[-self._max:]
            self._write_pos = 0
            self._samples_written += n
            return

        end = self._write_pos + n
        if end <= self._max:
            self._buf[self._write_pos:end] = data
        else:
            first = self._max - self._write_pos
            self._buf[self._write_pos:] = data[:first]
            self._buf[:n - first] = data[first:]
        self._write_pos = end % self._max
        self._samples_written += n

    def read_last(self, n_samples: int) -> np.ndarray:
        """Read the most recent n_samples from the buffer.

        If fewer than n_samples have been written, the result is
        zero-padded on the left (oldest side).
        """
        available = min(self._samples_written, self._max)
        to_read = min(n_samples, available)

        result = np.zeros(n_samples, dtype=np.float32)
        start = (self._write_pos - to_read) % self._max

        if start + to_read <= self._max:
            result[n_samples - to_read:] = self._buf[start:start + to_read]
        else:
            first = self._max - start
            result[n_samples - to_read:n_samples - to_read + first] = self._buf[start:]
            result[n_samples - to_read + first:] = self._buf[:to_read - first]

        return result
