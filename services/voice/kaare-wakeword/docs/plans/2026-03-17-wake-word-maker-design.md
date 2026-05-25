# Wake Word Maker — Web Recording App

**Date:** 2026-03-17
**Status:** Approved

## Overview

Web-based SPA for recording wake word samples (positive/negative), ambient audio, and TTS training data. Hosted on the Kåre server, accessed from any phone/browser on the local network.

## Design Decisions

- **Web over native:** No app signing, TestFlight, or Xcode. Just open a URL. Lower maintenance, works on all devices.
- **Household use:** Speaker profiles (Mikal, Partner, etc) for training data diversity. No auth needed (local network).
- **Direct upload:** Recordings POST to server, land directly in `data/` folders. No manual file transfer.
- **Server-side processing:** Trim, resample, split all happen server-side (reuses existing trim_positives.py logic).
- **Export:** ZIP download via share sheet for manual backup. iCloud not needed.

## Three Recording Modes

### Wake Word (MVP — Phase 1)
- Positive/Negative toggle
- Large circular record button
- Live scrolling waveform + dB meter during recording
- Playback with waveform after recording, approve/discard
- Auto-trim to 1.5s centered on speech (server-side)
- Counter: "12 positive, 4 negative this session"

### Ambient (Phase 2)
- Start/stop button + live timer
- Live dB meter as visual feedback
- After stop: entire recording sent to server, server splits to 1.5s clips
- Shows "X clips generated"

### TTS (Phase 3)
- Large text displaying sentence to read
- dB gate: record button disabled if background >40dB
- After recording: playback with waveform, approve -> next sentence
- Progress bar: "23 / 300"
- 300 curated Norwegian sentences with phonetic coverage

## UI

- Single-page app, mobile-first, dark theme
- Top: speaker picker + mode tabs (Wake Word | Ambient | TTS)
- Colors: blue=listening, green=OK, red=noise/discard
- Waveform: live scrolling during recording (Web Audio API AnalyserNode), static with scrub for playback

## Tech Stack

- **Frontend:** Vanilla HTML/JS/CSS, no build step
- **Audio:** Web Audio API (AudioContext + AudioWorklet for capture, AnalyserNode for waveform/dB)
- **Backend:** aiohttp (Python), separate from Kåre server
- **Storage:** Files directly in data/positive/, data/negative/, data/tts/{speaker}/

## API

### POST /api/upload
Content-Type: multipart/form-data
- file: WAV blob (raw from microphone)
- type: "wakeword_positive" | "wakeword_negative" | "ambient" | "tts"
- speaker: "mikal"
- text: "sentence..." (TTS only)

Server-side processing per type:
- wakeword_positive: resample 16kHz mono -> trim 1.5s (speech-centering) -> data/positive/
- wakeword_negative: resample 16kHz mono -> trim 1.5s -> data/negative/
- ambient: resample mono -> split 1.5s clips -> data/negative/
- tts: resample 16kHz mono -> data/tts/{speaker}/ with matching .txt file

### GET /api/stats
Returns: {"positive": 369, "negative": 1810, "tts": {"mikal": 0}, "speakers": ["mikal"]}

### GET /api/tts/next?speaker=mikal
Returns next unrecorded sentence for this speaker.

## File Naming

{type}_{speaker}_{timestamp}_{seq}.wav — guaranteed unique, no overwrite risk.

## Project Structure

recorder/
  static/
    index.html
    app.js
    style.css
    worklet.js      # AudioWorklet for capture
  server.py         # aiohttp backend
  sentences.json    # 300 Norwegian TTS sentences

Run: uv run python recorder/server.py --port 8081 --data-dir data/

## Phases

### Phase 1 (MVP): Wake Word recording
- Wake Word mode (positive + negative)
- Live waveform + dB meter
- Server-side trim to 1.5s
- Speaker picker (localStorage)
- Dark theme, mobile-first

### Phase 2: Ambient recording
- Long-recording with auto-split
- Stats page (counts per type/speaker)

### Phase 3: TTS recording
- TTS mode with sentences, dB gate, progression tracking
- 300 curated Norwegian sentences with phonetic coverage

## Not In Scope
- Authentication (local network only)
- Android-specific optimization
- Automatic retraining trigger
