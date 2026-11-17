#!/usr/bin/env python3
import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, "/kaare")
import re

from kaare_core import rooms
from kaare_core.config import get_service as _svc
from kaare_core.ha import action_truth as truth
from kaare_core.service_auth import is_service_request, service_headers

# ---------------- Config ----------------
ENV_PATH = "/kaare/configs/kare_ha.env"
ALIASES_PATH = "/kaare/configs/aliases.yaml"
SETTINGS_PATH = "/kaare/configs/settings.yaml"
TOKEN_PATH = "/kaare/configs/ha_token.env"


def load_env(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def load_aliases(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("aliases") or data
    aliases: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                key = str(k).strip().lower()
                val = str(v).strip()
                if key and val:
                    aliases[key] = val
            except Exception:
                continue
    return aliases


E = load_env(ENV_PATH)
KARE_LOG_URL = E.get("KARE_LOG_URL", "").rstrip("/") or "http://kaare-api:8000"
KARE_HA_TIMEOUT = float(E.get("KARE_HA_TIMEOUT", "5"))
ALLOWED_ACTIONS = {a.strip() for a in (E.get("KARE_ALLOWED_ACTIONS", "").split(",")) if a.strip()}
# The actions the GUI showed when the list above was saved (P139); empty = a list saved before the GUI recorded it.
OFFERED_ACTIONS = {a.strip() for a in (E.get("KARE_OFFERED_ACTIONS", "").split(",")) if a.strip()}


# HA REST API — for direkte tilstandslesing
def _load_ha_api_config():
    try:
        ha_url = (_svc("home_assistant", "url") or "").rstrip("/")
    except Exception:
        ha_url = ""
    try:
        tok_env = load_env(TOKEN_PATH)
        token = tok_env.get("HA_TOKEN", "")
    except Exception:
        token = ""
    return ha_url, token


HA_API_URL, HA_TOKEN = _load_ha_api_config()


def _load_verify_seconds() -> float:
    """How long to wait for HA to report an action's new state (N26; GUI: Integrations → Home Assistant). Tuya and
    Zigbee lights may report a moment after the action happened; 0 = only what HA's reply itself shows."""
    try:
        raw = _svc("home_assistant", "verify_seconds")
        return max(0.0, min(10.0, float(2.0 if raw is None else raw)))
    except Exception:
        return 2.0


VERIFY_SECONDS = _load_verify_seconds()
_VERIFY_POLL_S = 0.25
# HA keeps running a service after our request times out (a Cast «turn on» starts an app — ~14 s on a Google TV,
# P139 phase 5 live test). Then we keep reading the state this long instead of failing with a 500.
SLOW_SERVICE_WAIT_S = 20.0

ALIASES = load_aliases(ALIASES_PATH)

# --------------- Logging ----------------
LOG_DIR = "/kaare/logs"
os.makedirs(LOG_DIR, exist_ok=True)


class _GatewayLogFormatter(logging.Formatter):
    """Text lines get a timestamp and level; JSON lines (read by Argus with json.loads) stay exactly as written."""

    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if msg.startswith("{") and not record.exc_info:
            return msg
        return super().format(record)


_log_handler = logging.FileHandler(os.path.join(LOG_DIR, "kaare_ha_gateway.log"))
_log_handler.setFormatter(_GatewayLogFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])


# One pooled HTTP client for HA and Kåre calls (thread-safe; endpoints run in FastAPI's threadpool).
# follow_redirects=True keeps the behaviour of the requests library this replaced.
_http = httpx.Client(follow_redirects=True)


def now_utc():
    return dt.datetime.utcnow().isoformat() + "Z"


def log_json(**kw):
    logging.info(
        json.dumps({"ts": now_utc(), "source": "kaare-ha-gateway", **kw}, ensure_ascii=False)
    )


def post_ha_log(attrs: dict[str, Any]):
    try:
        _http.post(
            KARE_LOG_URL,
            json={
                "schema": "v1",
                "source": "kaare-ha-gateway",
                "subsystem": "ha",
                "instance": "default",
                "ts": now_utc(),
                "attrs": attrs,
            },
            headers=service_headers(),
            timeout=2.0,
        )
    except Exception as e:
        log_json(event="ha_log_error", error=str(e))


# ------------- Direkte HA REST-kall --------------


def ha_get(path: str, timeout: float | None = None):
    r = _http.get(
        f"{HA_API_URL}{path}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        timeout=timeout or KARE_HA_TIMEOUT,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"ha_status": r.status_code, "ha_body": r.text})
    return r.json()


def ha_state(entity_id: str) -> tuple[int, dict | None]:
    """(HTTP status, state) without raising on 404: HA has no such entity (the only documented existence check)."""
    r = _http.get(
        f"{HA_API_URL}/api/states/{entity_id}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        timeout=KARE_HA_TIMEOUT,
    )
    if r.status_code == 404:
        return 404, None
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"ha_status": r.status_code, "ha_body": r.text})
    return r.status_code, r.json()


def call_ha_service(domain: str, service: str, data: dict[str, Any], timeout: float | None = None):
    r = _http.post(
        f"{HA_API_URL}/api/services/{domain}/{service}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        json=data,
        timeout=timeout or KARE_HA_TIMEOUT,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"ha_status": r.status_code, "ha_body": r.text})
    return r.json()


def call_ha_service_slow_ok(domain: str, service: str, data: dict[str, Any]) -> tuple[Any, bool]:
    """(HA's reply, slow): a timeout is not an error — HA is still doing it, the caller verifies by reading state."""
    try:
        return call_ha_service(domain, service, data), False
    except httpx.TimeoutException:
        logging.info("HA %s.%s on %s did not answer within %ss — verifying by state", domain, service,
                 data.get("entity_id"), KARE_HA_TIMEOUT)
        return [], True


def map_action_to_service(entity_id: str, action: str, params: dict[str, Any]):
    if "." not in entity_id:
        raise HTTPException(status_code=400, detail="invalid entity_id")
    domain = entity_id.split(".", 1)[0]
    payload = {"entity_id": entity_id}

    if action in ("turn_on", "turn_off", "toggle"):
        if params and action == "turn_on":
            payload.update(params)
        final_domain = domain if domain in ("light", "switch", "input_boolean") else "homeassistant"
        return final_domain, action, payload
    if action == "open":
        return "cover", "open_cover", payload
    if action == "close":
        return "cover", "close_cover", payload
    if action == "set_level":
        level = (params or {}).get("level")
        if level is not None:
            payload["brightness_pct"] = int(level)
        return "light", "turn_on", payload
    if action == "set_color_temp":
        if (params or {}).get("color_temp_kelvin") is not None:
            payload["color_temp_kelvin"] = int(params["color_temp_kelvin"])
        elif (params or {}).get("color_temp") is not None:
            payload["color_temp"] = int(params["color_temp"])
        return "light", "turn_on", payload
    if action == "set_color":
        if (params or {}).get("rgb_color") is not None:
            payload["rgb_color"] = params["rgb_color"]
        return "light", "turn_on", payload
    if action == "set_temperature":
        if (params or {}).get("temperature") is not None:
            payload["temperature"] = params["temperature"]
        return "climate", "set_temperature", payload
    if action == "get_temperature":
        state = ha_get(f"/api/states/{entity_id}")
        temp = (state.get("attributes") or {}).get("current_temperature") or state.get("state")
        return None, None, {"status": "ok", "temperature": temp, "entity_id": entity_id}
    if action == "get_state":
        state = ha_get(f"/api/states/{entity_id}")
        return None, None, {"status": "ok", "state": state.get("state"), "entity_id": entity_id}
    raise HTTPException(status_code=400, detail=f"unsupported action '{action}'")


_MEDIA_ATTRS = (
    "volume_level",
    "is_volume_muted",
    "source",
    "source_list",
    "supported_features",
    "app_name",
    "media_title",
)


def _media_view(row: dict | None) -> dict[str, Any]:
    """The part of a media player's state the caller reports: state + volume/mute/source (source list capped)."""
    attrs = (row or {}).get("attributes") or {}
    out: dict[str, Any] = {k: attrs[k] for k in _MEDIA_ATTRS if k in attrs}
    if isinstance(out.get("source_list"), list):
        out["source_list"] = out["source_list"][:30]
    return out


def execute_media_command(entity_id: str, action: str, params: dict[str, Any], rid: str) -> dict[str, Any]:
    """A media_player action (P139 phase 1): checked against the player's feature flags and source list before the
    call, and against its attributes after (N26 — volume/mute/source are attributes, not the state)."""
    code, pre = ha_state(entity_id)
    pre_state = (pre or {}).get("state")

    def reply(outcome: str, row: dict | None, details: Any = None) -> dict[str, Any]:
        return {
            "status": "ok" if outcome in truth.DONE else outcome,
            "outcome": outcome,
            "rid": rid,
            "state": (row or {}).get("state", pre_state),
            "media": _media_view(row),
            "details": details,
        }

    stop = truth.pre_check(code, pre_state)
    if stop:
        return reply(stop, pre)
    attrs = (pre or {}).get("attributes") or {}
    if truth.media_unsupported(action, entity_id, attrs):
        return reply(truth.UNSUPPORTED, pre)
    service, _flags = truth.MEDIA_SERVICES[action]
    data: dict[str, Any] = {"entity_id": entity_id}
    if action == "set_volume":
        data["volume_level"] = round(max(0, min(100, int(params.get("volume_pct", 0)))) / 100, 2)
    elif action in ("mute", "unmute"):
        data["is_volume_muted"] = action == "mute"
    elif action == "select_source":
        source = truth.match_source(str(params.get("source") or ""), attrs)
        if source is None:
            return reply(truth.SOURCE_UNKNOWN, pre)
        data["source"] = source
    check = {k: v for k, v in data.items() if k != "entity_id"}
    already = truth.media_judge(action, check, pre, None)
    if already == truth.ALREADY:
        return reply(truth.ALREADY, pre)
    try:
        ha_resp, slow = call_ha_service_slow_ok("media_player", service, data)
    except HTTPException as e:
        return reply(truth.ERROR, pre, e.detail)
    seen = next(
        (r for r in (ha_resp if isinstance(ha_resp, list) else []) if r.get("entity_id") == entity_id),
        None,
    )
    outcome = truth.media_judge(action, check, pre, seen) if seen else None
    deadline = time.monotonic() + (SLOW_SERVICE_WAIT_S if slow else VERIFY_SECONDS)
    while outcome is None and time.monotonic() < deadline:
        time.sleep(_VERIFY_POLL_S)
        try:
            _c, seen = ha_state(entity_id)
        except Exception:
            continue
        outcome = truth.media_judge(action, check, pre, seen)
    return reply(outcome or truth.NOT_CONFIRMED, seen or pre, ha_resp)


def execute_ha_command(payload: dict[str, Any]) -> dict[str, Any]:
    entity_id = payload.get("entity_id")
    action = payload.get("action")
    params = payload.get("params") or {}
    rid = payload.get("request_id", "")

    if isinstance(entity_id, list):
        entity_id = entity_id[0] if entity_id else None
    entity_id = ALIASES.get(entity_id, entity_id)

    if action in truth.MEDIA_SERVICES:
        if not entity_id or "." not in entity_id:
            raise HTTPException(status_code=400, detail="invalid entity_id")
        return execute_media_command(entity_id, action, params, rid)

    domain, service, result = map_action_to_service(entity_id, action, params)

    if domain is None:
        return {"status": "ok", "rid": rid, "details": result}

    # N26: HA answers 200 also for an unknown entity and before slow devices report — check before and after.
    code, pre = ha_state(entity_id)
    pre_state = (pre or {}).get("state")
    stop = truth.pre_check(code, pre_state)
    if stop:
        return {"status": stop, "outcome": stop, "rid": rid, "state": pre_state, "details": None}

    ha_resp, slow = call_ha_service_slow_ok(domain, service, result)
    expected = truth.expected_states(action, entity_id, pre_state)
    seen = truth.changed_state_of(entity_id, ha_resp)
    outcome = truth.judge(action, expected, pre_state, seen)
    deadline = time.monotonic() + (SLOW_SERVICE_WAIT_S if slow else VERIFY_SECONDS)
    while outcome is None and time.monotonic() < deadline:
        time.sleep(_VERIFY_POLL_S)
        try:
            _c, now = ha_state(entity_id)
            seen = (now or {}).get("state")
        except Exception:
            continue
        outcome = truth.judge(action, expected, None, seen)
    if outcome is None:
        outcome = truth.NOT_CONFIRMED
    out = {
        "status": "ok" if outcome in truth.DONE else outcome,
        "outcome": outcome,
        "rid": rid,
        "state": seen or pre_state,
        "details": ha_resp,
    }
    if truth.domain_of(entity_id) == "media_player":
        # What is on screen now (app, title) — without it «paused» reads as «the film is paused» (P139).
        try:
            _c, row = ha_state(entity_id)
            out["media"] = _media_view(row)
        except Exception:
            out["media"] = _media_view(pre)
    return out


# --------------- Hjelpefunksjoner ----------------


def resolve_room(room_hint: str) -> str | None:
    """A spoken room/place word → place id from the place register (P137: rooms.yaml + HA areas). A word that
    names two places resolves to None — never «last one wins» (BUG-log 2026-10-17)."""
    if not room_hint:
        return None
    return rooms.get_register().resolve_place(room_hint)


def find_entity_in_room(place_id: str, action: str) -> str | None:
    """Best registered HA entity at a place for an action-type.
    turn_on/turn_off/set_level → prefer light.*, then switch.*
    set_temperature             → prefer climate.*
    get_temperature             → prefer sensor.*"""
    entities = [t.ref for t in rooms.get_register().things_at(place=place_id) if t.source == "ha"]
    if not entities:
        return None

    domain_pref = []
    if action in ("turn_on", "turn_off", "set_level", "set_color_temp", "set_color"):
        domain_pref = ["light.", "switch."]
    elif action == "set_temperature":
        domain_pref = ["climate."]
    elif action in ("get_temperature",):
        domain_pref = ["sensor."]

    for pref in domain_pref:
        for e in entities:
            if e.startswith(pref):
                return e

    # No preference matched – return the first
    return entities[0]


# --------------- API --------------------
class ApplyBody(BaseModel):
    action: str
    entity_id: str
    params: dict[str, Any] | None = None
    request_id: str | None = None
    source: str | None = "kare"


app = FastAPI(title="Kåre HA Gateway", version="0.5.0")


def _require_service(request: Request) -> None:
    """Only KTSB's own services (kaare_api, executor_ha, timers) may use the gateway (P120)."""
    if not is_service_request(request.headers):
        raise HTTPException(status_code=401, detail="Service token required.")


@app.get("/")
def root():
    return {"status": "ok", "service": "kaare-ha-gateway"}


@app.post("/api/reload")
def gateway_reload(_s: None = Depends(_require_service)):
    """Hot-reload all file-based config caches without restarting the gateway."""
    global ALIASES, HA_API_URL, HA_TOKEN, VERIFY_SECONDS
    global KARE_LOG_URL, KARE_HA_TIMEOUT, ALLOWED_ACTIONS, OFFERED_ACTIONS
    reloaded = []
    errors = []
    try:
        env = load_env(ENV_PATH)
        KARE_LOG_URL = env.get("KARE_LOG_URL", "").rstrip("/") or "http://kaare-api:8000"
        KARE_HA_TIMEOUT = float(env.get("KARE_HA_TIMEOUT", "5"))
        ALLOWED_ACTIONS = {a.strip() for a in env.get("KARE_ALLOWED_ACTIONS", "").split(",") if a.strip()}
        OFFERED_ACTIONS = {a.strip() for a in env.get("KARE_OFFERED_ACTIONS", "").split(",") if a.strip()}
        reloaded.append(f"kare_ha.env ({len(ALLOWED_ACTIONS) or 'all'} actions allowed)")
    except Exception as e:
        errors.append(f"kare_ha.env: {e}")
    try:
        ALIASES = load_aliases(ALIASES_PATH)
        reloaded.append(f"aliases.yaml ({len(ALIASES)} aliaser)")
    except Exception as e:
        errors.append(f"aliases.yaml: {e}")
    try:
        HA_API_URL, HA_TOKEN = _load_ha_api_config()
        VERIFY_SECONDS = _load_verify_seconds()
        reloaded.append(f"ha_api_config (verify {VERIFY_SECONDS:g}s)")
    except Exception as e:
        errors.append(f"ha_api_config: {e}")
    return {"reloaded": reloaded, "errors": errors}


@app.get("/health")
def health():
    try:
        r = _http.get(
            f"{HA_API_URL}/api/config",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            timeout=2.0,
        )
        ok = r.status_code == 200
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded"}


@app.get("/api/ha_status/{entity_id:path}")
def ha_status(entity_id: str, _s: None = Depends(_require_service)):
    """Les tilstand på en HA-entitet direkte fra HA REST API."""
    if not HA_API_URL or not HA_TOKEN:
        raise HTTPException(status_code=503, detail="HA API ikke konfigurert")
    try:
        r = _http.get(
            f"{HA_API_URL}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
            timeout=KARE_HA_TIMEOUT,
        )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Entitet '{entity_id}' ikke funnet i HA")
        r.raise_for_status()
        data = r.json()
        return {
            "entity_id": data.get("entity_id"),
            "state": data.get("state"),
            "unit": data.get("attributes", {}).get("unit_of_measurement", ""),
            "friendly": data.get("attributes", {}).get("friendly_name", entity_id),
            "attributes": data.get("attributes", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


class ExistsBody(BaseModel):
    entity_ids: list[str]


@app.post("/api/ha_exists")
def ha_exists(b: ExistsBody, _s: None = Depends(_require_service)):
    """Which of these entities HA has now (N26): {id: "ok" | "unavailable" | "not_found"} from one `GET /api/states`.
    Used to correct memories of actions HA only pretended to do."""
    ids = [e for e in b.entity_ids[:500] if isinstance(e, str) and e]
    states = {row.get("entity_id"): row.get("state") for row in ha_get("/api/states") or []}
    return {
        "states": {
            e: (
                "not_found"
                if e not in states
                else "unavailable"
                if states[e] == "unavailable"
                else "ok"
            )
            for e in ids
        }
    }


@app.post("/api/ha_apply")
def ha_apply(b: ApplyBody, _s: None = Depends(_require_service)):
    rid = b.request_id or f"rid-{int(time.time() * 1000)}"
    action = b.action.strip()
    if not truth.allowed(action, ALLOWED_ACTIONS, OFFERED_ACTIONS):
        post_ha_log(
            {"event": "reject_action", "rid": rid, "action": action, "entity_id": b.entity_id}
        )
        raise HTTPException(status_code=400, detail=f"action '{action}' not in allowed list")
    payload = {
        "request_id": rid,
        "action": action,
        "entity_id": b.entity_id,
        "params": b.params or {},
    }
    log_json(event="forward", rid=rid, to="ha_direct", payload=payload)
    post_ha_log(
        {
            "event": "apply_req",
            "rid": rid,
            "action": action,
            "entity_id": b.entity_id,
            "params": b.params or {},
        }
    )
    resp = execute_ha_command(payload)
    out = {
        "status": resp.get("status", "ok"),
        "outcome": resp.get("outcome", ""),
        "state": resp.get("state"),
        "media": resp.get("media"),
        "request_id": rid,
        "details": resp,
    }
    post_ha_log(
        {
            "event": "apply_resp",
            "rid": rid,
            "result": out["status"],
            "outcome": out["outcome"],
            "entity_id": b.entity_id,
            "action": action,
        }
    )
    log_json(event="forward_done", rid=rid, result=out["status"], outcome=out["outcome"])
    return out


# ---------- NL-parser + GUI -------------
class NLBody(BaseModel):
    prompt: str
    request_id: str | None = None
    dry_run: bool | None = False


class IntentBody(BaseModel):
    prompt: str
    intent: str | None = None
    slots: dict[str, Any] | None = None
    confidence: float | None = None
    needs_clarification: bool | None = False
    request_id: str | None = None
    dry_run: bool | None = False
    room: str | None = None  # TTS-tag fra smarthøyttaler


_COLOR_TEMP_PRESETS = {
    "stearinlys": 2200,
    "levende lys": 2200,
    "varm hvit": 2700,
    "varmt lys": 2700,
    "varm lys": 2700,
    "nøytral hvit": 4000,
    "nøytralt lys": 4000,
    "kjølig hvit": 6000,
    "kald hvit": 6500,
    "kaldt lys": 6500,
    "dagslys": 6500,
}

_COLOR_MAP = {
    "rød": [255, 0, 0],
    "rødt": [255, 0, 0],
    "grønn": [0, 180, 0],
    "grønt": [0, 180, 0],
    "blå": [0, 80, 255],
    "blått": [0, 80, 255],
    "gul": [255, 200, 0],
    "gult": [255, 200, 0],
    "lilla": [150, 0, 200],
    "lila": [150, 0, 200],
    "fiolett": [128, 0, 200],
    "oransje": [255, 120, 0],
    "rosa": [255, 80, 150],
    "turkis": [0, 200, 200],
    "cyan": [0, 220, 220],
}


def parse_prompt(prompt: str) -> dict[str, Any]:
    p = (prompt or "").strip().lower()
    off_kw = ["skru av", "slå av", "ha av", "turn off", "off"]
    on_kw = ["skru på", "slå på", "ha på", "turn on", "on"]
    temp_kw = [
        "sett temperatur",
        "sett temp",
        "set temperature",
        "set temp",
        "varm opp til",
        "kjøl ned til",
    ]
    level_kw = ["sett nivå", "sett lysstyrke", "dimm til", "set level", "set brightness"]
    color_temp_kw = [
        "fargetemperatur",
        "fargetemp",
        "color temp",
        "color temperature",
        "lystemperatur",
    ]

    action = "turn_off" if any(k in p for k in off_kw) else None
    if any(k in p for k in on_kw):
        action = "turn_on" if action is None else action
    if any(k in p for k in temp_kw):
        action = "set_temperature" if action is None else action
    if any(k in p for k in level_kw):
        action = "set_level" if action is None else action
    if any(k in p for k in color_temp_kw):
        action = "set_color_temp" if action is None else action

    params: dict[str, Any] = {}

    # Color temperature preset words (e.g. "varm hvit", "dagslys")
    if action in ("set_color_temp", None):
        for preset, kelvin in _COLOR_TEMP_PRESETS.items():
            if preset in p:
                action = "set_color_temp"
                params["color_temp_kelvin"] = kelvin
                break

    # Explicit kelvin number (e.g. "3000 kelvin" or "3000k")
    m_k = re.search(r"(\d{3,5})\s*(?:kelvin\b|k\b)", p)
    if m_k and action in ("set_color_temp", None):
        action = "set_color_temp"
        params["color_temp_kelvin"] = int(m_k.group(1))

    # Color names (e.g. "rødt", "blå")
    if action in ("set_color", None):
        for color_name, rgb in _COLOR_MAP.items():
            if color_name in p:
                action = "set_color"
                params["rgb_color"] = rgb
                break

    # General number extraction for temperature and brightness
    if action not in ("set_color_temp", "set_color"):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:grader|°|grad|%|prosent)?", p)
        if m:
            val = float(m.group(1).replace(",", "."))
            if action == "set_temperature":
                params["temperature"] = val
            if action == "set_level":
                params["level"] = val

    entity = None
    for alias_key, ent in ALIASES.items():
        if alias_key and alias_key in p:
            entity = ent
            break

    result: dict[str, Any] = {"action": action, "entity_id": entity}
    if params:
        result["params"] = params
    return result


@app.post("/api/exec_intent")
def exec_intent(b: IntentBody, _s: None = Depends(_require_service)):
    rid = b.request_id or f"rid-{int(time.time() * 1000)}"
    hint_used = bool(b.intent or b.slots)

    action = None
    entity_id = None
    params: dict[str, Any] = {}

    # 1) Hent action fra intent
    if b.intent:
        il = b.intent.lower()
        if "turn_off" in il or il.endswith(".off"):
            action = "turn_off"
        elif "turn_on" in il or il.endswith(".on"):
            action = "turn_on"
        elif "toggle" in il:
            action = None

    # 2) Hent action, entity og params fra slots
    if isinstance(b.slots, dict):
        if not entity_id:
            entity_id = (
                b.slots.get("entity_id") or b.slots.get("entity") or b.slots.get("target_entity")
            )
        if not action:
            slot_action = b.slots.get("action")
            if slot_action in (
                "turn_on",
                "turn_off",
                "toggle",
                "set_level",
                "set_color_temp",
                "set_color",
                "open",
                "close",
                "set_temperature",
                "get_temperature",
                "get_state",
            ):
                action = slot_action
        slot_params = b.slots.get("params")
        if isinstance(slot_params, dict):
            params.update(slot_params)

    # 3) Fallback til alias/NLU på prompten
    nl_parsed = parse_prompt(b.prompt)
    post_ha_log(
        {
            "event": "intent_exec_parse",
            "rid": rid,
            "prompt": b.prompt,
            "intent": b.intent,
            "slots": b.slots,
            "nl_parsed": nl_parsed,
            "confidence": b.confidence,
            "needs_clarification": b.needs_clarification,
            "room_hint": b.room,
        }
    )

    if not action:
        action = nl_parsed.get("action")
    if not entity_id:
        entity_id = nl_parsed.get("entity_id")
    if not params:
        params = nl_parsed.get("params") or {}

    # 4) Romoppslag – bruk TTS-tag eller rom fra slots
    room_hint = b.room or (b.slots or {}).get("room")
    if room_hint and not entity_id:
        canonical = resolve_room(room_hint)
        if canonical and action:
            entity_id = find_entity_in_room(canonical, action)
            log_json(
                event="room_lookup",
                rid=rid,
                room_hint=room_hint,
                canonical=canonical,
                resolved=entity_id,
            )

    # 5) Fortsatt ingen entity?
    if not action or not entity_id:
        return {
            "status": "intent_parse_failed",
            "request_id": rid,
            "parsed": {"action": action, "entity_id": entity_id},
            "hint_used": hint_used,
            "nl_parsed": nl_parsed,
        }

    # 6) Dry-run
    if b.dry_run:
        return {
            "status": "parsed",
            "request_id": rid,
            "parsed": {"action": action, "entity_id": entity_id, "params": params},
            "hint_used": hint_used,
            "confidence": b.confidence,
        }

    # 7) Kjør kommando
    resp = execute_ha_command(
        {
            "request_id": rid,
            "action": action,
            "entity_id": entity_id,
            "params": params,
        }
    )

    out = {
        "status": resp.get("status", "ok"),
        "request_id": rid,
        "details": resp,
        "parsed": {"action": action, "entity_id": entity_id, "params": params},
        "hint_used": hint_used,
        "confidence": b.confidence,
    }

    post_ha_log(
        {
            "event": "intent_exec_resp",
            "rid": rid,
            "result": out["status"],
            "entity_id": entity_id,
            "action": action,
            "params": params,
        }
    )

    return out


@app.post("/api/nl_apply")
def nl_apply(b: NLBody, _s: None = Depends(_require_service)):
    rid = b.request_id or f"rid-{int(time.time() * 1000)}"
    parsed = parse_prompt(b.prompt)
    post_ha_log({"event": "nl_parse", "rid": rid, "prompt": b.prompt, "parsed": parsed})
    if not parsed.get("action") or not parsed.get("entity_id"):
        return {"status": "parse_failed", "request_id": rid, "parsed": parsed}
    if b.dry_run:
        return {"status": "parsed", "request_id": rid, "parsed": parsed}
    resp = execute_ha_command(
        {
            "request_id": rid,
            "action": parsed["action"],
            "entity_id": parsed["entity_id"],
            "params": parsed.get("params", {}),
        }
    )
    out = {"status": resp.get("status", "ok"), "request_id": rid, "details": resp, "parsed": parsed}
    post_ha_log(
        {
            "event": "nl_apply_resp",
            "rid": rid,
            "result": out["status"],
            "entity_id": parsed["entity_id"],
            "action": parsed["action"],
        }
    )
    return out


# /command, /push-aliases and /gui removed (P120 Phase 3): no caller — the HA integration
# kare_ha_bridge is disabled, /push-aliases let anyone on the LAN overwrite aliases.yaml, and
# /kaare/www/gui.html does not exist.
