# Escalation System Design

Date: 2026-02-26
Status: Approved

## Problem

Kåre runs borealis-4b as the default NLU model. It handles simple smart home commands and casual conversation well, but struggles with complex questions, factual queries requiring current data, and nuanced reasoning. There is no mechanism to detect failure and route to a more capable model.

## Solution: Confidence Router with Tools

A multi-level system that combines self-scored confidence, hard failure signals, on-demand web search, rubber ducky (ask a stronger model for hints), and model escalation.

### Core Principles

1. Every model level has access to **all tools** (websearch, rubber ducky) — not just higher levels.
2. Escalation only happens when the answer quality is insufficient **after** tool use.
3. Transparent UX — Kåre tells the user what it is doing between levels.
4. Local models keep Kåre's personality. Claude is a last resort.

## Action Set

All models return JSON with these possible actions:

| Action | Description | System behavior |
|---|---|---|
| `ha_call_service` | Smart home command | Call Home Assistant API |
| `answer` | Normal response | TTS to satellite |
| `unknown` | Cannot answer | Triggers escalation |
| `needs_search` | Needs web info | Brave Search, re-prompt same model |
| `needs_help` | Needs stronger model hint | Rubber ducky via Claude API, re-prompt same model |
| `expect_human_response` | Waiting for user reply | Send `listen` message to satellite |
| `get_time` | Time/date query | Return current time/date, set timers |
| `ha_api` | Home Assistant REST API call | Call HA API (states, services, history) |

Every response must include a `confidence` field (1-5):

```json
{"action": "answer", "response": "Oslo er hovedstaden.", "confidence": 5}
{"action": "needs_search", "query": "strømpris nord-norge februar 2026", "confidence": 2}
{"action": "needs_help", "question": "Forskjellen mellom kvantekryptering og post-kvantum?", "confidence": 2}
{"action": "expect_human_response", "response": "Stua eller soverommet?", "confidence": 4}

{"action": "get_time", "query": "current_time", "confidence": 5}

{"action": "ha_api", "method": "GET", "path": "/api/states/sensor.temperature_living_room", "confidence": 4}
```

## Scoring Model

Combined score determines whether to escalate:

| Signal | Effect |
|---|---|
| Model self-reported `confidence` | Used directly (1-5) |
| JSON parse failure | Automatic score = 0 |
| `action: "unknown"` | Score -= 2 |
| Response text < 10 chars | Score -= 1 |
| Empty or repetitive response | Score -= 1 |

**Escalation threshold:** Final score < 3 triggers next level.

## Escalation Chain

```
Level 0: borealis-4b   (always in GPU, fast)
    ├── [needs_search?] → Brave Search → re-prompt 4b
    ├── [needs_help?]   → Claude hint → re-prompt 4b
    └── [score < 3?]    → escalate to level 1

Level 1: borealis-12b  (always in GPU)
    ├── [needs_search?] → Brave Search → re-prompt 12b
    ├── [needs_help?]   → Claude hint → re-prompt 12b
    └── [score < 3?]    → escalate to level 2

Level 2: borealis-27b  (always in GPU)
    ├── [needs_search?] → Brave Search → re-prompt 27b
    ├── [needs_help?]   → Claude hint → re-prompt 27b
    └── [score < 3?]    → escalate to level 3

Level 3: Claude API    (full delegation, includes conversation history)
```

Max 1 tool call (search or ducky) per level. Total chain timeout: 60 seconds.

## GPU Strategy

Server has 2x GPU with 48 GB total VRAM.

| Model | VRAM | Placement |
|---|---|---|
| borealis-4b (Q8_0) | ~5 GB | GPU 0, always loaded |
| borealis-12b (Q4_K_M) | ~8 GB | GPU 0, always loaded |
| borealis-27b (Q4_K_M) | ~16 GB | GPU 1, always loaded |
| KV-cache (num_ctx 4096) | ~200 MB/model | Distributed |
| **Total** | **~29 GB** | **of 48 GB** |

All three models stay warm in VRAM. Escalation is instant — no model loading delay.

**Required config:** Set `OLLAMA_MAX_LOADED_MODELS=3` in Ollama service.

## Web Search Flow

When a model returns `needs_search`:

1. System extracts the `query` field.
2. Brave Search API is called (top 3-5 results: title + snippet).
3. Results are injected back as context to the **same** model.
4. Model produces final answer with the new context.
5. Max 1 search per escalation level to prevent loops.

Implementation: New module `server/websearch.py` with a `search(query) -> list[SearchResult]` function. Brave API key via config. 5 second timeout.

## Rubber Ducky Flow

When a model returns `needs_help`:

1. System extracts the `question` field.
2. Question is sent to Claude API with a focused prompt (max 200 tokens response).
3. Claude's hint is injected back as context to the **local** model.
4. Local model formulates the final answer in its own voice/personality.
5. Max 1 ducky call per escalation level.

The local model always delivers the final response. Claude only provides factual hints. This preserves Kåre's personality and Norwegian language style.

## TTS Feedback Between Levels

Transparent messages played via TTS before each action:

| Event | Kåre says |
|---|---|
| Web search | "Vent litt, jeg sjekker på nett..." |
| Rubber ducky | "Hmm, la meg spørre en kollega..." |
| Model escalation | "La meg tenke litt hardere på det..." |
| Claude takeover | "Jeg henter inn eksperthjelp..." |

These are synthesized and played before the next level starts, so the user is never sitting in silence.

## Prompt Changes

Add to system prompt (DEFAULT_SYSTEM_PROMPT and system_prompt.txt):

```
Always include a "confidence" field (1-5) in your JSON response.
5 = completely certain, 3 = somewhat unsure, 1 = guessing or don't know.

If you need current information to answer, respond with:
{"action": "needs_search", "query": "your search query", "confidence": 1}

If the question is too complex and you need expert help, respond with:
{"action": "needs_help", "question": "your specific question for the expert", "confidence": 1}

If you are asking the user a question and expect a response, use:
{"action": "expect_human_response", "response": "your question to the user", "confidence": 4}
```

## Files to Create/Modify

| File | Change |
|---|---|
| `server/escalation.py` | New — EscalationRouter class, scoring logic, chain management |
| `server/websearch.py` | New — Brave Search API wrapper |
| `server/nlu.py` | Modify — prompt changes, remove old `expects_followup` heuristic, integrate router |
| `server/pipeline.py` | Modify — use EscalationRouter instead of direct NLU call |
| `server/server.py` | Modify — handle `expect_human_response` action, TTS feedback messages |
| `server/config.py` | Modify — add escalation config (model chain, thresholds, API keys) |

## Conversation Memory Integration

The existing conversation history system (`_conversations`, `_conversation_summaries`, idle timeout, clear commands) remains unchanged. The escalation system operates **within** a single turn — it retries the same user message on progressively stronger models, but the conversation history is shared across all levels.

When a model uses `needs_search` or `needs_help`, the tool results are injected as temporary context for that turn only — they do not persist in conversation history. Only the final answer is stored.
