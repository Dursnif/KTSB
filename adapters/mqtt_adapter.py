# MQTT adapter — subscribes to Frigate events and forwards to Kåre
# Requires: aiomqtt (install with: pip install aiomqtt)
# Config: configs/services.yaml (host/port/tls/topic/client_id/reconnect) + configs/mqtt.env (credentials)

import asyncio
import json
import logging
import os
import ssl
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc

log = logging.getLogger("mqtt_adapter")

_FRIGATE_LOG = Path("/kaare/logs/frigate_mqtt.log")


def _write_event_log(event_type: str, level: str, message: str, extra: dict | None = None) -> None:
    try:
        record: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "frigate-mqtt",
            "subsystem": "frigate",
            "stage": event_type,
            "level": level,
            "message": message,
        }
        if extra:
            record.update(extra)
        _FRIGATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_FRIGATE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

_STATUS: dict = {"connected": False, "last_event_ts": None, "events_received": 0}
_END_EVENT_CALLBACK: Callable | None = None


def register_end_event_callback(cb: Callable) -> None:
    """Register an async callback invoked with a normalized event dict on Frigate 'end' events."""
    global _END_EVENT_CALLBACK
    _END_EVENT_CALLBACK = cb


def get_status() -> dict:
    return dict(_STATUS)


def _load_mqtt_config() -> dict:
    mqtt = _svc("mqtt") or {}
    creds_file = "/kaare/configs/mqtt.env"
    try:
        for line in Path(creds_file).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

    topic_prefix = str(mqtt.get("topic_prefix", "frigate"))
    return {
        "host": mqtt.get("host", "127.0.0.1"),
        "port": int(mqtt.get("port", 1883)),
        "username": os.environ.get("MQTT_USER", ""),
        "password": os.environ.get("MQTT_PASSWORD", ""),
        "tls_enabled": bool(mqtt.get("tls_enabled", False)),
        "topic_events": f"{topic_prefix}/events",
        "client_id": str(mqtt.get("client_id", "")),
        "reconnect_interval": int(mqtt.get("reconnect_interval", 30)),
    }


async def _process_frigate_event(payload_bytes: bytes) -> None:
    """Processes a raw Frigate MQTT event and forwards to Kåre if relevant."""
    try:
        data = json.loads(payload_bytes)
    except Exception:
        return

    event_type = data.get("type")
    before = data.get("before", {})
    after = data.get("after", {})

    if event_type not in ("new", "update", "end"):
        return

    # "end" events carry the final state in "before"; "new"/"update" use "after"
    src = before if event_type == "end" else after

    camera = src.get("camera") or before.get("camera", "")
    label = src.get("label") or before.get("label", "")
    score = src.get("top_score") or src.get("score") or 0
    zones = src.get("entered_zones") or src.get("current_zones") or []
    event_id = src.get("id") or before.get("id", "")
    has_snapshot = bool(src.get("has_snapshot") or before.get("has_snapshot", False))

    # Duration only available on "end" events
    start_time = before.get("start_time") or 0
    end_time = before.get("end_time") or 0
    duration = round(end_time - start_time, 1) if (event_type == "end" and end_time > start_time) else 0.0

    # Frigate 0.17.x sends sub_label as ["name", confidence] — normalize to string + score
    sub_label_raw = src.get("sub_label") or before.get("sub_label")
    if isinstance(sub_label_raw, list) and sub_label_raw:
        sub_label = str(sub_label_raw[0]) if sub_label_raw[0] else None
        sub_label_score = float(sub_label_raw[1]) if len(sub_label_raw) > 1 else None
    elif isinstance(sub_label_raw, str) and sub_label_raw:
        sub_label = sub_label_raw
        sub_label_score = None
    else:
        sub_label = None
        sub_label_score = None

    _STATUS["events_received"] += 1
    _STATUS["last_event_ts"] = datetime.now(timezone.utc).isoformat()

    log.info(
        "Frigate MQTT event: type=%s camera=%s label=%s score=%.2f zones=%s sub_label=%s duration=%.1fs",
        event_type, camera, label, float(score), zones, sub_label, duration,
    )

    level = "warning" if (label == "person" and float(score) >= 0.6) else "info"
    _write_event_log(
        event_type=f"detection_{event_type}",
        level=level,
        message=f"{label} på {camera} ({int(float(score)*100)}%)" + (f" — {sub_label}" if sub_label else ""),
        extra={
            "camera": camera,
            "label": label,
            "score": float(score),
            "zones": zones,
            "event_id": event_id,
            "sub_label": sub_label,
            "sub_label_score": sub_label_score,
            "duration": duration,
        },
    )

    # On end events, fire the analysis callback (registered by kaare_api.py at startup)
    if event_type == "end" and _END_EVENT_CALLBACK is not None:
        event_dict = {
            "camera": camera,
            "label": label,
            "score": float(score),
            "top_score": float(src.get("top_score") or score),
            "zones": zones,
            "event_id": event_id,
            "sub_label": sub_label,
            "sub_label_score": sub_label_score,
            "duration": duration,
            "has_snapshot": has_snapshot,
        }
        asyncio.create_task(_END_EVENT_CALLBACK(event_dict))


async def run_mqtt_listener() -> None:
    """
    Runs indefinitely — subscribes to Frigate MQTT topics and processes events.
    Reconnects automatically on disconnect.
    Import aiomqtt lazily so startup still works if library is missing.
    """
    try:
        import aiomqtt
    except ImportError:
        log.error(
            "aiomqtt not installed. MQTT adapter disabled. "
            "Install with: pip install aiomqtt"
        )
        return

    cfg = _load_mqtt_config()
    if not cfg["host"]:
        log.info("MQTT: no host configured — listener disabled")
        return

    log.info(
        "Starting MQTT listener: %s:%s topic=%s",
        cfg["host"], cfg["port"], cfg["topic_events"],
    )

    tls_ctx = ssl.create_default_context() if cfg["tls_enabled"] else None

    while True:
        try:
            client_kwargs: dict = {
                "hostname": cfg["host"],
                "port": cfg["port"],
                "username": cfg["username"] or None,
                "password": cfg["password"] or None,
            }
            if tls_ctx is not None:
                client_kwargs["tls_context"] = tls_ctx
            if cfg["client_id"]:
                client_kwargs["identifier"] = cfg["client_id"]

            async with aiomqtt.Client(**client_kwargs) as client:
                _STATUS["connected"] = True
                log.info("MQTT connected to %s:%s", cfg["host"], cfg["port"])
                _write_event_log("mqtt_connected", "info", f"Connected to {cfg['host']}:{cfg['port']}")
                await client.subscribe(cfg["topic_events"])
                async for message in client.messages:
                    await _process_frigate_event(bytes(message.payload))
        except Exception as e:
            _STATUS["connected"] = False
            log.warning(
                "MQTT disconnected: %s — reconnecting in %ss",
                e, cfg["reconnect_interval"],
            )
            _write_event_log("mqtt_disconnected", "warning", f"Disconnected: {e}")
            await asyncio.sleep(cfg["reconnect_interval"])
