#!/usr/bin/env python3
"""
ha_log_bridge.py — Real-time Home Assistant event bridge for Vaktmester.

Connects to HA WebSocket on the Pi4 and writes all subscribed events as JSONL
to /kaare/logs/ha_events.log. No filtering here — that is Vaktmester's job.
No configuration required on the HA/Pi4 side — uses existing HA token.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets

sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc

# ─── Config ───────────────────────────────────────────────────────────────────

_HA_URL: str = _svc("home_assistant", "url")
HA_WS_URL: str = (
    _HA_URL.replace("http://", "ws://").replace("https://", "wss://")
    + "/api/websocket"
)

_HA_CFG: dict = (_svc("home_assistant", "log_bridge") or {})
SUBSCRIBE_EVENTS: list[str] = _HA_CFG.get("subscribe_events", [
    "state_changed",
    "call_service",
])
RECONNECT_DELAY: int = int(_HA_CFG.get("reconnect_delay", 10))

TOKEN_FILE = Path("/kaare/configs/ha_token.env")
LOG_PATH   = Path("/kaare/logs/ha_events.log")

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("ha_log_bridge")
logging.getLogger("websockets").setLevel(logging.WARNING)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def read_token() -> str:
    for line in TOKEN_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("HA_TOKEN not found in ha_token.env")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_entry(event_type: str, data: dict) -> dict | None:
    ts = utc_now()

    if event_type == "state_changed":
        new_state = data.get("new_state") or {}
        old_state = data.get("old_state") or {}
        ctx       = new_state.get("context") or {}
        return {
            "ts":        ts,
            "source":    "ha-events",
            "subsystem": "home",
            "stage":     "state_changed",
            "entity_id": data.get("entity_id", ""),
            "from":      old_state.get("state", ""),
            "to":        new_state.get("state", ""),
            "user_id":   ctx.get("user_id") or "",
            "parent_id": ctx.get("parent_id") or "",
        }

    if event_type == "call_service":
        svc_data = data.get("service_data") or {}
        return {
            "ts":        ts,
            "source":    "ha-events",
            "subsystem": "home",
            "stage":     "call_service",
            "domain":    data.get("domain", ""),
            "service":   data.get("service", ""),
            "entity_id": str(svc_data.get("entity_id", "")),
        }

    return None


def write_entry(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── WebSocket loop ───────────────────────────────────────────────────────────

async def connect_and_listen() -> None:
    token  = read_token()
    msg_id = 1

    async with websockets.connect(HA_WS_URL, ping_interval=30, ping_timeout=10) as ws:
        # Authenticate
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Expected auth_required, got: {msg.get('type')}")

        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        resp = json.loads(await ws.recv())
        if resp.get("type") != "auth_ok":
            raise RuntimeError(f"HA auth failed: {resp}")

        log.info("Connected and authenticated — %s", HA_WS_URL)

        # Subscribe to events
        for event_type in SUBSCRIBE_EVENTS:
            await ws.send(json.dumps({
                "id":         msg_id,
                "type":       "subscribe_events",
                "event_type": event_type,
            }))
            sub_resp = json.loads(await ws.recv())
            if sub_resp.get("success"):
                log.info("Subscribed to: %s", event_type)
            else:
                log.warning("Subscribe failed for %s: %s", event_type, sub_resp)
            msg_id += 1

        # Forward all events — filtering is Vaktmester's job
        indexed = 0
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") != "event":
                continue

            event      = msg.get("event", {})
            event_type = event.get("event_type", "")
            data       = event.get("data", {})

            entry = make_entry(event_type, data)
            if entry:
                try:
                    write_entry(entry)
                    indexed += 1
                    if indexed % 100 == 0:
                        log.info("Forwarded %d HA events", indexed)
                except Exception as exc:
                    log.warning("Write error: %s", exc)


# ─── Daemon ───────────────────────────────────────────────────────────────────

async def daemon() -> None:
    log.info("ha_log_bridge starting — events: %s", SUBSCRIBE_EVENTS)
    while True:
        try:
            await connect_and_listen()
        except Exception as exc:
            log.warning("Disconnected: %s — retry in %ds", exc, RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)


def main() -> None:
    try:
        asyncio.run(daemon())
    except KeyboardInterrupt:
        log.info("ha_log_bridge stopped.")


if __name__ == "__main__":
    main()
