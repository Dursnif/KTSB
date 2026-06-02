#!/usr/bin/env python3
import os, json, sys, time, logging, datetime as dt
from typing import Optional, Dict, Any, List
import requests, yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

sys.path.insert(0, "/kaare")
from kaare_core.config import get_service as _svc

# ---------------- Config ----------------
ENV_PATH      = "/kaare/configs/kare_ha.env"
ALIASES_PATH  = "/kaare/configs/aliases.yaml"
SETTINGS_PATH = "/kaare/configs/settings.yaml"
TOKEN_PATH    = "/kaare/configs/ha_token.env"
GUI_PATH      = "/kaare/www/gui.html"

def load_env(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#"): continue
            if "=" in line:
                k,v = line.split("=",1); out[k.strip()] = v.strip()
    return out

def load_aliases(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("aliases") or data
    aliases: Dict[str, str] = {}
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

def load_rooms(path: str) -> Dict[str, str]:
    """
    Leser rooms-seksjonen og returnerer en flat dict:
    romord → kanonisk romnavn
    f.eks. {"stua": "stue", "dagligstue": "stue", "verkstedet": "verksted", ...}
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("rooms") or {}
    result: Dict[str, str] = {}
    if isinstance(raw, dict):
        for canonical, synonyms in raw.items():
            # Legg til kanonisk navn selv
            result[str(canonical).strip().lower()] = str(canonical).strip().lower()
            if isinstance(synonyms, list):
                for s in synonyms:
                    result[str(s).strip().lower()] = str(canonical).strip().lower()
    return result

def load_room_entities(path: str) -> Dict[str, List[str]]:
    """
    Leser room_entities-seksjonen og returnerer:
    romnavn → liste av entity_id-er
    f.eks. {"stue": ["light.stue", "light.taklys_stue", ...], ...}
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("room_entities") or {}
    result: Dict[str, List[str]] = {}
    if isinstance(raw, dict):
        for room, entities in raw.items():
            if isinstance(entities, list):
                result[str(room).strip().lower()] = [str(e).strip() for e in entities]
    return result

E = load_env(ENV_PATH)
KARE_LOG_URL    = E.get("KARE_LOG_URL","").rstrip("/") or "http://kaare-api:8000"
KARE_HA_TIMEOUT = float(E.get("KARE_HA_TIMEOUT","5"))
ALLOWED_ACTIONS = {a.strip() for a in (E.get("KARE_ALLOWED_ACTIONS","").split(",")) if a.strip()}

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

ALIASES      = load_aliases(ALIASES_PATH)
ROOMS        = load_rooms(ALIASES_PATH)
ROOM_ENTITIES = load_room_entities(ALIASES_PATH)

# --------------- Logging ----------------
LOG_DIR = "/kaare/logs"
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR,"kaare_ha_gateway.log"),
                    level=logging.INFO, format="%(message)s")

def now_utc(): return dt.datetime.utcnow().isoformat()+"Z"
def log_json(**kw): logging.info(json.dumps({"ts":now_utc(),"source":"kaare-ha-gateway",**kw}, ensure_ascii=False))
def post_ha_log(attrs: Dict[str,Any]):
    try:
        requests.post(KARE_LOG_URL, json={"schema":"v1","source":"kaare-ha-gateway","subsystem":"ha",
                                          "instance":"default","ts":now_utc(),"attrs":attrs}, timeout=2.0)
    except Exception as e:
        log_json(event="ha_log_error", error=str(e))

# ------------- Direkte HA REST-kall --------------

def ha_get(path: str, timeout: float = None):
    r = requests.get(
        f"{HA_API_URL}{path}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        timeout=timeout or KARE_HA_TIMEOUT,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"ha_status": r.status_code, "ha_body": r.text})
    return r.json()

def call_ha_service(domain: str, service: str, data: Dict[str, Any], timeout: float = None):
    r = requests.post(
        f"{HA_API_URL}/api/services/{domain}/{service}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        json=data,
        timeout=timeout or KARE_HA_TIMEOUT,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail={"ha_status": r.status_code, "ha_body": r.text})
    return r.json()

def map_action_to_service(entity_id: str, action: str, params: Dict[str, Any]):
    if "." not in entity_id:
        raise HTTPException(status_code=400, detail="invalid entity_id")
    domain = entity_id.split(".", 1)[0]
    payload = {"entity_id": entity_id}

    if action in ("turn_on", "turn_off"):
        if params:
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

def execute_ha_command(payload: Dict[str, Any]) -> Dict[str, Any]:
    entity_id = payload.get("entity_id")
    action    = payload.get("action")
    params    = payload.get("params") or {}
    rid       = payload.get("request_id", "")

    if isinstance(entity_id, list):
        entity_id = entity_id[0] if entity_id else None
    entity_id = ALIASES.get(entity_id, entity_id)

    domain, service, result = map_action_to_service(entity_id, action, params)

    if domain is None:
        return {"status": "ok", "rid": rid, "details": result}

    ha_resp = call_ha_service(domain, service, result)
    return {"status": "ok", "rid": rid, "details": ha_resp}

# --------------- Hjelpefunksjoner ----------------

def resolve_room(room_hint: str) -> Optional[str]:
    """
    Normaliserer et romord til kanonisk romnavn.
    f.eks. "stua" → "stue", "verkstedet" → "verksted"
    Returnerer None hvis ukjent.
    """
    if not room_hint:
        return None
    return ROOMS.get(room_hint.strip().lower())

def find_entity_in_room(canonical_room: str, action: str) -> Optional[str]:
    """
    Finn beste entity i et rom basert på action-type.
    turn_on/turn_off/set_level → foretrekk light.*, deretter switch.*
    set_temperature             → foretrekk climate.*
    get_temperature             → foretrekk sensor.*temperatur*
    """
    entities = ROOM_ENTITIES.get(canonical_room, [])
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

    # Ingen preferanse matchet – returner første
    return entities[0]

# --------------- API --------------------
class ApplyBody(BaseModel):
    action: str
    entity_id: str
    params: Optional[Dict[str,Any]] = None
    request_id: Optional[str] = None
    source: Optional[str] = "kare"

app = FastAPI(title="Kåre HA Gateway", version="0.5.0")

@app.get("/")
def root(): return {"status":"ok","service":"kaare-ha-gateway"}

@app.post("/api/reload")
def gateway_reload():
    """Hot-reload all file-based config caches without restarting the gateway."""
    global ALIASES, ROOMS, ROOM_ENTITIES, HA_API_URL, HA_TOKEN
    reloaded = []
    errors = []
    try:
        ALIASES = load_aliases(ALIASES_PATH)
        ROOMS = load_rooms(ALIASES_PATH)
        ROOM_ENTITIES = load_room_entities(ALIASES_PATH)
        reloaded.append(f"aliases.yaml ({len(ALIASES)} aliaser)")
    except Exception as e:
        errors.append(f"aliases.yaml: {e}")
    try:
        HA_API_URL, HA_TOKEN = _load_ha_api_config()
        reloaded.append("ha_api_config")
    except Exception as e:
        errors.append(f"ha_api_config: {e}")
    return {"reloaded": reloaded, "errors": errors}

@app.get("/health")
def health():
    try:
        r = requests.get(
            f"{HA_API_URL}/api/config",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            timeout=2.0,
        )
        ok = r.status_code == 200
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded"}

@app.get("/api/ha_status/{entity_id:path}")
def ha_status(entity_id: str):
    """Les tilstand på en HA-entitet direkte fra HA REST API."""
    if not HA_API_URL or not HA_TOKEN:
        raise HTTPException(status_code=503, detail="HA API ikke konfigurert")
    try:
        r = requests.get(
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
            "state":     data.get("state"),
            "unit":      data.get("attributes", {}).get("unit_of_measurement", ""),
            "friendly":  data.get("attributes", {}).get("friendly_name", entity_id),
            "attributes": data.get("attributes", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/ha_apply")
def ha_apply(b: ApplyBody):
    rid = b.request_id or f"rid-{int(time.time()*1000)}"
    action = b.action.strip()
    if ALLOWED_ACTIONS and action not in ALLOWED_ACTIONS:
        post_ha_log({"event":"reject_action","rid":rid,"action":action,"entity_id":b.entity_id})
        raise HTTPException(status_code=400, detail=f"action '{action}' not in allowed list")
    payload = {"request_id":rid, "action":action, "entity_id":b.entity_id, "params":b.params or {}}
    log_json(event="forward", rid=rid, to="ha_direct", payload=payload)
    post_ha_log({"event":"apply_req","rid":rid,"action":action,"entity_id":b.entity_id,"params":b.params or {}})
    resp = execute_ha_command(payload)
    out = {"status":resp.get("status","ok"), "request_id":rid, "details":resp}
    post_ha_log({"event":"apply_resp","rid":rid,"result":out["status"],"entity_id":b.entity_id,"action":action})
    log_json(event="forward_done", rid=rid, result=out["status"])
    return out

# ---------- NL-parser + GUI -------------
class NLBody(BaseModel):
    prompt: str
    request_id: Optional[str] = None
    dry_run: Optional[bool] = False

class IntentBody(BaseModel):
    prompt: str
    intent: Optional[str] = None
    slots: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None
    needs_clarification: Optional[bool] = False
    request_id: Optional[str] = None
    dry_run: Optional[bool] = False
    room: Optional[str] = None  # TTS-tag fra smarthøyttaler

_COLOR_TEMP_PRESETS = {
    "stearinlys":   2200,
    "levende lys":  2200,
    "varm hvit":    2700,
    "varmt lys":    2700,
    "varm lys":     2700,
    "nøytral hvit": 4000,
    "nøytralt lys": 4000,
    "kjølig hvit":  6000,
    "kald hvit":    6500,
    "kaldt lys":    6500,
    "dagslys":      6500,
}

_COLOR_MAP = {
    "rød":     [255,   0,   0],
    "rødt":    [255,   0,   0],
    "grønn":   [  0, 180,   0],
    "grønt":   [  0, 180,   0],
    "blå":     [  0,  80, 255],
    "blått":   [  0,  80, 255],
    "gul":     [255, 200,   0],
    "gult":    [255, 200,   0],
    "lilla":   [150,   0, 200],
    "lila":    [150,   0, 200],
    "fiolett": [128,   0, 200],
    "oransje": [255, 120,   0],
    "rosa":    [255,  80, 150],
    "turkis":  [  0, 200, 200],
    "cyan":    [  0, 220, 220],
}

def parse_prompt(prompt: str) -> Dict[str,Any]:
    import re
    p = (prompt or "").strip().lower()
    off_kw       = ["skru av","slå av","ha av","turn off","off"]
    on_kw        = ["skru på","slå på","ha på","turn on","on"]
    temp_kw      = ["sett temperatur","sett temp","set temperature","set temp","varm opp til","kjøl ned til"]
    level_kw     = ["sett nivå","sett lysstyrke","dimm til","set level","set brightness"]
    color_temp_kw= ["fargetemperatur","fargetemp","color temp","color temperature","lystemperatur"]

    action = "turn_off" if any(k in p for k in off_kw) else None
    if any(k in p for k in on_kw):        action = "turn_on"        if action is None else action
    if any(k in p for k in temp_kw):      action = "set_temperature" if action is None else action
    if any(k in p for k in level_kw):     action = "set_level"       if action is None else action
    if any(k in p for k in color_temp_kw): action = "set_color_temp" if action is None else action

    params: Dict[str, Any] = {}

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
            val = float(m.group(1).replace(",","."))
            if action == "set_temperature": params["temperature"] = val
            if action == "set_level":       params["level"] = val

    entity = None
    for alias_key, ent in ALIASES.items():
        if alias_key and alias_key in p: entity = ent; break

    result: Dict[str, Any] = {"action": action, "entity_id": entity}
    if params:
        result["params"] = params
    return result

@app.post("/api/exec_intent")
def exec_intent(b: IntentBody):
    rid = b.request_id or f"rid-{int(time.time()*1000)}"
    hint_used = bool(b.intent or b.slots)

    action = None
    entity_id = None
    params: Dict[str, Any] = {}

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
                b.slots.get("entity_id")
                or b.slots.get("entity")
                or b.slots.get("target_entity")
            )
        if not action:
            slot_action = b.slots.get("action")
            if slot_action in ("turn_on", "turn_off", "toggle", "set_level", "set_color_temp", "set_color", "open", "close", "set_temperature", "get_temperature", "get_state"):
                action = slot_action
        slot_params = b.slots.get("params")
        if isinstance(slot_params, dict):
            params.update(slot_params)

    # 3) Fallback til alias/NLU på prompten
    nl_parsed = parse_prompt(b.prompt)
    post_ha_log({
        "event": "intent_exec_parse",
        "rid": rid,
        "prompt": b.prompt,
        "intent": b.intent,
        "slots": b.slots,
        "nl_parsed": nl_parsed,
        "confidence": b.confidence,
        "needs_clarification": b.needs_clarification,
        "room_hint": b.room,
    })

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
            log_json(event="room_lookup", rid=rid, room_hint=room_hint,
                     canonical=canonical, resolved=entity_id)

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
    resp = execute_ha_command({
        "request_id": rid,
        "action": action,
        "entity_id": entity_id,
        "params": params,
    })

    out = {
        "status": resp.get("status", "ok"),
        "request_id": rid,
        "details": resp,
        "parsed": {"action": action, "entity_id": entity_id, "params": params},
        "hint_used": hint_used,
        "confidence": b.confidence,
    }

    post_ha_log({
        "event": "intent_exec_resp",
        "rid": rid,
        "result": out["status"],
        "entity_id": entity_id,
        "action": action,
        "params": params,
    })

    return out


@app.post("/api/nl_apply")
def nl_apply(b: NLBody):
    rid = b.request_id or f"rid-{int(time.time()*1000)}"
    parsed = parse_prompt(b.prompt)
    post_ha_log({"event":"nl_parse","rid":rid,"prompt":b.prompt,"parsed":parsed})
    if not parsed.get("action") or not parsed.get("entity_id"):
        return {"status":"parse_failed","request_id":rid,"parsed":parsed}
    if b.dry_run: return {"status":"parsed","request_id":rid,"parsed":parsed}
    resp = execute_ha_command({"request_id":rid,"action":parsed["action"],"entity_id":parsed["entity_id"],"params":parsed.get("params",{})})
    out = {"status":resp.get("status","ok"), "request_id":rid, "details":resp, "parsed":parsed}
    post_ha_log({"event":"nl_apply_resp","rid":rid,"result":out["status"],"entity_id":parsed["entity_id"],"action":parsed["action"]})
    return out

@app.post("/command")
async def command_compat(request: Request):
    """Bakoverkompatibelt endepunkt — aksepterer samme payload som mini_kaareha gjorde."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    return execute_ha_command(payload)

@app.post("/push-aliases")
async def push_aliases(request: Request):
    global ALIASES, ROOMS, ROOM_ENTITIES

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        # Lagre rå yaml uendret – bevarer rooms, room_entities og kommentarer
        with open(ALIASES_PATH, "wb") as f:
            f.write(raw)

        ALIASES       = load_aliases(ALIASES_PATH)
        ROOMS         = load_rooms(ALIASES_PATH)
        ROOM_ENTITIES = load_room_entities(ALIASES_PATH)

        log_json(event="aliases_updated", count=len(ALIASES),
                 rooms=len(ROOMS), room_entities=len(ROOM_ENTITIES))
        post_ha_log({"event": "aliases_updated", "count": len(ALIASES),
                     "rooms": len(ROOMS), "room_entities": len(ROOM_ENTITIES)})

        return {"status": "ok", "count": len(ALIASES),
                "rooms": len(ROOMS), "room_entities": len(ROOM_ENTITIES)}

    except Exception as e:
        log_json(event="aliases_update_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/gui", response_class=HTMLResponse)
def gui():
    if os.path.exists(GUI_PATH):
        with open(GUI_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h3>GUI not found</h3>", status_code=200)
