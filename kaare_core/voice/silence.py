"""Adaptive end of speech (P138) — shared by the Wyoming server and the voice bridge's local (KTSB Direct) mic.

Pure: no I/O, no audio library, so both venvs and the tests can use it.
"""

import array
import statistics


class SilenceDetector:
    """End of speech (P138 T1) — one rule for every board and mic: Wyoming satellites and the KTSB Direct mic.

    Levels are relative to what this mic actually delivers, so a quiet far-field PDM mic and a loud headset both
    work: the noise floor is the typical level of the non-speech chunks (median — the quietest single chunk sits
    below where ordinary noise wanders, so «back to the floor» would never be reached), speech is clearly above it,
    and the end is
    `silence_sec` of audio back near the floor (or well below the speech level). No speech at all within
    `no_speech_sec` also ends the round, so a press with nothing said does not stream until the satellite's cap.
    """

    # Absolute floor for «speech» (mean-abs, full scale 1.0). Low on purpose: the Atom Echo's PDM mic has a noise floor
    # of ~0.0005 and quiet speech around 0.003–0.011 (measured 2026-10-18) — 0.004 hid most of a sentence. The real
    # decision is relative to the mic's own floor (START_FACTOR).
    MIN_SPEECH_LEVEL = 0.002
    START_FACTOR = 3.0  # speech = this many times the noise floor
    END_FLOOR_FACTOR = 2.0  # silence = back under this many times the floor …
    END_SPEECH_FACTOR = 0.25  # … or under this share of the speech level
    MIN_SPEECH_SEC = 0.2
    FLOOR_WINDOW = 200  # non-speech chunks kept for the floor median
    # Mics pop when they start (Atom PDM: 0.86 in the first chunk; the onboard analog codec: ~1.9 s with power save).
    # The first moment is recorded but not measured, or the pop becomes the «noise floor».
    WARMUP_SEC = 0.15
    PRIOR_WEIGHT = (
        20  # a known floor from the mic's previous round counts as this many quiet chunks
    )
    SPEECH_REASONS = ("silence", "late_speech")  # reasons that mean «there was speech — run STT»

    def __init__(
        self,
        silence_sec: float,
        rate: int,
        width: int,
        no_speech_sec: float = 6.0,
        prior_floor: float | None = None,
    ):
        """`prior_floor`: this mic's floor from its previous round (P138, 2026-10-18). Without it the floor is learnt
        from the first chunks — and someone who speaks right after the press makes their own voice the «noise»."""
        self.silence_sec = max(0.3, float(silence_sec))
        self.no_speech_sec = no_speech_sec
        self.bytes_per_sec = max(1, rate * width)
        self.width = width
        self.floor: float | None = None
        self._quiet: list[float] = []
        if prior_floor and prior_floor > 0:
            self.floor = float(prior_floor)
            self._quiet = [self.floor] * self.PRIOR_WEIGHT
        self._levels: list[
            tuple[float, float]
        ] = []  # (level, dur) after warm-up, for the late re-check
        self.speech_level = 0.0
        self.peak = 0.0  # loudest chunk — for the log, so a quiet mic can be told from silence
        self.speech_sec = 0.0
        self.silent_sec = 0.0
        self.total_sec = 0.0
        self.speech_started = False
        self.reason = ""

    def _level(self, chunk: bytes) -> float:
        if self.width != 2 or len(chunk) < 2:
            return 0.0
        samples = array.array("h", chunk[: len(chunk) // 2 * 2])
        return sum(abs(x) for x in samples) / len(samples) / 32768.0

    def feed(self, chunk: bytes) -> bool:
        """Add one int16 PCM chunk; True when the round should end (self.reason says why)."""
        return self.feed_level(self._level(chunk), len(chunk) / self.bytes_per_sec)

    def feed_level(self, level: float, dur: float) -> bool:
        """Add one chunk as its mean-abs level (0–1 full scale) and duration — for audio that is not int16 bytes,
        such as the float32 local mic."""
        self.total_sec += dur
        if self.total_sec <= self.WARMUP_SEC:
            return False
        self.peak = max(self.peak, level)
        if len(self._levels) < 4000:
            self._levels.append((level, dur))
        floor = self.floor or 0.0
        start_thr = max(floor * self.START_FACTOR, self.MIN_SPEECH_LEVEL)
        if level > 0 and (self.floor is None or level <= start_thr):
            self._quiet.append(level)
            del self._quiet[: -self.FLOOR_WINDOW]
            self.floor = statistics.median(self._quiet)
            floor = self.floor
            start_thr = max(floor * self.START_FACTOR, self.MIN_SPEECH_LEVEL)
        if not self.speech_started:
            if level > start_thr:
                self.speech_sec += dur
                self.speech_level = max(self.speech_level, level)
                if self.speech_sec >= self.MIN_SPEECH_SEC:
                    self.speech_started = True
            if not self.speech_started and self.total_sec >= self.no_speech_sec:
                self.reason = "late_speech" if self._late_speech() else "no_speech"
                return True
            return False
        if level > start_thr:
            self.speech_level = 0.8 * self.speech_level + 0.2 * level
        end_thr = max(floor * self.END_FLOOR_FACTOR, self.speech_level * self.END_SPEECH_FACTOR)
        if level < end_thr:
            self.silent_sec += dur
            if self.silent_sec >= self.silence_sec:
                self.reason = "silence"
                return True
        else:
            self.silent_sec = 0.0
        return False

    def _late_speech(self) -> bool:
        """Before throwing a round away as «no speech»: judge the whole recording again with a floor from its quieter
        half. Speech from the very first moment, before the floor was known, is found here (the round then ends
        at no_speech_sec instead of shortly after the last word — slower, never lost)."""
        if len(self._levels) < 10:
            return False
        ordered = sorted(lv for lv, _d in self._levels)
        floor = statistics.median(ordered[: len(ordered) // 2])
        thr = max(floor * self.START_FACTOR, self.MIN_SPEECH_LEVEL)
        loud = sum(d for lv, d in self._levels if lv > thr)
        if loud < self.MIN_SPEECH_SEC:
            return False
        self.floor = floor
        self.speech_level = max(lv for lv, _d in self._levels)
        return True
