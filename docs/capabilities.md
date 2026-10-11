# Kåre The Smart Butler — Capabilities

This document is a deep-dive companion to the [README](../README.md). It covers every
user-facing feature, the agent network behind them, and the technical architecture
underneath. Start at the top for the user experience; scroll down for implementation detail.

KTSB's primary assistant is called **the main agent** in this document. Its default name is
*Kåre*; you can give it any name under Settings → Assistant Settings. *Miss Kåre*, the
evaluator, is a separate agent.

**How well it works depends on the model.** Everything below describes what the code does.
With a 27B-class model as the main agent (24 GB+ GPU) that is what you get. On the default
9B, conversation works but tool use is unreliable — see section 15.

---

## Contents

1. [Chat Interface](#1-chat-interface)
2. [Smart Home Control](#2-smart-home-control)
3. [Camera Intelligence](#3-camera-intelligence)
4. [Home Awareness and Notifications](#4-home-awareness-and-notifications)
5. [Memory and Personalization](#5-memory-and-personalization)
6. [The Agent Network](#6-the-agent-network)
7. [Nightly Operations](#7-nightly-operations)
8. [Notes, Timers, and Media](#8-notes-timers-and-media)
9. [Search and Knowledge](#9-search-and-knowledge)
10. [Image Generation](#10-image-generation)
11. [Voice System](#11-voice-system)
12. [Users, Roles, and Access Control](#12-users-roles-and-access-control)
13. [Admin Panel and Configuration](#13-admin-panel-and-configuration)
14. [Tool System](#14-tool-system)
15. [LLM Backends and Integrations](#15-llm-backends-and-integrations)
16. [Service Profiles](#16-service-profiles)
17. [Security](#17-security)
18. [Capability Matrix](#18-capability-matrix)

---

## 1. Chat Interface

**Where:** Home page (user-facing)

The main interface is a chat window. Users type or speak; the main agent responds in text with
optional voice output on any speaker on the network.

**Input methods:**
- Text (keyboard)
- Voice — microphone input transcribed by Whisper (model size per language, picked in the GUI)
- Images — drag-and-drop, paste, or file picker; the main agent analyses the image content using
  a vision model before responding

**Response display:**
- Each message is labelled with who said it (the user's display name, the assistant's
  configured name), so a copied conversation keeps track of who said what
- Inline images rendered inside the message bubble, clickable to full-screen
- Execution trace — a collapsible panel showing which tools were called, in what order,
  and how long each step took; off by default, toggled in user settings
- Miss Kåre panel — a small floating comment from the evaluator agent, shown when she
  has something to add; dismissible

**Conversation state:**
- Recent turns are loaded on page open
- The main agent answers in the household language (a per-user reply language is in the code but not active
  yet)
- Miss Kåre's nightly portrait of each user is injected into her evaluation context,
  so her comments reflect longer-term observations about the user — not just the current turn

**Key files:** `frontend/src/pages/user/Home.tsx`, `kaare_core/routers/router_generate.py`

---

## 2. Smart Home Control

**Where:** Any user message; aliases configured in Settings → Aliases

The main agent connects to Home Assistant and can control anything HA knows about: lights, locks,
thermostats, covers, media players, scenes, and scripts.

**How it works:**
1. User says something like *"dim the bedroom lights to 30%"*
2. The main agent calls the `ha_control` tool with the resolved `entity_id` and action
3. The HA-gateway (port 8002) relays the call to Home Assistant's REST API
4. The main agent confirms the result in its reply

**Alias system:**
You define your own room names and device nicknames in Settings → Aliases. These map
names in any language to Home Assistant entity IDs. The main agent can therefore understand
*"the lamp in the corner of the bedroom"* without any training — you add the alias once.

**Reading the home:**
The `ha_read` tool lets the main agent list all rooms, all devices in a room, or check the current
state of a specific entity. Used automatically when a user asks *"what lights are on?"*

**History:**
`ha_control` supports `action: ha_history` — returns what state a device was in over a
time period, from Home Assistant's long-term statistics (over its WebSocket API). Switches and
other devices without statistics get their actual on/off times instead. Useful for questions
like *"was the heating on all week?"*

**Fastpath:**
Simple, high-confidence commands bypass the LLM entirely and go directly to the HA-gateway.
Rules are YAML-based and admin-verified, making responses near-instant for common patterns.

**Key files:** `kaare_ha_gateway.py`, `kaare_fastpath.py`, `kaare_core/ha/`,
`kaare_core/tools/executor_ha.py`

---

## 3. Camera Intelligence

**Where:** Any user message; camera configuration in Settings → Images/Camera

The main agent integrates with Frigate NVR for camera access and computer vision.

**What you can ask:**
- *"Is there anyone outside?"* → the main agent takes a snapshot and analyses it with a vision model
- *"What happened at the front door in the last two hours?"* → Frigate event query
- *"Show me the last time a car was detected in the driveway"* → filtered event lookup with
  snapshot display

**Automatic announcements:**
Configure each camera with detection rules: object type (person, car, animal, package),
minimum confidence, minimum duration, and whether to announce over speakers. When Frigate
fires a matching MQTT event, the main agent analyses the snapshot and speaks the result.

**Face recognition:**
Frigate 0.17+ supports face recognition. The main agent surfaces face matches in event queries and
can filter by known face label.

**Admin controls (Settings → Images/Camera):**
- Global enable/disable for all camera analysis
- Per-camera object configuration with confidence sliders and duration thresholds
- Storage limits for snapshots and analysis logs
- Analysis log with thumbnails, metadata, and retry for failed analyses
- Away-mode escalation panel: nighttime window (start/end hour), minimum confidence for
  escalation, and whether an unlabelled detection at a perimeter camera at night is
  automatically treated as high priority

**Key files:** `adapters/frigate_adapter.py`, `kaare_core/tools/executor_camera.py`,
`adapters/mqtt_adapter.py`

---

## 4. Home Awareness and Notifications

Beyond responding to direct requests, the main agent maintains background awareness of the
household — who's around, what sensors are reporting, and whether anything is out of the
ordinary — and can proactively notify a user when something needs attention.

### Household mode (home / away)

A single household-wide state (`state/household_state.yaml` — never per-user) tracks
whether the house is expected to be occupied. The main agent reads and writes it via the
`household` tool:

- `set_away` — with an optional reason and expected return date
- `set_home` — clears away state
- `get_status` — current mode, away-since timestamp, and reason

**Automatic return detection:** a background task polls three signals every 5 minutes
while away — the expected-return date, Home Assistant `person.*` geo-trackers, and
local-network `device_tracker.*` entities. Two or more signals agreeing on two
consecutive polls switches the household back to `home` automatically and clears any
away-mode flags written to user profiles.

### Situational context

A compact context block is assembled and injected into every system prompt, giving the main agent
always-available awareness without needing to call a tool:

- Who is speaking, from which node, and over which network (local / VPN / external)
- Current household mode (home/away)
- Recent activity from other users or nodes (drawn from STM)
- Frigate recent detections — tailed directly from the MQTT event log, deduplicated by
  camera and label
- Home Assistant entity states and recently-triggered automations
- Zigbee2MQTT sensor states, read directly over MQTT — bypasses Home Assistant entirely
- Current media activity (MPD via subprocess, Plex via a background poller)

All sources are optional and silently omitted if unavailable. Anything derived from a
sensor is phrased as "likely", never stated as certain.

### Anomaly detection (normalcy baseline)

A nightly job builds a frequency table from 28 days of Frigate detection history,
bucketed by camera, object label, hour, and weekday. Every new detection is scored with a
**deviation score** (0.0 = this happens at this hour almost every day, 1.0 = this has
never happened at this hour) and a **confidence** value based on how many days of history
exist for that bucket — under roughly two weeks of data, the score is recorded but not
yet used for alert routing. This is what lets away-mode alerting tell "the mail carrier,
every weekday around 11" apart from "someone at the back door at 3am."

The **Security** admin page shows a grouped, filterable feed of recent detections with
deviation scores, confidence badges, and click-to-view snapshot thumbnails. Column headers
(camera, object, deviation, timestamp, coverage, status) are clickable to sort within each
group. Admins can correct mis-scored events, individually or in batch; corrections feed
back into future baseline computation. A transient "undo last action" snackbar appears
after every correction — reverting restores the previous state, or removes the correction
entirely if the event was previously unassessed.

### Notifications

A single notification entry point is used by away-mode camera alerts (and available to
other sources) with four urgency tiers:

| Urgency | Behaviour |
|---------|-----------|
| Low | Injected into the user's short-term memory — surfaces next time they chat |
| Medium | Spoken announcement if outside quiet hours, otherwise held for chat |
| High | Spoken announcement immediately, regardless of hour |
| Critical | Spoken announcement immediately, plus a push notification once a push channel is configured |

In-memory deduplication per user and event type prevents repeated alerts for the same
ongoing situation; critical alerts are never deduplicated. Quiet hours, the push
notification target, per-user notification channel, and anomaly-alert thresholds are
configured in Settings → Notifications; per-camera away-mode timing lives in
Settings → Images/Camera (see section 3).

**Key files:** `kaare_core/tools/household_state.py`, `kaare_core/presence_monitor.py`,
`kaare_core/context_builder.py`, `kaare_core/normalcy.py`, `kaare_core/notify.py`,
`kaare_core/domain/frigate_responder.py`, `frontend/src/pages/admin/Security.tsx`

---

## 5. Memory and Personalization

The main agent has three memory layers, a shared knowledge store, an evolving user profile, a
self-image it writes itself, and a per-user portrait maintained by Miss Kåre.

### Short-Term Memory (STM)

Held in RAM, fully isolated per user (`STMRegistry` — one instance per user, not a shared
store with filtering). Stores the last 40 conversation turns plus tool call results. Auto-saved to disk every 5 seconds as a snapshot, and again on
controlled shutdown so no in-progress conversation is lost on a service restart.

The STM is injected into the LLM context at the start of every request — this is how the main agent
remembers what was said earlier in the same conversation. The current session is included
within a token budget, with only a couple of turns from before it; the main agent is told when older
turns from today are left out. Replayed user turns carry their local timestamp, and after a
long gap (6 hours by default) the main agent is told that the conversation above is from then and that
fresh data must be fetched with tools. Inside the tool loop, large tool results and images
are replaced by a short tag once the main agent has read them, and a token guard trims the oldest
content before the prompt reaches the model's limit.

### Long-Term Memory (LTM)

Stored in SQLite (`state/memory/interactions.db`). Every interaction is logged with full
metadata: prompt, intent, entity acted on, outcome, model used, confidence.

Each night, the nightjob compresses batches of 20 interactions per user into narrative
**episodes** — a short summary of what happened, what worked, what patterns emerged.
Episodes are more useful than raw logs because they fit in context.

The main agent can search its own LTM via the `memory` tool:
- `search` — word search across the user's own past conversations (decrypted first) and
  shared rows, ranked by how many search words match, plus episode summaries from semantic
  memory
- `fetch_stm` — retrieve older STM snapshots
- `fetch_unverified` — surface observations awaiting user confirmation

### Knowledge Store

World facts, the main agent's self-image and the main agent's own notes live in one SQLite database
(`state/memory/knowledge.db`), one row per fact with source, importance, validity dates and
a last-validated timestamp. Repeated writes to the same field update the row instead of
duplicating it. Relevant rows are queried live for each prompt, ranked, rather than pasting a
truncated file. A weekly pass in the night sequence re-validates the store (see section 7).
When upgrading from an older version, the previous flat files (`world.md`, `world_vars.json`,
`personality_self.md`, `notater.json`) are imported once on first start; the old files are
left untouched.

### Semantic Memory

KTSB indexes episodes in a Qdrant vector database, per user. Before every LLM call it fetches the
most relevant episodes and adds them as context. This is how the main agent remembers
things from months ago without those memories occupying permanent context space.

**Off on a fresh install.** Turn it on in Settings → LLM / Models → Semantic memory: pick a
size, click «Download», then «Use this»: small (MiniLM int8, 118 MB), medium (mpnet, 1.1 GB) or large
(BGE-M3, needs the `medium` profile). Each model gets its own collection, rebuilt from the
stored episodes, so switching back is instant; each model has its own calibrated similarity
threshold. Models live in the model folder set in Settings → LLM / Models → Model
storage (Expert).

### The main agent's own notes (memory drawers)

The main agent sorts what it writes for itself:

- **`kare`** — short follow-ups of its own (a title, at most 600 characters). The prompt
  shows this list as an index of titles.
- **`observations`** — things it notices; counted in patterns, never pasted into the prompt.
- **`lessons`** — rules it has learned; always in the prompt (length capped in the GUI).
- **`agenda`** — problems for the nightly dev slot (section 7).
- **`resolved`** — topics settled in a conversation, with the conclusion. A settled topic is
  locked; a new note about the same thing becomes an echo that gets the conclusion as its
  answer. Who may mark a topic settled is set in Settings → Assistant Settings → «Kåre's
  memory» (admin by default). Settled topics can be reopened.

**«Relevant now»:** for each message, the 2–3 notes and world facts that match it best are
added in full text. This needs an embedding service (BGE-M3 or semantic memory); without one
the card in Assistant Settings shows «Inactive» and the prompt carries only the index.

### User Profile

Each user has a YAML profile (`state/users/{user_id}/profile.yaml`) validated against a
shared 19-section fact schema (`configs/profile_template.yaml`) — communication style,
interests, health context, family dynamics, and more. Writes are schema-checked, not
free-form, so fields stay consistent across users and over time.

The main agent reads and writes the profile via the `user_profile` tool (the `curiosity` action asks
a targeted follow-up on a specific schema field). Miss Kåre also asks follow-up questions
during live conversation, captures facts against the same schema, and applies a novelty
gate so repeated or trivial answers aren't re-recorded. The nightly reflection meeting
writes new observations through the same schema and novelty gate — not ad-hoc fields — so
data captured live and data captured at night stay in the same shape.

### Self-Image

The main agent maintains a self-description in the knowledge store that it writes and edits itself
via the `self_image` tool. It is injected into the system prompt on every request. Who may
shape it (all users, selected users, admin only) is set in Settings → Assistant Settings;
The main agent's own autonomous updates are always allowed. A background reflection loop (every
10 minutes by default, fed by Jang's thoughts) can update it based on recent events, so the
self-image evolves with the main agent's interactions.

### Miss Kåre Portrait

Each night, Miss Kåre reads the last 24 hours of interactions for each user and writes
1–3 dated observations to `state/users/{user_id}/miss_kare_portrait.md`. This portrait
(capped at 3000 characters, 90-day rolling window) is injected into Miss Kåre's evaluation
context on every response and into the nightly reflection meeting. It gives Miss Kåre
continuity — her comments reflect what she has noticed about the user over time, not just
the current turn.

**Key files:** `kaare_core/memory/short_term.py`, `kaare_core/memory/long_term.py`,
`kaare_core/memory/semantic_memory.py`, `kaare_core/memory/knowledge_store.py`,
`kaare_core/memory/relevance.py`, `kaare_core/memory/memory_prompt.py`,
`kaare_core/memory/memory_embed_models.py`,
`kaare_core/users/profile_manager.py`, `kaare_miss_kare_nightjob.py`

---

## 6. The Agent Network

KTSB is not a single AI — it is a small team of six agents, each with a distinct role (Jing
and Jang are optional).

| Agent | Role |
|-------|------|
| **Main agent** (named Kåre by default) | Primary assistant. Handles all user interaction, orchestrates tools, forms responses. |
| **Miss Kåre** | Evaluator. Reads the main agent's responses and adds a short human comment when something touches her — warmth, worry, a quiet observation. Never technical. Also synthesises the nightly user portrait. |
| **Mechanic** | Technical generalist. Code, logs, hardware, system inspection — read-only. Called by the main agent via the `mechanic` tool; runs as a background job and the main agent is notified in chat when it finishes. |
| **Miss Library** | Knowledge lookup for the main agent — no persona. Searches the local wiki (if installed), the web and optionally Norwegian law, and answers as claims with verbatim quotes and their source. Code checks that each quote really appears in the source text; unsupported material is not shown as evidence. |
| **Jing** | Fast inner voice. Runs continuously, digesting Home Assistant events and system events into short thought fragments. |
| **Jang** | Slow reflection. Wakes periodically, reads Jing's thoughts, and decides whether to update the main agent's self-image or add a note. |

**Personalities:**
Miss Kåre has one fixed personality and speaks the user's language (nb/en/de) everywhere —
chat comments, fact capture, the nightly portrait and the reflection meeting. Mechanic has
personality variants (standard, analyst, critic, investigator) with editable text.

**Mechanic is read-only.** Its shell tool can only read: chaining (`;` `&&` `$(`),
redirection, and deleting or modifying flags and subcommands are rejected before anything
runs, and every command — including rejected ones — is written to the audit log. Shell is
one switch in Settings → Agents and is off by default. The only exception is a sudo command
the admin deliberately adds for a specific node on the Tools page, and even those are never
chained. Mechanic can only run the tools it was offered in that call.

**Resource sharing and queuing:**
Miss Kåre, Mechanic, Miss Library and the main agent's fallback form one agent pool. The pool is read
from `llm.yaml`: it is the set of distinct Ollama addresses the pool roles point at, so it
works the same with one instance (a fresh Docker install: everything on the built-in
Ollama) or several, local or on other machines in the network. A cross-process file lock
serialises access per instance — one caller per instance at a time. With two or more
instances a caller takes whichever instance frees up first; with one instance callers queue.
Miss Kåre's evaluator has a short timeout and skips rather than block a response.

The pool roles share one set of options (`agent_pool:` in `llm.yaml` — thinking, context
size, sampling); only the output length is set per role. Saving any pool role's card in
Settings → LLM writes that shared block.

Jing and Jang are designed to run on any hardware — locally via OpenVINO (Intel CPU/NPU),
MLX (Apple Silicon), or generic CPU. They can also run on a separate low-power device
with results pushed to KTSB via HTTP. No GPU dependency. Platform and model path are
configured in `configs/services.yaml` under `inner_voices:`. In Docker Compose they run as the
`inner_voices` profile from their own image (`kaare-inner-voices`) — not required for any
other feature.

**Agent tool access** is configured in Settings → Agents (stored in
`configs/tool_permissions.yaml`). Miss Kåre can consult Miss Library during evaluation, in
the user's language.

**Key files:** `kaare_core/agents/miss_kare/`, `kaare_core/agents/mechanic/`,
`kaare_core/agents/miss_library/`, `services/inner_voices/`, `kaare_core/model_lock.py`,
`kaare_core/reflection_loop.py`

---

## 7. Nightly Operations

One sequential job starts once per night at the start time set in Settings → Reflection →
Night sequence (03:00 by default, in the household timezone). A small scheduler
(`kaare_night_runner.py`) starts it — in Docker inside the `kaare-reflection` container, on a
bare-metal install as `kaare-night-runner.service`. If the machine was off at the start time,
the night still runs when KTSB is back within 6 hours; a restart never runs the same night
twice, and two runs never overlap. Each step starts when the previous one has finished and has
its own timeout; a failure or timeout in one step does not stop the steps after it.

### Step 0 — Data retention cleanup

`kaare_night_sequence.py` runs configurable retention rules before anything else:
- Deletes conversations (`interactions`) and episodes older than the set number of days
  (0 = keep forever, the default), and thought history (`think_cache`) older than 30 days
- Limits are set in Settings → General → Logs and data (Expert)

### Step 1 — Memory compression (up to 60 min)

`kaare_nightjob.py`:

1. Fetches all uncompressed interactions per user in batches of 20
2. Calls the LLM to produce a narrative episode per batch: what happened, what worked, patterns
3. Stores each episode in SQLite and indexes it in Qdrant for semantic search
4. Compresses yesterday's STM into a daily summary
5. Ingests Jing's accumulated thought fragments into the global episode stream (batches of 30)
6. Cleans up Argus events older than 30 days from Qdrant
7. Recomputes the Frigate anomaly baseline (see section 4) from the last 28 days of history

Result: tomorrow's semantic search includes today's conversations.

### Step 1b — Norwegian law refresh (optional, up to 15 min)

Only when the Lovdata source is switched on (Settings → Web Search & Weather, off by
default): downloads Lovdata's open datasets once and re-indexes them locally (see section 9).
A failed download keeps yesterday's index.

### Step 2 — Miss Kåre portrait synthesis (up to 30 min)

`kaare_miss_kare_nightjob.py` runs for each active user:

1. Reads the last 24 hours of interactions from LTM
2. Calls Miss Kåre to write 1–3 dated, concrete observations about the user — tied to what
   the user actually said or did, not psychological interpretation
3. Appends them to `state/users/{user_id}/miss_kare_portrait.md`

The portrait is capped at 3000 characters (90-day rolling window). It is injected into
Miss Kåre's evaluation context on every real-time response.

### Step 2b — Knowledge review (weekly, up to 15 min)

`knowledge_reflection.py` re-validates the knowledge store (section 5) on the main agent's
model, with its full personality and memory: adjusts importance and retires facts that are
out of date. It runs weekly, or the next night when a note has been marked important. Two
safety checks guard against a degenerate model reply: a batch is rejected when too many
changes share the same reasoning, and near-duplicate facts are clustered (BGE-M3) before
review so the model does not treat a whole batch as one item.

### Step 3 — Reflection meeting (up to 90 min)

A structured multi-agent meeting focused on understanding each user better. Runs once per
active non-admin user, under a meeting lock that prevents the background reflection loop
from interfering. On by default; switch in Settings → Reflection. «Start reflection meeting»
in the GUI runs it at once.

**Participants:** the main agent + Miss Kåre + Meeting Leader. No cloud participant —
household data stays local.

**Input context:** user profile, Miss Kåre portrait, 14 days of observations, last LTM
episodes, last daily summaries, admin-submitted topic (if any), the user's own submitted
topic and comment (if any). The decrypted user data goes to the models once per meeting;
the report on disk only notes that it was used, so it does not end up in plain text.

**Structure:** proposal round → agenda → two groups of rounds → closing. Prompts and
headings follow the household language (nb/en/de).

**Output per user:**
- `state/memory/reflections/{user_id}/{date}.md` — meeting transcript
- `state/users/{user_id}/observations.md` — new observations
- `state/users/{user_id}/profile.yaml` — updated with insights
- `state/users/{user_id}/user_knowledge.md` — stable, concluded facts

Reflections are PIN-protected in the GUI; users choose whether to read their own. A parent
account (see section 12) can additionally read and comment on a managed child's
reflection.

### Step 4 — Dev slot (25-minute budget by default)

A nightly review of how KTSB itself is doing — read-only, local, no cloud. It runs after the
reflection meeting has finished.

**Input:** the dev agenda (see below), the day's Argus errors, request traces (RIDs) and
earlier findings.

**Steps:**
1. **Janus** — a second context on the main agent's model that sees only history and
   priorities (no tools) — ranks the open topics.
2. The **main agent** cleans the agenda, reviews earlier findings, and investigates one
   topic or trace at a time with read-only tools. Every topic has a theme (answers,
   tools, memory, agents, house, system, other): first one topic from each theme, then up to
   «Topics per meeting» (3 by default). A topic tried without a finding moves to the back of
   the queue. The slot compares new topics with settled ones and can propose reopening
   one.
3. **Code checks every finding**: it must point to real evidence from that night's tool
   calls. A 9B verifier then checks that the quoted evidence says what the finding claims.
   Without a 9B, the code check alone still runs.
4. A **morning report** in the GUI (Reflections → dev tab, progress per step in the user's
   language) lists confirmed findings, suspicions, and proposals. Reports are never
   overwritten.

Weekly, monthly, half-year and yearly summaries are added: code counts, Janus summarises.

**Nothing is executed.** Proposals wait for the admin, who confirms or rejects them in chat
via the `dev_proposals` tool. The slot never writes to the house, the configuration or the code.

**The dev agenda:** anyone — chat, voice, timers, the reflection meeting, Frigate event
analysis — can put an item on it with `note(list_name="agenda")`. Nothing is deleted; an
item already on the list is counted instead («already on the agenda, seen N times»). The
agenda is never pasted into the prompt — only one line with its size. If the main agent
promises to «bring it up at the next dev meeting» without writing it down, the model
enforcer catches it. Admin-submitted topics become agenda items.

Settings → Reflection: on/off and «Topics per meeting»; in Expert mode also the time budget
(25 minutes by default — what is not reached waits for the next night), maximum traces and
tool rounds per topic. The night sequence stops the slot at the latest after 60 minutes, or
the budget plus 20 minutes if that is longer.

**Key files:** `kaare_night_sequence.py`, `kaare_nightjob.py`, `kaare_miss_kare_nightjob.py`,
`kaare_core/memory/knowledge_reflection.py`, `kaare_reflection.py`, `kaare_core/dev_slot/`,
`kaare_core/tools/agenda.py`, `kaare_core/memory/learning_store.py`

---

## 8. Notes, Timers, and Media

### Notes

Eight named lists in the `note` tool, each with its own behaviour:

| List | Purpose | Special actions |
|------|---------|-----------------|
| `shopping` | Shared shopping list | `mark_bought`, quantity, unit |
| `remember` | Personal reminders (per user) | `remind_on_login` — mentioned next time you open the chat; entries can be pinned (never evicted), the injected block is capped at 2000 characters with a visible "X of Y shown" marker, near-duplicate entries are merged on write, and the list has a hard 100-entry cap (FIFO, pinned entries excluded) |
| `kare` | The main agent's own short follow-ups (section 5) | `edit` (change text, keep id/date/pin), `mark_important`, `move` to another drawer |
| `observations` | What the main agent notices (section 5) | Never in the prompt; `resolve` / `reopen` |
| `lessons` | Rules the main agent keeps (section 5) | Always in the prompt |
| `resolved` | Settled topics with their conclusion (section 5) | `reopen` |
| `architect` | Project / design notes | General purpose |
| `agenda` | Items for the nightly dev slot (section 7), each with a theme | Append-only; repeats are counted, not duplicated; `resolve` / `reopen` |

The older Norwegian list names (`handle`, `huske`, `arkitekt`) are still accepted. Writing to
a personal reminder list requires a real user — background processes are pointed to the
right list instead. Tool-call syntax that leaks into note text is stripped on write. Replies
from the lists follow the user's language.

**Key files:** `kaare_core/tools/lister.py`, `kaare_core/tools/notisblokk.py`, `kaare_core/tools/agenda.py`,
`kaare_core/tools/lessons.py`, `kaare_core/memory/learning_store.py`

### Timers

Set one-shot or recurring timers by voice or text:
- *"Remind me in 20 minutes"*
- *"Every weekday morning at 08:00, tell me the weather"*

Repeat patterns: `hourly`, `daily`, `weekdays`, `weekend`, `weekly`.
Timers persist across restarts (`state/timers.json`). All active timers are visible in
Settings → Tools.

The `ack` action lets the main agent acknowledge pending chat notifications after they are delivered,
keeping the notification queue clean.

**Key file:** `kaare_core/tools/timer_service.py`

### Media

**Plex:** Search library, browse episodes, check what is currently playing, check playback
history, cast to any Plex client by name. The main agent uses Home Assistant as the casting bridge.

**Radio / MPD:** Play internet radio stations by name or URL, check status (including
current volume), stop, adjust volume. Station presets configured in
`configs/radio_stations.yaml`.

**Announce:** Speak any text over any speaker on the network — or display a message on any
screen node. Used automatically by camera alerts and timers.

**Key files:** `adapters/plex_adapter.py`, `kaare_core/tools/executor_media.py`

---

## 9. Search and Knowledge

### Web Search

Two search providers, with a configurable fallback:

| Provider | Default | Notes |
|----------|---------|-------|
| SearXNG | ✅ | Bundled as a service in `docker-compose.yml`; other installs point at their own instance |
| Brave Search | Optional | API key; country and language settings |

**Flow:** search → fetch the pages → Miss Library extracts claims with verbatim quotes →
code checks the quotes and computes how likely each claim is → the main agent answers.
The main agent decides per search whether the user's home location is relevant
(`near_home`); only then is the location from the GUI added to the query.

**Safe, polite fetching:**
- Addresses in the home network or VPN are never fetched from search results (SSRF guard);
  downloads have a byte limit and a content-type check; on doubt the fetch is refused.
- An honest user agent with the KTSB version, `robots.txt` respected, a minimum interval per
  site, and one total deadline for the whole search chain.

**Tiered sources** (Settings → Web Search & Weather):
- **Trusted list** — sites you approve, each with a type (public authority, encyclopedia,
  newsroom, research, official, other serious) and a trust level. A few result slots are
  reserved for them.
- **Open web** — on by default; results outside the list are used too, labelled as such.
- **Blocklist** — domains that are never used.

**Official source:** when a question is about a specific organisation or product, KTSB looks
up its official website via Wikidata and searches that site (sitemap) directly.

**Probability in code, not in the model:** each claim gets a probability from the kind of
sources behind it and how many independent ones agree — e.g. open web alone tops out at
0.85, two independent trusted sources at 0.99; nothing from the web reaches certainty. It is
said in words: highly likely / likely / uncertain / unlikely. A caution setting
(careful / balanced / open) shifts the scale. The main agent also states what is at stake
(trivial / normal / high / extraordinary): for high stakes it only advises on «highly
likely» claims and points the user to the source; an extraordinary claim stays «unresolved»
however many pages repeat it.

### Work rounds (background search)

A slow web or law search can be sent off as a work round: the main agent answers the user at
once, the search runs in the background (deadline set in the GUI, 3 minutes by default), and
the main agent comes back in the chat with its own answer when the result is in — the user
never gets the raw search output. Mechanic jobs and work rounds share one background runner.

### Local Wikipedia (optional, advanced)

Miss Library can search one or more local Wikipedia collections in Qdrant (BGE-M3 embeddings),
all in parallel with the primary first, fetch whole articles across them, and — when a
structured database is configured — return tables and lists. Wikis are registered under
`wiki:` in `services.yaml`; the shipped template has none. Building a collection is a manual
step today; GUI support for it is planned.

### Norwegian law (optional)

Lovdata's open datasets (current laws and central regulations, NLOD 2.0) can be enabled in
Settings → Web Search & Weather. They are downloaded once per night (or with «Update now»),
split per section and searched locally; lovdata.no itself is never crawled. The main agent
uses `library(action="law")`: Miss Library finds the verbatim sections, the main agent
interprets them, and every answer carries Lovdata's NLOD attribution. Off by default.

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

**Home Assistant sensors:** The main agent can read live sensor values directly from HA instead of
(or in addition to) the provider forecast. Nine entity fields: outdoor temperature, wind
speed, wind gust, wind direction, precipitation, precipitation last hour, precipitation
today, humidity, and air pressure.

**Key files:** `adapters/web_search_adapter.py`, `adapters/web_fetch.py`,
`adapters/official_source.py`, `adapters/lovdata_adapter.py`, `adapters/weather_adapter.py`,
`kaare_core/tools/executor_library.py`, `kaare_core/tools/source_confidence.py`,
`kaare_core/tools/work_round.py`, `kaare_core/tools/wiki_registry.py`

---

## 10. Image Generation

The main agent can generate and analyse images.

**Generation:** Any OpenAI-compatible image generation endpoint (user-configured in
Settings → LLM). Text prompt → image. Supports negative prompts.

**Editing:** the image adapter can also call an `/v1/images/edits` endpoint, but the main
agent's `kare_image` tool currently offers generation only.

**Storage:** Generated images are stored per user at `state/images/{user_id}/output/`.
Uploaded input images land in `state/images/{user_id}/input/`. Storage is automatically
rotated when a user exceeds 500 files or 200 MB — oldest files are deleted first.

**Viewing and analysis:** The `view_images` tool lets the main agent browse its own generated images
and re-analyse them with the vision model on demand.

**Key files:** `adapters/image_generation_adapter.py`, `kaare_core/image_store.py`

---

## 11. Voice System

KTSB runs a Wyoming-protocol TCP server (port 10300). Any Wyoming-compatible satellite —
including the official Home Assistant voice satellite firmware — can connect and use KTSB
as its STT/TTS backend.

### Speech-to-Text

Model: Whisper in three sizes per household language, picked and downloaded in Settings →
LLM / Models → Whisper (speech-to-text): Norwegian uses the National Library's NB-Whisper
small / medium / large, other languages Whisper small / medium / large-v3-turbo. The default is
the smallest model for the language. A custom model can be given by Hugging Face name or link
(checked for the CTranslate2 format). CPU threads default to «Auto». Models live in the model folder,
so a new container does not download them again. The GUI offers OpenVINO on bare metal only. STT and TTS follow the user's full `kare_language` selection directly (16
languages) — this is a separate mechanism from the 3-language (nb/en/de) collapse used for
GUI/pipe scaffold text, so voice input/output is not limited to those three.

### Text-to-Speech

Model: Piper, one ONNX voice model per language. A fresh install has none: the voice service
downloads the default voice for the household language on its first start; more voices per
language can be downloaded from Settings → LLM / Models → Piper, quality tier configurable per
voice. In the browser, speech goes through the same address as the GUI (`/voice` behind
Caddy), so microphone and playback also work over HTTPS. Speakers on the network fetch audio
from the LAN address set in the Piper card.
A pronunciation-correction lexicon (`configs/tts_lexicon*.yaml`, one file per language) is
applied before synthesis to fix loanword/brand-name mispronunciations without touching
code. TTS-ducking pauses MPD playback automatically during spoken responses.

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
default user (for voice-triggered identity). Each microphone node is also marked as
**indoor** or **outdoor**: an unrecognised voice at an indoor node is treated as the
Household identity, at an outdoor or door node as an Unknown person (see section 12).

Voice enrollment is per-user: record a short sample in Settings → Users, and the main agent will
identify you by voice on supported hardware.

**Node session unlock:**
Mic nodes (ESP32 and Wyoming satellites) can require a spoken phrase or PIN before personal
tools become available. Saying the phrase or PIN opens a 2-minute rolling session for that
node — the main agent then has access to memory, user profile, camera snapshots, and agent
delegation. The session renews automatically on each command and expires after 2 minutes
of silence. A `global_lists` option keeps the notes tool available without unlocking, for
shared household lists. Users configure their phrase and PIN in Settings → User Settings.
Say "lock" to close the session manually.

**Key files:** `kaare_core/voice/wyoming_server.py`, `kaare_core/voice/providers/`,
`services/voice/`, `kaare_core/app_state.py`, `kaare_core/users/profile_manager.py`

---

## 12. Users, Roles, and Access Control

### Roles

Five roles for people, plus two built-in system identities:

| Role | Additional tools vs. previous role |
|------|-------------------------------------|
| Child | Device control, timers, weather, local library, image generation, media, notes, announce, free reasoning |
| Teen | + web search, library with online access |
| Young Adult | + long-term memory access |
| Adult | + camera access (Frigate snapshots and events), household home/away mode |
| Admin | All tools, all settings |

People's roles always also include the main agent's introspective tools: self-image, world
model and user profile. Inner thoughts and thought history are admin only (section 14).

**System identities** — shown on the Users page under «Default users», configurable like
roles, but they cannot log in or be deleted:

| Identity | Who | Default access |
|----------|-----|----------------|
| **Household** (unrecognised voice) | Someone inside the house the system cannot identify — most likely a household member | Knows what is shared in the house, not individual people's data; device control, timers, weather, library, media, notes, announce |
| **Unknown person** | A stranger — an outdoor/door node, or anything that cannot be identified | Knows nothing about the house and cannot control Home Assistant; weather, library, announce |

The role lists are **enforced**, not just shown to the model: the main agent can only run
the tools and actions the caller's role has. An unknown or unrecognised caller gets the
least access, never adult or admin. The built-in admin account can only get a new PIN — it
cannot be edited, given VPN or a voice profile, or deleted.

### Authentication

- PIN-based login (bcrypt hashed, minimum 6 digits; trivial patterns such as `1234` blocked
  for new PINs; existing shorter PINs remain valid until changed)
- **PIN change enforced on first login:** the default admin (`admin` / `1234`) and new
  accounts get a token that can only change the PIN or log out until the PIN is changed
- Account recovery via a 24-word BIP39 seed phrase, shown once at account creation (or
  re-issuable from the Users page) — losing the PIN doesn't lose access to encrypted
  per-user data, since the seed phrase encodes the full private key losslessly
- JWT session tokens (HS256, expiry configurable — default 4 hours); logging out revokes
  the session key
- Failed logins and recoveries are limited **per account** (5 per 15 min) and **per
  address** (30 per 15 min), so one person's typos do not lock the whole household out;
  reflection PINs have their own limit
- 20 requests/min per user on `/api/generate` (429 + `Retry-After`)
- `/api/generate` requires a login; KTSB's own services (voice, timers, HA gateway,
  notifications) use a service token created automatically on first start
- Append-only audit log (`logs/audit.log`) covering camera access, admin user actions, shell commands, and rate-limit events; readable via `GET /api/audit/recent` (admin only)
- Configurable data retention (Settings → General → Logs and data, Expert): nightly cleanup of old interactions, episodes and thought history; conversations and episodes are kept forever by default (0 = unlimited), thought history 30 days

### VPN and remote access

WireGuard VPN with three per-user access levels:

| Level | What works remotely |
|-------|---------------------|
| `local_only` | No remote access |
| `ai_only` | Chat with the main agent; no home automation writes |
| `full_access` | Full functionality over VPN |

VPN client configs and QR codes are generated from Settings → Users. Requires a DuckDNS
hostname and a Caddy reverse proxy (included in the `vpn` Docker profile).

### Tool permission matrix

The admin can customise which tools each role and system identity can access — the
defaults above are a starting point. Changes take effect immediately without restart.

### Parent oversight

A user account can be flagged as a parent and linked to one or more child accounts
(managed via Settings → Users). A parent can:

- View a summary of a managed child's recent activity
- Read and leave a comment on a child's nightly reflection through a PIN-authenticated
  viewer ("My children" in the user menu)
- Generate a one-time temporary PIN for a child account

A child account can be marked as requiring a PIN before its own reflections are shown —
even to the child — keeping the nightly reflection meaningfully private while still
giving a parent a supervised way in.

**Key files:** `kaare_core/users/auth.py`, `kaare_core/users/store.py`,
`kaare_core/rate_limiter.py`, `kaare_core/audit.py`,
`kaare_core/vpn.py`, `configs/tool_permissions.yaml`,
`frontend/src/pages/user/UserChildren.tsx`

---

## 13. Admin Panel and Configuration

The admin panel is a React application (port 5173). All configuration happens here — no
manual YAML editing required after the initial install.

### Pages

| Page | Purpose |
|------|---------|
| Dashboard | Live service status, active users, onboarding checklist |
| Users | Grouped into «Default users» (built-in Administrator, Household, Unknown person) and the users you create (by role and name); create / edit / delete users, PIN management, VPN client QR codes, voice enrollment, tool permission matrix, parent/child linking |
| Settings | 13 configuration areas (see below) |
| Aliases | Map device names to HA entity IDs; manage room groupings |
| Nodes | Configure voice and display output nodes (14 types: 6 audio-only, 7 multi audio+display, 1 display-only) |
| Tools | SSH node management (add/edit/delete/test, linux and HA OS presets, sudo config); active timers with per-user counts and configurable max; tool execution log (last 80 entries, live, colour-coded by source) |
| Reflections | Reflection meeting notes by date, dev-slot morning reports with live progress per step, topic submission, PIN-protected user views |
| Security | Grouped, filterable feed of recent camera detections with anomaly deviation scores, confidence badges, and snapshot thumbnails; multi-select batch actions and baseline corrections |
| Maintenance | Hot-reload config, health check, hardware info («Detect hardware»), service restart, manual memory compression; **Update KTSB** for Docker installs (check GitHub for a newer release, install with PIN: automatic pre-update save point, signed images, health check and automatic rollback; never during the night sequence); server-side backup points (save / restore with PIN / download ZIP, max 5 of your own — automatic pre-update points are marked and not counted) — backup and restore cover users and keys, config, memory, knowledge store, profiles, lists, timers, images and VPN client keys; restore checks paths and restarts the API itself (also in Docker); «Reset to defaults» for the config files (the old files are kept as a snapshot first); run the test suite from the GUI, results shown inline |
| Agents | Read-only list of inter-agent exchanges (who, when, request ID — not the conversation text) |

In Docker mode, each restartable service reports one of four states — `running`,
`stopped`, `not_created` (outside the active `COMPOSE_PROFILES`, e.g. voice/embedding on a
minimal install), or `unavailable` (Docker socket unreachable) — and the restart control is
disabled with an explanation for the latter two instead of failing silently.

**Configuration files.** One owner writes everything the GUI sets to `configs/`: each save
takes a lock and replaces the file atomically, comments in the files survive, two saves at the
same moment both land, and `configs/.history/<file>/` keeps the previous version (the newest
20). Before a migration on a new version, a «Reset to defaults» or a restore, KTSB copies the
whole folder to `configs/.history/_snapshots/`. When several containers start at once, one of
them migrates and the others wait. KTSB sets a damaged or empty file aside before the template
replaces it.

**Logs** collect in RAM and reach disk every 2 seconds (Settings → General → Logs
and data) and on shutdown, instead of one disk write per line. The audit log and data files
are still written at once.

### Settings areas

A global Basic/Expert switch in the page header (persisted in the browser, not synced to
server config) hides advanced fields — vLLM Docker parameters, role sharing, normalcy
thresholds, per-camera filters, and the entire Agents tab (hidden outright in Basic mode,
not just its contents) — across all 13 areas at once. It only changes what's displayed;
the underlying config values are unaffected either way.

| Area | What you configure |
|------|-------------------|
| General | Location, timezone (one local time for clocks, reminders, quiet hours and dates everywhere, takes effect at once), GUI language, assistant language; **Logs and data** (Expert): how often buffered logs are written to disk (0–30 s, default 2) and how many days conversations, episodes and thought history are kept, with warnings for both directions; **Network and access** (Expert): local and VPN subnets (decide whether a user counts as at home), trusted reverse proxies (the only ones allowed to state the client's address), API documentation on/off (off by default) |
| Home Assistant | HA gateway URL and timeout; long-lived access token; allowed actions as a checkbox list (11 actions: turn on/off, set level, color, temperature, etc.) instead of free text; advanced log bridge settings (log URL, timeout, allowed action filter) |
| MQTT / VPN | MQTT broker: host, port, credentials, TLS, topic prefix, client ID, reconnect interval; WireGuard VPN: DuckDNS hostname, WireGuard port |
| LLM / Models | **Infrastructure:** Docker socket control, running container table. **Per agent role** (main agent, Miss Kåre, Mechanic, Miss Library, fallback, cloud, image, reason_freely): provider (Ollama / vLLM / OpenVINO / cloud), base URL, model selection with installed-model dropdown (pull / delete), thinking mode, keep-warm toggle, model sharing (share_with), GPU assignment, vLLM Docker parameters (gpu_memory_utilization, kv_cache_dtype, max_model_len), Docker restart (keeps the container's entrypoint and port binding, shows «loading model…» until the model answers, and rolls back to the previous container if the new one fails). A yellow mark on a role whose model is not downloaded in Ollama; an amber note on the main agent's card when its model is below the 27B class (basic mode). The pool roles (fallback, Miss Kåre, Miss Library, Mechanic) share one options block — saving any of their cards writes it. **Whisper (speech-to-text):** enable/disable, small / medium / large per household language with «Download» and «Use this», a custom model field, CPU threads (Auto). **Piper (text-to-speech):** one voice model per language, download and activate presets per language; LAN address for speakers. **BGE-M3 embedding:** device (NPU/CPU), model selection, model path. **Semantic memory:** enable/disable, small / medium / large with «Download» and «Use this». **Model storage** (Expert): where downloaded models live |
| Reflection | **Night sequence** start time; reflection meeting on/off and interval of the background reflection loop; Meeting Leader preset; token limits per participant (no fixed upper limit — a hint shows the recommended range); maximum rounds. **Dev slot:** on/off, topics per meeting; time budget, maximum traces, tool rounds per topic (Expert) |
| Web & Weather | Weather provider + feature toggles (feels-like, UV, sun times, alerts, air quality, tides, camera weather); 9 HA sensor entity fields; web search provider + fallback (SearXNG / Brave), Brave API key and locale; trusted sources (categories, domains with type and trust level), open web on/off, blocklist; caution level for probability words; work-round deadline; Norwegian law (Lovdata) on/off with «Update now» |
| Images/Camera | Frigate URL, snapshot and analysis log retention; per-camera analysis config (object types, confidence thresholds, announce on detection), detection log with thumbnails; away-mode escalation (nighttime window, minimum confidence, unknown-object-at-night toggle) |
| Notifications | Master enable toggle; push notification target (Home Assistant `notify` entity); anomaly-alert thresholds (minimum deviation score, minimum baseline confidence); per-camera filter; link to the Security event feed |
| Assistant Settings | Personality mode (6 levels: minimal / lightweight / standard / full / complete / custom with editable text); assistant name; hotword; self-image contributor control (all users / selected users / admin only); self-learned automation approval control — who can confirm or reject automation suggestions the main agent proposes from observed household habits; **Kåre's memory**: who may mark topics settled (admin by default), «Relevant now» on/off and its status, number of notes and thresholds, length limits for lessons (Expert) |
| Integrations | Frigate URL, timeout, enable/disable; Plex URL and token |
| Distribution | Three preset profiles (full / medium / lightweight) with one-click apply; per-domain enable/disable toggles (Home Assistant, Frigate, weather, time etc.); per-service enable/disable toggles (embedding, agents, voice, Jing, Jang) with availability badges |
| Agent Settings | Per-agent tool toggles (Mechanic: code exploration, system inspection, web search, Argus search, shell — off by default and read-only, memory; Miss Kåre: consult Miss Library; Miss Library: wiki); Mechanic personality preset (investigator / critic / analyst / custom). Miss Kåre has one fixed personality |
| Explanations | Plain-language guide to every setting (read-only) |

---

## 14. Tool System

The main agent uses a structured tool system. All tools follow the **action-parameter pattern**:
one tool per domain, with an `action` enum that selects the operation. This keeps the
schema small and predictable for the LLM.

### All 27 tools

| Tool | Domain | Actions |
|------|--------|---------|
| `ha_control` | HA device control | turn_on, turn_off, toggle, set_level, set_color_temp, set_color, ha_history |
| `ha_read` | HA state reading | room_list, room_devices, status |
| `timer` | Timers | clock, set, cancel, list, ack |
| `get_weather` | Weather | *(location optional)* |
| `web_search` | Web search | *(query, near_home, stakes; optional background)* |
| `library` | Wiki, web pages, law | search, fetch_article, fetch_table, fetch_url, online, law |
| `memory` | Memory retrieval | search, fetch_unverified, confirm, fetch_stm |
| `search_argus` | System log search | *(semantic query)* |
| `mechanic` | Mechanic agent | search, delegate, result, cancel, comment |
| `restart_docker_container` | Container management | *(container enum)* |
| `inner_thoughts` | Read Jing's thoughts | *(no parameters)* |
| `thought_history` | Thought history | *(count, filter, only_recovery)* |
| `self_image` | Self-image | read, update, edit, delete |
| `world` | World model | read, update_field, add, delete, edit, read_var, set_var, delete_var, list_vars |
| `user_profile` | User profile | read, update, update_house, set_field, edit, delete, curiosity |
| `note` | Notes (8 lists) | write, read, delete, clear, done, mark_bought, clear_all, pin, unpin, mark_important, edit, resolve, reopen, move |
| `reason_freely` | Extended reasoning | *(query)* |
| `self_trace` | The main agent's own request traces | fetch_trace, trace_patterns |
| `camera` | Frigate cameras | snapshot, events, frigate, list, analyze, show_event |
| `kare_image` | Image generation | generate |
| `view_images` | Image viewer | *(mode: view / analyze; folder: input / output / all)* |
| `media` | Plex + radio | plex_search, plex_episodes, plex_play, plex_sessions, plex_history, plex_library, plex_clients, radio_status, radio_play, radio_stop, radio_volume |
| `read_meeting` | Meeting notes | *(type: reflection / development)* |
| `announce` | TTS / display | say, display, list_display |
| `write_reflex` | Learned reflexes | suggest, confirm, reject, list |
| `dev_proposals` | Dev-slot proposals (admin) | list, confirm, reject |
| `household` | Home/away household mode | set_away, set_home, get_status |

Code, log and system exploration is not a main-agent tool: it goes through Mechanic (the
`mechanic` tool), which is read-only (section 6).

### Model tier requirements

Tools are filtered by the size of the model running the main agent. **Models under 9B get
no tools at all** — they cannot handle tool schemas reliably — so on a small model the main
agent can only talk. From 9B up, every tier below is available; vLLM and cloud backends are
not filtered.

| Tier | Tools |
|------|-------|
| 0 | timer, note, ha_control, ha_read, get_weather, announce, household |
| 3 | web_search, library, memory, camera, read_meeting, kare_image, view_images, media |
| 9 | mechanic, self_trace, restart_docker_container, search_argus, reason_freely, write_reflex, dev_proposals |
| always | self_image, world, user_profile (people's roles only — not Household or Unknown person); inner_thoughts, thought_history (admin only) |

What the always tools can read:

- `self_image` — the main agent's own self-description (one, shared by everyone).
- `world` — the household's world facts and variables (shared by everyone).
- `user_profile` — only the profile and observations of the user who is speaking: the user comes from the login,
  the model cannot pick another one. `update_house` writes shared facts about the house.
- `inner_thoughts` and `thought_history` — Jing's recent thought fragments and the main agent's own recent
  thinking. Both are shared logs that can contain the start of other users' messages, so **only the admin role
  gets them**: they are left out of every other role's tool list, and the tool refuses any other caller. The
  tool permission matrix on the Users page shows them as admin only.

Having a tool is not the same as using it well. A 9B model receives the tools, but picks
them unreliably (see *What to expect* in the README and section 15); the 27B class is what
KTSB is built around.

The fallback model (section 15) has its own ceiling on top of this (`max_tool_tier` in
`llm.yaml`; the shipped default is tier 0 plus the always tools), so its tool schema leaves
room for the conversation.

A consistency validator runs on every config load and service startup: it checks that
`tool_permissions.yaml` and `definitions.py` agree on tool names, and specifically that
every `always`-tier tool survives for every role. A silent mismatch here (e.g. a tool
renamed in one file but not the other) would otherwise make the LLM's tool list quietly
shrink without any error — the validator fails loudly (CRITICAL log + Argus alert) instead
of failing silently.

**Key files:** `kaare_core/tools/definitions.py`, `kaare_core/tools/executor.py`,
`kaare_core/tools/executor_*.py`, `kaare_core/config.py`

### Self-monitoring via RID traces

Every request gets a unique request ID (RID). The RID travels through the entire pipeline
and is written to the log sources: routing decisions, LLM calls (including Miss Library's),
tool executions, think-block cache, and model-enforcer corrections. One background
reflection cycle uses one RID.

The main agent can inspect these traces at runtime with the `self_trace` tool:

- **`fetch_trace`** — reconstruct the full execution history for a given RID: which routing
  stages ran, which LLM was called and how long it took, which tools fired, whether a
  fallback was used, and what the main agent was thinking (think-blocks).
- **`trace_patterns`** — analyse the N most recent traces and summarise: average latency,
  average LLM time, fallback rate, most-used tools, recovered empty responses, slowest
  request.

RIDs are prefixed by source type: `rid-` for user requests, `rid-refl-` for reflection
turns, `rid-meet-` for meeting and dev-slot turns, `rid-timer-` for timer-triggered
actions, `rid-stt-` for voice-originated requests.

The nightly dev slot (section 7) reads the day's traces when it looks for problems.

**Key file:** `kaare_core/tools/trace_reader.py`

---

## 15. LLM Backends and Integrations

### LLM providers

Each role — the main agent, Miss Kåre, Mechanic, Miss Library, the fallback, cloud — has
its own provider, address and model in Settings → LLM.

| Mode | How | When to use |
|------|-----|-------------|
| **Ollama** | Built into Docker (`ollama` profile), or your own Ollama server | Default; any machine |
| **vLLM** | External, configured in Settings → LLM (with a Docker restart button) | High-throughput GPU deployment of the main agent |
| **Cloud** | Any OpenAI-compatible API (HuggingFace router, NVIDIA NIM, …) — endpoint and key in the GUI | Explicit «ask online» questions only (see below) |

**What a fresh install runs.** The built-in Ollama with `huihui_ai/qwen3.5-abliterated:9b`
for every role (one model in memory), thinking off, 32k context. This is **basic mode**:
conversation works, but tool use is unreliable — the 9B may skip a search, pick the wrong
tool, or state things it has no source for. On CPU only, the first answer after a start can
take several minutes. The main agent's LLM card shows an amber note whenever its model is
below the 27B class.

**KTSB as intended** is a 27B-class model as the main agent — on a 24 GB+ GPU, in Ollama or
vLLM. Moving the main agent to the 27B is a change in Settings → LLM; the agent pool can
stay on the 9B.

**Models on another machine.** Any role can point at an Ollama or vLLM server elsewhere in
the home network — a GPU machine serving a weaker one works the same as local. Addresses
you configure in the GUI are always allowed: no security barrier (fetch guard, proxy trust,
port binding) blocks them. Choosing «own Ollama server» in onboarding takes effect at once,
without a restart.

**Cloud has no tools.** A cloud model answers only when the user explicitly asks to «use
online» / «ask online»; it does not control the house, read memory or use cameras. There is
no automatic fallback to the cloud — tool calling needs a local model.

**Fallback.** When the main model is unreachable, the main agent switches to the fallback
role (by default the same 9B) and back by itself when the main model recovers. The fallback
gets a reduced tool set (`max_tool_tier`, section 14) so its context is not used up by tool
definitions.

A warning appears in the log and on the role's card when a role points at a model that is
not downloaded in Ollama.

### Answer checks (model enforcer)

Before an answer reaches the user, code checks it and asks the model for **one** correction
round when it finds a problem: reply in the wrong language (the model reports its reply
language in a hidden tag), stray characters from another writing system in a nb/en/de reply,
cut-off thinking or an empty reply, a web search without the required `near_home` flag, a
promise to «bring it up at the dev meeting» without writing it to the agenda, or a claim that
something was done («noted», «saved», «added to …», «Done –») when no tool ran. In that last
case the model gets a round without tools in which it may say plainly what was and was not
done, and its new answer replaces the claim. Rejected, stopped and failed tool calls do not
count as done; questions and offers («shall I …») do not trigger the check. The check also
runs after the last tool round. `logs/enforcement.log` records every hit with the matched phrase.
Miss Library
has the same checks plus invented quotes and claims without support. When a Qwen3.5 model in
Ollama writes a broken tool call, the round is rebuilt from the raw output instead of failing
(logged as `[TOOL_XML_RESCUE]`).

### Integrations

| Integration | Protocol | Purpose |
|-------------|----------|---------|
| Home Assistant | REST + WebSocket | Device control, sensor data, event stream |
| Frigate | REST + MQTT | Camera snapshots, motion events, face recognition |
| Plex | Plex API | Media library search and casting |
| MQTT broker | MQTT | Frigate events, Zigbee2MQTT sensor data, device triggers |
| Qdrant | HTTP | Vector storage for semantic memory and Argus events |
| WireGuard | VPN tunnel | Secure remote access |
| DuckDNS + Caddy | DNS + HTTPS | Automatic TLS certificates and remote hostname |
| BGE-M3 | HTTP | 1024-dim dense + sparse embeddings (Argus indexing, local wiki search) |
| SearXNG | HTTP | Web search (bundled in Docker) or Brave Search API |
| Wikidata | HTTP | Finding an organisation's official website for web search |
| Lovdata open data | HTTPS download | Norwegian laws and regulations as a local source (optional) |

---

## 16. Service Profiles

Set `COMPOSE_PROFILES` in your `.env` to control which services start. Profiles combine:
`COMPOSE_PROFILES=ollama,full` starts everything.

| Profile | Services added | Notes |
|---------|---------------|-------|
| *(none)* | Core: API, GUI, agents, HA-gateway, Argus, semantic-embed, Qdrant, SearXNG, Caddy, the night runner (`kaare-reflection`) and the updater (`kaare-updater`) | Needs an LLM server elsewhere — your own Ollama or vLLM in the network (a cloud model alone cannot run tools) |
| `ollama` | Built-in Ollama LLM server | The default; leave it out when you use your own LLM server |
| `medium` | BGE-M3 embedding service (internal only) | Argus semantic search, local wiki search and «Relevant now»; switch it on in Settings → LLM / Models → BGE-M3 (off in the template) |
| `full` | Voice bridge — Whisper STT + Piper TTS (port 8011) | Enables voice input/output |
| `vpn` | WireGuard via wg-easy | Enables remote access |
| `inner_voices` | Jing and Jang (own image, `kaare-inner-voices`) | Optional background inner voices |

**Memory requirements (measured on an earlier Docker install, no GPU — not re-measured for v1.10):**

| Profiles active | RAM |
|-----------------|-----|
| Core only | ~460 MB |
| + medium (BGE-M3 embedding) | ~620 MB |
| + ollama (daemon only, no model loaded) | ~650 MB |
| + ollama with 7B Q4 model loaded | ~5 GB |
| + full (Whisper STT + Piper TTS) | +1.5–2 GB (estimated) |

Internal services (Ollama, HA gateway, embedding, semantic-embed, SearXNG) are not published outside Docker; containers reach each other by name. Third-party images are pinned to the versions tested for each release, and the KTSB images install exact, tested Python package versions.

_The default 9B model needs about 6.6 GB on its own. With a GPU, the LLM stays in VRAM — system RAM stays at the ~650 MB baseline regardless of model size. Ollama model RAM scales with quantisation level: 7B Q4\_K\_M ≈ 4 GB, 14B ≈ 8 GB._

---

## 17. Security

### Authentication and sessions

- PIN-based login (bcrypt hashed); minimum 6 digits for new and changed PINs; trivial patterns blocked (all-same digits, ascending/descending sequences such as `1234` or `9876`)
- Existing PINs shorter than 6 digits remain valid until the user changes their PIN
- JWT session tokens (HS256, random secret per install); expiry configurable — default 4 hours; logout revokes the session key
- Forced PIN change on first login, enforced in the backend: until the PIN is changed, the token can only change the PIN or log out (this covers the default `admin` / `1234`)
- Failed logins and recoveries: 5 per 15 minutes per account, 30 per 15 minutes per address
- Request rate limiting: 20 requests/min per user on `POST /api/generate` → HTTP 429 + `Retry-After` header; only KTSB's own services, with the service token, are exempt
- `POST /api/generate` requires a login or the service token; the user comes from the login, never from the request body

### Input validation

- **Mechanic shell guard**: the only shell KTSB runs is Mechanic's, and it can only read — chaining, redirection, and deleting or modifying flags and subcommands are rejected before execution; follow mode becomes «last 100 lines»; every command is audit-logged (section 6)
- **Path traversal**: file-reading tools resolve symlinks before checking that the path is within the allowed directory; backup restore checks every path in the ZIP and refuses anything outside `state/`
- **Web fetching**: search results pointing into the home network or VPN are never fetched; downloads have a byte limit and a content-type check, and the fetch fails closed (section 9)
- **Tool enforcement**: the main agent can only run tools its caller's role has, and Mechanic only the tools it was offered in that call

### Network security

- All API endpoints require authentication (`require_auth`, `require_admin`, or the service token), on the API (8000), HA gateway (8002) and voice bridge (8011) alike; a user reads only their own chat and notifications. The few open routes are harmless (root page, GUI language, assistant name), and a test fails when a new endpoint is added without a lock
- **Trusted proxies**: `X-Forwarded-For` is only believed from proxies listed in Settings → General → Network and access (default: the bundled Caddy and the same host), so a client cannot fake its address to get past the login limit
- Network context (local / VPN / external) fails closed: when in doubt, a client is not treated as local
- API documentation (`/docs`, `/redoc`, `/openapi.json`) is off by default; switch in the same card
- CORS: `cors_origins` in `settings.yaml`, default `*`. Sessions are Bearer tokens in the browser's session storage, not cookies, and credentials are not allowed across origins, so a foreign site cannot use a session by itself. Restrict the list for stricter setups
- In Docker, only the GUI (5173), API (8000), Caddy (80/443) and — with the profiles — voice (8011) and VPN are published; Ollama, HA gateway, embedding, semantic-embed and SearXNG stay inside Docker, and Qdrant is published on the host's loopback only. The "Restart vLLM" button keeps the port's IP binding
- Qdrant is reachable only from the Docker network and the host's loopback; an API key can be set in `configs/qdrant.env` (a fresh install has none)
- Image and camera snapshot endpoints accept the JWT either as a bearer header or a `?token=` query parameter (browser `<img>` tags cannot send headers)
- **Docker socket**: `kaare-api` mounts `/var/run/docker.sock` so the GUI's restart buttons work. Anyone who could run code inside the API container could control Docker on the host — leave the mount out if you don't need the buttons

### Encryption infrastructure (Phase 1)

Each personal user account has a per-user X25519 keypair (PyNaCl / libsodium):

- At account creation: a random X25519 keypair and an Argon2id salt are generated; the private key is encrypted with a key derived from the user's PIN (Argon2id, OWASP-recommended parameters) and stored in `users.db`
- At login: PIN → Argon2id derivation → decrypt private key → store in RAM for the duration of the JWT session; cleared on logout or token expiry
- `admin` system accounts never get a keypair — the admin role is for system management only, not personal data
- If a session outlives a service restart, the encrypted snapshot on disk cannot be decrypted with the (now-gone) in-RAM key; the API detects this and signals the frontend to re-authenticate rather than silently showing stale or empty history

**Vault file system:** agents that need to write to a user's profile while the user is offline (e.g. the nightly reflection meeting) use SealedBox encryption with the user's public key. The result is written as a `vault_*.bin` file. On next login, the vault files are decrypted with the private key, applied, and deleted.

**System key (background agent access):** each user also has a second private key encrypted under a system master key (`state/system_master.key`, excluded from backups) instead of the user's PIN. This lets background jobs — nightly reflection, nightly memory compression, STM restart-recovery — read and write a user's own encrypted data without a live PIN session, via an explicit `allow_system_key` opt-in at each call site (never a blanket fallback). The interactive `needs_unlock` GUI gate and PIN-based login are unaffected — they still require a live session.

This means encryption's real guarantee is **theft protection** (a stolen disk or NAS backup is unusable without a PIN or recovery seed phrase) and **no casual or automatic access** (admin does not see another user's data by accident or by default). It does not mean the data is cryptographically inaccessible to the admin — with code access, decrypting any user's data via the system key is a deliberate action, not a default one, but it is not prevented at the cryptography layer.

**Household-visible fields:** a small, hardcoded subset of profile fields (`name`, pronouns, basic preferences) is stored unencrypted in a separate `household_visible` section. This is what the main agent injects into the system prompt — it never includes conversation history or observations.

**At-rest encryption:** all personal data beyond household-visible fields is encrypted at rest using the per-user keypair:

- `profile.yaml` private section and `observations.md` — SealedBox-encrypted as `.enc` files; decrypted at session start
- STM autosave snapshots — sealed as `.enc` files on disk; decrypted at login with the in-RAM private key
- LTM SQLite rows — `ENC:` prefix scheme; prompt, response, and episode narrative fields encrypted on write, decrypted on read
- Qdrant `kaare_memory` narrative payloads — SealedBox on upsert; decrypted with session key on retrieval
- Existing plaintext data migrated automatically on first login after upgrade

**Account recovery:** a BIP39-derived 24-word seed phrase is generated at account creation, encoding the user's private key directly. The seed phrase resets the PIN and restores the exact original private key without admin involvement — all existing encrypted data (profile, observations, STM, LTM, semantic memory) remains readable after recovery, nothing is re-derived or lost. A standalone `/recover` page (no session required) handles the full flow.

### Audit and monitoring

- Append-only JSONL audit log at `logs/audit.log`
- Events logged: camera snapshots and event fetches, admin user-management actions (create, delete, PIN reset), Mechanic shell commands (also rejected ones), rejected `/api/generate` calls, rate-limit 429 events, and every step of an in-app update
- Accessible via `GET /api/audit/recent` (admin only, newest-first, max 500 entries)

### What leaves your machine

Conversations, memories, camera images and user data stay on your machine. The list below is everything KTSB
itself sends out of the house, and when. (Home Assistant, Frigate, Plex, MQTT and LLM servers are the addresses
you enter yourself; they are not listed.)

| What | To whom | When | How to switch it off |
|------|---------|------|----------------------|
| Search words — plus the town or country from Settings → General when the question is about your area | Public search engines, through the bundled SearXNG (or Brave, if you chose it) | **Default on**, whenever the main agent, Miss Library or Mechanic searches the web | No single switch. Remove `web_search` and online library from roles on the Users page (Tool permissions per role — admin always has every tool); Mechanic's web search in Settings → Agent Settings → Mechanic |
| A request for each page found (user agent names KTSB, its version and the project page) | The websites in the results | **Default on**, with web search | As for web search |
| The name of the organisation or product asked about | Wikidata, then that organisation's own website | **Default on**, when a web search is about a named subject | As for web search |
| Your location (latitude and longitude); a place name you ask about | The weather service (met.no by default); OpenStreetMap's place search for named places | **Default on**, when someone asks about the weather | Remove `get_weather` from roles on the Users page |
| Your IP address and browser details (a font request) | Google Fonts | **Default on**, whenever someone opens the web interface | — not switchable yet; the next release ships the fonts with KTSB |
| A request for the list of releases | GitHub | **Default on**, when an admin opens the Maintenance page on an updatable Docker install (at most every 6 hours) | — (the request carries no user data) |
| Downloads of the speech-to-text model and a Piper voice | Hugging Face | **Default on** with the `full` profile, on the first start of the voice service | Leave `full` out of `COMPOSE_PROFILES` |
| Your location | Kartverket or Stormglass (tides) | Only when tides are switched on | Settings → Web Search & Weather |
| The image description | The image provider (Hugging Face by default) | Only when an API key is set | Remove the key, or switch image generation off in Settings → LLM / Models |
| The question you asked (and the cloud role's own system text — no memory or history) | The cloud provider you configured | Only when you explicitly ask it to «ask online» | Leave the cloud role without a key |
| Downloads of the open law datasets (no user data) | Lovdata | Only when Norwegian law is switched on (off by default) | Settings → Web Search & Weather |
| Model and image downloads | Ollama's library, Hugging Face, GitHub's container registry | Only when you click Pull / Download / Update KTSB, or run `docker compose pull` | Don't click |
| Your domain name | Let's Encrypt (HTTPS certificate) | Only when `KAARE_DOMAIN` in `.env` is a real domain | `KAARE_DOMAIN=localhost` |

### Data retention

Configurable in Settings → General → Logs and data (Expert). The nightly Step 0 deletes conversations and LTM episodes older than the set limit (default `0` = keep forever) and thought history older than 30 days by default. The nightjob removes Argus events older than 30 days.

**Key files:** `kaare_core/users/auth.py`, `kaare_core/users/store.py`, `kaare_core/users/profile_manager.py`, `kaare_core/crypto.py`, `kaare_core/session_keys.py`, `kaare_core/rate_limiter.py`, `kaare_core/audit.py`, `kaare_core/vpn.py`, `kaare_core/client_ip.py`, `kaare_core/service_auth.py`, `kaare_core/agents/mechanic/shell_guard.py`, `adapters/web_fetch.py`

---

## 18. Capability Matrix

✅ means the code exists. What we have actually tested, and on which machine, is in the README under
*What's actually been tested*.

| Capability | Status | Min. profile | Key files |
|-----------|--------|-------------|-----------|
| Text chat | ✅ | Core | `router_generate.py` |
| Voice input (STT) | ✅ | full | `services/voice/` |
| Voice output (TTS) | ✅ | full | `services/voice/` |
| Image input (analysis) | ✅ | Core | `adapters/llm_adapter.py` |
| Image generation | ✅ | Core | `adapters/image_generation_adapter.py` |
| Image editing (inpainting) | 📋 adapter only, not offered to the main agent | Core | `adapters/image_generation_adapter.py` |
| HA device control | ✅ | Core | `kaare_ha_gateway.py` |
| HA state reading | ✅ | Core | `kaare_core/ha/` |
| HA device history | ✅ | Core | `kaare_core/tools/executor_ha.py` |
| Alias mapping | ✅ | Core | `configs/aliases.yaml` |
| Fastpath (reflexes) | ✅ | Core | `kaare_fastpath.py` |
| Frigate snapshots | ✅ | Core | `adapters/frigate_adapter.py` |
| Frigate event search | ✅ | Core | `adapters/frigate_adapter.py` |
| Face recognition (Frigate) | ✅ | Core | `adapters/frigate_adapter.py` |
| MQTT event triggers | ✅ | Core | `adapters/mqtt_adapter.py` |
| Household home/away mode | ✅ | Core | `kaare_core/tools/household_state.py` |
| Automatic return detection | ✅ | Core | `kaare_core/presence_monitor.py` |
| Situational context injection | ✅ | Core | `kaare_core/context_builder.py` |
| Frigate anomaly baseline | ✅ | Core | `kaare_core/normalcy.py` |
| Notification framework (urgency tiers, quiet hours) | ✅ | Core | `kaare_core/notify.py` |
| Security event feed | ✅ | Core | `frontend/src/pages/admin/Security.tsx` |
| Short-term memory (STM) | ✅ | Core | `kaare_core/memory/short_term.py` |
| Long-term memory (LTM) | ✅ | Core | `kaare_core/memory/long_term.py` |
| Semantic memory (Qdrant, model picked in the GUI, off by default) | ✅ | Core | `kaare_core/memory/semantic_memory.py` |
| Memory drawers + «Relevant now» | ✅ | Core (relevance needs embeddings) | `kaare_core/memory/relevance.py`, `kaare_core/memory/memory_prompt.py` |
| User profiles | ✅ | Core | `kaare_core/users/profile_manager.py` |
| Self-image (main agent writes its own) | ✅ | Core | `kaare_core/memory/knowledge_store.py` |
| Knowledge store (world, self-image, notes) | ✅ | Core | `kaare_core/memory/knowledge_store.py` |
| Weekly knowledge review | ✅ | Core | `kaare_core/memory/knowledge_reflection.py` |
| World model | ✅ | Core | `kaare_core/tools/executor_personality.py` |
| Miss Kåre per-user portrait | ✅ | Core | `kaare_miss_kare_nightjob.py` |
| 5-role access control, enforced per tool | ✅ | Core | `kaare_core/users/auth.py`, `kaare_core/routers/router_generate.py` |
| System identities (Household / Unknown person) | ✅ | Core | `kaare_core/users/store.py` |
| Parent / child oversight | ✅ | Core | `kaare_core/routers/router_users.py`, `frontend/src/pages/user/UserChildren.tsx` |
| WireGuard VPN | ✅ | vpn | `kaare_core/vpn.py` |
| Web search (SearXNG / Brave) | ✅ | Core | `adapters/web_search_adapter.py` |
| Tiered sources + probability in code | ✅ | Core | `kaare_core/tools/source_confidence.py` |
| Official source via Wikidata | ✅ | Core | `adapters/official_source.py` |
| Work rounds (background search) | ✅ | Core | `kaare_core/tools/work_round.py` |
| Norwegian law (Lovdata, optional) | ✅ | Core | `adapters/lovdata_adapter.py` |
| Local Wikipedia search (manual setup) | ✅ | medium | `kaare_core/tools/wiki_registry.py` |
| Weather (5 providers) | ✅ | Core | `adapters/weather_adapter.py` |
| HA local sensors (weather) | ✅ | Core | `adapters/weather_adapter.py` |
| Plex media control | ✅ | Core | `adapters/plex_adapter.py` |
| Radio / MPD | ✅ | Core | `kaare_core/tools/executor_media.py` |
| TTS announcements (7 targets) | ✅ | Core | `kaare_core/voice/providers/` |
| Screen display (8 node types) | ✅ | Core | `adapters/display/` |
| Timers (incl. recurring) | ✅ | Core | `kaare_core/tools/timer_service.py` |
| Notes (8 lists) | ✅ | Core | `kaare_core/tools/lister.py` |
| Miss Kåre evaluator | ✅ | Core | `kaare_core/agents/miss_kare/` |
| Mechanic technical agent | ✅ | Core | `kaare_core/agents/mechanic/` |
| Miss Library knowledge agent | ✅ | Core | `kaare_core/agents/miss_library/` |
| Jing (fast inner voice) | ✅ | inner_voices | `services/inner_voices/jing_runner.py` |
| Jang (slow reflection) | ✅ | inner_voices | `services/inner_voices/jang_runner.py` |
| Nightly sequence (start time in the GUI, default 03:00) | ✅ | Core | `kaare_night_runner.py`, `kaare_night_sequence.py` |
| Nightly reflection meeting | ✅ | Core | `kaare_reflection.py` |
| Nightly dev slot (read-only review + morning report) | ✅ | Core | `kaare_core/dev_slot/` |
| Argus log monitoring | ✅ | Core | `kaare_argus.py` |
| Think-block cache | ✅ | Core | `kaare_core/tools/think_cache.py` |
| i18n (Norwegian / English / German) | ✅ | Core | `frontend/src/locales/` |
| Onboarding wizard | ✅ | Core | `frontend/src/pages/Onboarding.tsx` |
| PWA (install as app) | ✅ | Core | `frontend/public/manifest.json` |
| Voice enrollment per user | ✅ | full | `kaare_core/voice/` |
| Voice node session unlock | ✅ | Core | `kaare_core/app_state.py`, `kaare_core/users/profile_manager.py` |
| Learned reflexes (write_reflex) | ✅ | Core | `kaare_fastpath.py` |
| Answer checks (model enforcer) | ✅ | Core | `kaare_core/model_enforcement.py` |
| Per-user end-to-end encryption | ✅ | Core | `kaare_core/crypto.py`, `kaare_core/session_keys.py`, `kaare_core/memory/`, `kaare_core/users/` |
| Account recovery (seed phrase) | ✅ | Core | `kaare_core/routers/router_users.py`, `frontend/src/pages/Recovery.tsx` |
| Backup / restore + server-side backup points | ✅ | Core | `kaare_core/routers/router_backup.py` |
| Config history, snapshots and safe migration | ✅ | Core | `kaare_core/config_store.py`, `kaare_core/config_migrations.py` |
| Whisper / semantic memory model download from the GUI | ✅ | full / Core | `kaare_core/voice/stt_models.py`, `kaare_core/memory/memory_embed_models.py` |
| In-app update (Docker) — signed release bundle + images, rollback | ✅ | Core | `services/updater/updater.py`, `kaare_core/routers/router_update.py` |
| Mood system (VAD) | 📋 | Core | — |
| LTM source trust weighting | 📋 | Core | — |
</content>
