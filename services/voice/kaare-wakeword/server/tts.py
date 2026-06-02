"""TTS with Google Translate TTS + disk cache.

Uses gTTS (same engine as Home Assistant's Google Translate TTS).
Responses are cached on disk so repeated phrases are instant.

Fallback chain: gTTS -> piper -> espeak-ng -> silence.
"""
from __future__ import annotations

import hashlib
import io
import logging
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Cache directory for TTS audio
_CACHE_DIR = Path.home() / ".cache" / "kaare-tts"


class PiperTTS:
    """TTS with Google Translate, disk caching, and local fallbacks.

    Args:
        voice: Piper voice name (used if piper is available as fallback).
        sample_rate: Output sample rate for raw PCM.
        language: Language code for gTTS (default: 'no' for Norwegian).
        cache_dir: Directory for cached audio files.
    """

    def __init__(
        self,
        voice: str = "en_US-lessac-medium",
        sample_rate: int = 22050,
        language: str = "no",
        cache_dir: Path | None = None,
    ):
        self.voice = voice
        self.sample_rate = sample_rate
        self.language = language
        self._cache_dir = cache_dir or _CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._gtts_available: bool | None = None  # lazy check

    def _cache_key(self, text: str) -> str:
        """Generate cache filename from text + language."""
        h = hashlib.md5(f"{self.language}:{text}".encode()).hexdigest()[:16]
        return h

    def _get_cached(self, text: str) -> bytes | None:
        """Return cached PCM audio if it exists."""
        path = self._cache_dir / f"{self._cache_key(text)}.pcm"
        if path.exists():
            log.debug("TTS cache hit: %s", text[:40])
            return path.read_bytes()
        return None

    def _put_cache(self, text: str, pcm: bytes) -> None:
        """Store PCM audio in cache."""
        path = self._cache_dir / f"{self._cache_key(text)}.pcm"
        path.write_bytes(pcm)

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to raw PCM audio bytes (int16).

        Tries gTTS first (Google Translate TTS), then piper, then espeak-ng.
        Results are cached on disk.
        """
        # Check cache first
        cached = self._get_cached(text)
        if cached is not None:
            return cached

        # Try gTTS (Google Translate TTS — same as Home Assistant)
        pcm = self._gtts_synthesize(text)
        if pcm:
            self._put_cache(text, pcm)
            return pcm

        # Fallback to piper
        pcm = self._piper_synthesize(text)
        if pcm:
            self._put_cache(text, pcm)
            return pcm

        # Fallback to espeak-ng
        pcm = self._espeak_fallback(text)
        if pcm:
            self._put_cache(text, pcm)
            return pcm

        return self._silence_fallback(text)

    def _gtts_synthesize(self, text: str) -> bytes | None:
        """Synthesize with Google Translate TTS (gTTS)."""
        if self._gtts_available is False:
            return None
        try:
            from gtts import gTTS
            from pydub import AudioSegment

            self._gtts_available = True

            tts = gTTS(text=text, lang=self.language, slow=False)
            mp3_buf = io.BytesIO()
            tts.write_to_fp(mp3_buf)
            mp3_buf.seek(0)

            # Convert MP3 -> raw PCM int16 at self.sample_rate
            audio = AudioSegment.from_mp3(mp3_buf)
            audio = audio.set_frame_rate(self.sample_rate).set_channels(1).set_sample_width(2)
            pcm = audio.raw_data

            log.info("gTTS synthesized %d bytes for: %s", len(pcm), text[:40])
            return pcm

        except ImportError:
            self._gtts_available = False
            log.warning("gTTS/pydub not installed, trying piper fallback")
            return None
        except Exception as exc:
            log.warning("gTTS failed: %s", exc)
            return None

    def _piper_synthesize(self, text: str) -> bytes | None:
        """Synthesize with piper (local neural TTS)."""
        try:
            result = subprocess.run(
                ["piper", "--model", self.voice, "--output-raw"],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                log.info("Piper synthesized %d bytes for: %s", len(result.stdout), text[:40])
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def _espeak_fallback(self, text: str) -> bytes | None:
        """Fallback to espeak-ng."""
        try:
            result = subprocess.run(
                ["espeak-ng", "--stdout", text],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                log.info("espeak-ng synthesized %d bytes for: %s", len(result.stdout), text[:40])
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def _silence_fallback(self, text: str) -> bytes:
        """Last resort: return silence."""
        duration_s = max(0.5, len(text) * 0.06)
        n_samples = int(duration_s * self.sample_rate)
        return np.zeros(n_samples, dtype=np.int16).tobytes()

    def synthesize_to_float32(self, text: str, target_rate: int = 16000) -> np.ndarray:
        """Synthesize and convert to float32 at target sample rate."""
        raw = self.synthesize(text)
        if not raw:
            return np.zeros(target_rate, dtype=np.float32)

        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if target_rate != self.sample_rate:
            import scipy.signal
            n_out = int(len(audio) * target_rate / self.sample_rate)
            audio = scipy.signal.resample(audio, n_out)

        return audio
