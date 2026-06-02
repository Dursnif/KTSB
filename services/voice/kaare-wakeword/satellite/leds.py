"""ReSpeaker Voice HAT LED control.

Drives 12 APA102 LEDs via GPIO bit-banging (data=GPIO23, clock=GPIO24,
power=GPIO5). Falls back to NoOpLEDs when lgpio is unavailable (macOS).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class VoiceHATLEDs:
    """Control Voice HAT 12 APA102 LEDs via GPIO bit-banging.

    This HAT routes LED data/clock to GPIO23/24 (not hardware SPI pins),
    so we bit-bang the APA102 protocol. GPIO5 enables LED VCC power.
    """

    NUM_LEDS = 12
    GPIO_DATA = 23
    GPIO_CLK = 24
    GPIO_POWER = 5

    def __init__(self):
        self._chip = None
        self._lgpio = None
        try:
            import lgpio
            self._chip = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self._chip, self.GPIO_POWER, 1)
            lgpio.gpio_claim_output(self._chip, self.GPIO_DATA, 0)
            lgpio.gpio_claim_output(self._chip, self.GPIO_CLK, 0)
            self._lgpio = lgpio
            time.sleep(0.05)
            log.info(
                "Voice HAT LEDs initialized (GPIO%d/GPIO%d, %d LEDs, power=GPIO%d)",
                self.GPIO_DATA, self.GPIO_CLK, self.NUM_LEDS, self.GPIO_POWER,
            )
        except Exception as e:
            log.warning("Could not init Voice HAT LEDs: %s", e)
            self._chip = None

    def _send_byte(self, val: int):
        chip = self._chip
        lgpio = self._lgpio
        d, c = self.GPIO_DATA, self.GPIO_CLK
        for i in range(7, -1, -1):
            lgpio.gpio_write(chip, d, (val >> i) & 1)
            lgpio.gpio_write(chip, c, 1)
            lgpio.gpio_write(chip, c, 0)

    def _write(self, colors: list[tuple[int, int, int]], brightness: int = 8):
        if not self._chip:
            return
        # Start frame: 32 zero bits
        for _ in range(4):
            self._send_byte(0x00)
        # LED frames: [0xE0 | brightness, B, G, R]
        for r, g, b in colors:
            self._send_byte(0xE0 | (brightness & 0x1F))
            self._send_byte(b)
            self._send_byte(g)
            self._send_byte(r)
        # End frame
        for _ in range((self.NUM_LEDS + 15) // 16):
            self._send_byte(0xFF)

    def off(self):
        self._write([(0, 0, 0)] * self.NUM_LEDS)

    def listening(self):
        """Blue — Kåre is listening."""
        self._write([(0, 80, 255)] * self.NUM_LEDS, brightness=12)

    def processing(self):
        """Green — processing/sending to server."""
        self._write([(0, 200, 50)] * self.NUM_LEDS, brightness=8)

    def responding(self):
        """Soft white — playing TTS response."""
        self._write([(180, 180, 200)] * self.NUM_LEDS, brightness=6)

    def close(self):
        self.off()
        if self._chip:
            try:
                self._lgpio.gpio_write(self._chip, self.GPIO_POWER, 0)
                self._lgpio.gpiochip_close(self._chip)
            except Exception:
                pass
            self._chip = None


class NoOpLEDs:
    """Fallback when no LED hardware is available."""

    def off(self): pass
    def listening(self): pass
    def processing(self): pass
    def responding(self): pass
    def close(self): pass


def create_leds(no_leds: bool = False) -> VoiceHATLEDs | NoOpLEDs:
    """Factory: create LED controller or no-op fallback."""
    if no_leds:
        log.info("LEDs disabled by config")
        return NoOpLEDs()
    try:
        leds = VoiceHATLEDs()
        if leds._chip is not None:
            return leds
    except Exception:
        pass
    log.info("Using NoOpLEDs (no hardware available)")
    return NoOpLEDs()
