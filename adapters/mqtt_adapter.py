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
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "/kaare")
import aiomqtt

from kaare_core import config_store, log_buffer
from kaare_core.background import spawn
from kaare_core.config import get_service as _svc
from kaare_core.frigate_user import box_track
from kaare_core.storage import rawlog
from kaare_core.tools.i18n import get_lang as _get_lang
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users import frigate_consent

log = logging.getLogger("mqtt_adapter")

_FRIGATE_LOG = Path("/kaare/logs/frigate_mqtt.log")
_ZIGBEE_CONTEXT_PATH = Path("/kaare/state/zigbee_context.json")


def _write_event_log(event_type: str, level: str, message: str, extra: dict | None = None) -> None:
    try:
        record: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "source": "frigate-mqtt",
            "subsystem": "frigate",
            "stage": event_type,
            "level": level,
            "message": message,
        }
        if extra:
            record.update(extra)
        log_buffer.write(rawlog.current_segment("frigate_mqtt"), record)  # P150: today's raw segment
    except Exception:
        pass


_STATUS: dict = {"connected": False, "last_event_ts": None, "events_received": 0}
_END_EVENT_HOOKS: list[Callable] = []
# P144-s06a: the Frigate user's shadow pipe gets Frigate's raw face claim (name + score) next to the stripped event,
# so it can check the claim before consent decides what is stored. RAM only — never logged, never written.
_FRIGATE_USER_HOOKS: list[Callable] = []
# P144-s19: extra topics on the same connection (the second face sensor): (topics() -> list, handles(topic) -> bool,
# on_message(topic, payload) — sync, RAM only). Subscribed while topics() is non-empty, re-checked every _RESUB_S.
_TOPIC_HANDLERS: list[tuple[Callable, Callable, Callable]] = []
_RESUB_S = 30


def register_end_event_callback(cb: Callable) -> None:
    """Register an async callback for Frigate 'end' events (appends to hook list)."""
    _END_EVENT_HOOKS.append(cb)


def register_end_event_hook(cb: Callable) -> None:
    """Register an additional hook for Frigate 'end' events."""
    _END_EVENT_HOOKS.append(cb)


def register_frigate_user_hook(cb: Callable) -> None:
    """Register the Frigate user's end-event hook: `await cb(event_dict, raw_name, raw_score)` (P144-s06a)."""
    _FRIGATE_USER_HOOKS.append(cb)


def register_topic_handler(topics: Callable, handles: Callable, on_message: Callable) -> None:
    """Add topics to the existing listener (P144-s19). Registering the same handler twice is a no-op."""
    entry = (topics, handles, on_message)
    if entry not in _TOPIC_HANDLERS:
        _TOPIC_HANDLERS.append(entry)


def _handler_topics() -> set[str]:
    out: set[str] = set()
    for topics, _h, _m in _TOPIC_HANDLERS:
        try:
            out.update(t for t in topics() if isinstance(t, str) and t)
        except Exception as e:
            log.debug("MQTT topic handler topics() failed: %s", e)
    return out


def _route_extra(topic: str, payload: bytes) -> bool:
    """Hand a message to the handler that owns its topic. True when one took it. Never raises."""
    for _t, handles, on_message in _TOPIC_HANDLERS:
        try:
            if handles(topic):
                on_message(topic, payload)
                return True
        except Exception as e:
            log.debug("MQTT topic handler failed for %s: %s", topic, type(e).__name__)
            return True
    return False


async def _keep_extra_subscriptions(client, subscribed: set[str]) -> None:
    """Follow the handlers' topic lists (a GUI switch takes effect without a restart): subscribe what is new,
    unsubscribe what is gone. Runs inside the connection; ends with it."""
    while True:
        await asyncio.sleep(_RESUB_S)
        wanted = _handler_topics()
        for t in sorted(wanted - subscribed):
            await client.subscribe(t)
            log.info("MQTT subscribed to extra topic: %s", t)
        for t in sorted(subscribed - wanted):
            await client.unsubscribe(t)
            log.info("MQTT unsubscribed extra topic: %s", t)
        subscribed.clear()
        subscribed.update(wanted)


def get_status() -> dict:
    return dict(_STATUS)


def _load_zigbee_awareness() -> list[dict]:
    """Load zigbee_awareness topic list from services.yaml mqtt.zigbee_awareness."""
    try:
        entries = (_svc("mqtt") or {}).get("zigbee_awareness") or []
        return [e for e in entries if isinstance(e, dict) and e.get("topic")]
    except Exception:
        return []


def _process_zigbee_awareness(topic: str, payload_bytes: bytes) -> None:
    """
    Process an incoming Zigbee2MQTT awareness topic message.
    Updates state/zigbee_context.json with current sensor state.
    """
    awareness = _load_zigbee_awareness()
    cfg = next((e for e in awareness if e.get("topic") == topic), None)
    if not cfg:
        return
    try:
        payload = json.loads(payload_bytes)
    except Exception:
        return

    state_field = cfg.get("state_field", "state")
    on_value = str(cfg.get("on_value", "true"))
    on_label = cfg.get("on_label", "on")
    off_label = cfg.get("off_label", "off")
    label = cfg.get("label", topic.split("/")[-1])

    raw_val = payload.get(state_field)
    if raw_val is None:
        return

    current_state = on_label if str(raw_val) == on_value else off_label

    try:
        ctx: dict = {}
        if _ZIGBEE_CONTEXT_PATH.exists():
            ctx = json.loads(_ZIGBEE_CONTEXT_PATH.read_text(encoding="utf-8"))
        ctx[topic] = {
            "label": label,
            "state": current_state,
            "ts": datetime.now(UTC).isoformat(),
        }
        _ZIGBEE_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ZIGBEE_CONTEXT_PATH.write_text(
            json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.debug("Zigbee awareness updated: %s = %s", label, current_state)
    except Exception as e:
        log.debug("Zigbee context write error: %s", e)


_MQTT_ENV_PATH = Path("/kaare/configs/mqtt.env")
# What a live connection depends on — a change in any of these (GUI save) reconnects; nothing else does.
_CONN_KEYS = ("host", "port", "username", "password", "tls_enabled", "client_id", "topic_events")
_config_changed: asyncio.Event | None = None
_listener_loop: asyncio.AbstractEventLoop | None = None


def _read_env_file(path: Path) -> dict:
    out: dict = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _load_mqtt_config() -> dict:
    mqtt = _svc("mqtt") or {}
    # The GUI writes mqtt.env; read it every time so a new password is used (it used to go through
    # os.environ.setdefault, which never replaced the first value). The process environment is the fallback.
    creds = _read_env_file(_MQTT_ENV_PATH)

    # P144-s19 item 6: Frigate's MQTT topic prefix is a Frigate setting (Integrations → Frigate) stored as
    # frigate.mqtt_topic_prefix; installs from before keep theirs under mqtt.topic_prefix (config migration step 10).
    frigate_cfg = _svc("frigate") or {}
    topic_prefix = str(frigate_cfg.get("mqtt_topic_prefix") or mqtt.get("topic_prefix") or "frigate")
    return {
        "host": mqtt.get("host", "127.0.0.1"),
        "port": int(mqtt.get("port", 1883)),
        "username": creds.get("MQTT_USER", os.environ.get("MQTT_USER", "")),
        "password": creds.get("MQTT_PASSWORD", os.environ.get("MQTT_PASSWORD", "")),
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
    duration = (
        round(end_time - start_time, 1) if (event_type == "end" and end_time > start_time) else 0.0
    )

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
    raw_claim_name, raw_claim_score = sub_label, sub_label_score  # P144-s06a: RAM only, Frigate user's hook
    # P144-s02: «Frigate may track me» = no → keep the event, drop the name (only names linked to
    # an opted-out person — sub_label also carries plates). Everything below (log, Argus, Jing,
    # context, responder hooks) reads from here. Phase 3: when the away alert starts using
    # sub_label, an opted-out household member must still not count as an unknown person.
    if sub_label and frigate_consent.is_untracked(sub_label):
        sub_label = None
        sub_label_score = None

    _STATUS["events_received"] += 1
    # J19: Frigate's box per update, kept in RAM (only on change, ≤ 1/s, capped) — never into the log below
    if event_type in ("new", "update"):
        box_track.note(event_id, after)
    _STATUS["last_event_ts"] = datetime.now(UTC).isoformat()

    log.debug(
        "Frigate MQTT event: type=%s camera=%s label=%s score=%.2f zones=%s sub_label=%s duration=%.1fs",
        event_type,
        camera,
        label,
        float(score),
        zones,
        sub_label,
        duration,
    )

    level = "warning" if (label == "person" and float(score) >= 0.6) else "info"
    _mqtt_lang = _get_lang("global")
    _sub_label_suffix = (
        _t_i18n("mqtt_frigate_sub_label_suffix", _mqtt_lang, sub_label=sub_label)
        if sub_label
        else ""
    )
    _write_event_log(
        event_type=f"detection_{event_type}",
        level=level,
        message=_t_i18n(
            "mqtt_frigate_detection",
            _mqtt_lang,
            label=label,
            camera=camera,
            pct=int(float(score) * 100),
            sub_label_suffix=_sub_label_suffix,
        ),
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

    # On end events, fire all registered hooks
    if event_type == "end" and (_END_EVENT_HOOKS or _FRIGATE_USER_HOOKS):
        has_clip = bool(src.get("has_clip") or before.get("has_clip", False))
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
            "has_clip": has_clip,
        }
        for _hook in _END_EVENT_HOOKS:
            spawn(_hook(event_dict))
        # P144-s06d: Frigate's motion facts (stationary, path) decide whether the event keeps a situation open —
        # the Frigate user's copy only; the other hooks' dict stays as it was.
        motion = {k: src.get(k) for k in ("stationary", "active", "motionless_count", "position_changes", "path_data")}
        # P144-s12: Frigate's plate reading (a vehicle claim, like a face) — the REST copy is read first, this is the
        # fallback. A known plate's name arrives as sub_label (raw_claim_name). Shape: "AB12345" or ["AB12345", score].
        plate_raw = src.get("recognized_license_plate") or before.get("recognized_license_plate")
        plate_score = src.get("recognized_license_plate_score") or before.get("recognized_license_plate_score")
        if isinstance(plate_raw, list):
            plate_raw, plate_score = (plate_raw[0] if plate_raw else None), (
                plate_raw[1] if len(plate_raw) > 1 else plate_score)
        plate = {"plate": plate_raw, "score": plate_score} if isinstance(plate_raw, str) and plate_raw else None
        boxes = box_track.pop(event_id, end=src)  # J19: the raw per-update boxes, the end box last
        for _hook in _FRIGATE_USER_HOOKS:
            spawn(_hook({**event_dict, "motion": motion, "plate": plate, "boxes": boxes}, raw_claim_name,
                        raw_claim_score))


async def publish_mqtt(topic: str, payload: str) -> bool:
    """
    Publish a single message to an MQTT topic.
    Returns True on success, False on failure or misconfiguration.
    """
    try:
        pass
    except ImportError:
        log.error("aiomqtt not installed — cannot publish to MQTT")
        return False

    cfg = _load_mqtt_config()
    if not cfg["host"]:
        log.warning("MQTT: no host configured — cannot publish")
        return False

    tls_ctx = ssl.create_default_context() if cfg["tls_enabled"] else None
    client_kwargs: dict = {
        "hostname": cfg["host"],
        "port": cfg["port"],
        "username": cfg["username"] or None,
        "password": cfg["password"] or None,
    }
    if tls_ctx is not None:
        client_kwargs["tls_context"] = tls_ctx
    if cfg["client_id"]:
        client_kwargs["identifier"] = cfg["client_id"] + "-pub"

    try:
        async with aiomqtt.Client(**client_kwargs) as client:
            await client.publish(topic, payload=payload.encode("utf-8"))
        log.info("MQTT published to %s: %s", topic, payload[:80])
        return True
    except Exception as e:
        log.warning("MQTT publish failed for topic %s: %s", topic, e)
        return False


def _conn_key(cfg: dict) -> tuple:
    return tuple(cfg.get(k) for k in _CONN_KEYS)


def _on_config_written(path: Path) -> None:
    """config_store hook: services.yaml or mqtt.env was saved → wake the listener; it reconnects only if a
    connection key really changed (DT04-S05d)."""
    if Path(path).name not in ("services.yaml", _MQTT_ENV_PATH.name):
        return
    loop, ev = _listener_loop, _config_changed
    if loop is not None and ev is not None and not loop.is_closed():
        loop.call_soon_threadsafe(ev.set)


config_store.add_write_listener(_on_config_written)


async def _wait_for_change(timeout: float | None) -> bool:
    """True if the config was saved within `timeout` seconds (None = wait forever)."""
    ev = _config_changed
    if ev is None:
        await asyncio.sleep(timeout or 0)
        return False
    try:
        await asyncio.wait_for(ev.wait(), timeout)
    except TimeoutError:
        return False
    ev.clear()
    return True


async def _consume(client, cfg: dict) -> None:
    await client.subscribe(cfg["topic_events"])
    # Subscribe to Zigbee awareness topics (opt-in, empty by default)
    zigbee_topics: set[str] = set()
    for entry in _load_zigbee_awareness():
        t = entry.get("topic", "")
        if t:
            await client.subscribe(t)
            zigbee_topics.add(t)
            log.info("MQTT subscribed to Zigbee awareness topic: %s", t)
    extra = _handler_topics()
    for t in sorted(extra):
        await client.subscribe(t)
        log.info("MQTT subscribed to extra topic: %s", t)
    resub = spawn(_keep_extra_subscriptions(client, extra), name="mqtt_extra_subscriptions")
    try:
        async for message in client.messages:
            msg_topic = str(message.topic)
            if msg_topic in zigbee_topics:
                _process_zigbee_awareness(msg_topic, bytes(message.payload))
            elif (msg_topic != cfg["topic_events"] and _TOPIC_HANDLERS
                  and _route_extra(msg_topic, bytes(message.payload))):
                pass
            else:
                await _process_frigate_event(bytes(message.payload))
    finally:
        resub.cancel()
    raise ConnectionError("message stream ended")


async def _connect_and_listen(cfg: dict) -> dict:
    """One connection. Returns the new config when a connection key changed; raises when the connection fails."""
    client_kwargs: dict = {
        "hostname": cfg["host"],
        "port": cfg["port"],
        "username": cfg["username"] or None,
        "password": cfg["password"] or None,
    }
    if cfg["tls_enabled"]:
        client_kwargs["tls_context"] = ssl.create_default_context()
    if cfg["client_id"]:
        client_kwargs["identifier"] = cfg["client_id"]
    key = _conn_key(cfg)
    async with aiomqtt.Client(**client_kwargs) as client:
        _STATUS.update(connected=True, error="")
        log.info("MQTT connected to %s:%s", cfg["host"], cfg["port"])
        _write_event_log("mqtt_connected", "info", f"Connected to {cfg['host']}:{cfg['port']}")
        consume = asyncio.create_task(_consume(client, cfg))
        try:
            while True:
                change = asyncio.create_task(_wait_for_change(None))
                done, _ = await asyncio.wait({consume, change}, return_when=asyncio.FIRST_COMPLETED)
                if consume in done:
                    change.cancel()
                    consume.result()  # raises the connection error
                new = _load_mqtt_config()
                if _conn_key(new) != key:
                    log.info("MQTT settings changed — reconnecting to %s:%s", new["host"], new["port"])
                    _write_event_log("mqtt_reconnect", "info", "Settings changed in the GUI — reconnecting")
                    return new
                cfg["reconnect_interval"] = new["reconnect_interval"]
        finally:
            consume.cancel()
            _STATUS["connected"] = False


async def run_mqtt_listener() -> None:
    """
    Runs indefinitely — subscribes to Frigate MQTT topics and processes events.
    Reconnects on disconnect, and when the MQTT settings are saved with a changed host/port/credentials/TLS/
    client id/topic prefix (no restart needed, rule 25). With no host it waits for one instead of stopping.
    """
    global _config_changed, _listener_loop
    _config_changed = asyncio.Event()
    _listener_loop = asyncio.get_running_loop()
    cfg = _load_mqtt_config()
    while True:
        if not cfg["host"]:
            _STATUS.update(connected=False, error="no host configured")
            log.info("MQTT: no host configured — listener waits for one (Integrations → MQTT)")
            await _wait_for_change(None)
            cfg = _load_mqtt_config()
            continue
        log.info("Starting MQTT listener: %s:%s topic=%s", cfg["host"], cfg["port"], cfg["topic_events"])
        try:
            cfg = await _connect_and_listen(cfg)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _STATUS.update(connected=False, error=str(e) or type(e).__name__)
            wait_s = max(1, int(cfg.get("reconnect_interval") or 30))
            log.warning("MQTT disconnected: %s — reconnecting in %ss", e, wait_s)
            _write_event_log("mqtt_disconnected", "warning", f"Disconnected: {e}")
            await _wait_for_change(wait_s)  # a GUI save cuts the wait short
            cfg = _load_mqtt_config()
