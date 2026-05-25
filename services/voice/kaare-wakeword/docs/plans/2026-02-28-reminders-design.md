# Kaare Reminders Design

**Date:** 2026-02-28
**Status:** Approved

## Overview

Voice-triggered reminders: "Kåre, minn meg på å gå ut med søppla før kl 10".
Delivered via broadcast (all satellites + Sonos) + HA phone notification.
Repeats every 5 minutes until dismissed by voice ("Kåre, ok/stopp/mottatt").

## NLU Actions

### set_reminder

LLM parses user's sentence into structured reminder:

```json
{"action": "set_reminder", "message": "Gå ut med søppla", "time": "10:00", "confidence": 5}
{"action": "set_reminder", "message": "Du har et møte", "time": "+30m", "confidence": 5}
{"action": "set_reminder", "message": "Ring tannlegen", "time": "tomorrow 09:00", "confidence": 5}
```

Time formats from LLM:
- `HH:MM` — today at this time (or tomorrow if already passed)
- `+Xm` — relative, X minutes from now
- `tomorrow HH:MM` — tomorrow at this time

### dismiss_reminder

Triggered by "Kåre, ok" / "Kåre, stopp" / "Kåre, mottatt" while a reminder is
actively repeating. Dismisses the most recent active reminder.

## Data Model (SQLite)

```sql
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    trigger_at REAL NOT NULL,
    satellite_id TEXT DEFAULT '',
    speaker TEXT DEFAULT '',
    repeat_count INTEGER DEFAULT 0,
    max_repeats INTEGER DEFAULT 3,
    repeat_interval INTEGER DEFAULT 300,
    dismissed INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
```

## Scheduler

`ReminderScheduler` — asyncio background task in server:

1. Polls SQLite every 30 seconds for due, non-dismissed reminders
2. For each due reminder: TTS → broadcast to all satellites + Sonos + HA notify
3. After delivery: increment `repeat_count`, set `trigger_at = now + repeat_interval`
4. Stop repeating when `dismissed = 1` OR `repeat_count >= max_repeats`

## Delivery Chain

```
Timer fires → ReminderScheduler
    → TTS.synthesize(message)
    → registry.broadcast_audio(audio)     -- all satellites
    → sonos.broadcast(message)            -- all Sonos speakers
    → HA notify service (phone push)      -- notify.mobile_app_*
```

## Dismiss Flow

Server tracks whether any reminder is actively repeating. When user says
"Kåre, ok/stopp/mottatt", the pipeline checks for active reminders before
sending to NLU. If found, dismisses the most recent one and confirms.

## Time Parsing

Server-side `parse_reminder_time()` converts LLM output to Unix timestamp:
- `"10:00"` → today 10:00 (or tomorrow if past)
- `"+30m"` → now + 30 minutes
- `"tomorrow 09:00"` → tomorrow 09:00

## Files

| File | Action |
|------|--------|
| `server/reminders.py` | **Create** — ReminderScheduler + SQLite + time parsing |
| `server/nlu.py` | **Modify** — add set_reminder to system prompt |
| `server/server.py` | **Modify** — start scheduler, handle set_reminder + dismiss |
| `tests/server/test_reminders.py` | **Create** — tests |

## Key Decisions

- Asyncio + SQLite polling (no new dependencies)
- 30s poll interval (good enough for reminders)
- Broadcast to ALL rooms (not room-specific)
- Max 3 repeats at 5 min intervals before auto-stop
- Voice dismiss via wake word + simple pattern match
- HA notify for phone push (uses existing ha_tool)
