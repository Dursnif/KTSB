"""
Background task: tracks HA entity states for the situational context block.

Tails /kaare/logs/ha_events.log and maintains state/ha_context.json with:
  - Current state per entity listed in services.yaml ha.awareness_entities
  - Recent automation triggers (last 2 hours, max 5)

On startup: seeds state from the last _SEED_BYTES of the log (avoids reading 10M lines).
Then polls for new lines every _POLL_INTERVAL seconds.

No-op if awareness_entities is empty or ha_events.log does not exist.
All errors are non-fatal.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from kaare_core.config import get_service
from kaare_core.storage import rawlog

logger = logging.getLogger("ha_context_task")

_HA_CONTEXT_PATH = Path("/kaare/state/ha_context.json")
_POLL_INTERVAL = 2.0  # seconds between file checks
_SEED_BYTES = 200_000  # bytes to read on startup for state reconstruction
_AUTO_MAX = 5  # max recent automations to keep
_AUTO_TTL_HOURS = 2  # only keep automations from last N hours


def _load_awareness_entities() -> list[str]:
    try:
        entities = get_service("home_assistant", "awareness_entities") or []
        return [str(e) for e in entities if e]
    except Exception:
        return []


def _write_context(state: dict) -> None:
    try:
        _HA_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HA_CONTEXT_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.debug("[ha_context] write error: %s", e)


def _parse_line(line: str) -> dict | None:
    try:
        return json.loads(line.strip())
    except Exception:
        return None


def _seed_state(entities: set[str], file_bytes: bytes) -> tuple[dict, list]:
    """
    Build initial entity states and recent automations from raw log bytes.
    Returns (entity_states, automations) where:
      entity_states = {entity_id: {"state": str, "ts": str}}
      automations   = [{"name": str, "entity_id": str, "ts": str}, ...]
    """
    entity_states: dict[str, dict] = {}
    automations: list[dict] = []

    now_utc = datetime.now(UTC)
    cutoff_ts = now_utc.timestamp() - _AUTO_TTL_HOURS * 3600

    text = file_bytes.decode("utf-8", errors="replace")
    for line in text.splitlines():
        ev = _parse_line(line)
        if not ev:
            continue

        stage = ev.get("stage", "")

        if stage == "state_changed":
            eid = ev.get("entity_id", "")
            if eid in entities:
                entity_states[eid] = {"state": ev.get("to", ""), "ts": ev.get("ts", "")}

        elif stage == "automation_triggered":
            try:
                ts_dt = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
                if ts_dt.timestamp() >= cutoff_ts:
                    automations.append(
                        {
                            "name": ev.get("name", ""),
                            "entity_id": ev.get("entity_id", ""),
                            "ts": ev.get("ts", ""),
                        }
                    )
            except Exception:
                pass

    # Keep only the most recent _AUTO_MAX automations
    automations = sorted(automations, key=lambda x: x.get("ts", ""), reverse=True)[:_AUTO_MAX]
    return entity_states, automations


async def start_ha_context_task() -> None:
    """Long-running background task. Exits early if no awareness_entities configured."""
    entities_list = _load_awareness_entities()
    if not entities_list:
        logger.info("[ha_context] No awareness_entities configured — task idle")
        return

    entities = set(entities_list)
    logger.info("[ha_context] Tracking %d entities: %s", len(entities), ", ".join(sorted(entities)))

    if not rawlog.segments("ha_events"):
        logger.info("[ha_context] no raw ha_events log — task idle")
        return

    # Seed state from the newest _SEED_BYTES across the raw segments (P150), then follow from the end
    try:
        seed_bytes = (await asyncio.to_thread(rawlog.tail_text, "ha_events", _SEED_BYTES)).encode("utf-8")
    except Exception as e:
        logger.warning("[ha_context] Failed to seed from log: %s", e)
        seed_bytes = b""
    tail = await asyncio.to_thread(rawlog.Tail, "ha_events")

    entity_states, automations = _seed_state(entities, seed_bytes)
    state = {"entities": entity_states, "recent_automations": automations}
    _write_context(state)
    logger.info(
        "[ha_context] Seeded %d entities, %d automations", len(entity_states), len(automations)
    )

    # Tail loop
    now_utc = datetime.now(UTC)
    cutoff_ts = now_utc.timestamp() - _AUTO_TTL_HOURS * 3600

    while True:
        try:
            await asyncio.sleep(_POLL_INTERVAL)

            # P150: follows the raw day segments — a new day or a deleted old segment never resets it to 0
            new_lines = await asyncio.to_thread(tail.read_new)
            if not new_lines:
                continue

            changed = False
            for line in new_lines:
                ev = _parse_line(line)
                if not ev:
                    continue

                stage = ev.get("stage", "")

                if stage == "state_changed":
                    eid = ev.get("entity_id", "")
                    if eid in entities:
                        entity_states[eid] = {"state": ev.get("to", ""), "ts": ev.get("ts", "")}
                        changed = True

                elif stage == "automation_triggered":
                    try:
                        ts_dt = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
                        now_ts = datetime.now(UTC).timestamp()
                        cutoff_ts = now_ts - _AUTO_TTL_HOURS * 3600
                        if ts_dt.timestamp() >= cutoff_ts:
                            automations.insert(
                                0,
                                {
                                    "name": ev.get("name", ""),
                                    "entity_id": ev.get("entity_id", ""),
                                    "ts": ev.get("ts", ""),
                                },
                            )
                            # Trim old and excess entries
                            automations = [
                                a
                                for a in automations
                                if datetime.fromisoformat(
                                    a["ts"].replace("Z", "+00:00")
                                ).timestamp()
                                >= cutoff_ts
                            ][:_AUTO_MAX]
                            changed = True
                    except Exception:
                        pass

            if changed:
                state = {"entities": entity_states, "recent_automations": automations}
                _write_context(state)

        except Exception as e:
            logger.warning("[ha_context] Tail error: %s", e)
