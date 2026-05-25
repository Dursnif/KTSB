# Kaare v2 Architecture Design

**Date:** 2026-02-27
**Status:** Approved

## Overview

Kaare v2 evolves from a simple wake-word + LLM voice assistant into a
multi-satellite, multi-speaker, proactive smart home brain with persistent
memory and deep Home Assistant integration.

## Current Architecture

```
RPi Satellite ──WebSocket──> GPU Server ──WebSocket──> RPi Satellite
(wake word,                  (STT, NLU,                (audio playback)
 audio capture)               TTS, tools)
```

- Single satellite (RPi + ReSpeaker HAT)
- Server on 192.168.87.242 (Ollama GPU, Whisper, Piper/gTTS)
- HA on 10.77.42.184:8123 (bridges both networks)
- Sonos partially implemented but disabled (network mismatch)
- No speaker recognition
- In-memory conversation history (lost on restart)

## Target Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │            Kaare Server (GPU)                │
                    │                                              │
                    │  Pipeline:                                   │
                    │  STT → VoiceID → NLU → Tools → TTS          │
                    │                                              │
                    │  Components:                                 │
                    │  ┌──────────────────┐  ┌──────────────────┐  │
                    │  │ SatelliteRegistry │  │ SpeakerProfiles  │  │
                    │  │ (room, IP, port)  │  │ (embeddings)     │  │
                    │  └──────────────────┘  └──────────────────┘  │
                    │  ┌──────────────────┐  ┌──────────────────┐  │
                    │  │ ConversationMem  │  │ NewsBriefing     │  │
                    │  │ (SQLite + Prism) │  │ (RSS feeds)      │  │
                    │  └──────────────────┘  └──────────────────┘  │
                    │                                              │
                    │  Inbound:                                    │
                    │  • WebSocket :8765  ← satellites (audio)     │
                    │  • REST :8080       ← HA automations         │
                    │  • mDNS listener   ← satellite discovery     │
                    │                                              │
                    │  Outbound:                                   │
                    │  • HTTP push → satellite:port/play (audio)   │
                    │  • HA API → script.sonos_tts_* (Sonos text)  │
                    │  • Prism API → index logs/conversations      │
                    └──────────────────────────────────────────────┘
                         │          │           │
          ┌──────────────┘          │           └──────────────┐
          ▼                         ▼                          ▼
  ┌──────────────┐         ┌──────────────┐          ┌──────────────┐
  │ Satellite A  │         │ Satellite B  │          │   Sonos      │
  │ (stue, RPi)  │         │ (kjokken)    │          │  (via HA)    │
  │              │         │              │          │              │
  │ WS → server  │         │ WS → server  │          │ HA scripts   │
  │ HTTP :8080   │         │ HTTP :8080   │          │ text-based   │
  │ ← server push│         │ ← server push│          │              │
  │ mDNS announce│         │ mDNS announce│          │              │
  └──────────────┘         └──────────────┘          └──────────────┘
```

## Component Designs

### 1. JSON-to-Speech Bug Fix

**Problem:** When the LLM returns invalid JSON (text mixed with JSON),
`_parse_response()` falls back to using the entire raw string as
`response_text`, which gets spoken aloud including JSON syntax.

**Fix:** In the `JSONDecodeError` handler, strip JSON-like content from
the raw text before using it as response_text. If nothing remains after
stripping, use a fallback message.

**Location:** `server/nlu.py:_parse_response()`

### 2. Satellite Registry + Push API

**Satellite-side changes:**
- Add HTTP server (aiohttp) with endpoints:
  - `POST /play` — receive WAV audio bytes, play immediately
  - `POST /volume` — adjust playback volume
  - `GET /status` — return current pipeline state
- Announce via mDNS (`_kaare-sat._tcp`) at startup
- Send `register` message on WebSocket connect:
  ```json
  {
    "type": "register",
    "satellite_id": "rpi-stue",
    "room": "living_room",
    "http_port": 8080,
    "capabilities": ["speaker", "mic", "wake_word"]
  }
  ```

**Server-side changes:**
- New `SatelliteRegistry` class:
  ```python
  class SatelliteRegistry:
      """Track connected satellites and their capabilities."""
      _satellites: dict[str, SatelliteInfo]

      def register(self, satellite_id, room, ip, http_port, capabilities)
      def unregister(self, satellite_id)
      def get_by_room(self, room) -> list[SatelliteInfo]
      def get_all_online(self) -> list[SatelliteInfo]
      def push_audio(self, satellite_id, wav_bytes) -> bool
      def broadcast_audio(self, wav_bytes, rooms=None) -> int
  ```
- mDNS listener (zeroconf library) for auto-discovery
- Update WebSocket handler to process `register` messages
- Audio response changes from WebSocket-back to HTTP push

### 3. Sonos via HA Scripts

**Replace** current `sonos.py` (HTTP file server approach) with HA script calls.

**New approach:** Send text (not audio) to HA, let HA handle TTS + playback.

Available HA scripts:
- `script.sonos_tts_norwegian_speak` — single speaker
- `script.sonos_broadcast` — all speakers
- `script.neo_sonos_broadcast_2025` — newer broadcast variant

**Room-to-Sonos mapping:**
- living_room → media_player.living_room
- garage → media_player.garage
- basement → media_player.basement
- kids_bedroom → media_player.kids_bedroom (quiet 21:00-07:00)

**Quiet hours logic** preserved from current implementation.

### 4. Speaker Recognition (VoiceID)

**Model:** ECAPA-TDNN via SpeechBrain (runs on server GPU).

**Enrollment:**
1. User says "Kaare, registrer stemmen min" (or "Kaare, register my voice")
2. Kaare asks for name, then requests 3-5 spoken sentences (~10-15s)
3. Server computes speaker embedding, stores in `profiles.json`
4. Profile includes: name, role (admin/family/child/guest), embedding vector

**Per-request identification:**
1. Audio from STT is also fed through speaker encoder
2. Embedding compared against profiles (cosine similarity)
3. Result injected into NLU context: `{speaker: "Mikalv", role: "admin", confidence: 0.92}`
4. Unknown speakers treated as "guest" with limited permissions

**Profile storage:**
```json
{
  "mikalv": {
    "name": "Mikalv",
    "role": "admin",
    "embedding": [0.1, -0.2, ...],
    "enrolled_at": "2026-02-27"
  },
  "partner": {
    "name": "Partner",
    "role": "family",
    "embedding": [...]
  }
}
```

**Role-based behavior:**
- System prompt is augmented with speaker context
- Children: restricted HA control (no lights after bedtime, etc.)
- Guests: basic Q&A only, no HA control
- Admin: full access including HA admin operations

### 5. Proactive Speech via HA

**Server REST API** (new endpoint on :8080):

```
POST /api/speak
{
    "text": "God morgen! Det er 5 grader ute.",
    "room": "living_room",
    "targets": ["satellite", "sonos"],
    "priority": "normal"
}
```

Priority levels:
- `normal` — respects quiet hours
- `urgent` — ignores quiet hours (fire alarm, security)

**HA automation example:**
```yaml
automation:
  - trigger:
      - platform: state
        entity_id: binary_sensor.living_room_motion
        to: "on"
    condition:
      - condition: time
        after: "06:30"
        before: "09:00"
    action:
      - service: rest_command.kaare_speak
        data:
          text: >
            God morgen! Det er {{ states('sensor.outdoor_temp') }} grader ute.
          room: "living_room"
```

### 6. Broadcast

**Voice-triggered:** "Kaare, broadcast at det er middag"

**NLU action:**
```json
{"action": "broadcast", "message": "Det er middag!", "confidence": 5}
```

**Server behavior:**
1. Generate response text (e.g., "Mikalv sier: det er middag alle sammen!")
2. Run TTS
3. Push audio to ALL online satellites via registry
4. Trigger Sonos broadcast via HA script
5. Respect quiet hours per room

**Room-filtered broadcast:** "Kaare, si til barna at de skal legge seg"
→ broadcast only to children's room satellites.

### 7. News Briefing (RSS)

**RSS feed aggregation + LLM summarization.**

**Modes:**
- **On-demand:** "Kaare, hva er nytt?" → fetch latest 3-5 headlines, summarize
- **Morning briefing (proactive):** Triggered by motion sensor + VoiceID.
  Includes: weather, news, calendar(?), time

**Implementation:**
```python
class NewsBriefing:
    def __init__(self, feeds: list[str]):
        self._feeds = feeds  # RSS URLs (NRK, VG, tech, etc.)

    def fetch_headlines(self, max_items=5) -> list[dict]:
        """Fetch latest news from all configured feeds."""

    def summarize(self, headlines, nlu_engine) -> str:
        """Use LLM to create a short Norwegian audio-friendly summary."""
```

**New NLU action:** `news_briefing`

### 8. Deep HA Integration

Beyond basic device control — Kaare as HA admin assistant.

**New capabilities:**
- "Kaare, hvilke enheter er offline?" → GET /api/states, filter unavailable
- "Kaare, hva bruker mest strom?" → read energy sensors
- "Kaare, lag en automation som..." → LLM generates HA YAML, saves via API
- "Kaare, hva skjedde i natt?" → read HA logbook
- "Kaare, rydd opp i duplikate enheter" → find similar entity_ids

**Implementation:** New `ha_admin_tool.py` in `server/tools/`.
Requires `admin` role (VoiceID) for sensitive operations.

### 9. Persistent Conversation Memory

**Replace** in-memory conversation history with persistent storage.

**Storage:** SQLite database on server.

**Schema:**
- conversations: id, speaker, satellite_id, started_at, ended_at
- messages: id, conversation_id, role, content, timestamp
- summaries: id, conversation_id, speaker, summary, topics, date

**Features:**
- Auto-summarize on idle timeout (existing logic, now persistent)
- Per-speaker history
- Recent context injection into system prompt
- Search: "Kaare, hva snakket vi om i gar?"

### 10. Logging + Prism Integration

**Enhanced logging:**
- Extend SessionLogger with: VoiceID results, per-step timing, model selection, errors
- Rotatable daily log files, auto-cleanup after 30 days
- Structured JSONL format (already in place)

**Prism indexing:**
- Async indexing: logs → Prism bulk API
- Index: transcriptions, NLU results, conversation summaries, errors
- New tool: `prism_tool.py` for search/index operations
- Kaare can search: "Kaare, nar sa jeg sist noe om varmepumpa?"
- Dashboard data: response times, model usage, error rates

## Network Topology

```
Network A: 192.168.87.0/24
  - Server: 192.168.87.242
  - RPi Satellite: 192.168.87.199
  - (future satellites on same network)

Network B: 10.77.42.0/24
  - HA: 10.77.42.184:8123
  - Sonos speakers (various IPs)

Bridge: HA bridges both networks
  - Server → HA API: works (routed)
  - Satellite → HA: not direct (via server)
  - Sonos → Server HTTP: does NOT work (different networks)
    → Solution: use HA scripts (text-based, not audio file serving)
```

## Data Flows

### Normal Voice Request
1. Satellite detects wake word
2. Satellite streams audio via WebSocket to server
3. Server: STT → VoiceID → NLU → Tool dispatch → TTS
4. Server pushes audio to satellite via HTTP POST /play
5. (Optional) Server sends text to Sonos via HA script

### Proactive Speech
1. HA automation triggers (e.g., motion sensor)
2. HA sends POST to server REST API: /api/speak
3. Server runs TTS
4. Server pushes to target satellite(s) + Sonos

### Broadcast
1. User: "Kaare, broadcast at det er middag"
2. NLU: action=broadcast
3. Server generates audio
4. Server pushes to ALL satellites + Sonos (respecting quiet hours)

### Voice Enrollment
1. User: "Kaare, registrer stemmen min"
2. Kaare asks for name + 3-5 sentences
3. Server computes speaker embedding
4. Stored in profiles.json

## Implementation Priority

| Phase | Components | Effort | Dependencies |
|-------|-----------|--------|-------------|
| 1 | JSON bug fix | Small | None |
| 2 | Satellite HTTP push API | Medium | None |
| 3 | Satellite Registry (WebSocket + mDNS) | Medium | Phase 2 |
| 4 | Sonos via HA scripts | Small | None |
| 5 | Proactive speech REST API | Medium | Phase 2, 3 |
| 6 | Broadcast | Medium | Phase 2, 3, 5 |
| 7 | Speaker Recognition (VoiceID) | Large | SpeechBrain setup |
| 8 | Persistent conversation memory | Medium | None |
| 9 | News briefing (RSS) | Medium | None |
| 10 | Deep HA integration | Large | Phase 7 (role-based) |
| 11 | Prism logging integration | Medium | Prism instance |

Phases 1-4 can be done in parallel. Phase 7 (VoiceID) is the largest
single effort but independent of most other phases.
