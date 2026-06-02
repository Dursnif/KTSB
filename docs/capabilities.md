# Kåre The Smart Butler — Capabilities

This document is a deep-dive companion to the [README](../README.md). It covers every
user-facing feature, the agent network behind them, and the technical architecture
underneath. Start at the top for the user experience; scroll down for implementation detail.

---

## Contents

1. [Chat Interface](#1-chat-interface)
2. [Smart Home Control](#2-smart-home-control)
3. [Camera Intelligence](#3-camera-intelligence)
4. [Memory and Personalization](#4-memory-and-personalization)
5. [The Agent Network](#5-the-agent-network)
6. [Nightly Operations](#6-nightly-operations)
7. [Notes, Timers, and Media](#7-notes-timers-and-media)
8. [Search and Knowledge](#8-search-and-knowledge)
9. [Image Generation](#9-image-generation)
10. [Voice System](#10-voice-system)
11. [Users, Roles, and Access Control](#11-users-roles-and-access-control)
12. [Admin Panel and Configuration](#12-admin-panel-and-configuration)
13. [Tool System](#13-tool-system)
14. [LLM Backends and Integrations](#14-llm-backends-and-integrations)
15. [Service Profiles](#15-service-profiles)
16. [Capability Matrix](#16-capability-matrix)

---

## 1. Chat Interface

**Where:** Home page (user-facing)

The main interface is a chat window. Users type or speak; Kåre responds in text with
optional voice output on any speaker on the network.

**Input methods:**
- Text (keyboard)
- Voice — microphone input transcribed by Whisper large-v3
- Images — drag-and-drop, paste, or file picker; Kåre analyses the image content using
  a vision model before responding

**Response display:**
- Inline images rendered inside the message bubble, clickable to full-screen
- Execution trace — a collapsible panel showing which tools were called, in what order,
  and how long each step took; off by default, toggled in user settings
- Miss Kåre panel — a small floating comment from the evaluator agent, shown when she
  has something to add; dismissible

**Conversation state:**
- Recent turns are loaded on page open
- Miss Kåre's nightly portrait of each user is injected into her evaluation context,
  so her comments reflect longer-term observations about the user — not just the current turn

**Key files:** `frontend/src/pages/user/Home.tsx`, `kaare_core/routers/router_generate.py`

---

## 2. Smart Home Control

**Where:** Any user message; aliases configured in Settings → Aliases

Kåre connects to Home Assistant and can control anything HA knows about: lights, locks,
thermostats, covers, media players, scenes, and scripts.

**How it works:**
1. User says something like *"dim the bedroom lights to 30%"*
2. Kåre calls the `styr_enhet` tool with the resolved `entity_id` and action
3. The HA-gateway (port 8002) relays the call to Home Assistant's REST API
4. Kåre confirms the result in its reply

**Alias system:**
You define your own room names and device nicknames in Settings → Aliases. These map
names in any language to Home Assistant entity IDs. Kåre can therefore understand
*"the lamp in the corner of the bedroom"* without any training — you add the alias once.

**Reading the home:**
The `les_ha` tool lets Kåre list all rooms, all devices in a room, or check the current
state of a specific entity. Used automatically when a user asks *"what lights are on?"*

**History:**
`styr_enhet` supports `action: ha_history` — returns what state a device was in over a
time period. Useful for questions like *"was the heating on all week?"*

**Fastpath:**
Simple, high-confidence commands bypass the LLM entirely and go directly to the HA-gateway.
Rules are YAML-based and admin-verified, making responses near-instant for common patterns.

**Key files:** `kaare_ha_gateway.py`, `kaare_fastpath.py`, `kaare_core/ha/`,
`kaare_core/tools/executor_ha.py`

---

## 3. Camera Intelligence

**Where:** Any user message; camera configuration in Settings → Cameras

Kåre integrates with Frigate NVR for camera access and computer vision.

**What you can ask:**
- *"Is there anyone outside?"* → Kåre takes a snapshot and analyses it with a vision model
- *"What happened at the front door in the last two hours?"* → Frigate event query
- *"Show me the last time a car was detected in the driveway"* → filtered event lookup with
  snapshot display

**Automatic announcements:**
Configure each camera with detection rules: object type (person, car, animal, package),
minimum confidence, minimum duration, and whether to announce over speakers. When Frigate
fires a matching MQTT event, Kåre analyses the snapshot and speaks the result.

**Face recognition:**
Frigate 0.17+ supports face recognition. Kåre surfaces face matches in event queries and
can filter by known face label.

**Admin controls (Settings → Cameras):**
- Global enable/disable for all camera analysis
- Per-camera object configuration with confidence sliders and duration thresholds
- Storage limits for snapshots and analysis logs
- Analysis log with thumbnails, metadata, and retry for failed analyses

**Key files:** `adapters/frigate_adapter.py`, `kaare_core/tools/executor_camera.py`,
`adapters/mqtt_adapter.py`

---

## 4. Memory and Personalization

Kåre has three memory layers, an evolving user profile, a self-image it writes itself,
and a per-user portrait maintained by Miss Kåre.

### Short-Term Memory (STM)

Held in RAM, global with per-user filtering. Stores the last 40 conversation turns plus
tool call results. Auto-saved to disk every 5 seconds as a snapshot.

The STM is injected into the LLM context at the start of every request — this is how Kåre
remembers what was said earlier in the same conversation.

### Long-Term Memory (LTM)

Stored in SQLite (`state/memory/interactions.db`). Every interaction is logged with full
metadata: prompt, intent, entity acted on, outcome, model used, confidence.

Each night, the nightjob compresses batches of 20 interactions per user into narrative
**episodes** — a short summary of what happened, what worked, what patterns emerged.
Episodes are more useful than raw logs because they fit in context.

Kåre can search its own LTM via the `minne` tool:
- `search` — semantic search across past episodes
- `fetch_stm` — retrieve older STM snapshots
- `fetch_unverified` — surface observations awaiting user confirmation

### Semantic Memory

Episodes are indexed in a Qdrant vector database (384-dim embeddings, per-user
partitioned). Before every LLM call, the most relevant episodes are retrieved and injected
as context. This is how Kåre remembers things from months ago without those memories
occupying permanent context space. Minimum similarity score: 0.35.

Requires the `medium` or `full` service profile.

### User Profile

Each user has a YAML profile (`state/users/{user_id}/profile.yaml`) with structured
sections: preferred communication style, interests, health context, family dynamics,
concerns, things that delight them.

Kåre reads and writes the profile via the `brukerprofil` tool. The nightly reflection
meeting produces new observations that are written to the profile automatically.

### Self-Image

Kåre maintains a free-text self-description (`state/personality_self.md`) that it writes
and edits itself via the `selvbilde` tool. This is injected at the end of the system
prompt on every request. Jang (the slow reflection agent on the NUC) updates it every
10 minutes based on recent events. Over time, Kåre's self-image evolves based on its
interactions.

### Miss Kåre Portrait

Each night, Miss Kåre reads the last 24 hours of interactions for each user and writes
1–3 dated observations to `state/users/{user_id}/miss_kare_portrait.md`. This portrait
(capped at 3000 characters, 90-day rolling window) is injected into Miss Kåre's evaluation
context on every response and into the nightly reflection meeting. It gives Miss Kåre
continuity — her comments reflect what she has noticed about the user over time, not just
the current turn.

**Key files:** `kaare_core/memory/short_term.py`, `kaare_core/memory/long_term.py`,
`kaare_core/memory/semantic_memory.py`, `kaare_core/users/profile_manager.py`,
`kaare_miss_kare_nightjob.py`

---

## 5. The Agent Network

KTSB is not a single AI — it is a small team. Six agents run simultaneously, each with a
distinct role.

| Agent | Role |
|-------|------|
| **Kåre** | Primary assistant. Handles all user interaction, orchestrates tools, forms responses. |
| **Miss Kåre** | Evaluator. Reads Kåre's responses and adds a short human comment when something touches her — warmth, worry, a quiet observation. Never technical. Also synthesises the nightly user portrait. |
| **Mechanic** | Technical generalist. Code, logs, hardware, system inspection. Called by Kåre via the `mechanic` tool or by the developer meeting. |
| **Miss Library** | Knowledge agent. Searches the web and synthesises results into cited answers. Does not use training data — only sources it can read and cite. |
| **Jing** | Fast inner voice. Runs continuously, digesting Home Assistant events and system events into short thought fragments. |
| **Jang** | Slow reflection. Wakes periodically, reads Jing's thoughts, and decides whether to update Kåre's self-image or add a note. |

**Personality variants:**
Miss Kåre has three personality variants (empathic / analytical / challenging) selectable
in Settings → Agents. Mechanic has four variants — standard, analyst, critic, and
investigator — each with a different tool set. The investigator variant (used in the
developer meeting) does not have shell access.

**Resource sharing and queuing:**
Miss Kåre, Mechanic, and Kåre's fallback path can all share a single Ollama instance.
A cross-process file-lock mutex (`fcntl.flock`) serialises access — only one caller uses
the model at a time, the others queue. The practical consequence: if the primary LLM goes
down while Mechanic is mid-job, Kåre's fallback will wait in line behind it. Miss Kåre's
evaluator has a 60-second timeout and silently skips rather than block a response;
Mechanic will wait up to 5 minutes; Kåre's fallback waits indefinitely until the lock
is free.

Jing and Jang are designed to run on a separate low-power device (CPU / NPU via OpenVINO)
and have no GPU dependency.

**Agent tool access** is configured in `configs/tool_permissions.yaml`. Miss Kåre can
consult Miss Library directly during evaluation via a structured query format.

**Key files:** `kaare_core/agents/miss_kare/`, `kaare_core/agents/mechanic/`,
`kaare_core/agents/miss_library/`, `kaare_core/model_lock.py`, `kaare_core/reflection_loop.py`

---

## 6. Nightly Operations

One sequential job starts at 03:00 via `kaare-night-sequence.timer`. Each step has a
timeout; a failure or timeout in one step does not stop the subsequent steps.

### Step 1 — Memory compression (up to 60 min)

`kaare_nightjob.py` runs first:

1. Fetches all uncompressed interactions per user in batches of 20
2. Calls the LLM to produce a narrative episode per batch: what happened, what worked, patterns
3. Stores each episode in SQLite and indexes it in Qdrant for semantic search
4. Compresses yesterday's STM into a daily summary
5. Ingests Jing's accumulated thought fragments into the global episode stream (batches of 30)
6. Cleans up Argus events older than 30 days from Qdrant

Result: tomorrow's semantic search includes today's conversations.

### Step 2 — Miss Kåre portrait synthesis (up to 30 min)

`kaare_miss_kare_nightjob.py` runs for each active user:

1. Reads the last 24 hours of interactions from LTM
2. Calls Miss Kåre (9B) to produce 1–3 dated observations about the user
3. Appends them to `state/users/{user_id}/miss_kare_portrait.md`

The portrait is capped at 3000 characters (90-day rolling window). It is injected into
Miss Kåre's evaluation context on every real-time response.

### Step 3 — Reflection meeting (up to 90 min)

A structured multi-agent meeting focused on understanding each user better. Runs once per
active non-admin user, under a meeting lock that prevents Jang from interfering.

**Participants:** Kåre + Miss Kåre + Meeting Leader + a user-configured cloud LLM (external perspective)

**Input context:** user profile, Miss Kåre portrait, 14 days of observations, last LTM
episodes, last daily summaries, admin-submitted topic (if any)

**Structure:** proposal round → agenda → two groups of rounds → cloud check-in → closing

**Output per user:**
- `state/memory/reflections/{user_id}/{date}.md` — full meeting transcript
- `state/users/{user_id}/observations.md` — new observations
- `state/users/{user_id}/profile.yaml` — updated with insights
- `state/users/{user_id}/user_knowledge.md` — stable, concluded facts

Reflections are PIN-protected in the GUI; users choose whether to read their own.

### Step 4 — Developer meeting (up to 60 min)

A technical investigation meeting focused on Kåre's own reliability.

**Participants:** Mechanic + Kåre + Meeting Leader + cloud LLM

**Mechanic investigates** (investigator variant, no shell access):
- Argus error log (last 24h)
- Service status and resource usage
- Git history (recent changes)
- Interaction patterns from LTM

**Output:** `state/memory/dev_meetings/{date}.md` — concrete proposals marked `FORSLAG:`
are visible in the Reflections tab.

**Key files:** `kaare_night_sequence.py`, `kaare_nightjob.py`, `kaare_miss_kare_nightjob.py`,
`kaare_reflection.py`, `kaare_dev_meeting.py`

---

## 7. Notes, Timers, and Media

### Notes

Four named lists, each with its own behaviour:

| List | Purpose | Special actions |
|------|---------|-----------------|
| `handle` | Shopping list | `mark_bought`, quantity, unit |
| `huske` | Reminders | `remind_on_login` — mentioned next time you open the chat |
| `kare` | Kåre's own notes | Internal use by Kåre |
| `arkitekt` | Project / design notes | General purpose |

**Key file:** `kaare_core/tools/notisblokk.py`

### Timers

Set one-shot or recurring timers by voice or text:
- *"Remind me in 20 minutes"*
- *"Every weekday morning at 08:00, tell me the weather"*

Repeat patterns: `hourly`, `daily`, `weekdays`, `weekend`, `weekly`.
Timers persist across restarts (`state/timers.json`). All active timers are visible in
Settings → Tools.

The `ack` action lets Kåre acknowledge pending chat notifications after they are delivered,
keeping the notification queue clean.

**Key file:** `kaare_core/tools/timer_service.py`

### Media

**Plex:** Search library, browse episodes, check what is currently playing, check playback
history, cast to any Plex client by name. Kåre uses Home Assistant as the casting bridge.

**Radio / MPD:** Play internet radio stations by name or URL, check status, stop, adjust
volume. Station presets configured in `configs/radio_stations.yaml`.

**Announce:** Speak any text over any speaker on the network — or display a message on any
screen node. Used automatically by camera alerts and timers.

**Key files:** `adapters/plex_adapter.py`, `kaare_core/tools/executor_media.py`

---

## 8. Search and Knowledge

### Web Search

Three providers supported, with automatic fallback:

| Provider | Default | Notes |
|----------|---------|-------|
| DuckDuckGo | ✅ | No API key required |
| Brave Search | Optional | API key in `configs/brave.env` |
| SearXNG | Optional | Self-hosted instance URL in settings |

**Flow:** search → fetch page content (trafilatura) → Miss Library synthesises → answer.
Miss Library only uses what she can read from the pages. A trusted-sources filter
(`configs/trusted_sources.yaml`) can restrict results to known-good domains.

### Weather

Five providers, configurable in Settings → Web Search & Weather:

| Provider | Coverage | Notes |
|----------|---------|-------|
| met.no | Norway / global | Default. Free, no key. MetAlerts warnings, air quality. |
| Open-Meteo | Global | Free, no key. UV index. |
| OpenWeatherMap | Global | API key required |
| WeatherAPI | Global | API key required |
| PirateWeather | Global | API key with free tier. Dark Sky-compatible. |

**Feature toggles:** feels-like temperature, UV index (Open-Meteo), sunrise/sunset, MetAlerts
warnings (met.no), air quality.

**Tides:** optional tide data with three providers — Kartverket (Norwegian coast, free),
Stormglass (global, API key required), or auto (Kartverket with Stormglass fallback).

**Camera weather:** a configured camera can be analysed by the vision model before
answering a weather question — useful for confirming whether it is actually raining or
sunny regardless of the forecast.

**Home Assistant sensors:** Kåre can read live sensor values directly from HA instead of
(or in addition to) the provider forecast. Nine entity fields: outdoor temperature, wind
speed, wind gust, wind direction, precipitation, precipitation last hour, precipitation
today, humidity, and air pressure.

**Key files:** `adapters/web_search_adapter.py`, `adapters/weather_adapter.py`,
`kaare_core/agents/miss_library/`

---

## 9. Image Generation

Kåre can generate and analyse images.

**Generation:** Any OpenAI-compatible image generation endpoint (user-configured in
Settings → LLM). Text prompt → image. Supports negative prompts.

**Editing:** Inpainting mode — upload an image and describe the change. Requires a
model that supports the `/v1/images/edits` endpoint.

**Storage:** Generated images are stored per user at `state/images/{user_id}/output/`.
Uploaded input images land in `state/images/{user_id}/input/`. Storage is automatically
rotated when a user exceeds 500 files or 200 MB — oldest files are deleted first.

**Viewing and analysis:** The `se_bilder` tool lets Kåre browse its own generated images
and re-analyse them with the vision model on demand.

**Key files:** `adapters/image_generation_adapter.py`, `kaare_core/image_store.py`

---

## 10. Voice System

Kåre runs a Wyoming-protocol TCP server (port 10300). Any Wyoming-compatible satellite —
including the official Home Assistant voice satellite firmware — can connect and use Kåre
as its STT/TTS backend.

### Speech-to-Text

Model: faster-whisper `large-v3`, int8 quantisation, CPU.
Handles Norwegian, English, and German without configuration changes.

### Text-to-Speech

Model: Piper (Norwegian ONNX model, 22050 Hz).
TTS-ducking pauses MPD playback automatically during spoken responses.

### Output Targets

Seven output node types are supported:

| Type | How it works |
|------|-------------|
| Wyoming | TCP satellite — mic in, WAV out |
| Chromecast / Nest Hub | Cast via Home Assistant |
| AirPlay | Direct AirPlay stream |
| Snapcast | Multi-room synchronised audio |
| ESP32 | Direct HTTP API to custom hardware |
| HA Media Player | Any HA `media_player` entity |
| DLNA | DLNA/UPnP-compatible renderers |

Seven additional display-capable node types are also supported (see Settings → Nodes):
Chromecast, Apple TV, Samsung TV, Android TV, Google TV, Amazon Fire TV, LG TV — all carry
both audio and display capability. A projector type (display-only) rounds out the total
to 14 node types across three sections (audio-only, multi, display-only).

Nodes are configured in Settings → Nodes. Each node has a type, room, and optional
default user (for voice-triggered identity).

Voice enrollment is per-user: record a short sample in Settings → Users, and Kåre will
identify you by voice on supported hardware.

**Key files:** `kaare_core/voice/wyoming_server.py`, `kaare_core/voice/providers/`,
`services/voice/`

---

## 11. Users, Roles, and Access Control

### Five roles

| Role | Additional tools vs. previous role |
|------|-------------------------------------|
| Child | Device control, timers, weather, local library, image generation, media, notes, announce |
| Teen | + web search, library with online access |
| Young Adult | + long-term memory access |
| Adult | + camera access (Frigate snapshots and events) |
| Admin | All tools, all settings |

All roles always include: self-image, world model, user profile, inner thoughts, thought
history — Kåre's introspective tools are never filtered.

### Authentication

- PIN-based login (bcrypt hashed, minimum 4 characters)
- JWT session tokens (HS256, expiry configurable — default 4 hours)
- Rate limiting: 5 failed attempts per 15 minutes per IP
- Forced PIN change on first login

### VPN and remote access

WireGuard VPN with three per-user access levels:

| Level | What works remotely |
|-------|---------------------|
| `local_only` | No remote access |
| `ai_only` | Chat with Kåre; no home automation writes |
| `full_access` | Full functionality over VPN |

VPN client configs and QR codes are generated from Settings → Users. Requires a DuckDNS
hostname and a Caddy reverse proxy (included in the `vpn` Docker profile).

### Tool permission matrix

The admin can customise which tools each role can access — the defaults above are a
starting point. Changes take effect immediately without restart.

**Key files:** `kaare_core/users/auth.py`, `kaare_core/users/store.py`,
`kaare_core/vpn.py`, `configs/tool_permissions.yaml`

---

## 12. Admin Panel and Configuration

The admin panel is a React application (port 5173). All configuration happens here — no
manual YAML editing required after the initial install.

### Pages

| Page | Purpose |
|------|---------|
| Dashboard | Live service status, active users, onboarding checklist |
| Users | Create / edit / delete users, PIN management, VPN client QR codes, voice enrollment, tool permission matrix |
| Settings | 12 configuration areas (see below) |
| Aliases | Map device names to HA entity IDs; manage room groupings |
| Nodes | Configure voice and display output nodes (14 types: 6 audio-only, 7 multi audio+display, 1 display-only) |
| Tools | SSH node management (add/edit/delete/test, linux and HA OS presets, sudo config); active timers with per-user counts and configurable max; tool execution log (last 80 entries, live, colour-coded by source) |
| Reflections | Meeting notes by date, topic submission, PIN-protected user views |
| System | Hot-reload config, health check, hardware info, service restart, manual memory compression, config rollback, automated test suite (36 tests — run from GUI, results shown inline) |
| Agent Messages | Read-only view of inter-agent exchanges |

### Settings areas

| Area | What you configure |
|------|-------------------|
| General | Location, timezone, GUI language, assistant language |
| Home Assistant | HA gateway URL and timeout; long-lived access token; advanced log bridge settings (log URL, timeout, allowed action filter) |
| MQTT / VPN | MQTT broker: host, port, credentials, TLS, topic prefix, client ID, reconnect interval; WireGuard VPN: DuckDNS hostname, WireGuard port |
| LLM / Models | **Infrastructure:** Docker socket control, running container table. **Per agent role** (Kåre, Miss Kåre, Mechanic, Miss Library, fallback, cloud, image): provider (Ollama / vLLM / OpenVINO / cloud), base URL, model selection with installed-model dropdown (pull / delete), thinking mode, keep-warm toggle, model sharing (share_with), GPU assignment, vLLM Docker parameters (gpu_memory_utilization, kv_cache_dtype, max_model_len), Docker restart. **Whisper STT:** enable/disable, backend (faster-whisper or OpenVINO), model preset (large-v3 / turbo / medium / small), compute type, language. **Piper TTS:** one voice model per language, download and activate presets per language. **BGE-M3 embedding:** device (NPU/CPU), model selection, model path. **Semantic memory embedding (MiniLM):** enable/disable, model path |
| Reflection | Meeting schedule (enable/disable); token limits per participant; Meeting Leader preset for both reflection (standard / analytical / challenging / custom) and developer meeting (standard / strict / exploratory / custom) |
| Web & Weather | Weather provider + feature toggles (feels-like, UV, sun times, alerts, air quality, tides, camera weather); 9 HA sensor entity fields; web search provider + fallback provider (DDG / Brave / SearXNG); Brave API key and locale; trusted-sources list (categories + domains, add/remove) |
| Images | Frigate URL, snapshot and analysis log retention; per-camera analysis config (object types, confidence thresholds, announce on detection), detection log with thumbnails |
| Assistant Settings | Personality mode (6 levels: minimal / lightweight / standard / full / complete / custom with editable text); assistant name; hotword; self-image contributor control (all users / selected users / admin only) |
| Integrations | Frigate URL, timeout, enable/disable; Plex URL and token |
| Distribution | Three preset profiles (full / medium / lightweight) with one-click apply; per-domain enable/disable toggles (Home Assistant, Frigate, weather, time etc.); per-service enable/disable toggles (embedding, agents, voice, Jing, Jang) with availability badges |
| Agents | Per-agent tool toggles (Mechanic: code exploration, system inspection, web search, Argus search, shell, memory; Miss Kåre: consult Miss Library; Miss Library: consult Miss Library toggle); Mechanic role for developer meetings (investigator / critic / analyst / custom); Miss Kåre role for reflection meetings (empathic / analytical / challenging / custom) |
| Explanations | Plain-language guide to every setting (read-only) |

---

## 13. Tool System

Kåre uses a structured tool system. All tools follow the **action-parameter pattern**:
one tool per domain, with an `action` enum that selects the operation. This keeps the
schema small and predictable for the LLM.

### All 28 tools

| Tool | Domain | Actions |
|------|--------|---------|
| `styr_enhet` | HA device control | turn_on, turn_off, toggle, set_level, set_color_temp, set_color, ha_history |
| `les_ha` | HA state reading | room_list, room_devices, status |
| `timer` | Timers | clock, set, cancel, list, ack |
| `get_weather` | Weather | *(location optional)* |
| `søk_nett` | Web search | *(query)* |
| `library` | Wiki + web knowledge | search, fetch_article, fetch_url, online |
| `minne` | Memory retrieval | search, fetch_unverified, confirm, fetch_stm |
| `søk_i_argus` | System log search | *(semantic query)* |
| `mechanic` | Mechanic agent | search, delegate, result, cancel, comment |
| `restart_docker_container` | Container management | *(container enum)* |
| `les_indre_tanker` | Read Jing's thoughts | *(no parameters)* |
| `les_tankehistorikk` | Thought history | *(count, filter, only_recovery)* |
| `selvbilde` | Self-image | read, update, edit, delete |
| `verden` | World model | read, update_field, add, delete, edit, read_var, set_var, delete_var, list_vars |
| `brukerprofil` | User profile | read, update, update_house, set_field, edit, delete, curiosity |
| `notat` | Notes (4 lists) | write, read, delete, clear, done, mark_bought, clear_all |
| `reason_freely` | Extended reasoning | *(query)* |
| `utforsk_kode` | Code exploration | read, list, search |
| `inspiser_system` | System inspection | log, services, resources, git_diff, git_log, fetch_trace, trace_patterns |
| `kamera` | Frigate cameras | snapshot, events, frigate, list, analyze, show_event |
| `ssh_kommando` | SSH to configured nodes | *(node enum + command)* |
| `local_kommando` | Local shell | *(command)* |
| `kare_image` | Image generation | generate, edit |
| `se_bilder` | Image viewer | *(mode: vis / analyser; folder: input / output / all)* |
| `media` | Plex + radio | plex_search, plex_episodes, plex_play, plex_sessions, plex_history, plex_library, plex_clients, radio_status, radio_play, radio_stop, radio_volume |
| `les_møte` | Meeting notes | *(type: reflection / development)* |
| `announce` | TTS / display | say, display, list_display |
| `skriv_reflex` | Learned reflexes | suggest, confirm, reject, list |

### Model tier requirements

Tools are filtered based on the active model size. Models under 9B receive no tools at all.

| Tier | Minimum model size | Tools |
|------|--------------------|-------|
| 0 | 0.8B+ | timer, notat, styr_enhet, les_ha, get_weather, announce |
| 3 | 3B+ | søk_nett, library, minne, kamera, les_møte, kare_image, se_bilder, media |
| 9 | 9B+ | mechanic, utforsk_kode, inspiser_system, ssh_kommando, local_kommando, restart_docker_container, søk_i_argus, reason_freely, skriv_reflex |
| always | any | selvbilde, verden, brukerprofil, les_indre_tanker, les_tankehistorikk |

**Key files:** `kaare_core/tools/definitions.py`, `kaare_core/tools/executor.py`,
`kaare_core/tools/executor_*.py`

### Self-monitoring via RID traces

Every request gets a unique request ID (RID). The RID travels through the entire pipeline
and is written to four log sources: routing decisions, LLM calls, tool executions, and
think-block cache.

Kåre can inspect these traces at runtime using two actions in the `inspiser_system` tool:

- **`fetch_trace`** — reconstruct the full execution history for a given RID: which routing
  stages ran, which LLM was called and how long it took, which tools fired, whether a
  fallback was used, and what Kåre was thinking (think-blocks).
- **`trace_patterns`** — analyse the N most recent traces and summarise: average latency,
  average LLM time, fallback rate, most-used tools, recovered empty responses, slowest
  request.

RIDs are prefixed by source type: `rid-` for user requests, `rid-refl-` for reflection
meeting turns, `rid-meet-` for developer meeting turns, `rid-timer-` for timer-triggered
actions, `rid-stt-` for voice-originated requests.

The developer meeting (Step 4 of the nightly job) uses `trace_patterns` automatically to
check whether latency or fallback rate has degraded overnight.

**Key file:** `kaare_core/tools/trace_reader.py`

---

## 14. LLM Backends and Integrations

### LLM providers

Kåre supports any OpenAI-compatible LLM endpoint. Three deployment modes:

| Mode | How | When to use |
|------|-----|-------------|
| **Ollama** | Included in Docker (`ollama` profile) | Standard local deployment |
| **vLLM** | External, configured in Settings → LLM | High-throughput GPU deployment |
| **Cloud** | Any OpenAI-compatible API — user supplies endpoint URL and key | No local GPU, or additional model variety |

Each agent role (Kåre, Miss Kåre, Mechanic, Miss Library) has its own model and provider
configuration. Roles can share a provider via the `share_with` setting.

Fallback waterfall: primary provider → cloud (if configured and a conversational call) →
Ollama fallback on the Miss Kåre instance. Tool-calling requests skip the cloud step and
fall back directly to the local Ollama instance. The fallback shares the same lock as
Miss Kåre and Mechanic — if either is active when the fallback is needed, Kåre queues
and waits.

### Integrations

| Integration | Protocol | Purpose |
|-------------|----------|---------|
| Home Assistant | REST + WebSocket | Device control, sensor data, event stream |
| Frigate | REST + MQTT | Camera snapshots, motion events, face recognition |
| Plex | Plex API | Media library search and casting |
| MQTT broker | MQTT | Frigate events, sensor data, device triggers |
| Qdrant | HTTP | Vector storage for semantic memory and Argus events |
| WireGuard | VPN tunnel | Secure remote access |
| DuckDNS + Caddy | DNS + HTTPS | Automatic TLS certificates and remote hostname |
| BGE-M3 | HTTP | 1024-dim dense + sparse embeddings (Argus indexing) |

---

## 15. Service Profiles

Set `COMPOSE_PROFILES` in your `.env` to control which services start. Profiles combine:
`COMPOSE_PROFILES=ollama,full` starts everything.

| Profile | Services added | Notes |
|---------|---------------|-------|
| *(none)* | Core: API, GUI, agents, HA-gateway, Argus, semantic-embed, Qdrant, Caddy | Requires a cloud or external LLM |
| `ollama` | Local Ollama LLM server | Recommended for GPU installs |
| `medium` | BGE-M3 embedding service (port 11446) | Enables Argus semantic memory |
| `full` | Voice bridge — Whisper STT + Piper TTS (port 8011) | Enables voice input/output |
| `vpn` | WireGuard via wg-easy | Enables remote access |

**Memory requirements (approximate):**

| Profiles active | RAM without GPU | RAM with GPU |
|-----------------|----------------|--------------|
| Core only | 4 GB | 4 GB |
| + ollama (7B model) | 16 GB | 4 GB + VRAM |
| + medium | +1 GB | +1 GB |
| + full | +2 GB | +2 GB |

---

## 16. Capability Matrix

| Capability | Status | Min. profile | Key files |
|-----------|--------|-------------|-----------|
| Text chat | ✅ | Core | `router_generate.py` |
| Voice input (STT) | ✅ | full | `services/voice/` |
| Voice output (TTS) | ✅ | full | `services/voice/` |
| Image input (analysis) | ✅ | Core | `adapters/llm_adapter.py` |
| Image generation | ✅ | Core | `adapters/image_generation_adapter.py` |
| Image editing (inpainting) | ✅ | Core | `adapters/image_generation_adapter.py` |
| HA device control | ✅ | Core | `kaare_ha_gateway.py` |
| HA state reading | ✅ | Core | `kaare_core/ha/` |
| HA device history | ✅ | Core | `kaare_core/tools/executor_ha.py` |
| Alias mapping | ✅ | Core | `configs/aliases.yaml` |
| Fastpath (reflexes) | ✅ | Core | `kaare_fastpath.py` |
| Frigate snapshots | ✅ | Core | `adapters/frigate_adapter.py` |
| Frigate event search | ✅ | Core | `adapters/frigate_adapter.py` |
| Face recognition (Frigate) | ✅ | Core | `adapters/frigate_adapter.py` |
| MQTT event triggers | ✅ | Core | `adapters/mqtt_adapter.py` |
| Short-term memory (STM) | ✅ | Core | `kaare_core/memory/short_term.py` |
| Long-term memory (LTM) | ✅ | Core | `kaare_core/memory/long_term.py` |
| Semantic memory (Qdrant) | ✅ | medium | `kaare_core/memory/semantic_memory.py` |
| User profiles | ✅ | Core | `kaare_core/users/profile_manager.py` |
| Self-image (Kåre writes own) | ✅ | Core | `state/personality_self.md` |
| World model | ✅ | Core | `kaare_core/tools/executor_personality.py` |
| Miss Kåre per-user portrait | ✅ | Core | `kaare_miss_kare_nightjob.py` |
| 5-role access control | ✅ | Core | `kaare_core/users/auth.py` |
| WireGuard VPN | ✅ | vpn | `kaare_core/vpn.py` |
| Web search (DDG/Brave/SearXNG) | ✅ | Core | `adapters/web_search_adapter.py` |
| Local Wikipedia search | 📋 | medium | `kaare_core/agents/miss_library/` |
| Weather (5 providers) | ✅ | Core | `adapters/weather_adapter.py` |
| HA local sensors (weather) | ✅ | Core | `adapters/weather_adapter.py` |
| Plex media control | ✅ | Core | `adapters/plex_adapter.py` |
| Radio / MPD | ✅ | Core | `kaare_core/tools/executor_media.py` |
| TTS announcements (7 targets) | ✅ | Core | `kaare_core/voice/providers/` |
| Screen display (8 node types) | ✅ | Core | `adapters/display/` |
| Timers (incl. recurring) | ✅ | Core | `kaare_core/tools/timer_service.py` |
| Notes (4 lists) | ✅ | Core | `kaare_core/tools/notisblokk.py` |
| Miss Kåre evaluator | ✅ | Core | `kaare_core/agents/miss_kare/` |
| Mechanic technical agent | ✅ | Core | `kaare_core/agents/mechanic/` |
| Miss Library knowledge agent | ✅ | Core | `kaare_core/agents/miss_library/` |
| Jing (fast inner voice) | ✅ | Core | NUC systemd service |
| Jang (slow reflection) | ✅ | Core | `kaare_core/reflection_loop.py` |
| Nightly sequential job (03:00) | ✅ | Core | `kaare_night_sequence.py` |
| Nightly reflection meeting | ✅ | Core | `kaare_reflection.py` |
| Nightly developer meeting | ✅ | Core | `kaare_dev_meeting.py` |
| Argus log monitoring | ✅ | Core | `kaare_argus.py` |
| Think-block cache | ✅ | Core | `kaare_core/tools/think_cache.py` |
| i18n (Norwegian / English / German) | ✅ | Core | `frontend/src/locales/` |
| Onboarding wizard | ✅ | Core | `frontend/src/pages/Onboarding.tsx` |
| PWA (install as app) | ✅ | Core | `frontend/public/manifest.json` |
| Voice enrollment per user | ✅ | full | `kaare_core/voice/` |
| Learned reflexes (skriv_reflex) | ✅ | Core | `kaare_fastpath.py` |
| Mood system (VAD) | 📋 | Core | — |
| LTM source trust weighting | 📋 | Core | — |
| Per-user LTM encryption | 📋 | Core | — |
