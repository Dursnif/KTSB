import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from adapters.llm_adapter import ask_llm, ask_llm_cloud, ask_llm_with_tools, ask_vlm
from kaare_core.tools.i18n import t, get_lang
from kaare_core.agents.miss_kare.evaluator import evaluate as _miss_kare_evaluate
from kaare_core.agents.miss_kare.stm import MissKareSTM
from kaare_core.audit import audit_log as _audit
from kaare_core.config import get_model, get_service, reload_capability_services, get_settings as _get_settings
from kaare_core.rate_limiter import check_rate_limit as _rate_check
from kaare_core.ha.clarification import ha_clarification_rescue
from kaare_core.memory.short_term import ShortTermMemory, STMRegistry
from kaare_core.routers.router_generate import handle_generate
from kaare_core.routers.router_users import router as users_router
from kaare_core.users import store as _user_store
from kaare_core.users.auth import (
    get_network_context as _get_network_context,
    require_admin as _require_admin,
    require_auth as _require_auth,
    require_image_auth as _require_image_auth,
    touch_last_seen as _touch_last_seen,
)
from kaare_core.users.store import init_db as init_users_db
from kaare_fastpath import match_fastpath
import kaare_core.app_state as app_state
from kaare_core.routers.router_argus import router as argus_router
from kaare_core.routers.router_media import router as media_router
from kaare_core.routers.router_meetings import router as meetings_router
from kaare_core.routers.router_system import router as system_router
from kaare_core.routers.router_settings import router as settings_router
from kaare_core.routers.router_memory import router as memory_router
from kaare_core.routers.router_backup import router as backup_router

_miss_kare_stm = MissKareSTM()
_miss_kare_latest: dict[str, str] = {}  # user_id → latest comment (cleared on fetch)

# _last_user_prompt_time lives in app_state — reflection_loop reads it from there

_MK_PREFIXES = ("miss kåre", "miss kare")  # case-insensitive prefix triggers

def _detect_miss_kare_addressed(prompt: str) -> bool:
    return prompt.lower().startswith(_MK_PREFIXES)

# backward-compat alias — remove when all callers use ask_llm directly
call_llm = ask_llm

# Optional module — never crash if unavailable
try:
    from services.voice.voice_manager import register_voice_endpoints
    _voice_manager_ok = True
except Exception as _e:
    _voice_manager_ok = False
    import logging as _logging
    _logging.getLogger("kaare_api").warning("voice_manager not available: %s", _e)

ROUTE_LOG = "/kaare/logs/route_decisions.log"
os.makedirs(os.path.dirname(ROUTE_LOG), exist_ok=True)

def _route_log(stage: str, **fields):
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "kaare",
        "subsystem": "routing",
        "stage": stage,
        **fields,
    }
    try:
        with open(ROUTE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _normalize_text(text: str) -> str:
    """Normalize text using lang_normalize.yaml rules before alias lookup."""
    if not text:
        return ""

    t = str(text).strip().lower()
    if not t:
        return ""

    rules = app_state.LANG_NORMALIZE if isinstance(app_state.LANG_NORMALIZE, dict) else {}
    noise = rules.get("noise") or []
    verbs = rules.get("verbs") or {}
    synonyms = rules.get("synonyms") or []
    words = rules.get("words") or {}

    try:
        if isinstance(noise, list):
            for phrase in noise:
                if isinstance(phrase, str) and phrase.strip():
                    p = phrase.strip().lower()
                    if p and p in t:
                        t = t.replace(p, " ")
    except Exception:
        pass

    t = " ".join(t.split())

    # sort longest-first to avoid partial matches (e.g. "skru av" before "av")
    try:
        if isinstance(verbs, dict) and verbs:
            for k in sorted([x for x in verbs.keys() if isinstance(x, str)], key=len, reverse=True):
                v = verbs.get(k)
                if not isinstance(v, str):
                    continue
                kk = k.strip().lower()
                vv = v.strip().lower()
                if kk and vv and kk in t:
                    t = t.replace(kk, vv)
    except Exception:
        pass

    t = " ".join(t.split())

    try:
        pairs = []
        if isinstance(synonyms, list):
            for item in synonyms:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) == 2
                    and isinstance(item[0], str)
                    and isinstance(item[1], str)
                ):
                    src = item[0].strip().lower()
                    dst = item[1].strip().lower()
                    if src and dst and src != dst:
                        pairs.append((src, dst))
        for src, dst in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
            if src in t:
                t = t.replace(src, dst)
    except Exception:
        pass

    t = " ".join(t.split())

    try:
        if isinstance(words, dict) and words:
            wmap = {}
            for k, v in words.items():
                if isinstance(k, str) and isinstance(v, str):
                    kk = k.strip().lower()
                    vv = v.strip().lower()
                    if kk and vv:
                        wmap[kk] = vv

            if wmap:
                toks = []
                for tok in t.split():
                    toks.append(wmap.get(tok, tok))
                t = " ".join(toks)
    except Exception:
        pass

    return " ".join(t.split())


CAPABILITY_MAP_PATH = "/kaare/capability_map.yaml"

with open(CAPABILITY_MAP_PATH, "r", encoding="utf-8") as f:
    app_state.CAPABILITY_MAP = yaml.safe_load(f)

ALIASES_PATH = "/kaare/configs/aliases.yaml"

try:
    with open(ALIASES_PATH, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
        app_state.ALIASES = loaded.get("aliases", loaded)
except Exception:
    app_state.ALIASES = {}
if isinstance(app_state.ALIASES, dict) and "aliases" in app_state.ALIASES:
    app_state.ALIASES = app_state.ALIASES.get("aliases") or {}


LANG_NORMALIZE_PATH = "/kaare/configs/lang_normalize.yaml"
try:
    with open(LANG_NORMALIZE_PATH, "r", encoding="utf-8") as f:
        _sv = yaml.safe_load(f) or {}
        app_state.LANG_NORMALIZE = (_sv.get("normalize") if isinstance(_sv, dict) else {}) or {}
except Exception:
    app_state.LANG_NORMALIZE = {}



HA_LOG_PATH = "/kaare/argus_inbox/ha_bridge.log"
os.makedirs(os.path.dirname(HA_LOG_PATH), exist_ok=True)


app = FastAPI(title="Kåre Hoved-AI Orkestrator")
app.include_router(users_router)
app.include_router(argus_router)
app.include_router(media_router)
app.include_router(meetings_router)
app.include_router(system_router)
app.include_router(settings_router)
app.include_router(memory_router)
app.include_router(backup_router)
init_users_db()

# Suppress uvicorn access log for high-frequency poll endpoints
import logging as _logging

class _SuppressPolls(_logging.Filter):
    _QUIET = {
        "/api/miss_kare/comment", "/api/tools/recent",
        "/api/memory/compress/status", "/api/meetings/status",
    }
    def filter(self, record: _logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(ep in msg for ep in self._QUIET)

_logging.getLogger("uvicorn.access").addFilter(_SuppressPolls())


@app.on_event("startup")
async def _startup():
    from kaare_core.tools.timer_service import restore_timers, start_action_queue_worker
    from kaare_core.agents.mechanic.job_store import start_background_tasks as _start_job_store
    restore_timers()
    await start_action_queue_worker()
    _start_job_store()
    _restore_stm()
    asyncio.create_task(_start_mqtt())
    from kaare_core.reflection_loop import start_reflection_loop
    asyncio.create_task(start_reflection_loop())
    asyncio.create_task(_stm_snapshot_loop())


def _stm_cfg() -> dict:
    try:
        return yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text()).get("stm", {})
    except Exception:
        return {}


def _restore_stm() -> None:
    """Migrate legacy snapshot to per-user files, load snapshots, load per-user daily summaries."""
    import logging
    logger = logging.getLogger("kaare_api")
    new_dir = "/kaare/state/stm_users"
    old_path = "/kaare/state/stm_snapshot.json"
    try:
        if not Path(new_dir).exists() and Path(old_path).exists():
            migrated = app_state.STM_REGISTRY.migrate_legacy_snapshot(old_path, new_dir)
            if migrated:
                logger.info("STM: migrated legacy snapshot to per-user files in %s", new_dir)
        app_state.STM_REGISTRY.load_snapshots(new_dir)
        counts = app_state.STM_REGISTRY.snapshot_counts()
        logger.info("STM snapshots restored: %s", counts)
    except Exception as e:
        logger.warning("STM restore failed: %s", e)
    try:
        from kaare_core.memory.long_term import load_latest_daily_summary
        from kaare_core.users.store import list_users
        all_user_ids = [u["username"] for u in list_users()] + ["global"]
        for uid in all_user_ids:
            summary = load_latest_daily_summary(user_id=uid)
            if summary:
                app_state.STM_REGISTRY.get(uid).set_daily_summary(summary)
                logger.info("STM daily summary loaded for %s (%d chars)", uid, len(summary))
    except Exception as e:
        logger.warning("Could not load STM daily summaries: %s", e)


async def _stm_snapshot_loop() -> None:
    """Background task: save per-user STM snapshots every N seconds; rotate daily history."""
    from datetime import timedelta
    import logging
    logger = logging.getLogger("kaare_api")

    cfg = _stm_cfg()
    snapshot_dir = "/kaare/state/stm_users"
    history_dir = Path(cfg.get("history_dir", "/kaare/state/stm_history"))
    interval = int(cfg.get("snapshot_interval_seconds", 300))
    history_days = int(cfg.get("history_days", 7))

    last_date = datetime.now(timezone.utc).date()

    while True:
        await asyncio.sleep(interval)
        try:
            app_state.STM_REGISTRY.snapshot_all(snapshot_dir)

            today = datetime.now(timezone.utc).date()
            if today != last_date:
                daily_dir = history_dir / last_date.isoformat()
                app_state.STM_REGISTRY.snapshot_all(str(daily_dir))
                last_date = today
                logger.info("STM daily snapshot saved: %s", daily_dir)

                # Prune history older than history_days
                if history_dir.exists():
                    cutoff = today - timedelta(days=history_days)
                    for f in history_dir.glob("????-??-??"):
                        try:
                            if datetime.strptime(f.name, "%Y-%m-%d").date() < cutoff:
                                import shutil
                                shutil.rmtree(f)
                                logger.info("STM history pruned: %s", f.name)
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("STM snapshot loop error: %s", e)


async def _start_mqtt():
    try:
        from adapters.mqtt_adapter import run_mqtt_listener, register_end_event_callback
        from kaare_core.domain.frigate_responder import handle_end_event, load_camera_config
        load_camera_config()
        register_end_event_callback(handle_end_event)
        await run_mqtt_listener()
    except Exception as e:
        import logging
        logging.getLogger("kaare_api").warning("MQTT adapter not started: %s", e)


# Every 10 minutes, Kåre reads Jang's distilled thoughts and decides whether
# to update his self-image (personality_self.md) or notepad. Runs silently —
# no user output, no TTS. Paused 10 min before each scheduled meeting and
# during any running meeting.

def _load_reflection_config() -> tuple[bool, int]:
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text()).get("kare_reflection", {})
        return bool(cfg.get("enabled", False)), int(cfg.get("interval_seconds", 600))
    except Exception:
        return False, 600

app_state._REFLECTION_ENABLED, app_state._JANG_INTERVAL_S = _load_reflection_config()

try:
    import yaml as _cors_yaml
    _cors_cfg = _cors_yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
    _cors_origins: list = _cors_cfg.get("cors_origins", ["*"])
except Exception:
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,  # We use JWT in headers, not cookies — credentials=True is unnecessary
    allow_methods=["*"],
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


def _create_stm_registry() -> STMRegistry:
    """Create STMRegistry with per-user STM parameters from settings.yaml."""
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
        c = cfg.get("stm", {})
        kwargs = dict(
            dialog_max_turns=int(c.get("dialog_max_turns", 40)),
            dialog_max_text=int(c.get("dialog_max_text", 600)),
            actions_max=int(c.get("actions_max", 80)),
            actions_max_text=int(c.get("actions_max_text", 300)),
            state_max_keys=int(c.get("state_max_keys", 2000)),
            context_max_chars=int(c.get("context_max_chars", 20000)),
            context_last_dialog_turns=int(c.get("context_last_dialog_turns", 30)),
            context_last_actions=int(c.get("context_last_actions", 10)),
            context_last_state_keys=int(c.get("context_last_state_keys", 24)),
        )
    except Exception as _e:
        import logging as _l
        _l.getLogger("kaare_api").warning("STM config read failed, using defaults: %s", _e)
        kwargs = {}
    return STMRegistry(stm_kwargs=kwargs)


# Parameters come from configs/settings.yaml [stm:]
app_state.STM_REGISTRY = _create_stm_registry()

if _voice_manager_ok:
    register_voice_endpoints(app)

@app.get("/")
def read_root():
    return {"message": t("api_hello", get_lang("global"))}


@app.post("/api/reload")
async def api_reload():
    """
    Hot-reload all file-based config caches without restarting Kåre.
    Covers: aliases, lang_normalize, capability_map, llm.yaml, settings.yaml,
            personality files, and the HA gateway's own caches.
    """
    reloaded = []
    errors = []

    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            new_aliases = loaded.get("aliases", loaded)
        if isinstance(new_aliases, dict) and "aliases" in new_aliases:
            new_aliases = new_aliases.get("aliases") or {}
        app_state.ALIASES = new_aliases if isinstance(new_aliases, dict) else {}
        reloaded.append("aliases.yaml")
    except Exception as e:
        errors.append(f"aliases.yaml: {e}")

    try:
        with open(LANG_NORMALIZE_PATH, "r", encoding="utf-8") as f:
            _sv = yaml.safe_load(f) or {}
            app_state.LANG_NORMALIZE = (_sv.get("normalize") if isinstance(_sv, dict) else {}) or {}
        reloaded.append("lang_normalize.yaml")
    except Exception as e:
        errors.append(f"lang_normalize.yaml: {e}")

    try:
        with open(CAPABILITY_MAP_PATH, "r", encoding="utf-8") as f:
            app_state.CAPABILITY_MAP = yaml.safe_load(f) or {}
        reloaded.append("capability_map.yaml")
    except Exception as e:
        errors.append(f"capability_map.yaml: {e}")

    try:
        from adapters import web_search_adapter as _wsa
        _wsa.reload_config()
        reloaded.append("trusted_sources.yaml")
        reloaded.append("websearch_config")
    except Exception as e:
        errors.append(f"trusted_sources.yaml: {e}")

    try:
        from adapters import llm_adapter
        llm_reloaded = llm_adapter.reload_config()
        reloaded.extend(llm_reloaded)
    except Exception as e:
        errors.append(f"llm_adapter: {e}")

    try:
        from kaare_core import config as _core_cfg
        core_reloaded = _core_cfg.reload_config()
        reloaded.extend([f"core:{x}" for x in core_reloaded])
    except Exception as e:
        errors.append(f"core_config: {e}")

    try:
        _svc_cfg = yaml.safe_load(open("/kaare/configs/services.yaml", encoding="utf-8")) or {}
        _ha_url = _svc_cfg.get("home_assistant", {}).get("url", "")
        if not _ha_url:
            reloaded.append("gateway: skipped (HA not configured)")
        else:
            import httpx as _hx
            _gw_url = _svc_cfg.get("internal", {}).get("ha_gateway", "http://127.0.0.1:8002")
            async with _hx.AsyncClient(timeout=5.0) as _client:
                _r = await _client.post(f"{_gw_url}/api/reload")
                _gw = _r.json()
                reloaded.extend([f"gateway:{x}" for x in _gw.get("reloaded", [])])
                errors.extend([f"gateway:{x}" for x in _gw.get("errors", [])])
    except Exception as e:
        errors.append(f"gateway: {e}")

    try:
        app_state._REFLECTION_ENABLED, app_state._JANG_INTERVAL_S = _load_reflection_config()
        reloaded.append("kare_reflection")
    except Exception as e:
        errors.append(f"kare_reflection: {e}")

    try:
        from kaare_core.domain.frigate_responder import load_camera_config
        load_camera_config()
        reloaded.append("frigate_cameras.yaml")
    except Exception as e:
        errors.append(f"frigate_cameras.yaml: {e}")

    _logging.getLogger("kaare_api").info("[reload] Reloaded: %s", reloaded)
    return {
        "reloaded": reloaded,
        "errors": errors,
        "aliases_count": len(app_state.ALIASES),
        "lang_normalize_keys": list(app_state.LANG_NORMALIZE.keys()),
    }



_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

def _get_kare_lang() -> str:
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return data.get("kare_language") or data.get("language", "nb")
    except Exception:
        return "nb"


def _fp_lang() -> str:
    """Return normalized 2-letter language code for fastpath responses."""
    raw = _get_kare_lang().lower().split("-")[0].split("_")[0]
    return "nb" if raw in ("no", "nb", "nn") else raw


_FASTPATH_STRINGS: dict[str, dict[str, str]] = {
    "vpn_blocked": {
        "nb": "Du er ikke hjemme og har ikke ekstern tilgang. Spør en administrator om å aktivere det.",
        "en": "You are not at home and do not have external access. Ask an administrator to enable it.",
        "de": "Sie sind nicht zu Hause und haben keinen externen Zugang. Bitten Sie einen Administrator, dies zu aktivieren.",
    },
    "ha_blocked": {
        "nb": "Smarthus-kontroll er ikke tilgjengelig eksternt for din bruker.",
        "en": "Smart home control is not available remotely for your account.",
        "de": "Smart-Home-Steuerung ist für Ihr Konto nicht remote verfügbar.",
    },
    "fastpath_error": {
        "nb": "Klarte ikke å utføre kommandoen, prøv igjen.",
        "en": "Command failed, please try again.",
        "de": "Befehl fehlgeschlagen, bitte erneut versuchen.",
    },
}

def _fpt(key: str) -> str:
    """Return a localized fastpath string."""
    lang = _fp_lang()
    bucket = _FASTPATH_STRINGS.get(key, {})
    return bucket.get(lang) or bucket.get("en") or next(iter(bucket.values()), key)

def _clock_text(time_str: str) -> str:
    lang = _fp_lang()
    if lang == "de":
        return f"Es ist {time_str} Uhr."
    if lang == "en":
        return f"The time is {time_str}."
    return f"Klokka er {time_str}."

def _action_text(action: str, entity_id: str) -> str:
    lang = _fp_lang()
    if lang == "de":
        verb = "Eingeschaltet" if action == "turn_on" else "Ausgeschaltet"
        return f"{verb}: {entity_id}."
    if lang == "en":
        verb = "Turned on" if action == "turn_on" else "Turned off"
        return f"{verb} {entity_id}."
    verb = "skrudde på" if action == "turn_on" else "skrudde av"
    return f"Jeg {verb} {entity_id}."


def _mqtt_fastpath_text(topic: str) -> str:
    lang = _fp_lang()
    label = topic.rsplit("/", 1)[-1]
    if lang == "de":
        return f"Befehl gesendet ({label})."
    if lang == "en":
        return f"Command sent ({label})."
    return f"Kommando sendt ({label})."


def _os_fastpath_text(action: str) -> str:
    import psutil
    lang = _fp_lang()
    try:
        if action == "cpu_stats":
            pct = psutil.cpu_percent(interval=0.2)
            if lang == "de":
                return f"CPU-Auslastung: {pct:.0f} %."
            if lang == "en":
                return f"CPU usage: {pct:.0f}%."
            return f"CPU-belastning: {pct:.0f} %."
        if action == "ram_stats":
            mem = psutil.virtual_memory()
            used_gb = mem.used / 1_073_741_824
            total_gb = mem.total / 1_073_741_824
            if lang == "de":
                return f"RAM: {used_gb:.1f} GB / {total_gb:.1f} GB ({mem.percent:.0f} %)."
            if lang == "en":
                return f"RAM: {used_gb:.1f} GB / {total_gb:.1f} GB ({mem.percent:.0f}%)."
            return f"RAM: {used_gb:.1f} GB / {total_gb:.1f} GB ({mem.percent:.0f} %)."
        if action == "temp_stats":
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    all_temps = [t.current for readings in temps.values() for t in readings]
                    avg = sum(all_temps) / len(all_temps)
                    if lang == "de":
                        return f"Durchschnittliche CPU-Temperatur: {avg:.0f} °C."
                    if lang == "en":
                        return f"Average CPU temperature: {avg:.0f} °C."
                    return f"Gjennomsnittlig CPU-temperatur: {avg:.0f} °C."
            except Exception:
                pass
            if lang == "de":
                return "Temperatursensoren nicht verfügbar."
            if lang == "en":
                return "Temperature sensors not available."
            return "Temperatursensorer ikke tilgjengelig."
    except Exception as exc:
        return f"OS stats error: {exc}"
    return action


@app.post("/api/ha_log")
async def ha_log_endpoint(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if "source" not in payload:
        payload["source"] = "ha_bridge"
    if "subsystem" not in payload:
        payload["subsystem"] = "ha"
    if "ts" not in payload:
        payload["ts"] = datetime.utcnow().isoformat()

    with open(HA_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return JSONResponse(content={"status": "ok", "written_to": HA_LOG_PATH})


class PromptRequest(BaseModel):
    prompt: str
    images: list[str] | None = None
    source: str | None = None
    user_id: str | None = None
    context: dict | None = None
    rid: str | None = None


@app.post("/api/generate")
async def generate(request: PromptRequest, http: Request):
    prompt = (request.prompt or "").strip()
    images = request.images
    capability_hints = {
        "domains": list(app_state.CAPABILITY_MAP.get("domains", {}).keys()),
        "rooms": list(app_state.CAPABILITY_MAP.get("rooms", {}).keys()),
        "weather_enabled": app_state.CAPABILITY_MAP.get("domains", {}).get("weather", {}).get("enabled", False),
        "time_date_enabled": app_state.CAPABILITY_MAP.get("domains", {}).get("time_date", {}).get("enabled", False),
    }
    source = (request.source or http.headers.get("X-Kaare-Source") or "gui").lower()
    # Use caller-supplied RID when present (voice bridge, dev_meeting, etc.)
    if request.rid:
        rid = request.rid
    else:
        rid = f"rid-{int(time.time()*1000)}"
    _route_log("generate_in", rid=rid, prompt_preview=prompt[:120])
    from kaare_core.memory.long_term import USER_GLOBAL
    user_id = (request.user_id or "").strip() or USER_GLOBAL
    print(f"[KÅRE] request rid={rid} user={user_id} source={source} chars={len(prompt)} images={len(images or [])}")

    # Rate limiting — skip internal sources (reflection, dev_meeting, voice bridge, STT)
    if source not in ("reflection", "dev_meeting", "voice_bridge", "stt", "fastpath"):
        _rl_cfg = _get_settings().get("rate_limit", {})
        if _rl_cfg.get("enabled", True):
            _rl_limit = int(_rl_cfg.get("generate_per_minute", 20))
            _rl_key = f"generate:{user_id}"
            if not _rate_check(_rl_key, _rl_limit):
                _audit("rate_limited", user_id, f"source={source} limit={_rl_limit}/min", request_ip=http.client.host if http.client else "")
                return JSONResponse(
                    status_code=429,
                    content={"ok": False, "error": "rate_limited", "text": "Too many requests. Please wait a moment before sending another message."},
                    headers={"Retry-After": "60"},
                )

    # Track real user activity for Jang injection cooldown
    app_state._last_user_prompt_time = time.time()

    # Update presence so admin dashboard shows correct online status
    if user_id and user_id != USER_GLOBAL:
        _touch_last_seen(user_id)

    _client_ip = http.client.host if http.client else "127.0.0.1"
    _network_ctx = _get_network_context(_client_ip)
    _block_ha_write = False

    if _network_ctx != "local":
        _user_rec = _user_store.get_user(user_id) if user_id and user_id != USER_GLOBAL else None
        _vpn_access = (_user_rec or {}).get("vpn_access", "local_only")
        if _vpn_access == "local_only":
            return {"text": _fpt("vpn_blocked")}
        _block_ha_write = (_vpn_access == "ai_only")

    fast = match_fastpath(prompt)
    _route_log("fastpath_check", rid=rid, hit=bool(fast))
    if fast:
        print(f"[KÅRE] FASTPATH MATCH: {fast}")
        _route_log(
            "fastpath_match",
            rid=rid,
            action=fast.get("action"),
            entity_id=fast.get("entity_id"),
            route=fast.get("route"),
            source_tag=fast.get("source"),
        )

        route = fast.get("route")

        if route == "clock_fastpath":
            from kaare_core.config import get_local_tz as _get_local_tz
            now_str = datetime.now(tz=_get_local_tz()).strftime("%H:%M")
            _route_log("fastpath_clock_done", rid=rid, now=now_str)
            return {"text": _clock_text(now_str)}

        if route == "os_fastpath":
            text = _os_fastpath_text(fast.get("action", "cpu_stats"))
            _route_log("fastpath_os_done", rid=rid, os_action=fast.get("action"))
            return {"text": text}

        # Device control routes (ha + mqtt) — blocked for ai_only VPN access
        if _block_ha_write:
            return {"text": _fpt("ha_blocked")}

        if route == "mqtt_fastpath":
            from adapters.mqtt_adapter import publish_mqtt as _publish_mqtt
            _topic = fast.get("topic", "")
            _payload = fast.get("payload", "{}")
            try:
                ok = await _publish_mqtt(_topic, _payload)
            except Exception as _e:
                ok = False
                _route_log("fastpath_mqtt_error", rid=rid, topic=_topic, error=str(_e))
            if not ok:
                return {"text": _fpt("fastpath_error")}
            _fp_text = _mqtt_fastpath_text(_topic)
            _user_stm = app_state.get_stm(user_id)
            _user_stm.add_dialog(role="user", text=prompt, user_id=user_id)
            _user_stm.add_dialog(role="assistant", text=_fp_text, user_id=user_id)
            _route_log("fastpath_mqtt_done", rid=rid, topic=_topic)
            return {"text": _fp_text}

        # Default: ha_fastpath
        ha_payload = {
            "prompt": prompt,
            "action": fast["action"],
            "entity_id": fast["entity_id"],
            "params": {},
            "kare_route": fast["route"],
            "fastpath_source": fast["source"],
            "dry_run": False
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(
                    "http://127.0.0.1:8002/api/nl_apply",
                    json=ha_payload
                )
            out = r.json()
            print(f"[KÅRE] FASTPATH RESULTAT: {out}")
            _route_log("fastpath_done", rid=rid, ha_result=out)

            _fp_text = _action_text(fast["action"], fast["entity_id"])
            _user_stm = app_state.get_stm(user_id)
            _user_stm.add_dialog(role="user", text=prompt, user_id=user_id)
            _user_stm.record_action(fast["action"], fast["entity_id"], ok=True, user_id=user_id, meta={"source": "fastpath"})
            app_state.STM_REGISTRY.set_entity_state(fast["entity_id"], fast["action"], source="fastpath")
            _user_stm.add_dialog(role="assistant", text=_fp_text, user_id=user_id)
            return {"text": _fp_text}

        except Exception as e:
            print(f"[KÅRE FASTPATH FEIL] {e}")
            _route_log("fastpath_error", rid=rid, error=str(e))
            return {"text": _fpt("fastpath_error")}


    mk_addressed = _detect_miss_kare_addressed(prompt)
    print(f"[MISS KÅRE] rid={rid} addressed={mk_addressed}")

    _speaker_note = ""
    if source == "stt" and request.context:
        _sr = request.context.get("speaker_result", {})
        _confirmed_by   = _sr.get("confirmed_by", "none")
        _confirmed_user = _sr.get("confirmed_user")
        _best_guess     = _sr.get("best_guess")
        _confidence     = _sr.get("confidence", 0.0)
        _threshold      = _sr.get("threshold", 0.75)
        _lang           = get_lang(user_id)
        if _confirmed_by == "voice":
            _speaker_note = t("stt_voice_confirmed", _lang,
                               user=_confirmed_user, pct=int(_confidence * 100))
        elif _confirmed_by == "default":
            if _best_guess and _best_guess != _confirmed_user:
                _speaker_note = t("stt_voice_default_guess", _lang,
                                   user=_confirmed_user, guess=_best_guess,
                                   pct=int(_confidence * 100))
            elif _best_guess:
                _speaker_note = t("stt_voice_default_match", _lang,
                                   user=_confirmed_user, pct=int(_confidence * 100))
            else:
                _speaker_note = t("stt_voice_default_no_enrollment", _lang,
                                   user=_confirmed_user)
        else:
            if _best_guess:
                _speaker_note = t("stt_voice_unknown_guess", _lang,
                                   guess=_best_guess, pct=int(_confidence * 100),
                                   threshold=int(_threshold * 100))
            else:
                _speaker_note = t("stt_voice_unknown_no_enrollment", _lang)

    result = await handle_generate(
        prompt=prompt,
        images=images,
        source=source,
        rid=rid,
        user_id=user_id,
        memory=app_state.get_stm(user_id),
        miss_kare_addressed=mk_addressed,
        api_intent_to_ha=api_intent_to_ha,
        api_exec_ha_direct=exec_ha_direct,
        api_ask_llm=ask_llm,
        api_ask_vlm=ask_vlm,
        api_ask_cloud=ask_llm_cloud,
        block_ha_write=_block_ha_write,
        network_context=_network_ctx,
        speaker_note=_speaker_note,
    )
    result["rid"] = rid

    # fire-and-forget — never blocks the response
    async def _run_miss_kare(u_msg: str, k_reply: str, uid: str, addressed: bool):
        print(f"[MISS KÅRE] evaluator starter rid={rid} addressed={addressed}")
        try:
            comment = await _miss_kare_evaluate(u_msg, k_reply, uid, addressed_directly=addressed)
            print(f"[MISS KÅRE] evaluator ferdig rid={rid} silent={comment == '[STILLE]'}")
            _miss_kare_stm.add(uid, u_msg, k_reply, comment)
            if comment != "[STILLE]":
                _miss_kare_latest[uid] = comment    # frontend poller dette
        except Exception as e:
            print(f"[MISS KÅRE] evaluator krasjet: {e}")

    if app_state._AGENT_ENABLED.get("miss_kare", True):
        asyncio.create_task(_run_miss_kare(prompt, result.get("text", ""), user_id, mk_addressed))

    return result




@app.get("/api/miss_kare/comment")
async def miss_kare_comment(user_id: str = "global"):
    """
    Frontend poller dette etter at Kåre har svart.
    Returnerer Miss Kåres siste kommentar for brukeren (eller tom streng).
    Kommentaren slettes etter henting – vis én gang.
    """
    comment = _miss_kare_latest.pop(user_id, "")
    return {"comment": comment, "user_id": user_id}



@app.get("/api/chat_history")
async def api_chat_history(user_id: str = "global", limit: int = 60, _u=Depends(_require_auth)):
    """
    Returns the last N dialog turns for a user from STM (already persisted to disk).
    Used by the frontend to restore chat after logout/login.
    Only returns user/assistant turns (skips internal system turns).
    """
    limit = max(1, min(limit, 200))
    _user_stm = app_state.get_stm(user_id)
    with _user_stm._lock:
        turns = [
            t for t in _user_stm._dialog
            if t.user_id == user_id and t.role in ("user", "assistant")
        ]
    turns = turns[-limit:]
    return {
        "user_id": user_id,
        "turns": [{"role": t.role, "text": t.text, "ts": t.ts} for t in turns],
    }




# In-place-mutated state — owned by app_state, aliased here for backward compat
@app.get("/api/pending_notifications")
async def api_pending_notifications(user_id: str, _u=Depends(_require_auth)):
    """Returns unacked chat notifications for a user (delivered by timers)."""
    from kaare_core.tools.timer_service import get_pending_notifications as _get_pn
    return {"notifications": _get_pn(user_id)}


@app.post("/api/pending_notifications/{notif_id}/ack")
async def api_ack_notification(notif_id: str, user_id: str, _u=Depends(_require_auth)):
    """Ack a pending chat notification so it is not delivered again."""
    from kaare_core.tools.timer_service import ack_notification as _ack
    ok = _ack(notif_id, user_id)
    return {"ok": ok, "notif_id": notif_id}


@app.get("/api/show")
def show_status():
    return {"status": "ok", "source": "kaare"}


@app.get("/api/think_history")
def api_think_history(
    n: int = 20,
    search: str = "",
    role: str = "",
    recovered_only: bool = False,
    _u=Depends(_require_admin),
):
    """Recent LLM think-block entries from the rolling cache."""
    from kaare_core.tools.think_cache import read_think_history
    try:
        entries = read_think_history(
            n=min(n, 50),
            search=search or None,
            role=role or None,
            recovered_only=recovered_only,
        )
        return {"ok": True, "count": len(entries), "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/agent_messages")
def api_agent_messages(limit: int = 50):
    from kaare_core.memory.long_term import get_ltm
    return get_ltm().get_agent_messages(limit=limit)



METRICS_LOG = "/kaare/logs/metrics_requests.log"
os.makedirs(os.path.dirname(METRICS_LOG), exist_ok=True)

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        raw = await request.body()
        async def _receive():
            return {"type": "http.request", "body": raw}
        request._receive = _receive

        resp = await call_next(request)
        dur_ms = (time.perf_counter() - start) * 1000.0

        prompt = ""
        try:
            data = json.loads(raw or b"{}")
            prompt = (data.get("prompt") or data.get("input") or data.get("intent") or "")
        except Exception:
            pass

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "endpoint": request.url.path,
            "status": getattr(resp, "status_code", 0),
            "duration_ms": round(dur_ms, 2),
            "prompt_preview": (prompt[:120] if prompt else ""),
            "prompt_hash": (hashlib.sha1(prompt.encode("utf-8")).hexdigest() if prompt else None),
        }
        try:
            with open(METRICS_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return resp

app.add_middleware(MetricsMiddleware)
from collections import defaultdict
from pathlib import Path

def _iter_metrics(lines_max=50000):
    p = Path(METRICS_LOG)
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines()[-lines_max:]:
            try:
                yield json.loads(line)
            except Exception:
                continue
    except Exception:
        return

@app.get("/api/tools/recent")
def api_tools_recent(n: int = 100):
    """Siste N tool-kall + aktive timere + timer-counts per bruker. Brukes av admin Tools-fane."""
    from kaare_core.tools.timer_service import get_active_timers, get_timer_counts_by_user
    tool_log = Path("/kaare/logs/tool_calls.log")
    calls = []
    try:
        lines = tool_log.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            try:
                calls.append(json.loads(line))
                if len(calls) >= n:
                    break
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return {
        "ok": True,
        "timers": get_active_timers(),
        "timer_counts": get_timer_counts_by_user(),
        "calls": calls,
    }


@app.get("/api/metrics/hourly")
def api_metrics_hourly(hours: int = 24):
    per_hour = defaultdict(int)
    per_ep = defaultdict(lambda: defaultdict(int))
    for r in _iter_metrics():
        ts = (r.get("ts","")[:13])  # YYYY-MM-DD HH
        per_hour[ts] += 1
        per_ep[r.get("endpoint","")][ts] += 1
    keys = sorted(per_hour.keys())[-hours:]
    return {
        "hours": keys,
        "total_per_hour": {k: per_hour.get(k,0) for k in keys},
        "per_endpoint": {ep: {k: per_ep[ep].get(k,0) for k in keys} for ep in per_ep}
    }

@app.get("/api/metrics/prompts")
def api_metrics_prompts(min_calls: int = 1, top: int = 20):
    agg = {}
    for r in _iter_metrics():
        ph = r.get("prompt_hash")
        if not ph:
            continue
        a = agg.setdefault(ph, {"count":0, "sum_ms":0.0, "preview": r.get("prompt_preview","")})
        a["count"] += 1
        try:
            a["sum_ms"] += float(r.get("duration_ms",0))
        except Exception:
            pass
    rows = [
        {"prompt_hash": h,
         "avg_ms": round(v["sum_ms"]/v["count"], 2) if v["count"] else 0.0,
         "count": v["count"],
         "preview": v["preview"]}
        for h,v in agg.items() if v["count"] >= min_calls
    ]
    rows.sort(key=lambda x: (x["avg_ms"], x["count"]), reverse=True)
    return {"items": rows[:top], "total_prompts": len(rows)}

class LLMRequest(BaseModel):
    prompt: str

@app.post("/api/ask_llm")
async def api_ask_llm(req: LLMRequest):
    """
    Proxy to Kåre's main LLM via llm_adapter (provider-agnostic).
    Works with both Ollama and vLLM — reads config from llm.yaml.
    """
    t0 = time.perf_counter()
    result = await ask_llm(req.prompt)
    dt_ms = int((time.perf_counter() - t0) * 1000)

    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("text", "no response"),
            "latency_ms": dt_ms,
        }

    return {
        "ok": True,
        "latency_ms": dt_ms,
        "text": result["text"],
    }


@app.post("/api/ask_cloud")
async def api_ask_cloud(req: LLMRequest):
    """
    Sender prompt til NVIDIA cloud-modell (meta/llama-3.1-405b-instruct).
    Brukes for tunge spørsmål eller eksplisitt 'bruk online'-kommando.
    API-nøkkel leses fra configs/nvidia.env.
    """
    result = await ask_llm_cloud(req.prompt)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "cloud_error"), "text": ""}
    return {"ok": True, "text": result["text"]}



@app.post("/api/intent_to_ha")
async def api_intent_to_ha(req: dict):
    return {"ok": False, "error": "intent_to_ha_removed", "message": t("api_use_generate", get_lang("global"))}

async def exec_ha_direct(entity_id: str, action: str) -> dict:
    """Direct call to HA-gateway with a known entity_id and action."""
    rid = f"rid-{int(time.time()*1000)}"
    payload = {
        "prompt": f"{action} {entity_id}",
        "intent": f"ha.light.{action}",
        "slots": {"entity_id": entity_id, "action": action},
        "request_id": rid,
        "needs_clarification": False,
        "confidence": 1.0,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post("http://127.0.0.1:8002/api/exec_intent", json=payload)
            ha_data = r.json()
        return {
            "ok": ha_data.get("status") == "ok",
            "ha_result": {"status": ha_data.get("status"), "parsed": {"action": action, "entity_id": entity_id}},
        }
    except Exception as e:
        return {"ok": False, "error": f"exec_ha_direct feilet: {e}"}


