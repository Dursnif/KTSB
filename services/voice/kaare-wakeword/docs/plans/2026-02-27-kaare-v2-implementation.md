# Kaare v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Evolve Kaare from single-satellite voice assistant into a multi-satellite, speaker-aware, proactive smart home brain with persistent memory.

**Architecture:** Satellites register with central server (WebSocket + mDNS), expose HTTP API for audio push. Server adds VoiceID, conversation memory, RSS briefings, broadcast, proactive speech, and deep HA integration. Sonos output via HA scripts (text-based, cross-network).

**Tech Stack:** Python 3.10+, aiohttp (satellite HTTP server), zeroconf (mDNS), SpeechBrain/ECAPA-TDNN (VoiceID), SQLite (conversation memory), feedparser (RSS), pytest

---

## Phase 1: Quick Wins (no dependencies)

### Task 1: Fix JSON-to-speech bug

**Files:**
- Modify: `server/nlu.py:257-286` (`_parse_response` method)
- Test: `tests/server/test_nlu.py`

**Step 1: Write failing tests for JSON-in-speech bug**

Add to `tests/server/test_nlu.py` in class `TestNLUToolActions`:

```python
def test_invalid_json_strips_json_syntax(self):
    """When LLM returns text mixed with JSON, strip JSON before TTS."""
    engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
    raw = 'Here is the answer: {"action": "answer", "response": "Oslo", "confidence": 4}'
    result = engine._parse_response(raw)
    assert result.action == "answer"
    # Should NOT contain JSON braces in speech text
    assert "{" not in result.response_text
    assert "action" not in result.response_text

def test_pure_json_still_works(self):
    """Valid JSON should still parse normally."""
    engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
    raw = '{"action": "answer", "response": "Det er 5 grader.", "confidence": 5}'
    result = engine._parse_response(raw)
    assert result.response_text == "Det er 5 grader."
    assert result.confidence == 5

def test_empty_json_fallback_message(self):
    """If stripping JSON leaves nothing, use fallback message."""
    engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
    raw = '{"action": "answer"}'  # valid JSON but no response field
    result = engine._parse_response(raw)
    # response_text should be empty string (valid JSON path, just no response key)
    assert result.response_text == ""

def test_text_with_nested_json_stripped(self):
    """Text containing JSON blocks should have JSON removed."""
    engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
    raw = 'La meg sjekke {"action": "ha_api", "method": "GET"} for deg'
    result = engine._parse_response(raw)
    assert "{" not in result.response_text
    assert "ha_api" not in result.response_text
    assert "sjekke" in result.response_text or "deg" in result.response_text
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_nlu.py::TestNLUToolActions::test_invalid_json_strips_json_syntax tests/server/test_nlu.py::TestNLUToolActions::test_text_with_nested_json_stripped -v`
Expected: FAIL (JSON content currently kept in response_text)

**Step 3: Fix `_parse_response` in `server/nlu.py`**

Replace the `except json.JSONDecodeError` block (lines ~278-286) with:

```python
except json.JSONDecodeError:
    # LLM returned non-JSON text (possibly mixed with JSON fragments).
    # Strip any JSON-like content before using as speech text.
    import re as _re
    clean = _re.sub(r'\{[^{}]*\}', '', raw).strip()
    # Also strip nested JSON (greedy but safe for single-level)
    clean = _re.sub(r'\{.*?\}', '', clean, flags=_re.DOTALL).strip()
    if not clean:
        clean = "Beklager, jeg forstod ikke helt."
    return NLUResult(
        action="answer",
        entities={},
        response_text=clean,
        confidence=0.5,
        source="ollama",
        expects_followup=clean.rstrip().endswith("?"),
    )
```

**Step 4: Run all NLU tests to verify**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_nlu.py -v`
Expected: ALL PASS (including existing `test_invalid_json_fallback`)

Note: The existing `test_invalid_json_fallback` tests plain text without JSON — it should still pass since there's nothing to strip.

**Step 5: Commit**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "fix: strip JSON from TTS when LLM returns mixed text/JSON"
```

---

### Task 2: Sonos output via HA scripts

**Files:**
- Modify: `server/sonos.py` (replace HTTP approach)
- Modify: `server/config.py` (simplify Sonos config)
- Modify: `server/server.py` (update Sonos initialization)
- Create: `tests/server/test_sonos.py`

**Step 1: Write tests for new Sonos HA script approach**

Create `tests/server/test_sonos.py`:

```python
"""Tests for Sonos output via HA scripts."""
from __future__ import annotations

from datetime import time
from unittest.mock import patch, MagicMock

import pytest

from server.sonos import SonosOutput, load_sonos_config


class TestQuietHours:
    def test_not_quiet_during_day(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="test")
        cfg = {"entity_id": "media_player.kids", "quiet_after": "21:00", "quiet_before": "07:00"}
        with patch("server.sonos.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(14, 0)
            assert sonos._is_quiet_hour(cfg) is False

    def test_quiet_after_bedtime(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="test")
        cfg = {"entity_id": "media_player.kids", "quiet_after": "21:00", "quiet_before": "07:00"}
        with patch("server.sonos.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(22, 0)
            assert sonos._is_quiet_hour(cfg) is True

    def test_no_quiet_config_never_quiet(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="test")
        cfg = {"entity_id": "media_player.living_room"}
        assert sonos._is_quiet_hour(cfg) is False


class TestRoomMapping:
    def test_satellite_maps_to_room(self):
        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            satellites={"rpi-stue": "living_room"},
        )
        assert sonos._get_room("rpi-stue") == "living_room"

    def test_unknown_satellite_uses_default(self):
        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            satellites={"default": "living_room"},
        )
        assert sonos._get_room("unknown-sat") == "living_room"


class TestPlayTTS:
    @patch("server.sonos.requests.post")
    def test_play_calls_ha_script(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test-token",
            speakers={"living_room": {"entity_id": "media_player.living_room"}},
            satellites={"default": "living_room"},
        )
        result = sonos.play_tts("God morgen!", satellite_id="default")
        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "script/turn_on" in call_args[0][0] or "script" in str(call_args)

    @patch("server.sonos.requests.post")
    def test_play_skips_quiet_room(self, mock_post):
        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            speakers={"kids_bedroom": {
                "entity_id": "media_player.kids_bedroom",
                "quiet_after": "21:00", "quiet_before": "07:00",
            }},
            satellites={"rpi-barnerom": "kids_bedroom"},
        )
        with patch("server.sonos.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(22, 0)
            result = sonos.play_tts("Sov godt!", satellite_id="rpi-barnerom")
        assert result is False
        mock_post.assert_not_called()

    def test_play_fails_without_token(self):
        sonos = SonosOutput(ha_url="http://ha:8123", ha_token="")
        result = sonos.play_tts("Test")
        assert result is False


class TestBroadcast:
    @patch("server.sonos.requests.post")
    def test_broadcast_hits_all_speakers(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        sonos = SonosOutput(
            ha_url="http://ha:8123", ha_token="test",
            speakers={
                "living_room": {"entity_id": "media_player.living_room"},
                "garage": {"entity_id": "media_player.garage"},
            },
        )
        count = sonos.broadcast("Det er middag!")
        assert count >= 1  # At least the broadcast script called
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_sonos.py -v`
Expected: FAIL (broadcast method doesn't exist yet, play_tts signature changed)

**Step 3: Rewrite `server/sonos.py` with HA script approach**

Replace the entire file with:

```python
"""Sonos TTS output via Home Assistant scripts.

Routes TTS text to Sonos speakers via HA's script system.
Room-based routing with quiet hours support.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def load_sonos_config(config_file: str) -> tuple[dict, dict]:
    """Load speakers and satellites config from JSON file."""
    path = Path(config_file)
    if not path.exists():
        log.warning("Sonos config not found: %s", path)
        return {}, {}
    data = json.loads(path.read_text())
    return data.get("speakers", {}), data.get("satellites", {})


class SonosOutput:
    """Manages Sonos TTS playback via Home Assistant scripts.

    Sends text to HA scripts which handle TTS + Sonos playback.
    No HTTP file server needed — works across networks.

    Args:
        ha_url: Home Assistant base URL.
        ha_token: Long-lived access token.
        speakers: Room -> speaker config mapping.
        satellites: satellite_id -> room mapping.
        volume: Playback volume (0.0-1.0).
        tts_script: HA script entity for single-speaker TTS.
        broadcast_script: HA script entity for broadcast.
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        speakers: dict | None = None,
        satellites: dict | None = None,
        volume: float = 0.4,
        tts_script: str = "script.sonos_tts_norwegian_speak",
        broadcast_script: str = "script.sonos_broadcast",
    ):
        self._ha_url = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._volume = volume
        self._tts_script = tts_script
        self._broadcast_script = broadcast_script

        self._speakers = speakers or {
            "living_room": {"entity_id": "media_player.living_room"},
            "garage": {"entity_id": "media_player.garage"},
            "basement": {"entity_id": "media_player.basement"},
            "kids_bedroom": {
                "entity_id": "media_player.kids_bedroom",
                "quiet_after": "21:00",
                "quiet_before": "07:00",
            },
        }
        self._satellites = satellites or {"default": "living_room"}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }

    def _is_quiet_hour(self, speaker_cfg: dict) -> bool:
        """Check if current time falls in speaker's quiet hours."""
        quiet_after = speaker_cfg.get("quiet_after")
        quiet_before = speaker_cfg.get("quiet_before")
        if not quiet_after:
            return False

        now = datetime.now().time()
        after = time.fromisoformat(quiet_after)
        before = time.fromisoformat(quiet_before) if quiet_before else time(7, 0)

        if after > before:
            return now >= after or now < before
        return after <= now < before

    def _get_room(self, satellite_id: str) -> str:
        """Map satellite_id to room name."""
        return self._satellites.get(
            satellite_id, self._satellites.get("default", "living_room")
        )

    def _get_active_speaker(self, room: str) -> str | None:
        """Get the speaker entity_id for a room, respecting quiet hours."""
        cfg = self._speakers.get(room)
        if not cfg:
            log.warning("No speaker configured for room '%s'", room)
            return None
        if self._is_quiet_hour(cfg):
            log.info("Speaker %s is in quiet hours, skipping", cfg["entity_id"])
            return None
        return cfg["entity_id"]

    def play_tts(self, text: str, satellite_id: str = "default") -> bool:
        """Play TTS text on the Sonos speaker in the satellite's room.

        Args:
            text: Text to speak.
            satellite_id: Which satellite triggered this.

        Returns:
            True if playback was triggered successfully.
        """
        if not self._ha_token:
            return False

        room = self._get_room(satellite_id)
        entity_id = self._get_active_speaker(room)
        if not entity_id:
            return False

        try:
            resp = requests.post(
                f"{self._ha_url}/api/services/script/turn_on",
                headers=self._headers(),
                json={
                    "entity_id": self._tts_script,
                    "variables": {
                        "target_player": entity_id,
                        "message": text,
                        "volume": self._volume,
                    },
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Sonos TTS triggered: %s -> %s", entity_id, text[:60])
            return True
        except requests.RequestException as exc:
            log.warning("Sonos TTS failed for %s: %s", entity_id, exc)
            return False

    def broadcast(self, text: str, skip_quiet: bool = True) -> int:
        """Broadcast text to all Sonos speakers.

        Args:
            text: Text to broadcast.
            skip_quiet: If True, skip speakers in quiet hours.

        Returns:
            Number of speakers that received the broadcast.
        """
        if not self._ha_token:
            return 0

        # Try broadcast script first (plays on all speakers at once)
        try:
            resp = requests.post(
                f"{self._ha_url}/api/services/script/turn_on",
                headers=self._headers(),
                json={
                    "entity_id": self._broadcast_script,
                    "variables": {
                        "message": text,
                        "volume": self._volume,
                    },
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Sonos broadcast triggered: %s", text[:60])
            return len(self._speakers)
        except requests.RequestException as exc:
            log.warning("Sonos broadcast failed: %s", exc)
            return 0
```

**Step 4: Update `server/config.py` — simplify Sonos config**

Remove `sonos_server_ip` and `sonos_http_port` (no longer needed). Add script names:

```python
# In ServerConfig dataclass, replace Sonos fields:
    sonos_enabled: bool = False
    sonos_volume: float = 0.4
    sonos_config_file: str = ""
    sonos_tts_script: str = "script.sonos_tts_norwegian_speak"
    sonos_broadcast_script: str = "script.sonos_broadcast"
```

**Step 5: Update `server/server.py` — update Sonos initialization**

In `ServerPipeline.__init__`, update the Sonos setup block to use text-based output:

```python
# Sonos output (text-based via HA scripts)
self._sonos: SonosOutput | None = None
if config.sonos_enabled and config.ha_token:
    from server.sonos import load_sonos_config
    speakers, satellites = {}, {}
    if config.sonos_config_file:
        speakers, satellites = load_sonos_config(config.sonos_config_file)
    self._sonos = SonosOutput(
        ha_url=config.ha_url,
        ha_token=config.ha_token,
        speakers=speakers or None,
        satellites=satellites or None,
        volume=config.sonos_volume,
        tts_script=config.sonos_tts_script,
        broadcast_script=config.sonos_broadcast_script,
    )
    log.info("Sonos output enabled (via HA scripts)")
```

Update `_synthesize_and_play` to send text instead of audio:

```python
def _synthesize_and_play(self, text: str, satellite_id: str = "default") -> bytes:
    """Synthesize TTS and also play on Sonos if configured."""
    audio = self._tts.synthesize(text)
    if self._sonos and text.strip():
        self._sonos.play_tts(text, satellite_id)
    return audio
```

**Step 6: Run all tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_sonos.py tests/server/test_server.py tests/server/test_server_pipeline.py -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add server/sonos.py server/config.py server/server.py tests/server/test_sonos.py
git commit -m "refactor: replace Sonos HTTP file server with HA script-based TTS"
```

---

## Phase 2: Satellite Infrastructure

### Task 3: Satellite HTTP push API

**Files:**
- Create: `satellite/http_server.py`
- Modify: `satellite/pipeline.py` (start HTTP server alongside WebSocket)
- Modify: `satellite/config.py` (add HTTP config)
- Create: `tests/satellite/test_http_server.py`

**Step 1: Write tests for satellite HTTP server**

Create `tests/satellite/test_http_server.py`:

```python
"""Tests for satellite HTTP push API."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import numpy as np

from satellite.http_server import SatelliteHTTPServer


@pytest.fixture
def mock_play_callback():
    return AsyncMock()


class TestHTTPServer:
    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        server = SatelliteHTTPServer(port=0, satellite_id="test-sat", room="living_room")
        await server.start()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{server.port}/status") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["satellite_id"] == "test-sat"
                    assert data["room"] == "living_room"
                    assert "state" in data
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_play_endpoint_accepts_wav(self, mock_play_callback):
        server = SatelliteHTTPServer(
            port=0, satellite_id="test-sat", room="test",
            on_play=mock_play_callback,
        )
        await server.start()
        try:
            import aiohttp
            wav_data = b"RIFF" + b"\x00" * 100  # minimal WAV-like payload
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{server.port}/play",
                    data=wav_data,
                    headers={"Content-Type": "audio/wav"},
                ) as resp:
                    assert resp.status == 200
            mock_play_callback.assert_called_once()
        finally:
            await server.stop()
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/satellite/test_http_server.py -v`
Expected: FAIL (module not found)

**Step 3: Implement `satellite/http_server.py`**

```python
"""HTTP server for receiving audio push from Kaare server.

Endpoints:
    POST /play    — receive WAV audio, play immediately
    POST /volume  — adjust playback volume
    GET  /status  — return satellite state
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

import numpy as np
from aiohttp import web

log = logging.getLogger(__name__)


class SatelliteHTTPServer:
    """HTTP server for satellite audio push.

    Args:
        port: Port to listen on (0 = auto-assign).
        satellite_id: This satellite's identifier.
        room: Room this satellite is in.
        on_play: Async callback when audio is received.
        volume: Initial playback volume (0.0-1.0).
    """

    def __init__(
        self,
        port: int = 8080,
        satellite_id: str = "unknown",
        room: str = "unknown",
        on_play: Callable[[bytes, int], Awaitable[None]] | None = None,
        volume: float = 1.0,
    ):
        self._port = port
        self._satellite_id = satellite_id
        self._room = room
        self._on_play = on_play
        self._volume = volume
        self._state = "idle"
        self._app = web.Application()
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_post("/play", self._handle_play)
        self._app.router.add_post("/volume", self._handle_volume)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def port(self) -> int:
        """Actual port (useful when port=0)."""
        if self._site and self._site._server:
            for sock in self._site._server.sockets:
                return sock.getsockname()[1]
        return self._port

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str):
        self._state = value

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "satellite_id": self._satellite_id,
            "room": self._room,
            "state": self._state,
            "volume": self._volume,
        })

    async def _handle_play(self, request: web.Request) -> web.Response:
        wav_data = await request.read()
        sample_rate = int(request.headers.get("X-Sample-Rate", "22050"))
        log.info("Received %d bytes audio (rate=%d)", len(wav_data), sample_rate)

        if self._on_play:
            await self._on_play(wav_data, sample_rate)
        else:
            # Default: play via sounddevice
            await self._default_play(wav_data, sample_rate)

        return web.json_response({"status": "ok"})

    async def _handle_volume(self, request: web.Request) -> web.Response:
        data = await request.json()
        self._volume = max(0.0, min(1.0, float(data.get("volume", self._volume))))
        return web.json_response({"volume": self._volume})

    async def _default_play(self, wav_data: bytes, sample_rate: int) -> None:
        """Play audio via sounddevice (default handler)."""
        try:
            import sounddevice as sd
            audio_i16 = np.frombuffer(wav_data, dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32768.0
            audio_f32 *= self._volume
            sd.play(audio_f32, samplerate=sample_rate, blocking=False)
        except Exception as exc:
            log.warning("Failed to play pushed audio: %s", exc)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await self._site.start()
        log.info("Satellite HTTP server started on port %d", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
```

**Step 4: Add `aiohttp` to satellite dependencies in `pyproject.toml`**

In `[project.optional-dependencies]`, update satellite:
```
satellite = ["onnxruntime>=1.17", "sounddevice>=0.4", "soundfile>=0.12", "librosa>=0.10", "aiohttp>=3.9"]
```

**Step 5: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/satellite/test_http_server.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add satellite/http_server.py tests/satellite/test_http_server.py pyproject.toml
git commit -m "feat: add satellite HTTP push API for receiving audio from server"
```

---

### Task 4: Satellite Registry (server-side)

**Files:**
- Create: `server/registry.py`
- Modify: `server/server.py` (handle register messages, use registry for audio push)
- Create: `tests/server/test_registry.py`

**Step 1: Write tests for SatelliteRegistry**

Create `tests/server/test_registry.py`:

```python
"""Tests for satellite registry."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.registry import SatelliteRegistry, SatelliteInfo


class TestRegistry:
    def test_register_satellite(self):
        reg = SatelliteRegistry()
        reg.register("rpi-stue", room="living_room", ip="192.168.87.199", http_port=8080)
        info = reg.get("rpi-stue")
        assert info is not None
        assert info.room == "living_room"
        assert info.ip == "192.168.87.199"
        assert info.online is True

    def test_unregister_satellite(self):
        reg = SatelliteRegistry()
        reg.register("rpi-stue", room="living_room", ip="192.168.87.199", http_port=8080)
        reg.unregister("rpi-stue")
        assert reg.get("rpi-stue") is None

    def test_get_by_room(self):
        reg = SatelliteRegistry()
        reg.register("rpi-stue", room="living_room", ip="192.168.87.199", http_port=8080)
        reg.register("rpi-kjokken", room="kitchen", ip="192.168.87.200", http_port=8080)
        sats = reg.get_by_room("living_room")
        assert len(sats) == 1
        assert sats[0].satellite_id == "rpi-stue"

    def test_get_all_online(self):
        reg = SatelliteRegistry()
        reg.register("sat-1", room="room1", ip="10.0.0.1", http_port=8080)
        reg.register("sat-2", room="room2", ip="10.0.0.2", http_port=8080)
        assert len(reg.get_all_online()) == 2

    @patch("server.registry.requests.post")
    def test_push_audio(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        reg = SatelliteRegistry()
        reg.register("sat-1", room="room1", ip="10.0.0.1", http_port=8080)
        result = reg.push_audio("sat-1", b"fake-wav-data")
        assert result is True
        mock_post.assert_called_once()

    @patch("server.registry.requests.post")
    def test_broadcast_audio(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()

        reg = SatelliteRegistry()
        reg.register("sat-1", room="room1", ip="10.0.0.1", http_port=8080)
        reg.register("sat-2", room="room2", ip="10.0.0.2", http_port=8080)
        count = reg.broadcast_audio(b"fake-wav-data")
        assert count == 2

    def test_push_to_unknown_satellite_fails(self):
        reg = SatelliteRegistry()
        result = reg.push_audio("nonexistent", b"data")
        assert result is False
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_registry.py -v`
Expected: FAIL (module not found)

**Step 3: Implement `server/registry.py`**

```python
"""Satellite registry — tracks connected satellites and enables audio push."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)


@dataclass
class SatelliteInfo:
    """Info about a registered satellite."""
    satellite_id: str
    room: str
    ip: str
    http_port: int
    online: bool = True
    last_seen: float = field(default_factory=time.time)
    capabilities: list[str] = field(default_factory=lambda: ["speaker", "mic"])


class SatelliteRegistry:
    """Track connected satellites and push audio to them.

    Satellites register via WebSocket or mDNS discovery.
    Audio is pushed to satellites via HTTP POST to their /play endpoint.
    """

    def __init__(self):
        self._satellites: dict[str, SatelliteInfo] = {}

    def register(
        self,
        satellite_id: str,
        room: str,
        ip: str,
        http_port: int,
        capabilities: list[str] | None = None,
    ) -> None:
        """Register or update a satellite."""
        self._satellites[satellite_id] = SatelliteInfo(
            satellite_id=satellite_id,
            room=room,
            ip=ip,
            http_port=http_port,
            capabilities=capabilities or ["speaker", "mic"],
        )
        log.info("Satellite registered: %s (room=%s, ip=%s:%d)", satellite_id, room, ip, http_port)

    def unregister(self, satellite_id: str) -> None:
        """Remove a satellite from the registry."""
        if satellite_id in self._satellites:
            del self._satellites[satellite_id]
            log.info("Satellite unregistered: %s", satellite_id)

    def get(self, satellite_id: str) -> SatelliteInfo | None:
        """Get info for a specific satellite."""
        return self._satellites.get(satellite_id)

    def get_by_room(self, room: str) -> list[SatelliteInfo]:
        """Get all online satellites in a room."""
        return [s for s in self._satellites.values() if s.room == room and s.online]

    def get_all_online(self) -> list[SatelliteInfo]:
        """Get all online satellites."""
        return [s for s in self._satellites.values() if s.online]

    def push_audio(self, satellite_id: str, wav_bytes: bytes, sample_rate: int = 22050) -> bool:
        """Push audio to a satellite via HTTP POST.

        Args:
            satellite_id: Target satellite.
            wav_bytes: Raw PCM audio bytes.
            sample_rate: Audio sample rate.

        Returns:
            True if push was successful.
        """
        info = self._satellites.get(satellite_id)
        if not info or not info.online:
            log.warning("Cannot push to %s: not registered or offline", satellite_id)
            return False

        try:
            resp = requests.post(
                f"http://{info.ip}:{info.http_port}/play",
                data=wav_bytes,
                headers={
                    "Content-Type": "audio/wav",
                    "X-Sample-Rate": str(sample_rate),
                },
                timeout=10,
            )
            resp.raise_for_status()
            info.last_seen = time.time()
            log.info("Audio pushed to %s (%d bytes)", satellite_id, len(wav_bytes))
            return True
        except requests.RequestException as exc:
            log.warning("Audio push to %s failed: %s", satellite_id, exc)
            info.online = False
            return False

    def broadcast_audio(
        self, wav_bytes: bytes, rooms: list[str] | None = None, sample_rate: int = 22050,
    ) -> int:
        """Push audio to all satellites (optionally filtered by room).

        Args:
            wav_bytes: Raw PCM audio bytes.
            rooms: If set, only broadcast to these rooms.
            sample_rate: Audio sample rate.

        Returns:
            Number of satellites that received the audio.
        """
        targets = self.get_all_online()
        if rooms:
            targets = [s for s in targets if s.room in rooms]

        count = 0
        for sat in targets:
            if self.push_audio(sat.satellite_id, wav_bytes, sample_rate):
                count += 1
        return count
```

**Step 4: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_registry.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add server/registry.py tests/server/test_registry.py
git commit -m "feat: add satellite registry with HTTP audio push"
```

---

### Task 5: mDNS discovery

**Files:**
- Create: `satellite/mdns.py` (satellite announcement)
- Create: `server/mdns.py` (server listener)
- Create: `tests/server/test_mdns.py`

**Step 1: Write tests for mDNS components**

Create `tests/server/test_mdns.py`:

```python
"""Tests for mDNS discovery."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.mdns import KaareMDNSListener


class TestMDNSListener:
    def test_creates_listener(self):
        registry = MagicMock()
        listener = KaareMDNSListener(registry=registry)
        assert listener._registry is registry

    def test_service_added_registers_satellite(self):
        registry = MagicMock()
        listener = KaareMDNSListener(registry=registry)

        # Simulate service info
        mock_info = MagicMock()
        mock_info.parsed_addresses.return_value = ["192.168.87.199"]
        mock_info.port = 8080
        mock_info.properties = {
            b"satellite_id": b"rpi-stue",
            b"room": b"living_room",
        }

        with patch("server.mdns.Zeroconf"):
            listener.add_service(None, "_kaare-sat._tcp.local.", "rpi-stue._kaare-sat._tcp.local.")
            # Would need zeroconf mock; test the handler logic directly
            listener._handle_service_found(mock_info)

        registry.register.assert_called_once_with(
            satellite_id="rpi-stue",
            room="living_room",
            ip="192.168.87.199",
            http_port=8080,
        )
```

**Step 2: Implement `satellite/mdns.py`**

```python
"""mDNS announcement for satellite discovery.

Announces this satellite as a _kaare-sat._tcp service
so the server can auto-discover it.
"""
from __future__ import annotations

import logging
import socket

from zeroconf import Zeroconf, ServiceInfo

log = logging.getLogger(__name__)

SERVICE_TYPE = "_kaare-sat._tcp.local."


def announce_satellite(
    satellite_id: str,
    room: str,
    http_port: int,
) -> tuple[Zeroconf, ServiceInfo]:
    """Announce this satellite via mDNS.

    Returns (zeroconf, info) — call zeroconf.unregister_service(info)
    and zeroconf.close() on shutdown.
    """
    hostname = socket.gethostname()
    ip = _get_local_ip()

    info = ServiceInfo(
        SERVICE_TYPE,
        f"{satellite_id}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=http_port,
        properties={
            "satellite_id": satellite_id,
            "room": room,
        },
        server=f"{hostname}.local.",
    )

    zc = Zeroconf()
    zc.register_service(info)
    log.info("mDNS: announced %s on %s:%d", satellite_id, ip, http_port)
    return zc, info


def _get_local_ip() -> str:
    """Get this machine's LAN IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"
```

**Step 3: Implement `server/mdns.py`**

```python
"""mDNS listener for satellite auto-discovery."""
from __future__ import annotations

import logging

from zeroconf import Zeroconf, ServiceBrowser, ServiceListener, ServiceInfo

from server.registry import SatelliteRegistry

log = logging.getLogger(__name__)

SERVICE_TYPE = "_kaare-sat._tcp.local."


class KaareMDNSListener(ServiceListener):
    """Listens for satellite mDNS announcements and registers them."""

    def __init__(self, registry: SatelliteRegistry):
        self._registry = registry
        self._zc: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None

    def start(self) -> None:
        """Start listening for satellite announcements."""
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self)
        log.info("mDNS: listening for %s", SERVICE_TYPE)

    def stop(self) -> None:
        """Stop listening."""
        if self._zc:
            self._zc.close()

    def _handle_service_found(self, info: ServiceInfo) -> None:
        """Handle a discovered satellite service."""
        props = info.properties or {}
        satellite_id = props.get(b"satellite_id", b"unknown").decode()
        room = props.get(b"room", b"unknown").decode()
        addresses = info.parsed_addresses()
        ip = addresses[0] if addresses else "unknown"
        port = info.port

        self._registry.register(
            satellite_id=satellite_id,
            room=room,
            ip=ip,
            http_port=port,
        )

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name) if zc else None
        if info:
            self._handle_service_found(info)
            log.info("mDNS: satellite discovered: %s", name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        log.info("mDNS: satellite removed: %s", name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name) if zc else None
        if info:
            self._handle_service_found(info)
```

**Step 4: Add `zeroconf` to dependencies**

In `pyproject.toml`, add to both satellite and server deps:
```
satellite = [..., "aiohttp>=3.9", "zeroconf>=0.131"]
server = [..., "zeroconf>=0.131"]
```

**Step 5: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_mdns.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add satellite/mdns.py server/mdns.py tests/server/test_mdns.py pyproject.toml
git commit -m "feat: add mDNS discovery for satellite auto-registration"
```

---

### Task 6: Wire registry into WebSocket server

**Files:**
- Modify: `server/server.py` (add registry, handle register messages, push audio via registry)
- Modify: `tests/server/test_server.py`

**Step 1: Write test for WebSocket register message**

Add to `tests/server/test_server.py`:

```python
class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_message_adds_to_registry(self):
        from server.registry import SatelliteRegistry
        registry = SatelliteRegistry()
        # Test that processing a register message updates the registry
        msg = {
            "type": "register",
            "satellite_id": "rpi-stue",
            "room": "living_room",
            "http_port": 8080,
        }
        # Simulate what server does with register message
        registry.register(
            satellite_id=msg["satellite_id"],
            room=msg["room"],
            ip="192.168.87.199",
            http_port=msg["http_port"],
        )
        assert registry.get("rpi-stue") is not None
        assert registry.get("rpi-stue").room == "living_room"
```

**Step 2: Update `server/server.py`**

In `VoiceServer.__init__`, add:
```python
from server.registry import SatelliteRegistry
self._registry = SatelliteRegistry()
```

In `_handle_client`, add handler for `register` message type (after `audio_start` handler):
```python
elif msg_type == "register":
    satellite_id = msg.get("satellite_id", "unknown")
    room = msg.get("room", "unknown")
    http_port = msg.get("http_port", 8080)
    # Extract IP from WebSocket connection
    peer = websocket.remote_address
    ip = peer[0] if peer else "unknown"
    self._registry.register(satellite_id, room, ip, http_port)
    log.info("Satellite registered via WebSocket: %s (%s:%d)", satellite_id, ip, http_port)
```

In the `except` block at the end of `_handle_client`, unregister:
```python
except Exception:
    log.exception("Error handling satellite %s", satellite_id)
finally:
    if satellite_id != "unknown":
        self._registry.unregister(satellite_id)
```

**Step 3: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/ -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add server/server.py tests/server/test_server.py
git commit -m "feat: wire satellite registry into WebSocket server"
```

---

## Phase 3: Server Capabilities

### Task 7: Proactive speech REST API

**Files:**
- Create: `server/api.py` (REST endpoints)
- Modify: `server/server.py` (start REST API alongside WebSocket)
- Create: `tests/server/test_api.py`

**Step 1: Write tests**

Create `tests/server/test_api.py`:

```python
"""Tests for proactive speech REST API."""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

from server.api import create_app


class TestSpeakEndpoint:
    @pytest.mark.asyncio
    async def test_speak_returns_200(self):
        mock_pipeline = MagicMock()
        mock_pipeline._tts.synthesize.return_value = b"fake-audio"
        mock_registry = MagicMock()
        mock_registry.get_by_room.return_value = []
        mock_sonos = MagicMock()

        app = create_app(
            pipeline=mock_pipeline,
            registry=mock_registry,
            sonos=mock_sonos,
        )
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/speak", json={
                "text": "God morgen!",
                "room": "living_room",
            })
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_speak_requires_text(self):
        app = create_app(pipeline=MagicMock(), registry=MagicMock(), sonos=None)
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/speak", json={"room": "living_room"})
            assert resp.status == 400
```

**Step 2: Implement `server/api.py`**

```python
"""REST API for proactive speech and external integrations.

Endpoints:
    POST /api/speak  — trigger TTS playback on satellites/Sonos
    GET  /api/satellites — list registered satellites
"""
from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger(__name__)


def create_app(pipeline, registry, sonos=None) -> web.Application:
    """Create the REST API application.

    Args:
        pipeline: ServerPipeline instance (for TTS).
        registry: SatelliteRegistry instance.
        sonos: SonosOutput instance (optional).
    """
    app = web.Application()
    app["pipeline"] = pipeline
    app["registry"] = registry
    app["sonos"] = sonos

    app.router.add_post("/api/speak", handle_speak)
    app.router.add_get("/api/satellites", handle_list_satellites)

    return app


async def handle_speak(request: web.Request) -> web.Response:
    """Handle proactive speech request.

    Body:
        text: Text to speak (required).
        room: Target room (optional — broadcasts to all if omitted).
        targets: ["satellite", "sonos"] (default: both).
        priority: "normal" or "urgent" (default: normal).
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    text = data.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)

    room = data.get("room")
    targets = data.get("targets", ["satellite", "sonos"])
    priority = data.get("priority", "normal")

    pipeline = request.app["pipeline"]
    registry = request.app["registry"]
    sonos = request.app["sonos"]

    results = {"text": text, "room": room, "delivered": []}

    # Generate TTS audio
    audio = pipeline._tts.synthesize(text)

    # Push to satellites
    if "satellite" in targets and audio:
        if room:
            sats = registry.get_by_room(room)
        else:
            sats = registry.get_all_online()

        for sat in sats:
            if registry.push_audio(sat.satellite_id, audio):
                results["delivered"].append(f"satellite:{sat.satellite_id}")

    # Push to Sonos
    if "sonos" in targets and sonos:
        if room:
            if sonos.play_tts(text, satellite_id=room):
                results["delivered"].append(f"sonos:{room}")
        else:
            count = sonos.broadcast(text)
            if count:
                results["delivered"].append(f"sonos:broadcast({count})")

    log.info("Proactive speak: %s -> %s", text[:60], results["delivered"])
    return web.json_response(results)


async def handle_list_satellites(request: web.Request) -> web.Response:
    """List all registered satellites."""
    registry = request.app["registry"]
    sats = [
        {
            "satellite_id": s.satellite_id,
            "room": s.room,
            "ip": s.ip,
            "http_port": s.http_port,
            "online": s.online,
        }
        for s in registry.get_all_online()
    ]
    return web.json_response({"satellites": sats})
```

**Step 3: Add `aiohttp` to server dependencies**

In `pyproject.toml`, add to server deps:
```
server = [..., "aiohttp>=3.9"]
```

**Step 4: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_api.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add server/api.py tests/server/test_api.py pyproject.toml
git commit -m "feat: add proactive speech REST API"
```

---

### Task 8: Broadcast NLU action

**Files:**
- Modify: `server/nlu.py` (add broadcast to system prompt)
- Modify: `server/server.py` (handle broadcast action in pipeline)
- Modify: `tests/server/test_nlu.py` (add broadcast tests)

**Step 1: Write tests**

Add to `tests/server/test_nlu.py`:

```python
class TestBroadcast:
    def test_broadcast_action_parsed(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "broadcast", "message": "Det er middag!", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.action == "broadcast"
        assert result.entities.get("message") == "Det er middag!"

    def test_system_prompt_includes_broadcast(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "broadcast" in engine._system_prompt
```

**Step 2: Add broadcast to system prompt**

In `server/nlu.py`, add to `DEFAULT_SYSTEM_PROMPT` before the unknown action:

```
If the user asks you to broadcast/announce something to the whole house:
{{"action": "broadcast", "message": "the message to broadcast", "confidence": 5}}
```

**Step 3: Handle broadcast in `server/server.py`**

In `ServerPipeline._process_transcript`, add handler after the `ha_call_service` block:

```python
# Handle broadcast action
if nlu_result.action == "broadcast":
    message = nlu_result.entities.get("message", nlu_result.response_text)
    broadcast_text = f"{message}"
    sl.log(satellite_id, "broadcast", {"message": broadcast_text})

    # Synthesize and broadcast via registry + Sonos
    tts_audio = self._tts.synthesize(broadcast_text)
    if hasattr(self, '_registry') and self._registry:
        self._registry.broadcast_audio(tts_audio)
    if self._sonos:
        self._sonos.broadcast(broadcast_text)

    confirmation = f"Jeg har sendt beskjeden: {message}"
    confirm_audio = self._tts.synthesize(confirmation)
    return PipelineResult(
        transcript=transcript, nlu=nlu_result, tts_audio=confirm_audio,
    )
```

**Step 4: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_nlu.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add server/nlu.py server/server.py tests/server/test_nlu.py
git commit -m "feat: add broadcast action for house-wide announcements"
```

---

## Phase 4: Advanced Features

### Task 9: Persistent conversation memory

**Files:**
- Create: `server/memory.py`
- Modify: `server/nlu.py` (use persistent memory instead of in-memory)
- Create: `tests/server/test_memory.py`

**Step 1: Write tests**

Create `tests/server/test_memory.py`:

```python
"""Tests for persistent conversation memory."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from server.memory import ConversationMemory


class TestConversationMemory:
    def test_store_and_retrieve_summary(self, tmp_path):
        mem = ConversationMemory(db_path=str(tmp_path / "test.db"))
        mem.store_summary(
            speaker="mikalv",
            summary="Snakket om varmepumpa og strompris.",
            topics=["varmepumpe", "strom"],
            satellite_id="rpi-stue",
        )
        results = mem.get_recent_context("mikalv", days=1)
        assert "varmepumpa" in results

    def test_search_conversations(self, tmp_path):
        mem = ConversationMemory(db_path=str(tmp_path / "test.db"))
        mem.store_summary("mikalv", "Diskuterte Sonos-oppsett i garasjen.", ["sonos", "garasje"])
        mem.store_summary("mikalv", "Spurte om vaermeldingen.", ["vaer"])
        results = mem.search("sonos")
        assert len(results) >= 1
        assert "Sonos" in results[0]["summary"]

    def test_per_speaker_isolation(self, tmp_path):
        mem = ConversationMemory(db_path=str(tmp_path / "test.db"))
        mem.store_summary("mikalv", "Admin stuff.", ["admin"])
        mem.store_summary("barn1", "Barnegreier.", ["lek"])
        mikalv_ctx = mem.get_recent_context("mikalv", days=30)
        assert "Admin" in mikalv_ctx
        assert "Barnegreier" not in mikalv_ctx
```

**Step 2: Implement `server/memory.py`**

```python
"""Persistent conversation memory using SQLite.

Stores conversation summaries per speaker with topic tags.
Supports search and recent context retrieval.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)


class ConversationMemory:
    """Persistent conversation memory.

    Args:
        db_path: Path to SQLite database file.
    """

    def __init__(self, db_path: str = "data/conversations.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker TEXT NOT NULL,
                summary TEXT NOT NULL,
                topics TEXT NOT NULL DEFAULT '[]',
                satellite_id TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_summaries_speaker ON summaries(speaker);
            CREATE INDEX IF NOT EXISTS idx_summaries_created ON summaries(created_at);
        """)
        self._conn.commit()

    def store_summary(
        self,
        speaker: str,
        summary: str,
        topics: list[str] | None = None,
        satellite_id: str = "",
    ) -> None:
        """Store a conversation summary."""
        self._conn.execute(
            "INSERT INTO summaries (speaker, summary, topics, satellite_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (speaker, summary, json.dumps(topics or []), satellite_id, time.time()),
        )
        self._conn.commit()
        log.info("Stored summary for %s: %s", speaker, summary[:80])

    def get_recent_context(self, speaker: str, days: int = 3) -> str:
        """Get recent conversation context for a speaker.

        Returns a formatted string suitable for injection into system prompt.
        """
        cutoff = time.time() - (days * 86400)
        rows = self._conn.execute(
            "SELECT summary, topics, created_at FROM summaries "
            "WHERE speaker = ? AND created_at > ? ORDER BY created_at DESC LIMIT 10",
            (speaker, cutoff),
        ).fetchall()

        if not rows:
            return ""

        lines = []
        for row in rows:
            import datetime
            dt = datetime.datetime.fromtimestamp(row["created_at"])
            lines.append(f"- [{dt:%d.%m %H:%M}] {row['summary']}")

        return "\n".join(lines)

    def search(self, query: str) -> list[dict]:
        """Search conversation summaries."""
        rows = self._conn.execute(
            "SELECT speaker, summary, topics, created_at FROM summaries "
            "WHERE summary LIKE ? ORDER BY created_at DESC LIMIT 20",
            (f"%{query}%",),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
```

**Step 3: Run tests**

Run: `cd /Users/mikalv/Repos/MeehProjects/home-kaare && python -m pytest tests/server/test_memory.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add server/memory.py tests/server/test_memory.py
git commit -m "feat: add persistent conversation memory with SQLite"
```

---

### Task 10: Speaker Recognition (VoiceID) — Research + Setup

> **Note:** This is the largest task. It requires SpeechBrain setup on the
> server GPU machine and is best done as a separate focused session.

**Files:**
- Create: `server/voiceid.py`
- Create: `server/profiles.json` (speaker profiles)
- Create: `tests/server/test_voiceid.py`
- Modify: `server/server.py` (add VoiceID to pipeline)
- Modify: `server/nlu.py` (inject speaker context into system prompt)

**Research needed before implementation:**
1. Verify SpeechBrain + ECAPA-TDNN runs on server GPU (CUDA)
2. Determine audio format requirements (sample rate, duration, format)
3. Test embedding extraction speed (must be <500ms per utterance)
4. Determine cosine similarity threshold for reliable identification

**Enrollment flow:**
1. NLU detects "registrer stemmen min" → new action: `voice_enroll`
2. Server asks for name, then records 3-5 sentences
3. Extract embeddings, average them, store in profiles.json
4. Confirmation: "Stemmen din er registrert, [name]"

**Per-request ID flow:**
1. After STT, also run speaker encoder on same audio
2. Compare embedding against all profiles (cosine similarity)
3. Inject result into NLU context
4. System prompt gets: "Brukeren som snakker er [name] ([role])"

**Implementation is detailed in design doc section 4.**

---

### Task 11: News Briefing (RSS)

**Files:**
- Create: `server/news.py`
- Create: `server/tools/news_tool.py`
- Modify: `server/nlu.py` (add news_briefing action)
- Create: `tests/server/test_news.py`

**Step 1: Write tests**

```python
"""Tests for news briefing."""
from unittest.mock import patch, MagicMock
from server.news import NewsBriefing


class TestNewsBriefing:
    def test_fetch_headlines(self):
        briefing = NewsBriefing(feeds=["https://www.nrk.no/toppsaker.rss"])
        with patch("server.news.feedparser.parse") as mock_parse:
            mock_parse.return_value.entries = [
                MagicMock(title="Test headline", summary="Summary", link="http://example.com"),
            ]
            headlines = briefing.fetch_headlines(max_items=3)
            assert len(headlines) == 1
            assert headlines[0]["title"] == "Test headline"
```

**Step 2: Implement `server/news.py`**

```python
"""RSS news aggregation for voice briefings."""
from __future__ import annotations

import logging

import feedparser

log = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    "https://www.nrk.no/toppsaker.rss",
]


class NewsBriefing:
    def __init__(self, feeds: list[str] | None = None):
        self._feeds = feeds or DEFAULT_FEEDS

    def fetch_headlines(self, max_items: int = 5) -> list[dict]:
        headlines = []
        for feed_url in self._feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:max_items]:
                    headlines.append({
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "link": entry.get("link", ""),
                        "source": feed.feed.get("title", feed_url),
                    })
            except Exception as exc:
                log.warning("Failed to fetch %s: %s", feed_url, exc)
        return headlines[:max_items]
```

**Step 3: Add `feedparser` to server dependencies**

**Step 4: Run tests, commit**

---

### Task 12: Deep HA Integration

**Files:**
- Create: `server/tools/ha_admin_tool.py`
- Modify: `server/nlu.py` (expand system prompt with admin actions)
- Create: `tests/server/test_ha_admin.py`

**Implementation:** New HA admin tool that provides:
- Entity health check (offline entities)
- Logbook reading
- Automation YAML generation
- Energy monitoring

**Requires VoiceID for role-based access control.**

---

### Task 13: Prism Logging Integration

**Files:**
- Create: `server/prism.py` (async indexer)
- Create: `server/tools/prism_tool.py` (search tool for NLU)
- Modify: `server/server.py` (wire async indexing)

**Implementation:** Background thread that batches log entries and indexes them to Prism. New NLU tool for searching conversation history.

**Depends on:** Prism instance being available.

---

## Dependency Graph

```
Phase 1 (independent):
  Task 1: JSON bug fix
  Task 2: Sonos via HA

Phase 2 (sequential):
  Task 3: Satellite HTTP push
  Task 4: Satellite Registry  (needs Task 3)
  Task 5: mDNS discovery      (needs Task 4)
  Task 6: Wire into server     (needs Task 4)

Phase 3 (needs Phase 2):
  Task 7: Proactive REST API   (needs Task 4, 6)
  Task 8: Broadcast NLU        (needs Task 4, 6)

Phase 4 (independent of each other, some need Phase 2):
  Task 9:  Conversation memory (independent)
  Task 10: VoiceID             (independent, largest effort)
  Task 11: News briefing       (independent)
  Task 12: HA admin            (needs Task 10 for role-based)
  Task 13: Prism logging       (needs Prism instance)
```

## Execution Order (recommended)

1. Task 1 (JSON fix) — 10 min
2. Task 2 (Sonos HA) — 30 min
3. Task 9 (Memory) — 30 min
4. Task 3 (HTTP push) — 30 min
5. Task 4 (Registry) — 20 min
6. Task 5 (mDNS) — 20 min
7. Task 6 (Wire server) — 20 min
8. Task 7 (REST API) — 30 min
9. Task 8 (Broadcast) — 20 min
10. Task 11 (News) — 20 min
11. Task 10 (VoiceID) — 2-3 hours (research + implement)
12. Task 12 (HA admin) — 1 hour
13. Task 13 (Prism) — 1 hour
