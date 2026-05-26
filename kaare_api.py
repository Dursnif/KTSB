# kaare_api.py
# sudo cat /kaare/kaare_api.py
"""
KÅRE - HOVED-AI / ORKESTRATOR
-----------------------------
Kåre er hoved-AI og orkestrator for hele det lokale AI-system:
- Tar imot intents/kommandoer fra Home Assistant (via API)
- Logger og ruter forespørsler til andre AI-moduler (Ollama, Mini-Kåreha m.fl)
- Kan utvides med logikk, logging, kontroll og svar til alle systemer

API-endepunkter:
    GET  /           - Sjekk at Kåre kjører ("heartbeat")
    POST /api/ha_intent  - Motta intent fra Home Assistant (JSON)
"""

# --- YAML ---
import yaml
# --- Standard library ---
import asyncio
import os
import sys
import signal
import json
import subprocess
import time
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# --- Third-party ---
import yaml
import httpx
from fastapi import FastAPI, Request, HTTPException, Depends, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

# --- Kåre-interne ---
from kaare_fastpath import match_fastpath
from kaare_core.ha.clarification import ha_clarification_rescue
from kaare_core.routers.router_users import router as users_router
from kaare_core.users.store import init_db as init_users_db
from kaare_core.users.auth import (
    touch_last_seen as _touch_last_seen,
    require_auth as _require_auth,
    require_admin as _require_admin,
    get_network_context as _get_network_context,
)
from kaare_core.users import store as _user_store
from kaare_core.config import get_model, get_service, reload_capability_services
from adapters.llm_adapter import ask_llm, ask_vlm, ask_llm_cloud, ask_llm_with_tools

# --- ShortTermMemory + generate-router (RAM korttidsminne v1) ---
from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.routers.router_generate import handle_generate

# --- Miss Kåre sideview ---
from kaare_core.agents.miss_kare.evaluator import evaluate as _miss_kare_evaluate
from kaare_core.agents.miss_kare.stm import MissKareSTM

_miss_kare_stm = MissKareSTM()
_miss_kare_latest: dict[str, str] = {}    # user_id → siste kommentar (for frontend, slettes ved henting)

_last_user_prompt_time: float = 0.0      # updated on every real user prompt via /api/generate

_MK_PREFIXES = ("miss kåre", "miss kare")  # case-insensitive prefix triggers

def _detect_miss_kare_addressed(prompt: str) -> bool:
    return prompt.lower().startswith(_MK_PREFIXES)

# Aliasing for å støtte eldre moduler som bruker call_llm
call_llm = ask_llm

# --- Voice Manager (WebSocket for voice-noder) ---
# Valgfri modul – Kåre skal ALDRI krasje fordi denne mangler
try:
    from services.voice.voice_manager import register_voice_endpoints
    _voice_manager_ok = True
except Exception as _e:
    _voice_manager_ok = False
    import logging as _logging
    _logging.getLogger("kaare_api").warning("voice_manager ikke tilgjengelig: %s", _e)

# --- ROUTE DEBUG LOG (Kåre -> beslutningslogg) ----------------------
ROUTE_LOG = "/kaare/logs/route_decisions.log"
os.makedirs(os.path.dirname(ROUTE_LOG), exist_ok=True)

def _route_log(stage: str, **fields):
    """
    Skriv én JSON-linje per beslutningspunkt.
    Ikke la logging kunne knekke API-et.
    """
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


def _sprakvask_text(text: str) -> str:
    """Normaliser tekst før alias-oppslag.
    Bruker /kaare/configs/sprakvask.yaml via global SPRAKVASK.
    Støtter:
      - noise: fjerner faste fraser
      - verbs: normaliserer verbfraser (f.eks. "skru av" -> "slå av")
      - synonyms: frase-til-frase (variant -> standard)
      - words: enkeltord (variant -> standard)
    """
    if not text:
        return ""

    # Start: grunnvask
    t = str(text).strip().lower()
    if not t:
        return ""

    # Hent regler (defensivt)
    rules = SPRAKVASK if isinstance(SPRAKVASK, dict) else {}
    noise = rules.get("noise") or []
    verbs = rules.get("verbs") or {}
    synonyms = rules.get("synonyms") or []
    words = rules.get("words") or {}

    # 1) Fjern "støy" i tale (enkle substring-fjerninger)
    try:
        if isinstance(noise, list):
            for phrase in noise:
                if isinstance(phrase, str) and phrase.strip():
                    p = phrase.strip().lower()
                    if p and p in t:
                        t = t.replace(p, " ")
    except Exception:
        pass

    # Normaliser whitespace etter noise
    t = " ".join(t.split())

    # 2) Normaliser verbfraser (substring replace)
    #    (valg: gjør lengste først for å unngå deltreff)
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

    # 3) Synonymer (frase -> frase): forventer ["variant", "standard"]
    #    Vi gjør også lengste først.
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

    # 4) Enkeltord-mapping (variant -> standard)
    #    Vi tokeniserer på whitespace og bytter eksakt match.
    try:
        if isinstance(words, dict) and words:
            # normaliser keys/values
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

    # Siste opprydding
    return " ".join(t.split())



# --- /ROUTE DEBUG LOG ------------------------------------------------

print("### API HIT /api/generate ###")

# --- capability_map.yaml -------------
CAPABILITY_MAP_PATH = "/kaare/capability_map.yaml"

with open(CAPABILITY_MAP_PATH, "r", encoding="utf-8") as f:
    CAPABILITY_MAP = yaml.safe_load(f)
# --- /capability_map.yaml -------------

# --- aliases.yaml (for entity match) ---
ALIASES_PATH = "/kaare/configs/aliases.yaml"

try:
    with open(ALIASES_PATH, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
        ALIASES = loaded.get("aliases", loaded)
except Exception:
    ALIASES = {}
if isinstance(ALIASES, dict) and "aliases" in ALIASES:
    ALIASES = ALIASES.get("aliases") or {}


# --- /aliases.yaml ---

# --- sprakvask.yaml (language normalization) ---
SPRAKVASK_PATH = "/kaare/configs/sprakvask.yaml"
SPRAKVASK = {}

try:
    with open(SPRAKVASK_PATH, "r", encoding="utf-8") as f:
        _sv = yaml.safe_load(f) or {}
        SPRAKVASK = (_sv.get("normalize") if isinstance(_sv, dict) else {}) or {}
except Exception:
    SPRAKVASK = {}
# --- /sprakvask.yaml ---



HA_LOG_PATH = "/kaare/vaktmester_inbox/ha_bridge.log"
os.makedirs(os.path.dirname(HA_LOG_PATH), exist_ok=True)


app = FastAPI(title="Kåre Hoved-AI Orkestrator")
app.include_router(users_router)
init_users_db()

# Dempe uvicorn access-log for støyende poll-endepunkter
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
    from kaare_core.tools.timer_service import restore_timers
    restore_timers()
    _load_stm_snapshot()
    _load_stm_daily_summary()
    _configure_stm_autosave()
    asyncio.create_task(_start_mqtt())
    asyncio.create_task(_jang_injection_loop())
    asyncio.create_task(_stm_snapshot_loop())


def _load_stm_daily_summary() -> None:
    """Load yesterday's compressed STM summary into RAM at startup."""
    try:
        from kaare_core.memory.long_term import load_latest_daily_summary
        summary = load_latest_daily_summary()
        if summary:
            STM.set_daily_summary(summary)
            import logging
            logging.getLogger("kaare_api").info("STM daily summary loaded (%d chars)", len(summary))
    except Exception as e:
        import logging
        logging.getLogger("kaare_api").warning("Could not load STM daily summary: %s", e)


def _stm_cfg() -> dict:
    try:
        return yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text()).get("stm", {})
    except Exception:
        return {}


def _configure_stm_autosave() -> None:
    """Tell STM where to autosave after each mutation."""
    cfg = _stm_cfg()
    path = cfg.get("snapshot_path", "/kaare/state/stm_snapshot.json")
    interval = float(cfg.get("autosave_min_interval_seconds", 5.0))
    STM.configure_autosave(path, min_interval=interval)
    import logging
    logging.getLogger("kaare_api").info("STM autosave configured: %s (min interval %.0fs)", path, interval)


def _load_stm_snapshot() -> None:
    """Restore STM from the last periodic snapshot if it exists."""
    cfg = _stm_cfg()
    path = cfg.get("snapshot_path", "/kaare/state/stm_snapshot.json")
    try:
        ok = STM.load_snapshot(path)
        import logging
        if ok:
            counts = STM.snapshot_counts()
            logging.getLogger("kaare_api").info(
                "STM snapshot restored: %d dialog turns, %d actions, %d state keys",
                counts["dialog_turns"], counts["actions"], counts["state_keys"],
            )
    except Exception as e:
        import logging
        logging.getLogger("kaare_api").warning("Could not load STM snapshot: %s", e)


async def _stm_snapshot_loop() -> None:
    """Background task: save STM snapshot every N seconds; rotate daily history."""
    from datetime import timedelta
    import logging
    logger = logging.getLogger("kaare_api")

    cfg = _stm_cfg()
    snapshot_path = cfg.get("snapshot_path", "/kaare/state/stm_snapshot.json")
    history_dir = Path(cfg.get("history_dir", "/kaare/state/stm_history"))
    interval = int(cfg.get("snapshot_interval_seconds", 300))
    history_days = int(cfg.get("history_days", 7))

    last_date = datetime.now(timezone.utc).date()

    while True:
        await asyncio.sleep(interval)
        try:
            STM.save_snapshot(snapshot_path)

            today = datetime.now(timezone.utc).date()
            if today != last_date:
                # Save yesterday as a dated archive
                daily_path = str(history_dir / f"{last_date.isoformat()}.json")
                STM.save_snapshot(daily_path)
                last_date = today
                logger.info("STM daily snapshot saved: %s", daily_path)

                # Prune snapshots older than history_days
                if history_dir.exists():
                    cutoff = today - timedelta(days=history_days)
                    for f in history_dir.glob("*.json"):
                        try:
                            if datetime.strptime(f.stem, "%Y-%m-%d").date() < cutoff:
                                f.unlink()
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


# ── Jang reflection loop ─────────────────────────────────────────────────────
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

_REFLECTION_ENABLED: bool
_JANG_INTERVAL_S: int
_REFLECTION_ENABLED, _JANG_INTERVAL_S = _load_reflection_config()

_last_jang_run_time: float = 0.0
_JANG_LAST_READ_PATH = Path("/kaare/state/jang_last_read.txt")
_JANG_TS_RE = re.compile(r"^\[Jang (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")

# Lock file written by pre-meeting timers and meeting scripts themselves.
# Jang checks this instead of relying on time-based blackout windows.
_MEETING_LOCK = Path("/kaare/state/meeting_active.lock")

# Subset of tools available during internal Jang reflection — only self-maintenance
_JANG_TOOLS: list | None = None


def _get_jang_tools() -> list:
    global _JANG_TOOLS
    if _JANG_TOOLS is None:
        from kaare_core.tools.definitions import KAARE_TOOLS
        allowed = {
            "les_selvbilde", "oppdater_selvbilde", "rediger_selvbilde", "slett_fra_selvbilde",
            "skriv_notat",
        }
        _JANG_TOOLS = [t for t in KAARE_TOOLS if t.get("function", {}).get("name") in allowed]
    return _JANG_TOOLS


def _in_meeting_blackout() -> bool:
    if not _MEETING_LOCK.exists():
        return False
    # Treat lock as stale if older than 3 hours — guards against crashes that skip cleanup
    age_seconds = time.time() - _MEETING_LOCK.stat().st_mtime
    if age_seconds > 10800:
        try:
            _MEETING_LOCK.unlink(missing_ok=True)
            _logging.getLogger("kaare_api").warning(
                "meeting_active.lock was %.0f hours old — removed as stale", age_seconds / 3600
            )
        except Exception:
            pass
        return False
    return True


def _read_jang_last_read() -> datetime | None:
    try:
        return datetime.fromisoformat(_JANG_LAST_READ_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_jang_last_read() -> None:
    try:
        from kaare_core.config import get_local_tz as _get_local_tz
        _JANG_LAST_READ_PATH.write_text(
            datetime.now(tz=_get_local_tz()).isoformat(timespec="seconds"), encoding="utf-8"
        )
    except Exception as e:
        _logging.getLogger("kaare_api").warning("jang_reflection: could not write last_read: %s", e)


def _filter_new_thoughts(text: str, since: datetime | None) -> str:
    """Return only entries from inner_thoughts.txt newer than `since`."""
    if since is None:
        return text

    from kaare_core.config import get_local_tz as _get_local_tz
    local_tz = _get_local_tz()

    entries: list[str] = []
    current_lines: list[str] = []
    current_ts: datetime | None = None

    for line in text.splitlines():
        m = _JANG_TS_RE.match(line)
        if m:
            if current_lines and current_ts and current_ts > since:
                entries.append("\n".join(current_lines))
            try:
                current_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
            except ValueError:
                current_ts = None
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines and current_ts and current_ts > since:
        entries.append("\n".join(current_lines))

    return "\n\n".join(entries)


async def _run_jang_reflection() -> None:
    global _last_jang_run_time
    _log = _logging.getLogger("kaare_api")

    thoughts_path = Path("/kaare/state/inner_thoughts.txt")
    if not thoughts_path.exists() or not thoughts_path.stat().st_size:
        return

    try:
        raw_text = thoughts_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        _log.warning("jang_reflection: could not read inner_thoughts: %s", e)
        return

    if not raw_text:
        return

    since = _read_jang_last_read()
    thoughts = _filter_new_thoughts(raw_text, since)

    _last_jang_run_time = time.time()

    if not thoughts.strip():
        return  # no new entries since last read

    _write_jang_last_read()
    print("[KÅRE REFLECTION] Starting internal reflection cycle")

    micro_prompt = (
        "This is a private internal reflection session. No user is present.\n\n"
        "The following new thoughts were generated by Jing and Jang, your inner voices:\n\n"
        f"{thoughts}\n\n"
        "Review these thoughts. Consider:\n"
        "- Does anything reveal something genuine about who you are or how you function? → oppdater_selvbilde\n"
        "- Is there a thought worth keeping as a note for later? → skriv_notat\n"
        "Only use tools if there is something genuinely worth doing. "
        "Trivial or already-known things do not need action. "
        "When done, respond briefly in Norwegian with what you did, or 'Ingen endringer.' if nothing was needed."
    )

    messages = [{"role": "user", "content": micro_prompt}]
    tools = _get_jang_tools()

    from kaare_core.tools.executor import execute_tool

    for _round in range(3):
        try:
            result = await ask_llm_with_tools(
                messages=messages,
                tools=tools,
                rid=f"jang-{int(time.time()*1000)}",
                user_id="kare",
            )
        except Exception as e:
            _log.warning("jang_reflection: ask_llm_with_tools failed: %s", e)
            break

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            print(f"[KÅRE REFLECTION] Reflection done. response={((result.get('text') or '')[:160])!r}")
            break

        messages.append({
            "role": "assistant",
            "content": result.get("text") or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            print(f"[KÅRE REFLECTION] Tool call: {name}  args={str(args)[:120]}")
            try:
                tool_result = await execute_tool(name, args)
            except Exception as e:
                tool_result = f"Tool error: {e}"
            messages.append({"role": "tool", "name": name, "content": str(tool_result)})

    print("[KÅRE REFLECTION] Reflection cycle complete")


async def _jang_injection_loop() -> None:
    _log = _logging.getLogger("kaare_api")
    await asyncio.sleep(120)  # let startup settle

    while True:
        await asyncio.sleep(60)
        try:
            if not _REFLECTION_ENABLED:
                continue
            now = time.time()
            if now - _last_jang_run_time < _JANG_INTERVAL_S:
                continue
            if now - _last_user_prompt_time < _JANG_INTERVAL_S - 1:
                continue
            if _in_meeting_blackout():
                continue
            await _run_jang_reflection()
        except Exception as e:
            _log.warning("jang_injection_loop: unhandled error: %s", e)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # vi strammer inn senere
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _create_stm() -> ShortTermMemory:
    """Create STM with parameters from settings.yaml, falling back to safe defaults."""
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
        c = cfg.get("stm", {})
        return ShortTermMemory(
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
        return ShortTermMemory()


# Global short-term memory. Parameters come from configs/settings.yaml [stm:].
STM = _create_stm()

# --- Aktiver voice-endepunkter (WebSocket + admin API) ---
if _voice_manager_ok:
    register_voice_endpoints(app)

@app.get("/")
def read_root():
    return {"message": "Hei fra Kåre! Hoved-AI kjører."}


@app.post("/api/reload")
async def api_reload():
    """
    Hot-reload all file-based config caches without restarting Kåre.
    Covers: aliases, sprakvask, capability_map, llm.yaml, settings.yaml,
            personality files, and the HA gateway's own caches.
    """
    global ALIASES, SPRAKVASK, CAPABILITY_MAP
    reloaded = []
    errors = []

    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            new_aliases = loaded.get("aliases", loaded)
        if isinstance(new_aliases, dict) and "aliases" in new_aliases:
            new_aliases = new_aliases.get("aliases") or {}
        ALIASES = new_aliases if isinstance(new_aliases, dict) else {}
        reloaded.append("aliases.yaml")
    except Exception as e:
        errors.append(f"aliases.yaml: {e}")

    try:
        with open(SPRAKVASK_PATH, "r", encoding="utf-8") as f:
            _sv = yaml.safe_load(f) or {}
            SPRAKVASK = (_sv.get("normalize") if isinstance(_sv, dict) else {}) or {}
        reloaded.append("sprakvask.yaml")
    except Exception as e:
        errors.append(f"sprakvask.yaml: {e}")

    try:
        with open(CAPABILITY_MAP_PATH, "r", encoding="utf-8") as f:
            CAPABILITY_MAP = yaml.safe_load(f) or {}
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
        global _REFLECTION_ENABLED, _JANG_INTERVAL_S
        _REFLECTION_ENABLED, _JANG_INTERVAL_S = _load_reflection_config()
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
        "aliases_count": len(ALIASES),
        "sprakvask_keys": list(SPRAKVASK.keys()),
    }


@app.get("/api/health_check")
async def api_health_check(_u=Depends(_require_admin)):
    """Run scripts/health_check.py --json and return structured results."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, "/kaare/scripts/health_check.py", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "PYTHONPATH": "/kaare"},
            ),
        )
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "total_errors": 1, "error": "Health check timed out (>30s)"}
    except Exception as e:
        return {"ok": False, "total_errors": 1, "error": str(e)}


# ── Frigate camera settings ───────────────────────────────────────────────────

_FRIGATE_CAMERAS_PATH = Path("/kaare/configs/frigate_cameras.yaml")

@app.get("/api/settings/cameras")
async def api_get_cameras(_u=Depends(_require_admin)):
    """
    Returns camera config merged with live Frigate camera+label discovery.
    Cameras known to Frigate but not in config appear with analyze: false defaults.
    """
    import httpx as _hx
    from kaare_core.domain.frigate_responder import _cfg

    cfg = _cfg()
    saved_cameras: dict = cfg.get("cameras", {})
    roles: dict = cfg.get("roles", {})
    enabled: bool = cfg.get("enabled", True)

    # Discover available labels from Frigate config
    available_labels: list[str] = []
    frigate_cameras: list[str] = []
    try:
        from adapters.frigate_adapter import get_cameras as _get_cameras
        frigate_cam_list = await _get_cameras()
        frigate_cameras = [c["api_name"] for c in frigate_cam_list]
    except Exception:
        pass

    try:
        svc_cfg = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text()) or {}
        frigate_url = (svc_cfg.get("frigate") or {}).get("url", "http://localhost:5000")
        async with _hx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{frigate_url}/api/config")
            fcfg = r.json()
            objects = fcfg.get("objects", {}) or {}
            available_labels = objects.get("track", [])
    except Exception:
        available_labels = ["person", "car", "postnord", "deer", "dog", "cat"]

    # Merge: saved config + auto-discovered cameras
    merged_cameras = {}
    all_cam_ids = list(saved_cameras.keys()) + [c for c in frigate_cameras if c not in saved_cameras]
    for cam_id in all_cam_ids:
        if cam_id in saved_cameras:
            merged_cameras[cam_id] = saved_cameras[cam_id]
        else:
            # Auto-discovered, not yet configured
            merged_cameras[cam_id] = {
                "display_name": cam_id,
                "role": "road_facing",
                "labels": {lbl: {"analyze": False, "min_confidence": 0.65, "announce": False} for lbl in available_labels},
            }
        merged_cameras[cam_id]["_id"] = cam_id

    return {
        "enabled": enabled,
        "cameras": list(merged_cameras.values()),
        "roles": roles,
        "available_labels": available_labels,
        "storage": cfg.get("storage", {}),
    }


@app.get("/api/settings/cameras/_storage_usage")
async def api_get_cameras_storage_usage(_u=Depends(_require_admin)):
    """Returns actual disk usage for camera snapshots and analysis log in MB."""
    snap_dir = Path("/kaare/state/frigate_snapshots")
    log_file = Path("/kaare/logs/frigate_analysis.log")
    try:
        snap_mb = round(sum(f.stat().st_size for f in snap_dir.iterdir() if f.is_file()) / 1_048_576, 1)
    except Exception:
        snap_mb = 0.0
    try:
        log_mb = round(log_file.stat().st_size / 1_048_576, 1) if log_file.exists() else 0.0
    except Exception:
        log_mb = 0.0
    return {"snapshots_mb": snap_mb, "log_mb": log_mb}


@app.put("/api/settings/cameras/_global")
async def api_put_cameras_global(payload: dict, _u=Depends(_require_admin)):
    """Toggle global enabled flag for all automatic Frigate event analysis."""
    try:
        raw = yaml.safe_load(_FRIGATE_CAMERAS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    raw["enabled"] = bool(payload.get("enabled", True))
    _FRIGATE_CAMERAS_PATH.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    from kaare_core.domain.frigate_responder import load_camera_config
    load_camera_config()
    return {"ok": True, "enabled": raw["enabled"]}


@app.put("/api/settings/cameras/_storage")
async def api_put_cameras_storage(payload: dict, _u=Depends(_require_admin)):
    """Update storage size limits. Values are clamped: 0=disabled, snapshots 100–10000 MB, log 10–1000 MB."""
    try:
        raw = yaml.safe_load(_FRIGATE_CAMERAS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    storage = raw.setdefault("storage", {})

    if "snapshots_max_mb" in payload:
        val = int(payload["snapshots_max_mb"])
        storage["snapshots_max_mb"] = 0 if val == 0 else max(100, min(val, 10_000))

    if "log_max_mb" in payload:
        val = int(payload["log_max_mb"])
        storage["log_max_mb"] = 0 if val == 0 else max(10, min(val, 1_000))

    _FRIGATE_CAMERAS_PATH.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    from kaare_core.domain.frigate_responder import load_camera_config
    load_camera_config()
    return {"ok": True, "storage": storage}


@app.put("/api/settings/cameras/{camera_id}")
async def api_put_camera(camera_id: str, payload: dict, _u=Depends(_require_admin)):
    """Update config for one camera. Writes to frigate_cameras.yaml and hot-reloads."""
    try:
        raw = yaml.safe_load(_FRIGATE_CAMERAS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    if "cameras" not in raw:
        raw["cameras"] = {}

    # Remove internal _id field before saving
    payload.pop("_id", None)
    raw["cameras"][camera_id] = payload

    _FRIGATE_CAMERAS_PATH.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    from kaare_core.domain.frigate_responder import load_camera_config
    load_camera_config()
    return {"ok": True, "camera_id": camera_id}


# ── VPN client management ─────────────────────────────────────────────────────

class VpnCreateRequest(BaseModel):
    username: str
    device_name: str

@app.get("/api/vpn/clients")
async def api_vpn_list(payload: dict = Depends(_require_admin)):
    """Lists all WireGuard VPN clients."""
    from kaare_core.vpn import list_clients
    try:
        return list_clients()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/vpn/clients", status_code=201)
async def api_vpn_create(req: VpnCreateRequest, payload: dict = Depends(_require_admin)):
    """Creates a new WireGuard client. Returns config text for QR rendering."""
    from kaare_core.vpn import create_client
    try:
        return create_client(req.username, req.device_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/vpn/clients/{client_name}")
async def api_vpn_delete(client_name: str, payload: dict = Depends(_require_admin)):
    """Removes a WireGuard client by name."""
    from kaare_core.vpn import delete_client
    try:
        delete_client(client_name)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── VPN settings ──────────────────────────────────────────────────────────────

@app.get("/api/settings/vpn")
async def api_get_vpn_settings(_u=Depends(_require_admin)):
    """Return VPN endpoint config from settings.yaml."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    vpn = data.get("vpn", {})
    return {"duckdns_host": vpn.get("duckdns_host", ""), "wg_port": int(vpn.get("wg_port", 51820))}


@app.put("/api/settings/vpn")
async def api_put_vpn_settings(payload: dict, _u=Depends(_require_admin)):
    """Update VPN endpoint config in settings.yaml."""
    allowed = {"duckdns_host", "wg_port"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        data.setdefault("vpn", {}).update(payload)
        _SETTINGS_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}")
    return {"ok": True}


@app.get("/api/system_status")
def api_system_status():
    """
    Returnerer hvilke moduler Kåre har aktivert.
    Leser capability_map.yaml + services.yaml + ha_token.env.
    Domain must be enabled AND actually configured to show as active.
    """
    domains = CAPABILITY_MAP.get("domains", {})

    # Home Assistant: enabled in capability_map AND ha_token.env has a token
    ha_domain_on = domains.get("home_assistant", {}).get("enabled", False)
    ha_token = ""
    try:
        _ha_env = Path("/kaare/configs/ha_token.env")
        if _ha_env.exists():
            for ln in _ha_env.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    ha_token = ln.partition("=")[2].strip()
                    break
    except Exception:
        pass
    ha_enabled = ha_domain_on and bool(ha_token)

    # Frigate: enabled in capability_map AND a URL is configured in services.yaml
    frigate_domain_on = domains.get("frigate", {}).get("enabled", False)
    frigate_url = ""
    try:
        _svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        frigate_url = _svc.get("frigate", {}).get("url", "")
    except Exception:
        pass
    frigate_enabled = frigate_domain_on and bool(frigate_url)

    try:
        from adapters.mqtt_adapter import get_status as mqtt_status
        mqtt = mqtt_status()
        mqtt_enabled = mqtt.get("connected", False)
    except Exception:
        mqtt_enabled = False

    return {
        "modules": [
            {"name": "Local LLM", "enabled": True},
            {"name": "Memory Module", "enabled": True},
            {"name": "Home Assistant Bridge", "enabled": ha_enabled},
            {"name": "Frigate Adapter", "enabled": frigate_enabled},
            {"name": "MQTT", "enabled": mqtt_enabled},
        ]
    }


def _build_service_catalog() -> list[dict]:
    """System services (systemd processes). URLs read from services.yaml."""
    return [
        {"key": "kaare_api",     "name": "Kåre",          "description": "Main orchestrator API",
         "color": "#646cff",     "check_url": get_service("internal", "kaare_api") + "/"},
        {"key": "ha_gateway",    "name": "HA Gateway",    "description": "Home Assistant command executor",
         "color": "#f0a500",     "check_url": get_service("internal", "ha_gateway") + "/"},
        {"key": "semantic_embed", "name": "Semantic Embed", "description": "384-dim embedding for semantic memory",
         "color": "#60a5fa",     "check_url": get_service("internal", "semantic_embed") + "/"},
        {"key": "agents_server", "name": "Agents Server", "description": "Library + Pettersmart HTTP API",
         "color": "#5ba8a0",     "check_url": get_service("internal", "agents") + "/"},
        {"key": "voice_bridge",  "name": "Voice Bridge",  "description": "Piper TTS + Whisper STT",
         "color": "#a78bfa",     "check_url": get_service("internal", "voice_bridge") + "/"},
        {"key": "embedding",     "name": "Embedding",     "description": "BGE-M3 vector service (NPU)",
         "color": "#fbbf24",     "check_url": get_service("ollama", "embed") + "/health"},
        {"key": "qdrant",        "name": "Qdrant",        "description": "Vector database — semantic memory",
         "color": "#34d399",     "check_url": get_service("storage", "qdrant") + "/readyz"},
        {"key": "vaktmester",    "name": "Vaktmester",    "description": "System log monitor and alerter",
         "color": "#fb7185",     "check_url": "file:///kaare/state/vaktmester/report.json:600"},
        {"key": "jing_svc",      "name": "Jing (ainuc)",  "description": "Fast inner voice service",
         "color": "#a78bfa",     "check_url": "file:///kaare/state/jing_thoughts.txt:600"},
        {"key": "jang_svc",      "name": "Jang (ainuc)",  "description": "Slow inner voice service",
         "color": "#f472b6",     "check_url": "file:///kaare/state/inner_thoughts.txt:2100"},
    ]


def _build_model_catalog() -> list[dict]:
    """LLM/ML models being served. Model names and platforms derived from llm.yaml + models.yaml.
    OpenVINO models on ainuc and Whisper are checked via file age / voice-bridge."""
    try:
        _llm = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        _llm = {}
    try:
        _svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        _embed_model = _svc.get("embedding", {}).get("hf_model") or get_model("embed")
    except Exception:
        _embed_model = get_model("embed")

    def _platform(role: str) -> str:
        s = _llm.get(role, {})
        provider = s.get("provider", "ollama")
        if provider == "vllm":
            return "vLLM"
        if provider not in ("ollama",):
            return "Cloud"
        base_url = s.get("base_url", "")
        if "ollama:11434" in base_url or not base_url:
            return "Ollama (builtin)"
        host = base_url.replace("http://", "").replace("https://", "").split(":")[0]
        return f"Ollama ({host})"

    def _model(role: str) -> str:
        try:
            model_role = _llm.get(role, {}).get("model_role", role)
            return get_model(model_role)
        except Exception:
            return get_model(role)

    def _ollama_check(role: str) -> str:
        base = (_llm.get(role, {}).get("base_url") or "http://ollama:11434").rstrip("/")
        host_port = base.replace("http://", "").replace("https://", "")
        return f"ollama-model://{host_port}|{_model(role)}"

    return [
        {"key": "llm_kare",      "name": "Kåre",               "model": _model("kare"),
         "platform": _platform("kare"),      "color": "#646cff",
         "check_url": _ollama_check("kare")},
        {"key": "llm_miss_kare", "name": "Miss Kåre",           "model": _model("miss_kare"),
         "platform": _platform("miss_kare"), "color": "#c084fc",
         "check_url": _ollama_check("miss_kare")},
        {"key": "llm_library",   "name": "Library / Pettersmart", "model": _model("library"),
         "platform": _platform("library"),   "color": "#5ba8a0",
         "check_url": _ollama_check("library")},
        {"key": "llm_jing",      "name": "Jing",                "model": "qwen2.5-0.5b-openvino",
         "platform": "ainuc (OpenVINO)",     "color": "#a78bfa",
         "check_url": "file:///kaare/state/jing_thoughts.txt:600"},
        {"key": "llm_jang",      "name": "Jang",                "model": "qwen2.5-3b-openvino",
         "platform": "ainuc (OpenVINO)",     "color": "#f472b6",
         "check_url": "file:///kaare/state/inner_thoughts.txt:2100"},
        {"key": "whisper",       "name": "Whisper STT",         "model": "nb-whisper-large",
         "platform": "Voice Bridge",         "color": "#60a5fa",
         "check_url": get_service("internal", "voice_bridge") + "/"},
        {"key": "bge_m3",        "name": _embed_model.split("/")[-1], "model": _embed_model,
         "platform": "Embedding service",    "color": "#fbbf24",
         "check_url": get_service("ollama", "embed") + "/health"},
    ]

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

@app.get("/api/settings")
def api_get_settings():
    """Returnerer redigerbare innstillinger (uten hemmeligheter)."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    loc = data.get("location") or data.get("lokasjon", {})
    return {
        "location": loc,
        "log_level": data.get("log_level", "INFO"),
    }

@app.put("/api/settings/location")
async def api_put_location(payload: dict):
    """Update location in settings.yaml."""
    allowed = {"city", "postal_code", "country", "lat", "lon", "timezone"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        data.setdefault("location", {}).update(payload)
        # Remove legacy Norwegian key if present
        data.pop("lokasjon", None)
        _SETTINGS_PATH.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}")
    return {"ok": True, "location": data["location"]}


@app.get("/api/settings/language")
async def api_get_language():
    """Return GUI language and Kåre response language."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    return {
        "language": data.get("language", "nb"),
        "kare_language": data.get("kare_language") or data.get("language", "nb"),
    }


@app.put("/api/settings/language")
async def api_put_language(payload: dict):
    """Update GUI language and/or Kåre response language in settings.yaml."""
    gui_allowed = {"nb", "en", "de"}
    lang = payload.get("language")
    kare_lang = payload.get("kare_language")
    if lang is not None and lang not in gui_allowed:
        raise HTTPException(400, f"GUI language must be one of: {', '.join(sorted(gui_allowed))}")
    if kare_lang is not None and (not isinstance(kare_lang, str) or not kare_lang.strip()):
        raise HTTPException(400, "kare_language must be a non-empty string")
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        if lang is not None:
            data["language"] = lang
        if kare_lang is not None:
            data["kare_language"] = kare_lang.strip()
        _SETTINGS_PATH.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}")
    try:
        from adapters.llm_adapter import reload_config as _reload_llm
        _reload_llm()
    except Exception:
        pass
    return {"ok": True, "language": data.get("language"), "kare_language": data.get("kare_language")}


# ── Config file paths ─────────────────────────────────────────────────────────

_LLM_PATH       = Path("/kaare/configs/llm.yaml")
_MODELS_PATH    = Path("/kaare/configs/models.yaml")
_SERVICES_PATH  = Path("/kaare/configs/services.yaml")
_MQTT_ENV_PATH  = Path("/kaare/configs/mqtt.env")
_HA_TOKEN_PATH  = Path("/kaare/configs/ha_token.env")
_KARE_HA_PATH   = Path("/kaare/configs/kare_ha.env")
_BRAVE_ENV_PATH = Path("/kaare/configs/kare_llm.env")
_NVIDIA_ENV_PATH = Path("/kaare/configs/nvidia.env")
_LLM_KEYS_PATH  = Path("/kaare/configs/llm_keys.env")
_ALIASES_PATH   = Path("/kaare/configs/aliases.yaml")
_NODES_PATH     = Path("/kaare/configs/nodes.yaml")
_PLEX_ENV_PATH  = Path("/kaare/configs/plex.env")

_EDITABLE_LLM_ROLES   = {"default", "miss_kare", "pettersmart", "library", "fallback", "cloud", "image_edit"}
_AGENT_TOGGLEABLE     = {"miss_kare", "pettersmart", "library", "fallback", "cloud", "image_edit"}
_OLLAMA_OPTION_KEYS   = {"num_ctx", "num_predict", "temperature", "presence_penalty", "top_k", "top_p"}
_CLOUD_OPTION_KEYS    = {"temperature", "top_p", "max_tokens"}
_IMAGE_OPTION_KEYS    = {"num_inference_steps", "guidance_scale", "true_cfg_scale", "response_format", "enabled"}
_VLLM_OPTION_KEYS     = {"max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty"}
_VLLM_DOCKER_KEYS     = {"max_model_len", "kv_cache_dtype", "gpu_memory_utilization", "max_num_seqs", "gpu_id"}
_OLLAMA_ENV_KEYS      = {"num_threads", "num_parallel", "max_loaded_models", "flash_attention", "kv_cache_type"}

_OLLAMA_PULL_STATUS: dict[str, dict] = {}

_AGENT_ENABLED: dict[str, bool] = {}

def _reload_agent_enabled() -> None:
    try:
        data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
        for r in _AGENT_TOGGLEABLE:
            _AGENT_ENABLED[r] = bool(data.get(r, {}).get("enabled", True))
    except Exception:
        pass

_reload_agent_enabled()


def _read_env_key(path: Path, key: str) -> str:
    """Read a single key from a KEY=value env file."""
    if not path.exists():
        return ""
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("=")
            if k.strip() == key:
                return v.strip()
    return ""


def _write_env_key(path: Path, key: str, value: str) -> None:
    """Update or append KEY=value in an env file, preserving other lines."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written = False
    for i, line in enumerate(lines):
        if line.strip() and not line.strip().startswith("#") and line.partition("=")[0].strip() == key:
            lines[i] = f"{key}={value}"
            written = True
            break
    if not written:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mask_token(tok: str) -> str:
    return (tok[:8] + "..." + tok[-6:]) if len(tok) > 14 else ("***" if tok else "")


# ── LLM config ────────────────────────────────────────────────────────────────

@app.get("/api/settings/llm")
def api_get_llm(_u=Depends(_require_auth)):
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    result: dict = {}
    for role in _EDITABLE_LLM_ROLES:
        if role not in data:
            continue
        s = data[role]
        provider = s.get("provider", "ollama")
        model_role_key = s.get("model_role", role)
        entry: dict = {
            "provider": provider,
            "base_url": s.get("base_url", ""),
            "model_role": model_role_key,
            "model": get_model(model_role_key),
            "timeout": s.get("timeout"),
        }
        if role in _AGENT_TOGGLEABLE:
            entry["enabled"] = bool(s.get("enabled", True))
        if provider == "ollama":
            entry["think"] = s.get("think")
            entry["options"] = {k: v for k, v in (s.get("options") or {}).items() if k in _OLLAMA_OPTION_KEYS}
            entry["gpu_id"] = s.get("gpu_id")
            entry["ollama_env"] = {k: v for k, v in (s.get("ollama_env") or {}).items() if k in _OLLAMA_ENV_KEYS}
        elif provider == "vllm":
            entry["think"] = s.get("think")
            entry["options"] = {k: v for k, v in (s.get("options") or {}).items() if k in _VLLM_OPTION_KEYS}
            entry["vllm_docker"] = {k: v for k, v in (s.get("vllm_docker") or {}).items() if k in _VLLM_DOCKER_KEYS}
        elif role == "image_edit":
            # Image generation role: image-specific params instead of text LLM params
            for k in _IMAGE_OPTION_KEYS:
                if k in s:
                    entry[k] = s[k]
            model_role_edit_key = s.get("model_role_edit", "image_edit_edit")
            entry["model_role_edit"] = model_role_edit_key
            entry["model_edit"] = get_model(model_role_edit_key)
            api_key_env = s.get("api_key_env", "IMAGE_EDIT_API_KEY")
            entry["api_key_env"] = api_key_env
            tok = _read_env_key(_LLM_KEYS_PATH, api_key_env) or _read_env_key(_NVIDIA_ENV_PATH, api_key_env)
            entry["api_key_set"] = bool(tok)
            entry["api_key_masked"] = _mask_token(tok)
        else:
            # non-Ollama text LLM: top-level temperature/top_p/max_tokens
            for k in _CLOUD_OPTION_KEYS:
                if k in s:
                    entry[k] = s[k]
            api_key_env = s.get("api_key_env", f"{role.upper()}_API_KEY")
            entry["api_key_env"] = api_key_env
            tok = _read_env_key(_LLM_KEYS_PATH, api_key_env) or _read_env_key(_NVIDIA_ENV_PATH, api_key_env)
            entry["api_key_set"] = bool(tok)
            entry["api_key_masked"] = _mask_token(tok)
        result[role] = entry
    return result


@app.put("/api/settings/llm/{role}")
async def api_put_llm_role(role: str, payload: dict, _u=Depends(_require_admin)):
    if role not in _EDITABLE_LLM_ROLES:
        raise HTTPException(400, f"Unknown role: {role}")
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    s = data[role]
    for field in ("provider", "base_url", "model_role", "think", "timeout"):
        if field in payload:
            if payload[field] is None and field in ("think", "timeout"):
                s.pop(field, None)
            else:
                s[field] = payload[field]
    provider = s.get("provider", "ollama")
    if provider == "ollama" and "options" in payload and isinstance(payload["options"], dict):
        s.setdefault("options", {})
        for k, v in payload["options"].items():
            if k in _OLLAMA_OPTION_KEYS:
                s["options"][k] = v
    elif provider == "vllm":
        if "options" in payload and isinstance(payload["options"], dict):
            s.setdefault("options", {})
            for k, v in payload["options"].items():
                if k in _VLLM_OPTION_KEYS:
                    s["options"][k] = v
        if "vllm_docker" in payload and isinstance(payload["vllm_docker"], dict):
            s.setdefault("vllm_docker", {})
            for k, v in payload["vllm_docker"].items():
                if k in _VLLM_DOCKER_KEYS:
                    s["vllm_docker"][k] = v
        if "think" in payload:
            if payload["think"] is None:
                s.pop("think", None)
            else:
                s["think"] = payload["think"]
    elif role == "image_edit":
        for k in _IMAGE_OPTION_KEYS:
            if k in payload:
                s[k] = payload[k]
        if "model_role_edit" in payload:
            s["model_role_edit"] = payload["model_role_edit"]
        if "api_key" in payload and payload["api_key"]:
            env_var = s.get("api_key_env", "IMAGE_EDIT_API_KEY")
            _write_env_key(_LLM_KEYS_PATH, env_var, payload["api_key"])
    elif provider != "ollama":
        for k in _CLOUD_OPTION_KEYS:
            if k in payload:
                s[k] = payload[k]
        # Write API key to env file if provided
        if "api_key" in payload and payload["api_key"]:
            env_var = s.get("api_key_env", f"{role.upper()}_API_KEY")
            target = _NVIDIA_ENV_PATH if role == "cloud" else _LLM_KEYS_PATH
            _write_env_key(target, env_var, payload["api_key"])
    if provider == "ollama" and "gpu_id" in payload:
        if payload["gpu_id"] is None:
            s.pop("gpu_id", None)
        else:
            s["gpu_id"] = int(payload["gpu_id"])
    if provider == "ollama" and "ollama_env" in payload and isinstance(payload["ollama_env"], dict):
        s.setdefault("ollama_env", {})
        for k, v in payload["ollama_env"].items():
            if k in _OLLAMA_ENV_KEYS:
                if v is None:
                    s["ollama_env"].pop(k, None)
                else:
                    s["ollama_env"][k] = v
        if not s["ollama_env"]:
            s.pop("ollama_env", None)
    if role in _AGENT_TOGGLEABLE and "enabled" in payload:
        s["enabled"] = bool(payload["enabled"])

    _LLM_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    _reload_agent_enabled()

    # Write model name(s) to models.yaml if provided
    if "model" in payload and payload["model"]:
        mdata = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
        mdata[s.get("model_role", role)] = payload["model"]
        _MODELS_PATH.write_text(yaml.dump(mdata, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    if role == "image_edit" and "model_edit" in payload and payload["model_edit"]:
        mdata = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
        mdata[s.get("model_role_edit", "image_edit_edit")] = payload["model_edit"]
        _MODELS_PATH.write_text(yaml.dump(mdata, allow_unicode=True, default_flow_style=False), encoding="utf-8")

    return {"ok": True}


@app.post("/api/settings/llm/{role}/restart_docker")
async def api_restart_vllm_docker(role: str, _u=Depends(_require_admin)):
    """
    Restart the vLLM Docker container for a given role after config changes.
    Only valid for roles with provider=vllm.
    """
    import subprocess
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    provider = data[role].get("provider", "ollama")
    if provider != "vllm":
        raise HTTPException(400, f"Role {role} uses provider={provider}, not vllm")

    # Container name follows convention vllm-{role}
    container = f"vllm-{role}"
    try:
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip(), "container": container}
        return {"ok": True, "container": container}
    except Exception as e:
        return {"ok": False, "error": str(e), "container": container}


# ── Ollama auto-discover ──────────────────────────────────────────────────────

@app.post("/api/settings/llm/discover_ollama")
async def api_discover_ollama(_u=Depends(_require_admin)):
    """
    Scan for reachable Ollama instances on known addresses and the local subnet.
    Returns a list of found instances with their base_url and available models.
    """
    import ipaddress

    settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    subnet_str = settings.get("network", {}).get("local_subnet", "192.168.0.0/24")

    candidates: list[str] = [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://host.docker.internal:11434",
        "http://host-gateway:11434",
    ]
    try:
        network = ipaddress.IPv4Network(subnet_str, strict=False)
        for host in network.hosts():
            url = f"http://{host}:11434"
            if url not in candidates:
                candidates.append(url)
    except Exception:
        pass

    found = []

    async def _probe(url: str):
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                r = await client.get(f"{url}/api/tags")
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", [])]
                    found.append({"url": url, "models": models})
        except Exception:
            pass

    await asyncio.gather(*[_probe(u) for u in candidates])
    return {"found": found}


# ── Ollama model management ───────────────────────────────────────────────────

def _ollama_base_url(role: str) -> str:
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    return data[role].get("base_url", "").rstrip("/")


@app.get("/api/system/gpus")
async def api_get_gpus(_u=Depends(_require_auth)):
    """List available GPUs via nvidia-smi. Returns empty list if not available."""
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        gpus = []
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 3:
                    gpus.append({"id": int(parts[0]), "name": parts[1], "vram_gb": round(int(parts[2]) / 1024, 1)})
        return {"gpus": gpus}
    except Exception:
        return {"gpus": []}


@app.get("/api/system/hardware")
async def api_get_hardware(refresh: bool = False, _u=Depends(_require_auth)):
    """Detect host hardware (CPU, RAM, GPU, NPU) and cache result in state/hardware.json."""
    import platform

    hw_path = Path("/kaare/state/hardware.json")

    if not refresh and hw_path.exists():
        try:
            return json.loads(hw_path.read_text())
        except Exception:
            pass

    result: dict = {
        "detected_at": datetime.utcnow().isoformat(),
        "source": "container",
        "platform": platform.system().lower(),
        "cpu": {"model": "unknown", "cores": os.cpu_count() or 0},
        "ram_gb": 0,
        "gpus": [],
        "npu": {"detected": False},
    }

    # CPU
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
        model_lines = [l.split(":", 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith("model name")]
        cores = cpuinfo.count("processor\t:")
        result["cpu"] = {"model": model_lines[0] if model_lines else platform.processor() or "unknown", "cores": cores or (os.cpu_count() or 0)}
    except Exception:
        result["cpu"] = {"model": platform.processor() or "unknown", "cores": os.cpu_count() or 0}

    # RAM
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    result["ram_gb"] = round(int(line.split()[1]) / 1024 / 1024, 1)
                    break
    except Exception:
        pass

    # NVIDIA GPU
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 3:
                    result["gpus"].append({"type": "nvidia", "id": int(parts[0]), "name": parts[1], "vram_gb": round(int(parts[2]) / 1024, 1)})
    except Exception:
        pass

    # AMD GPU (rocm-smi)
    if not result["gpus"]:
        try:
            r = subprocess.run(["rocm-smi", "--showproductname", "--csv"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                for i, line in enumerate(r.stdout.strip().splitlines()):
                    if line and not line.lower().startswith("device"):
                        result["gpus"].append({"type": "amd", "id": i, "name": line.strip(), "vram_gb": None})
        except Exception:
            pass

    # Intel GPU (check DRM subsystem)
    if not result["gpus"]:
        try:
            dri_path = "/sys/class/drm"
            if os.path.exists(dri_path):
                for entry in os.listdir(dri_path):
                    uevent_path = f"{dri_path}/{entry}/device/uevent"
                    if os.path.exists(uevent_path):
                        with open(uevent_path) as f:
                            content = f.read()
                        if "intel" in content.lower() or "i915" in content.lower():
                            result["gpus"].append({"type": "intel", "id": 0, "name": "Intel GPU", "vram_gb": None})
                            break
        except Exception:
            pass

    # Intel NPU
    try:
        accel_path = "/dev/accel"
        if os.path.exists(accel_path):
            devices = os.listdir(accel_path)
            if devices:
                result["npu"] = {"detected": True, "devices": devices}
    except Exception:
        pass

    try:
        hw_path.parent.mkdir(parents=True, exist_ok=True)
        hw_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception:
        pass

    return result


@app.get("/api/ollama/models/{role}")
async def api_get_ollama_models(role: str, _u=Depends(_require_auth)):
    """List models installed in the Ollama instance used by this role."""
    base_url = _ollama_base_url(role)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.delete("/api/ollama/models/{role}/{model_name:path}")
async def api_delete_ollama_model(role: str, model_name: str, _u=Depends(_require_admin)):
    """Delete a model from the Ollama instance used by this role."""
    base_url = _ollama_base_url(role)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request("DELETE", f"{base_url}/api/delete", json={"model": model_name})
            return {"ok": resp.status_code in (200, 204)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _stream_ollama_pull(role: str, base_url: str, model: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream("POST", f"{base_url}/api/pull", json={"model": model}) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    done = data.get("status") in ("success",)
                    _OLLAMA_PULL_STATUS[role] = {
                        "pulling": not done,
                        "status": data.get("status", ""),
                        "completed": data.get("completed", 0),
                        "total": data.get("total", 0),
                        "error": None,
                    }
        _OLLAMA_PULL_STATUS[role]["pulling"] = False
    except Exception as e:
        _OLLAMA_PULL_STATUS[role] = {"pulling": False, "status": "", "completed": 0, "total": 0, "error": str(e)}


@app.post("/api/ollama/pull/{role}")
async def api_ollama_pull(role: str, payload: dict, _u=Depends(_require_admin)):
    """Start pulling an Ollama model in the background. Poll /status for progress."""
    model = payload.get("model", "").strip()
    if not model:
        raise HTTPException(400, "model is required")
    base_url = _ollama_base_url(role)
    _OLLAMA_PULL_STATUS[role] = {"pulling": True, "status": "Starter nedlasting…", "completed": 0, "total": 0, "error": None}
    asyncio.create_task(_stream_ollama_pull(role, base_url, model))
    return {"ok": True}


@app.get("/api/ollama/pull/status/{role}")
async def api_ollama_pull_status(role: str, _u=Depends(_require_auth)):
    """Return current pull progress for a role."""
    return _OLLAMA_PULL_STATUS.get(role, {"pulling": False, "status": "", "completed": 0, "total": 0, "error": None})


_OLLAMA_ROLE_KEYS = {"kare", "miss_kare", "library", "pettersmart", "fallback"}
_SERVICES_OLLAMA_KEYS = {"kare", "library", "miss_kare", "proxy"}

@app.get("/api/settings/ollama_source")
async def api_get_ollama_source(_u=Depends(_require_auth)):
    """Return current Ollama base URL and whether it appears to be the built-in container."""
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    url = ""
    for role in ("kare", "miss_kare", "library", "pettersmart", "fallback"):
        role_data = data.get(role, {})
        if role_data.get("provider", "ollama") == "ollama" and role_data.get("base_url"):
            url = role_data["base_url"]
            break
    builtin = "ollama:11434" in url
    return {"url": url or "http://ollama:11434", "builtin": builtin}


@app.put("/api/settings/ollama_source")
async def api_put_ollama_source(payload: dict, _u=Depends(_require_admin)):
    """Update Ollama base URL for all Ollama-backed roles in llm.yaml and services.yaml."""
    url = (payload.get("url") or "http://ollama:11434").rstrip("/")

    # Update llm.yaml — all roles with provider: ollama
    llm = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    for role in _OLLAMA_ROLE_KEYS:
        if role in llm and llm[role].get("provider", "ollama") == "ollama":
            llm[role]["base_url"] = url
    _LLM_PATH.write_text(yaml.dump(llm, allow_unicode=True, default_flow_style=False), encoding="utf-8")

    # Update services.yaml — ollama.* keys (excluding embed)
    svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    if "ollama" not in svc:
        svc["ollama"] = {}
    for key in _SERVICES_OLLAMA_KEYS:
        svc["ollama"][key] = url
    _SERVICES_PATH.write_text(yaml.dump(svc, allow_unicode=True, default_flow_style=False), encoding="utf-8")

    return {"ok": True, "url": url}


# ── Image serving and stats ───────────────────────────────────────────────────

@app.get("/api/image/{image_id}")
async def api_serve_image(image_id: str):
    from kaare_core.image_store import find_image
    safe_id = "".join(c for c in image_id if c.isalnum() or c in "_-")
    path = find_image(safe_id)
    if not path:
        raise HTTPException(404, "Image not found")
    suffix = path.suffix.lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix.lstrip("."), "image/png")
    return FileResponse(path, media_type=media)


@app.get("/api/frigate_snapshot/{event_id:path}")
async def api_serve_frigate_snapshot(event_id: str):
    import re as _re
    safe = _re.sub(r"[^A-Za-z0-9._\-]", "", event_id)
    path = Path("/kaare/state/frigate_snapshots") / f"{safe}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Frigate snapshot not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/admin/images/stats")
async def api_image_stats(_user=Depends(_require_admin)):
    from kaare_core.image_store import all_users_stats
    return all_users_stats()


@app.get("/api/settings/images")
async def api_get_image_settings(_user=Depends(_require_admin)):
    import yaml as _yaml
    cfg = _yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text()) or {}
    img = cfg.get("images", {})
    return {
        "max_per_user_count": int(img.get("max_per_user_count", 500)),
        "max_per_user_mb": float(img.get("max_per_user_mb", 200)),
    }


@app.put("/api/settings/images")
async def api_put_image_settings(payload: dict, _user=Depends(_require_admin)):
    import yaml as _yaml
    p = Path("/kaare/configs/settings.yaml")
    cfg = _yaml.safe_load(p.read_text()) or {}
    img = cfg.setdefault("images", {})
    if "max_per_user_count" in payload:
        img["max_per_user_count"] = int(payload["max_per_user_count"])
    if "max_per_user_mb" in payload:
        img["max_per_user_mb"] = float(payload["max_per_user_mb"])
    p.write_text(_yaml.dump(cfg, allow_unicode=True, sort_keys=False))
    return {"ok": True}


# ── Models config ─────────────────────────────────────────────────────────────

@app.get("/api/settings/models")
def api_get_models(_u=Depends(_require_auth)):
    return yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}


@app.put("/api/settings/models")
async def api_put_models(payload: dict, _u=Depends(_require_admin)):
    allowed = {"kare", "miss_kare", "library", "embed", "cloud"}
    bad = set(payload) - allowed
    if bad:
        raise HTTPException(400, f"Unknown model roles: {bad}")
    data = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
    data.update(payload)
    _MODELS_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True, "models": data}


# ── Services config (HA + MQTT) ───────────────────────────────────────────────

@app.get("/api/settings/services")
def api_get_services(_u=Depends(_require_auth)):
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    ha = data.get("home_assistant", {})
    mqtt = data.get("mqtt", {})
    frigate = data.get("frigate", {})
    plex = data.get("media", {}).get("plex", {})
    emb = data.get("embedding", {})
    voice_stt = data.get("voice", {}).get("stt", {})
    cap = yaml.safe_load(Path(CAPABILITY_MAP_PATH).read_text(encoding="utf-8")) or {}
    frigate_enabled = cap.get("domains", {}).get("frigate", {}).get("enabled", False)
    return {
        "home_assistant": {
            "url": ha.get("url", ""),
            "timeout": ha.get("timeout", 5),
        },
        "mqtt": {
            "host": mqtt.get("host", ""),
            "port": mqtt.get("port", 1883),
            "tls_enabled": bool(mqtt.get("tls_enabled", False)),
            "topic_prefix": mqtt.get("topic_prefix", "frigate"),
            "client_id": mqtt.get("client_id", ""),
            "reconnect_interval": int(mqtt.get("reconnect_interval", 30)),
        },
        "frigate": {
            "url": frigate.get("url", ""),
            "timeout": frigate.get("timeout", 10),
            "snapshot_timeout": frigate.get("snapshot_timeout", 5),
            "enabled": frigate_enabled,
        },
        "plex": {
            "url": plex.get("url", ""),
            "timeout": plex.get("timeout", 10),
        },
        "embedding": {
            "device":      emb.get("device", "NPU"),
            "hf_model":    emb.get("hf_model", "BAAI/bge-m3"),
            "model_path":  emb.get("model_path", ""),
            "emb_enabled": emb.get("enabled", True),
        },
        "memory_embed": {
            "enabled":   bool(data.get("memory_embed", {}).get("enabled", False)),
            "model_dir": data.get("memory_embed", {}).get("model_dir", ""),
        },
        "voice": {
            "stt_backend":          voice_stt.get("backend", "openvino"),
            "faster_whisper_model": voice_stt.get("faster_whisper_model", "large-v3"),
            "compute_type":         voice_stt.get("faster_whisper_compute_type", "int8"),
            "language":             voice_stt.get("language", "no"),
            "stt_enabled":          voice_stt.get("enabled", True),
            "model_dir":            voice_stt.get("model_dir", ""),
        },
    }


@app.put("/api/settings/services/ha")
async def api_put_services_ha(payload: dict, _u=Depends(_require_admin)):
    if not all(k in {"url", "timeout"} for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    data.setdefault("home_assistant", {}).update(payload)
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    # Keep legacy ha_url in settings.yaml in sync
    if "url" in payload:
        sdata = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        if "ha_url" in sdata:
            sdata["ha_url"] = payload["url"]
            _SETTINGS_PATH.write_text(yaml.dump(sdata, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


def _write_mqtt_env(username: str, password: str) -> None:
    lines = []
    if _MQTT_ENV_PATH.exists():
        for line in _MQTT_ENV_PATH.read_text(encoding="utf-8").splitlines():
            k = line.split("=")[0].strip()
            if k not in ("MQTT_USER", "MQTT_PASSWORD"):
                lines.append(line)
    lines.append(f"MQTT_USER={username}")
    lines.append(f"MQTT_PASSWORD={password}")
    _MQTT_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.put("/api/settings/services/mqtt")
async def api_put_services_mqtt(payload: dict, _u=Depends(_require_admin)):
    allowed = {"host", "port", "username", "password", "tls_enabled", "topic_prefix", "client_id", "reconnect_interval"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    mqtt = data.setdefault("mqtt", {})
    if "host" in payload:
        mqtt["host"] = payload["host"]
    if "port" in payload:
        mqtt["port"] = int(payload["port"])
    if "tls_enabled" in payload:
        mqtt["tls_enabled"] = bool(payload["tls_enabled"])
    if "topic_prefix" in payload:
        mqtt["topic_prefix"] = str(payload["topic_prefix"])
    if "client_id" in payload:
        mqtt["client_id"] = str(payload["client_id"])
    if "reconnect_interval" in payload:
        mqtt["reconnect_interval"] = int(payload["reconnect_interval"])
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    if "username" in payload or "password" in payload:
        existing = {}
        if _MQTT_ENV_PATH.exists():
            for line in _MQTT_ENV_PATH.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
        _write_mqtt_env(
            payload.get("username", existing.get("MQTT_USER", "")),
            payload.get("password", existing.get("MQTT_PASSWORD", "")),
        )
    return {"ok": True}


@app.put("/api/settings/services/frigate")
async def api_put_services_frigate(payload: dict, _u=Depends(_require_admin)):
    if not all(k in {"url", "timeout", "snapshot_timeout", "enabled"} for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    frigate = data.setdefault("frigate", {})
    if "url" in payload:
        frigate["url"] = payload["url"]
    if "timeout" in payload:
        frigate["timeout"] = int(payload["timeout"])
    if "snapshot_timeout" in payload:
        frigate["snapshot_timeout"] = int(payload["snapshot_timeout"])
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    if "enabled" in payload:
        try:
            cap = yaml.safe_load(Path(CAPABILITY_MAP_PATH).read_text(encoding="utf-8")) or {}
            cap.setdefault("domains", {}).setdefault("frigate", {})["enabled"] = bool(payload["enabled"])
            Path(CAPABILITY_MAP_PATH).write_text(
                yaml.dump(cap, allow_unicode=True, default_flow_style=False), encoding="utf-8"
            )
            global CAPABILITY_MAP
            CAPABILITY_MAP = cap
        except Exception:
            pass
    return {"ok": True}


@app.put("/api/settings/services/plex")
async def api_put_services_plex(payload: dict, _u=Depends(_require_admin)):
    if not all(k in {"url", "timeout"} for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    media = data.setdefault("media", {})
    plex = media.setdefault("plex", {})
    if "url" in payload:
        plex["url"] = payload["url"]
    if "timeout" in payload:
        plex["timeout"] = int(payload["timeout"])
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


@app.put("/api/settings/services/embedding")
async def api_put_services_embedding(payload: dict, _u=Depends(_require_admin)):
    allowed = {"device", "hf_model", "model_path", "emb_enabled"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    valid_devices = {"NPU", "CPU", "torch"}
    if "device" in payload and payload["device"] not in valid_devices:
        raise HTTPException(400, f"device must be one of: {valid_devices}")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    emb = data.setdefault("embedding", {})
    if "device" in payload:
        emb["device"] = payload["device"]
    if "hf_model" in payload:
        emb["hf_model"] = str(payload["hf_model"])
    if "model_path" in payload:
        emb["model_path"] = str(payload["model_path"])
    if "emb_enabled" in payload:
        emb["enabled"] = bool(payload["emb_enabled"])
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


@app.put("/api/settings/services/memory-embed")
async def api_put_services_memory_embed(payload: dict, _u=Depends(_require_admin)):
    allowed = {"enabled", "model_dir"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    me = data.setdefault("memory_embed", {})
    if "enabled" in payload:
        me["enabled"] = bool(payload["enabled"])
    if "model_dir" in payload:
        me["model_dir"] = str(payload["model_dir"])
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


@app.put("/api/settings/services/voice")
async def api_put_services_voice(payload: dict, _u=Depends(_require_admin)):
    allowed = {"stt_backend", "faster_whisper_model", "compute_type", "language", "stt_enabled", "model_dir"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    valid_backends = {"openvino", "faster_whisper"}
    valid_compute_types = {"int8", "float16", "float32", "int8_float16"}
    valid_languages = {"no", "nn", "en", "de", "fr", "es", "zh", "auto"}
    if "stt_backend" in payload and payload["stt_backend"] not in valid_backends:
        raise HTTPException(400, f"stt_backend must be one of: {valid_backends}")
    if "compute_type" in payload and payload["compute_type"] not in valid_compute_types:
        raise HTTPException(400, f"compute_type must be one of: {valid_compute_types}")
    if "language" in payload and payload["language"] not in valid_languages:
        raise HTTPException(400, f"language must be one of: {valid_languages}")
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    stt = data.setdefault("voice", {}).setdefault("stt", {})
    if "stt_backend" in payload:
        stt["backend"] = payload["stt_backend"]
    if "faster_whisper_model" in payload:
        stt["faster_whisper_model"] = str(payload["faster_whisper_model"])
    if "compute_type" in payload:
        stt["faster_whisper_compute_type"] = payload["compute_type"]
    if "language" in payload:
        stt["language"] = payload["language"]
    if "stt_enabled" in payload:
        stt["enabled"] = bool(payload["stt_enabled"])
    if "model_dir" in payload:
        stt["model_dir"] = str(payload["model_dir"])
    _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


# ── HA token ──────────────────────────────────────────────────────────────────

@app.get("/api/settings/ha-token")
def api_get_ha_token(_u=Depends(_require_auth)):
    tok = _read_env_key(_HA_TOKEN_PATH, "HA_TOKEN")
    return {"is_set": bool(tok), "masked": _mask_token(tok)}


@app.put("/api/settings/ha-token")
async def api_put_ha_token(payload: dict, _u=Depends(_require_admin)):
    tok = payload.get("token", "").strip()
    if not tok:
        raise HTTPException(400, "Token cannot be empty")
    _write_env_key(_HA_TOKEN_PATH, "HA_TOKEN", tok)
    return {"ok": True}


# ── HA Log Bridge (kare_ha.env) ───────────────────────────────────────────────

@app.get("/api/settings/ha-bridge")
def api_get_ha_bridge(_u=Depends(_require_auth)):
    return {
        "log_url": _read_env_key(_KARE_HA_PATH, "KARE_LOG_URL"),
        "timeout": _read_env_key(_KARE_HA_PATH, "KARE_HA_TIMEOUT"),
        "allowed_actions": _read_env_key(_KARE_HA_PATH, "KARE_ALLOWED_ACTIONS"),
    }


@app.put("/api/settings/ha-bridge")
async def api_put_ha_bridge(payload: dict, _u=Depends(_require_admin)):
    allowed = {"log_url", "timeout", "allowed_actions"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown field")
    env_map = {
        "log_url": "KARE_LOG_URL",
        "timeout": "KARE_HA_TIMEOUT",
        "allowed_actions": "KARE_ALLOWED_ACTIONS",
    }
    for k, v in payload.items():
        _write_env_key(_KARE_HA_PATH, env_map[k], str(v))
    return {"ok": True}


# ── API secrets (Brave, NVIDIA) ───────────────────────────────────────────────

@app.get("/api/settings/secrets")
def api_get_secrets(_u=Depends(_require_auth)):
    brave = _read_env_key(_BRAVE_ENV_PATH, "BRAVE_API_KEY")
    nvidia = _read_env_key(_NVIDIA_ENV_PATH, "NVIDIA_API_KEY")
    return {
        "brave":  {"is_set": bool(brave),  "masked": _mask_token(brave)},
        "nvidia": {"is_set": bool(nvidia), "masked": _mask_token(nvidia)},
    }


@app.put("/api/settings/secrets/{name}")
async def api_put_secret(name: str, payload: dict, _u=Depends(_require_admin)):
    key_val = payload.get("key", "").strip()
    if not key_val:
        raise HTTPException(400, "API key cannot be empty")
    if name == "brave":
        _write_env_key(_BRAVE_ENV_PATH, "BRAVE_API_KEY", key_val)
    elif name == "nvidia":
        _write_env_key(_NVIDIA_ENV_PATH, "NVIDIA_API_KEY", key_val)
    else:
        raise HTTPException(400, f"Unknown secret: {name}")
    return {"ok": True}


# ── Weather settings ─────────────────────────────────────────────────────────

_WEATHER_ENV_PATH = Path("/kaare/configs/weather.env")

_WEATHER_PROVIDERS = {"met.no", "open-meteo", "openweathermap", "weatherapi"}

@app.get("/api/settings/weather")
async def api_get_weather(_u=Depends(_require_admin)):
    """Return weather config from settings.yaml + API key status from weather.env."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    wcfg = data.get("weather", {"provider": "met.no", "forecast_days": 2})

    owm_key  = _read_env_key(_WEATHER_ENV_PATH, "OPENWEATHERMAP_API_KEY")
    wapi_key = _read_env_key(_WEATHER_ENV_PATH, "WEATHERAPI_KEY")

    return {
        "provider":      wcfg.get("provider", "met.no"),
        "forecast_days": int(wcfg.get("forecast_days", 2)),
        "openweathermap_key_set": bool(owm_key),
        "openweathermap_key_masked": _mask_token(owm_key) if owm_key else "",
        "weatherapi_key_set": bool(wapi_key),
        "weatherapi_key_masked": _mask_token(wapi_key) if wapi_key else "",
    }


@app.put("/api/settings/weather")
async def api_put_weather(payload: dict, _u=Depends(_require_admin)):
    """Update weather config in settings.yaml and API keys in weather.env."""
    provider = payload.get("provider", "met.no")
    if provider not in _WEATHER_PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {provider}. Valid: {sorted(_WEATHER_PROVIDERS)}")

    forecast_days = int(payload.get("forecast_days", 2))
    if not (1 <= forecast_days <= 7):
        raise HTTPException(400, "forecast_days must be 1–7")

    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        data["weather"] = {"provider": provider, "forecast_days": forecast_days}
        _SETTINGS_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}")

    if key := payload.get("openweathermap_key", "").strip():
        _write_env_key(_WEATHER_ENV_PATH, "OPENWEATHERMAP_API_KEY", key)
    if key := payload.get("weatherapi_key", "").strip():
        _write_env_key(_WEATHER_ENV_PATH, "WEATHERAPI_KEY", key)

    return {"ok": True, "provider": provider, "forecast_days": forecast_days}


# ── Websearch settings ────────────────────────────────────────────────────────

@app.get("/api/settings/websearch")
async def api_get_websearch(_u=Depends(_require_admin)):
    """Return websearch config from services.yaml (with settings.yaml fallback for migration)."""
    defaults = {
        "provider": "ddg",
        "fallback": "ddg",
        "fetch_count": 10,
        "max_results": 3,
        "content_max": 3000,
        "searxng_url": "",
        "brave_country": "NO",
        "brave_search_lang": "nb",
    }
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        ws = svc.get("web_search", {})
        if ws:
            return {**defaults, **ws}
        # Migration: read from settings.yaml if services.yaml has no web_search yet
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return {**defaults, **s.get("websearch", {})}
    except Exception:
        return defaults


@app.put("/api/settings/websearch")
async def api_put_websearch(payload: dict, _u=Depends(_require_admin)):
    """Update websearch config in services.yaml web_search section."""
    allowed = {"provider", "fallback", "fetch_count", "max_results", "content_max", "searxng_url", "brave_country", "brave_search_lang"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")
    try:
        data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        data.setdefault("web_search", {}).update(payload)
        _SERVICES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"Could not write services.yaml: {e}")
    try:
        from adapters import web_search_adapter as _wsa
        _wsa.reload_config()
    except Exception:
        pass
    return {"ok": True}


# ── Kåre reflection ───────────────────────────────────────────────────────────

_LEDER_PRESET_DIR   = Path("/kaare/configs/meeting_leder")
_VALID_LEDER_DEV_PRESETS        = {"standard", "streng", "utforskende", "egendefinert"}
_VALID_LEDER_REFLECTION_PRESETS = {"standard", "analytisk", "utfordrende", "egendefinert"}


def _get_kare_lang() -> str:
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return data.get("kare_language") or data.get("language", "nb")
    except Exception:
        return "nb"


def _leder_preset_text(meeting: str, preset: str) -> str:
    lang = _get_kare_lang()
    suffix = "" if lang == "nb" else f"_{lang}"
    path = _LEDER_PRESET_DIR / f"{meeting}_{preset}{suffix}.md"
    fallback = _LEDER_PRESET_DIR / f"{meeting}_{preset}.md"
    try:
        return (path if path.exists() else fallback).read_text(encoding="utf-8")
    except Exception:
        return ""


@app.get("/api/settings/reflection")
async def api_get_reflection(_u=Depends(_require_admin)):
    """Return reflection meeting config from settings.yaml."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    cfg = data.get("kare_reflection", {})
    preset = cfg.get("leder_preset", "standard")
    default_preset = _leder_preset_text("reflection", preset if preset != "egendefinert" else "standard")
    custom_text = _leder_preset_text("reflection", "egendefinert")
    return {
        "enabled":              bool(cfg.get("enabled", False)),
        "interval_seconds":     int(cfg.get("interval_seconds", 600)),
        "max_rounds":           int(cfg.get("max_rounds", 6)),
        "kare_max_tokens":      int(cfg.get("kare_max_tokens", 1000)),
        "miss_kare_max_tokens": int(cfg.get("miss_kare_max_tokens", 500)),
        "leder_preset":         preset,
        "leder_preset_default": default_preset,
        "leder_preset_custom":  custom_text,
    }


@app.put("/api/settings/reflection")
async def api_put_reflection(payload: dict, _u=Depends(_require_admin)):
    """Update reflection meeting config in settings.yaml."""
    global _REFLECTION_ENABLED, _JANG_INTERVAL_S
    allowed = {"enabled", "interval_seconds", "max_rounds", "kare_max_tokens",
               "miss_kare_max_tokens", "leder_preset", "leder_preset_custom"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")
    if "interval_seconds" in payload and int(payload["interval_seconds"]) < 60:
        raise HTTPException(400, "interval_seconds must be at least 60")
    if "max_rounds" in payload and int(payload["max_rounds"]) < 2:
        raise HTTPException(400, "max_rounds must be at least 2")
    if "leder_preset" in payload and payload["leder_preset"] not in _VALID_LEDER_REFLECTION_PRESETS:
        raise HTTPException(400, f"leder_preset must be one of: {_VALID_LEDER_REFLECTION_PRESETS}")
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        cfg_update = {k: v for k, v in payload.items() if k != "leder_preset_custom"}
        data.setdefault("kare_reflection", {}).update(cfg_update)
        _SETTINGS_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        if "leder_preset_custom" in payload:
            (_LEDER_PRESET_DIR / "reflection_egendefinert.md").write_text(
                str(payload["leder_preset_custom"]), encoding="utf-8"
            )
        _REFLECTION_ENABLED, _JANG_INTERVAL_S = _load_reflection_config()
    except Exception as e:
        raise HTTPException(500, f"Could not write settings: {e}")
    return {"ok": True}


@app.get("/api/settings/dev-meeting")
async def api_get_dev_meeting(_u=Depends(_require_admin)):
    """Return dev meeting config from settings.yaml."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    cfg = data.get("dev_meeting", {})
    preset = cfg.get("leder_preset", "standard")
    default_preset = _leder_preset_text("dev", preset if preset != "egendefinert" else "standard")
    custom_text = _leder_preset_text("dev", "egendefinert")
    return {
        "max_rounds":          int(cfg.get("max_rounds", 6)),
        "max_invest_rounds":   int(cfg.get("max_invest_rounds", 5)),
        "kare_max_tokens":     int(cfg.get("kare_max_tokens", 2500)),
        "kare_invest_tokens":  int(cfg.get("kare_invest_tokens", 1000)),
        "leder_preset":        preset,
        "leder_preset_default": default_preset,
        "leder_preset_custom": custom_text,
    }


@app.put("/api/settings/dev-meeting")
async def api_put_dev_meeting(payload: dict, _u=Depends(_require_admin)):
    """Update dev meeting config in settings.yaml."""
    allowed = {"max_rounds", "max_invest_rounds", "kare_max_tokens",
               "kare_invest_tokens", "leder_preset", "leder_preset_custom"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")
    if "max_rounds" in payload and int(payload["max_rounds"]) < 2:
        raise HTTPException(400, "max_rounds must be at least 2")
    if "max_invest_rounds" in payload and int(payload["max_invest_rounds"]) < 1:
        raise HTTPException(400, "max_invest_rounds must be at least 1")
    if "leder_preset" in payload and payload["leder_preset"] not in _VALID_LEDER_DEV_PRESETS:
        raise HTTPException(400, f"leder_preset must be one of: {_VALID_LEDER_DEV_PRESETS}")
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        cfg_update = {k: v for k, v in payload.items() if k != "leder_preset_custom"}
        data.setdefault("dev_meeting", {}).update(cfg_update)
        _SETTINGS_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        if "leder_preset_custom" in payload:
            (_LEDER_PRESET_DIR / "dev_egendefinert.md").write_text(
                str(payload["leder_preset_custom"]), encoding="utf-8"
            )
    except Exception as e:
        raise HTTPException(500, f"Could not write settings: {e}")
    return {"ok": True}


# ── Kåre-innstillinger ────────────────────────────────────────────────────────

_VALID_CONTRIBUTOR_MODES = {"all", "selected", "admin_only"}


_PERSONALITY_CORE_CUSTOM_PATH = Path("/kaare/configs/personality_core_custom.md")
_PERSONALITY_CORE_STANDARD_PATH = Path("/kaare/configs/personality_core.md")
_VALID_PERSONALITY_MODES = ["minimal", "letvekt", "standard", "full", "komplett", "egendefinert"]

_PERSONALITY_CORE_BY_LANG = {
    "nb": Path("/kaare/configs/personality_core.md"),
    "en": Path("/kaare/configs/personality_core_en.md"),
    "de": Path("/kaare/configs/personality_core_de.md"),
}

def _get_personality_default(lang: str) -> str:
    path = _PERSONALITY_CORE_BY_LANG.get(lang, _PERSONALITY_CORE_BY_LANG["en"])
    if path.exists():
        return path.read_text(encoding="utf-8")
    return _PERSONALITY_CORE_STANDARD_PATH.read_text(encoding="utf-8") if _PERSONALITY_CORE_STANDARD_PATH.exists() else ""


@app.get("/api/settings/kare")
async def api_get_kare_settings(_u=Depends(_require_admin)):
    """Return Kåre-specific settings from settings.yaml."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    ps = data.get("personality_self", {})
    custom_text = (
        _PERSONALITY_CORE_CUSTOM_PATH.read_text(encoding="utf-8")
        if _PERSONALITY_CORE_CUSTOM_PATH.exists()
        else ""
    )
    return {
        "assistant_name": data.get("assistant_name", "Kåre"),
        "hotword": data.get("hotword", "Kåre"),
        "personality_mode": data.get("personality_mode", "standard"),
        "personality_core_custom": custom_text,
        "personality_core_default": _get_personality_default(data.get("kare_language") or data.get("language", "nb")),
        "personality_self": {
            "contributors": ps.get("contributors", "all"),
            "allowed_users": ps.get("allowed_users", []),
        },
    }


@app.put("/api/settings/kare")
async def api_put_kare_settings(payload: dict, _u=Depends(_require_admin)):
    """Update Kåre-specific settings in settings.yaml."""
    ps = payload.get("personality_self")
    contributor_mode = None
    allowed = []
    if ps is not None:
        contributor_mode = ps.get("contributors")
        if contributor_mode and contributor_mode not in _VALID_CONTRIBUTOR_MODES:
            raise HTTPException(400, f"contributors must be one of: {_VALID_CONTRIBUTOR_MODES}")
        allowed = ps.get("allowed_users", [])
        if not isinstance(allowed, list):
            raise HTTPException(400, "allowed_users must be a list")
    personality_mode = payload.get("personality_mode")
    if personality_mode and personality_mode not in _VALID_PERSONALITY_MODES:
        raise HTTPException(400, f"personality_mode must be one of: {_VALID_PERSONALITY_MODES}")
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        if "assistant_name" in payload:
            data["assistant_name"] = str(payload["assistant_name"]).strip()
        if "hotword" in payload:
            data["hotword"] = str(payload["hotword"]).strip()
        if personality_mode:
            data["personality_mode"] = personality_mode
        if ps is not None:
            data.setdefault("personality_self", {})
            if contributor_mode:
                data["personality_self"]["contributors"] = contributor_mode
            data["personality_self"]["allowed_users"] = allowed
        _SETTINGS_PATH.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}")
    if personality_mode == "egendefinert" and "personality_core_custom" in payload:
        try:
            _PERSONALITY_CORE_CUSTOM_PATH.write_text(
                str(payload["personality_core_custom"]), encoding="utf-8"
            )
        except Exception as e:
            raise HTTPException(500, f"Could not write personality_core_custom.md: {e}")
    try:
        from adapters import llm_adapter as _llm
        _llm.reload_config()
    except Exception:
        pass
    return {"ok": True}


# ── Trusted sources ───────────────────────────────────────────────────────────

_TRUSTED_PATH = Path("/kaare/configs/trusted_sources.yaml")


@app.get("/api/settings/trusted-sources")
async def api_get_trusted_sources(_u=Depends(_require_admin)):
    """Return trusted_sources.yaml as structured data."""
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        return data.get("sources", {})
    except Exception as e:
        raise HTTPException(500, f"Could not read trusted_sources.yaml: {e}")


@app.put("/api/settings/trusted-sources")
async def api_put_trusted_sources(payload: dict, _u=Depends(_require_admin)):
    """
    Replace trusted_sources.yaml sources.
    Payload: { "category_key": [{"domain": "x.com", "beskrivelse": "..."}, ...], ... }
    """
    # Validate structure
    for cat_key, entries in payload.items():
        if not isinstance(entries, list):
            raise HTTPException(400, f"Category '{cat_key}' must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or "domain" not in entry:
                raise HTTPException(400, f"Each entry must have a 'domain' field")

    try:
        existing = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        existing["sources"] = payload
        _TRUSTED_PATH.write_text(
            yaml.dump(existing, allow_unicode=True, default_flow_style=False),
            encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(500, f"Could not write trusted_sources.yaml: {e}")

    domain_count = sum(len(v) for v in payload.values())
    return {"ok": True, "categories": len(payload), "domains": domain_count}


# ── Plex token ────────────────────────────────────────────────────────────────

@app.get("/api/settings/plex-token")
def api_get_plex_token(_u=Depends(_require_auth)):
    tok = _read_env_key(_PLEX_ENV_PATH, "PLEX_TOKEN")
    return {"is_set": bool(tok), "masked": _mask_token(tok)}


@app.put("/api/settings/plex-token")
async def api_put_plex_token(payload: dict, _u=Depends(_require_admin)):
    tok = payload.get("token", "").strip()
    if not tok:
        raise HTTPException(400, "Token cannot be empty")
    _write_env_key(_PLEX_ENV_PATH, "PLEX_TOKEN", tok)
    return {"ok": True, "restart_required": True}


# ── Aliases ───────────────────────────────────────────────────────────────────

@app.get("/api/settings/aliases")
def api_get_aliases(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_ALIASES_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    return {
        "aliases": data.get("aliases") or {},
        "rooms": data.get("rooms") or {},
        "room_entities": data.get("room_entities") or {},
    }


@app.put("/api/settings/aliases")
async def api_put_aliases(payload: dict, _u=Depends(_require_admin)):
    allowed = {"aliases", "rooms", "room_entities"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown section in payload")
    data = yaml.safe_load(_ALIASES_PATH.read_text(encoding="utf-8")) or {}
    for section in allowed:
        if section in payload:
            data[section] = payload[section]
    _ALIASES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    global ALIASES
    ALIASES = data.get("aliases", {}) or {}
    return {"ok": True}


# ── Nodes ─────────────────────────────────────────────────────────────────────

@app.get("/api/settings/nodes")
def api_get_nodes(_u=Depends(_require_admin)):
    data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
    return {"nodes": data.get("nodes", {})}


@app.put("/api/settings/nodes")
async def api_put_nodes(payload: dict, _u=Depends(_require_admin)):
    if "nodes" not in payload:
        raise HTTPException(400, "Missing 'nodes' key in payload")
    data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
    data["nodes"] = payload["nodes"]
    _NODES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


# ── Capabilities ──────────────────────────────────────────────────────────────

@app.get("/api/settings/capabilities")
def api_get_capabilities(_u=Depends(_require_admin)):
    data = yaml.safe_load(Path(CAPABILITY_MAP_PATH).read_text(encoding="utf-8")) or {}
    return {
        "domains": data.get("domains", {}),
        "distribution_profile": data.get("distribution_profile", ""),
        "services": data.get("services", {}),
    }


@app.put("/api/settings/capabilities")
async def api_put_capabilities(payload: dict, _u=Depends(_require_admin)):
    allowed = {"domains", "distribution_profile", "services"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown key in payload")
    cap_path = Path(CAPABILITY_MAP_PATH)
    data = yaml.safe_load(cap_path.read_text(encoding="utf-8")) or {}
    if "domains" in payload:
        data["domains"] = payload["domains"]
    if "distribution_profile" in payload:
        data["distribution_profile"] = payload["distribution_profile"]
    if "services" in payload:
        data["services"] = payload["services"]
    cap_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    global CAPABILITY_MAP
    CAPABILITY_MAP = data
    reload_capability_services()
    return {"ok": True}


# ── Onboarding status ─────────────────────────────────────────────────────────

@app.get("/api/onboarding/status")
def api_onboarding_status(_u=Depends(_require_admin)):
    from kaare_core.users.store import list_users as _list_users
    steps = []

    # Step 1: location configured (lat/lon != 0) — stored under lokasjon: key
    try:
        settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        lok = settings.get("lokasjon", settings)
        lat = lok.get("lat", 0) or 0
        lon = lok.get("lon", 0) or 0
        location_ok = float(lat) != 0.0 and float(lon) != 0.0
    except Exception:
        location_ok = False
    steps.append({"id": "location", "label": "Lokasjon satt", "complete": location_ok})

    # Step 2: at least one user other than the system "admin" account exists
    try:
        users = _list_users()
        has_user = any(u.get("username") != "admin" for u in users)
    except Exception:
        has_user = False
    steps.append({"id": "user", "label": "Bruker opprettet", "complete": has_user})

    # Step 3: distribution profile selected
    try:
        cap = yaml.safe_load(Path(CAPABILITY_MAP_PATH).read_text(encoding="utf-8")) or {}
        profile_ok = bool(cap.get("distribution_profile", ""))
    except Exception:
        profile_ok = False
    steps.append({"id": "distribution", "label": "Distribusjonsprofil valgt", "complete": profile_ok})

    # Optional integration hints (not blocking)
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        ha_set = bool(svc.get("home_assistant", {}).get("url", ""))
        frigate_set = bool(svc.get("frigate", {}).get("url", ""))
        plex_set = bool(svc.get("media", {}).get("plex", {}).get("url", ""))
        mqtt_set = bool(svc.get("mqtt", {}).get("host", ""))
    except Exception:
        ha_set = frigate_set = plex_set = mqtt_set = False

    optional_hints = [
        {"id": "ha", "label": "Home Assistant URL", "set": ha_set},
        {"id": "mqtt", "label": "MQTT Broker", "set": mqtt_set},
        {"id": "frigate", "label": "Frigate URL", "set": frigate_set},
        {"id": "plex", "label": "Plex Server URL", "set": plex_set},
    ]

    complete = all(s["complete"] for s in steps)
    return {"complete": complete, "steps": steps, "optional_hints": optional_hints}


# ── Connection test ───────────────────────────────────────────────────────────

@app.post("/api/settings/test-connection")
async def api_test_connection(payload: dict, _u=Depends(_require_auth)):
    """Ping a URL and return reachable status. Used by Settings page."""
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            return {"ok": r.status_code < 500, "status_code": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Service restart ───────────────────────────────────────────────────────────

_RESTARTABLE_SERVICES = {
    "kaare":           "kaare.service",
    "gateway":         "kaare_ha_gateway.service",
    "semantic_embed":  "kaare-semantic-embed.service",
    "agents":          "kaare-agents.service",
    "embedding":       "kaare-embedding.service",
    "vaktmester":      "kaare-vaktmester.service",
    "voice":           "kaare-voice-bridge.service",
    "frontend":        "kaare-frontend.service",
    "ha-log-bridge":   "kaare-ha-log-bridge.service",
}

_IN_DOCKER = os.path.exists("/.dockerenv")

@app.post("/api/admin/restart/{service_key}")
async def api_restart_service(service_key: str, background_tasks: BackgroundTasks, _u=Depends(_require_admin)):
    if service_key not in _RESTARTABLE_SERVICES:
        raise HTTPException(400, f"Unknown service: {service_key}")
    unit = _RESTARTABLE_SERVICES[service_key]

    if _IN_DOCKER:
        if service_key == "kaare":
            # Send SIGTERM to self — Docker compose (restart: unless-stopped) brings it back
            def _docker_self_restart():
                time.sleep(0.8)
                os.kill(os.getpid(), signal.SIGTERM)
            background_tasks.add_task(_docker_self_restart)
            return {"ok": True, "unit": unit, "docker": True}
        # Other services are separate containers — can't restart from inside
        return {"ok": False, "docker": True, "error": f"In Docker: restart '{service_key}' with 'docker compose restart {service_key}'"}

    # Bare-metal / systemd path
    import subprocess as _sp

    if service_key == "kaare":
        def _self_restart():
            time.sleep(0.6)
            _sp.run(["sudo", "/bin/systemctl", "restart", unit], capture_output=True)
        background_tasks.add_task(_self_restart)
        return {"ok": True, "unit": unit}

    try:
        result = _sp.run(
            ["sudo", "/bin/systemctl", "restart", unit],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "systemctl failed"}
        return {"ok": True, "unit": unit}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/admin/tool_permissions")
async def api_get_tool_permissions(_u=Depends(_require_admin)):
    """Return current tool_permissions.yaml as JSON."""
    from kaare_core.config import get_tool_permissions
    return get_tool_permissions()


@app.put("/api/admin/tool_permissions")
async def api_put_tool_permissions(data: dict, _u=Depends(_require_admin)):
    """Write new tool_permissions config and hot-reload."""
    from kaare_core.config import save_tool_permissions
    try:
        save_tool_permissions(data)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/settings/agent_tools")
async def api_get_agent_tools(_u=Depends(_require_admin)):
    """Return the agent_tools section of tool_permissions.yaml."""
    from kaare_core.config import get_tool_permissions
    return get_tool_permissions().get("agent_tools", {})


@app.put("/api/settings/agent_tools")
async def api_put_agent_tools(data: dict, _u=Depends(_require_admin)):
    """Update agent_tools section in tool_permissions.yaml and hot-reload."""
    from kaare_core.config import get_tool_permissions, save_tool_permissions
    try:
        current = get_tool_permissions()
        current["agent_tools"] = data
        save_tool_permissions(current)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Meeting roles ─────────────────────────────────────────────────────────────

_MEETING_ROLE_PS_CUSTOM = Path("/kaare/configs/meeting_role_pettersmart_custom.md")
_MEETING_ROLE_MK_CUSTOM = Path("/kaare/configs/meeting_role_miss_kare_custom.md")
_VALID_PS_ROLES = {"undersøker", "kritiker", "analytiker", "egendefinert"}
_VALID_MK_ROLES = {"empatisk", "analytiker", "utfordrende", "egendefinert"}

_PS_AGENT_DIR = Path("/kaare/kaare_core/agents/pettersmart")
_MK_AGENT_DIR = Path("/kaare/kaare_core/agents/miss_kare")


def _ps_preset_file(role: str) -> Path:
    if role == "undersøker":
        return _PS_AGENT_DIR / "personlighet_undersøker.md"
    if role in ("kritiker", "analytiker"):
        return _PS_AGENT_DIR / f"personlighet_{role}.md"
    return _PS_AGENT_DIR / "personlighet.md"


def _mk_preset_file(role: str) -> Path:
    if role in ("analytiker", "utfordrende"):
        return _MK_AGENT_DIR / f"personlighet_{role}.md"
    return _MK_AGENT_DIR / "personlighet.md"


@app.get("/api/settings/meeting-roles")
async def api_get_meeting_roles(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    mr = data.get("meeting_roles", {})
    ps_role = mr.get("pettersmart", "undersøker")
    mk_role = mr.get("miss_kare", "empatisk")

    ps_custom = _MEETING_ROLE_PS_CUSTOM.read_text(encoding="utf-8") if _MEETING_ROLE_PS_CUSTOM.exists() else ""
    mk_custom = _MEETING_ROLE_MK_CUSTOM.read_text(encoding="utf-8") if _MEETING_ROLE_MK_CUSTOM.exists() else ""

    ps_def_file = _ps_preset_file(ps_role if ps_role != "egendefinert" else "undersøker")
    mk_def_file = _mk_preset_file(mk_role if mk_role != "egendefinert" else "empatisk")
    ps_default = ps_def_file.read_text(encoding="utf-8") if ps_def_file.exists() else ""
    mk_default = mk_def_file.read_text(encoding="utf-8") if mk_def_file.exists() else ""

    return {
        "pettersmart": ps_role,
        "pettersmart_custom": ps_custom,
        "pettersmart_default": ps_default,
        "miss_kare": mk_role,
        "miss_kare_custom": mk_custom,
        "miss_kare_default": mk_default,
    }


@app.put("/api/settings/meeting-roles")
async def api_put_meeting_roles(payload: dict, _u=Depends(_require_admin)):
    ps_role = payload.get("pettersmart")
    mk_role = payload.get("miss_kare")

    if ps_role and ps_role not in _VALID_PS_ROLES:
        raise HTTPException(400, f"pettersmart role must be one of: {_VALID_PS_ROLES}")
    if mk_role and mk_role not in _VALID_MK_ROLES:
        raise HTTPException(400, f"miss_kare role must be one of: {_VALID_MK_ROLES}")

    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        data.setdefault("meeting_roles", {})
        if ps_role:
            data["meeting_roles"]["pettersmart"] = ps_role
        if mk_role:
            data["meeting_roles"]["miss_kare"] = mk_role
        _SETTINGS_PATH.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}")

    if ps_role == "egendefinert" and "pettersmart_custom" in payload:
        try:
            _MEETING_ROLE_PS_CUSTOM.write_text(str(payload["pettersmart_custom"]), encoding="utf-8")
        except Exception as e:
            raise HTTPException(500, f"Could not write pettersmart custom: {e}")

    if mk_role == "egendefinert" and "miss_kare_custom" in payload:
        try:
            _MEETING_ROLE_MK_CUSTOM.write_text(str(payload["miss_kare_custom"]), encoding="utf-8")
        except Exception as e:
            raise HTTPException(500, f"Could not write miss_kare custom: {e}")

    return {"ok": True}


@app.post("/api/admin/settings/rollback")
async def api_settings_rollback(_u=Depends(_require_admin)):
    """Restore configs/settings.yaml, llm.yaml, models.yaml, services.yaml from configs_default/."""
    defaults_dir = Path("/kaare/configs_default")
    configs_dir = Path("/kaare/configs")
    files = ["settings.yaml", "llm.yaml", "models.yaml", "services.yaml", "trusted_sources.yaml"]
    restored = []
    errors = []
    for fname in files:
        src = defaults_dir / fname
        dst = configs_dir / fname
        if not src.exists():
            errors.append(f"{fname}: no default found")
            continue
        try:
            dst.write_bytes(src.read_bytes())
            restored.append(fname)
        except Exception as e:
            errors.append(f"{fname}: {e}")
    return {"ok": len(errors) == 0, "restored": restored, "errors": errors}


@app.get("/api/admin/services")
async def api_admin_services(_u=Depends(_require_auth)):
    """Return list of restartable services with current status."""
    statuses = {}
    if _IN_DOCKER:
        # In Docker each service is a separate container — can't use systemctl.
        # Assume running (docker compose restart: unless-stopped manages this).
        for key, unit in _RESTARTABLE_SERVICES.items():
            statuses[key] = {"unit": unit, "active": True, "docker": True}
        return statuses

    import subprocess as _sp
    for key, unit in _RESTARTABLE_SERVICES.items():
        try:
            r = _sp.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3)
            statuses[key] = {"unit": unit, "active": r.stdout.strip() == "active"}
        except Exception:
            statuses[key] = {"unit": unit, "active": False}
    return statuses


async def _check_url(url: str | None) -> bool:
    """Check if a service or model endpoint is reachable.
    Supports:
      file://PATH:MAX_AGE_SECONDS — file must exist and be newer than max_age
      ollama-model://HOST:PORT|MODEL_NAME — model must be present in /api/tags
      http(s)://... — status < 500
    """
    if not url:
        return False
    if url.startswith("file://"):
        rest = url[7:]
        if ":" in rest:
            path_str, max_age_str = rest.rsplit(":", 1)
            max_age = int(max_age_str)
        else:
            path_str, max_age = rest, 600
        try:
            return (time.time() - Path(path_str).stat().st_mtime) < max_age
        except Exception:
            return False
    if url.startswith("ollama-model://"):
        rest = url[len("ollama-model://"):]
        host_port, _, model_name = rest.partition("|")
        if not model_name or not host_port:
            return False
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"http://{host_port}/api/tags")
                if r.status_code != 200:
                    return False
                available = [m.get("name", "") for m in r.json().get("models", [])]
                # If a tag is specified (e.g. qwen3:8b), require exact match.
                # If no tag (e.g. qwen3), match any tag variant (qwen3:8b, qwen3:latest).
                if ":" in model_name:
                    return model_name in available
                else:
                    return any(av == model_name or av.startswith(model_name + ":") for av in available)
        except Exception:
            return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            return r.status_code < 500
    except Exception:
        return False


@app.get("/api/system/overview")
async def api_system_overview():
    """Returns live status of all system services and LLM/ML models."""
    services = _build_service_catalog()
    models   = _build_model_catalog()

    svc_results, mdl_results = await asyncio.gather(
        asyncio.gather(*[_check_url(s["check_url"]) for s in services]),
        asyncio.gather(*[_check_url(m["check_url"]) for m in models]),
    )

    return {
        "services": [{**s, "online": ok, "check_url": None} for s, ok in zip(services, svc_results)],
        "models":   [{**m, "online": ok, "check_url": None} for m, ok in zip(models,   mdl_results)],
    }


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


@app.get("/api/show")
def show_status():
    return {"status": "ok", "source": "kaare"}


@app.get("/api/think_history")
def api_think_history(
    n: int = 20,
    search: str = "",
    role: str = "",
    recovered_only: bool = False,
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


@app.get("/api/memory/recent")
def api_memory_recent(limit: int = 20):
    """Siste N interaksjoner fra langtidsminnet."""
    from kaare_core.memory.long_term import get_ltm
    try:
        rows = get_ltm().get_recent(limit=limit)
        return {"ok": True, "count": len(rows), "interactions": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/memory/stats")
def api_memory_stats():
    """Statistikk over langtidsminnet (utfall og feedback-fordeling)."""
    from kaare_core.memory.long_term import get_ltm
    try:
        return {"ok": True, **get_ltm().get_stats()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/memory/search")
def api_memory_search(q: str, limit: int = 8):
    """Søk i langtidsminnet. Brukes av Kåre-tool og til debugging."""
    from kaare_core.memory.long_term import get_ltm
    try:
        hits = get_ltm().search_interactions(q, limit=limit)
        return {"ok": True, "query": q, "count": len(hits), "hits": hits}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class PromptRequest(BaseModel):
    prompt: str
    images: list[str] | None = None
    source: str | None = None
    user_id: str | None = None


   # Hoved-inngangen for Kåre (brukes av Hub, sener også TTS)

@app.post("/api/generate")
async def generate(request: PromptRequest, http: Request):
    """
    Hoved-inngangen for Kåre (brukes av Hub-GUI og framtidig TTS).

    Flyt:
      1. Fastpath → HA-gateway (direkte, uten LLM).
      2. Intent-pipeline → HA-gateway (alias + action-mapping).
      3. Fallback til stor LLM via api_ask_llm.
    """
    prompt = (request.prompt or "").strip()
    images = request.images
    capability_hints = {
        "domains": list(CAPABILITY_MAP.get("domains", {}).keys()),
        "rooms": list(CAPABILITY_MAP.get("rooms", {}).keys()),
        "weather_enabled": CAPABILITY_MAP.get("domains", {}).get("weather", {}).get("enabled", False),
        "time_date_enabled": CAPABILITY_MAP.get("domains", {}).get("time_date", {}).get("enabled", False),
    }
    print(f"[KÅRE] Mottatt prompt: {prompt}")
    rid = f"rid-{int(time.time()*1000)}"
    _route_log("generate_in", rid=rid, prompt_preview=prompt[:120])

    # ------------------------------------------------------------
    # ADAPTER ROUTING (ekstern input: Frigate / GenAI osv.)
    # ------------------------------------------------------------

    source = (request.source or http.headers.get("X-Kaare-Source") or "gui").lower()
    from kaare_core.memory.long_term import USER_GLOBAL
    user_id = (request.user_id or "").strip() or USER_GLOBAL

    # Track real user activity for Jang injection cooldown
    global _last_user_prompt_time
    _last_user_prompt_time = time.time()

    # Update presence so admin dashboard shows correct online status
    if user_id and user_id != USER_GLOBAL:
        _touch_last_seen(user_id)

    # -----------------------------------------------------
    # NETWORK ACCESS CONTROL
    # Determines if this request comes from local network, VPN, or external.
    # Enforces per-user vpn_access setting.
    # -----------------------------------------------------
    _client_ip = http.client.host if http.client else "127.0.0.1"
    _network_ctx = _get_network_context(_client_ip)
    _block_ha_write = False

    if _network_ctx != "local":
        _user_rec = _user_store.get_user(user_id) if user_id and user_id != USER_GLOBAL else None
        _vpn_access = (_user_rec or {}).get("vpn_access", "local_only")
        if _vpn_access == "local_only":
            return {"text": "Du er ikke hjemme og har ikke ekstern tilgang. Spør en administrator om å aktivere det."}
        _block_ha_write = (_vpn_access == "ai_only")

    # -----------------------------------------------------
    # FASTPATH: direkte handling uten Intent/LLM (hvis match)
    # -----------------------------------------------------
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

        # Fastpath: klokke (IKKE HA) – svar direkte fra host OS
        if fast.get("route") == "clock_fastpath":
            from kaare_core.config import get_local_tz as _get_local_tz
            now_str = datetime.now(tz=_get_local_tz()).strftime("%H:%M")
            _route_log("fastpath_clock_done", rid=rid, now=now_str)
            return {"text": f"Klokka er {now_str}."}


        # Block HA commands for users with ai_only VPN access
        if _block_ha_write:
            return {"text": "Smarthus-kontroll er ikke tilgjengelig eksternt for din bruker."}

        # Bygg payload til HA-gateway (samme format som nl_apply)
        payload = {
            "prompt": prompt,
            "action": fast["action"],
            "entity_id": fast["entity_id"],
            "params": {},
            "kare_route": fast["route"],
            "fastpath_source": fast["source"],
            "dry_run": False
        }

        # Send direkte til HA-gateway
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(
                    "http://127.0.0.1:8002/api/nl_apply",
                    json=payload
                )
            out = r.json()
            print(f"[KÅRE] FASTPATH RESULTAT: {out}")
            _route_log("fastpath_done", rid=rid, ha_result=out)

            # Svar til GUI/TTS
            return {"text": f"Jeg { 'skrudd på' if fast['action']=='turn_on' else 'skrudd av' } {fast['entity_id']}."}

        except Exception as e:
            print(f"[KÅRE FASTPATH FEIL] {e}")
            _route_log("fastpath_error", rid=rid, error=str(e))
            return {"text": "Fastpath feilet, prøv igjen eller bruk vanlig kommando."}


    # ------------------------------------------------------------
    # Resten av flyten håndteres av router_generate.py
    # (korttidsminne + HA->LLM fallback)
    # ------------------------------------------------------------

    mk_addressed = _detect_miss_kare_addressed(prompt)
    print(f"[MISS KÅRE] prefix detektert: {mk_addressed} | prompt start: {prompt[:30]!r}")

    result = await handle_generate(
        prompt=prompt,
        images=images,
        source=source,
        rid=rid,
        user_id=user_id,
        memory=STM,
        miss_kare_addressed=mk_addressed,
        api_intent_to_ha=api_intent_to_ha,
        api_exec_ha_direct=exec_ha_direct,
        api_ask_llm=ask_llm,
        api_ask_vlm=ask_vlm,
        api_ask_cloud=ask_llm_cloud,
        block_ha_write=_block_ha_write,
        network_context=_network_ctx,
    )

    # Miss Kåre ser over skulderen – fire-and-forget, blokkerer aldri svar
    async def _run_miss_kare(u_msg: str, k_reply: str, uid: str, addressed: bool):
        print(f"[MISS KÅRE] evaluator starter | addressed={addressed}")
        try:
            comment = await _miss_kare_evaluate(u_msg, k_reply, uid, addressed_directly=addressed)
            print(f"[MISS KÅRE] evaluator ferdig | comment={comment[:80]!r}")
            _miss_kare_stm.add(uid, u_msg, k_reply, comment)
            if comment != "[STILLE]":
                _miss_kare_latest[uid] = comment    # frontend poller dette
        except Exception as e:
            print(f"[MISS KÅRE] evaluator krasjet: {e}")

    if _AGENT_ENABLED.get("miss_kare", True):
        asyncio.create_task(_run_miss_kare(prompt, result.get("text", ""), user_id, mk_addressed))

    return result



# --- Miss Kåre sideview ---

@app.get("/api/miss_kare/comment")
async def miss_kare_comment(user_id: str = "global"):
    """
    Frontend poller dette etter at Kåre har svart.
    Returnerer Miss Kåres siste kommentar for brukeren (eller tom streng).
    Kommentaren slettes etter henting – vis én gang.
    """
    comment = _miss_kare_latest.pop(user_id, "")
    return {"comment": comment, "user_id": user_id}


# --- Chat history ---

@app.get("/api/chat_history")
async def api_chat_history(user_id: str = "global", limit: int = 60):
    """
    Returns the last N dialog turns for a user from STM (already persisted to disk).
    Used by the frontend to restore chat after logout/login.
    Only returns user/assistant turns (skips internal system turns).
    """
    limit = max(1, min(limit, 200))
    with STM._lock:
        turns = [
            t for t in STM._dialog
            if t.user_id == user_id and t.role in ("user", "assistant")
        ]
    turns = turns[-limit:]
    return {
        "user_id": user_id,
        "turns": [{"role": t.role, "text": t.text, "ts": t.ts} for t in turns],
    }


# --- Vaktmester delta endpoint ---

@app.post("/api/vaktmester_delta")
async def api_vaktmester_delta(payload: dict, request: Request):
    """
    Tar imot delta/summary fra Vaktmester-Kåre.
    Lagrer en linje i kare_log.txt som Kåre kan plukke opp videre.
    """
    import json, time, os
    log_path = "/kaare/logs/vaktmester_delta.log"
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] VAKTMESTER_DELTA {json.dumps(payload, ensure_ascii=False)}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
    return {"ok": True, "stored": len(line)}
# --- /Vaktmester delta endpoint ---
# --- Vaktmester status (siste N deltarapporter) -------------------
@app.get("/api/vaktmester_status")
def api_vaktmester_status(limit: int = 20):
    import json
    from pathlib import Path
    p = Path("/kaare/logs/vaktmester_delta.log")
    events = []
    if p.exists():
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            if "VAKTMESTER_DELTA " in line:
                try:
                    payload = json.loads(line.split("VAKTMESTER_DELTA ", 1)[1])
                    events.append(payload.get("summary", payload))
                    if len(events) >= limit:
                        break
                except Exception:
                    continue
    return {"count": len(events), "events": events}
# --- /Vaktmester status -------------------------------------------
# --- Vaktmester brief (menneskelig oppsummering) ------------------
@app.get("/api/vaktmester_brief")
def api_vaktmester_brief(limit: int = 1):
    import json
    from pathlib import Path

    p = Path("/kaare/logs/vaktmester_delta.log")
    events = []
    if p.exists():
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            if "VAKTMESTER_DELTA " in line:
                try:
                    payload = json.loads(line.split("VAKTMESTER_DELTA ", 1)[1])
                    ev = payload.get("summary", payload)
                    events.append(ev)
                    if len(events) >= limit:
                        break
                except Exception:
                    continue

    if not events:
        return {"brief": "Ingen vaktmester-rapporter enda.", "events": []}

    last = events[0]
    sev = last.get("severity", "ok")
    ts  = last.get("timestamp", "?")
    ne  = last.get("new_error_total", 0)
    nw  = last.get("new_warning_total", 0)

    def fmt_top(items):
        if not items: return "ingen"
        return ", ".join(f"{k.split('.')[-1]}:+{v}" for k,v in items[:3])

    top_err = fmt_top(last.get("top_error_components", []))
    top_warn= fmt_top(last.get("top_warning_components", []))
    tot_e   = last.get("totals",{}).get("errors",0)
    tot_w   = last.get("totals",{}).get("warnings",0)

    brief = (
        f"[{ts}] Status: {sev.upper()}. "
        f"Nye feil: {ne}, nye varsler: {nw}. "
        f"Topp feilkomponenter: {top_err}. "
        f"Topp varselkomponenter: {top_warn}. "
        f"Totalt (akkumulert): errors={tot_e}, warnings={tot_w}."
    )
    return {"brief": brief, "events": events}
# --- /Vaktmester brief --------------------------------------------
# --- Vaktmester advice (foreslå tiltak basert på siste delta) -----
@app.get("/api/vaktmester_advice")
def api_vaktmester_advice(limit: int = 1):
    rules_path = Path("/kaare/kaare_advice_rules.yaml")
    log_path   = Path("/kaare/logs/vaktmester_delta.log")

    # last regler
    rules = []
    if rules_path.exists():
        try:
            rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or []
        except Exception:
            rules = []

    # hent siste delta
    payload = None
    if log_path.exists():
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            if "VAKTMESTER_DELTA " in line:
                try:
                    payload = json.loads(line.split("VAKTMESTER_DELTA ", 1)[1])
                    break
                except Exception:
                    continue
    if not payload:
        return {"advice": [], "note": "Ingen deltarapporter enda."}

    delta_err = payload.get("delta_errors", {}) or {}
    delta_wrn = payload.get("delta_warnings", {}) or {}
    summary   = payload.get("summary", {})

    out = []
    for r in rules:
        patt = re.compile(r.get("match", ".*"))
        hits_err = sum(v for k,v in delta_err.items() if patt.search(k))
        hits_wrn = sum(v for k,v in delta_wrn.items() if patt.search(k))
        trig = False
        if r.get("threshold_errors")   and hits_err >= int(r["threshold_errors"]):   trig = True
        if r.get("threshold_warnings") and hits_wrn >= int(r["threshold_warnings"]): trig = True
        if trig:
            out.append({"id": r.get("id"), "advice": r.get("advice"),
                        "hits": {"errors": hits_err, "warnings": hits_wrn}})
    out.sort(key=lambda x: (x["hits"]["errors"], x["hits"]["warnings"]), reverse=True)
    return {"timestamp": summary.get("timestamp"),
            "severity": summary.get("severity"),
            "advice": out}
# --- /Vaktmester advice -------------------------------------------
@app.get("/api/vaktmester_types")
def api_vaktmester_types():
    import json
    from pathlib import Path
    log_path = Path("/kaare/logs/vaktmester_delta.log")

    payload = None
    if log_path.exists():
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            if "VAKTMESTER_DELTA " in line:
                try:
                    payload = json.loads(line.split("VAKTMESTER_DELTA ", 1)[1])
                    break
                except Exception:
                    continue

    if not payload:
        return {"delta_warnings": {}, "delta_errors": {}, "note": "Ingen delta funnet"}

    summary = payload.get("summary", {}) or {}
    return {
        "timestamp": summary.get("timestamp"),
        "severity": summary.get("severity"),
        "delta_warnings": payload.get("delta_warnings", {}) or {},
        "delta_errors": payload.get("delta_errors", {}) or {},
        "top_warning_components": summary.get("top_warning_components", []) or [],
        "top_error_components": summary.get("top_error_components", []) or [],
    }
@app.get("/api/reflections/{username}")
def api_reflections_list(username: str):
    from pathlib import Path
    d = Path(f"/kaare/state/memory/reflections/{username}")
    if not d.exists():
        return []
    files = sorted(d.glob("[0-9][0-9][0-9][0-9]-*.md"), reverse=True)
    return [f.stem for f in files]

class _PinBody(BaseModel):
    pin: str

@app.post("/api/reflections/{username}/{date}")
def api_reflection_get(username: str, date: str, body: _PinBody):
    from pathlib import Path
    import re as _re
    from fastapi import HTTPException
    from kaare_core.users.store import get_user_with_hash, verify_pin
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Ugyldig dato-format")
    user = get_user_with_hash(username)
    if not user or not verify_pin(body.pin, user["pin_hash"]):
        raise HTTPException(403, "Feil PIN")
    p = Path(f"/kaare/state/memory/reflections/{username}/{date}.md")
    if not p.exists():
        raise HTTPException(404, "Ikke funnet")
    return {"date": date, "content": p.read_text(encoding="utf-8")}

@app.get("/api/dev-meetings")
def api_dev_meetings_list():
    from pathlib import Path
    d = Path("/kaare/state/memory/dev_meetings")
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), reverse=True)
    return [f.stem for f in files]

@app.get("/api/dev-meetings/{date}")
def api_dev_meeting_get(date: str):
    from pathlib import Path
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        from fastapi import HTTPException
        raise HTTPException(400, "Ugyldig dato-format")
    p = Path(f"/kaare/state/memory/dev_meetings/{date}.md")
    if not p.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "Ikke funnet")
    return {"date": date, "content": p.read_text(encoding="utf-8")}


# ── Meeting topics + comments ──────────────────────────────────────────────────
import json as _json

_TOPICS_FILE = Path("/kaare/state/meeting_topics.json")
_COMMENTS_DIR = Path("/kaare/state/meeting_comments")

def _load_topics() -> dict:
    try:
        return _json.loads(_TOPICS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"reflection": "", "dev": ""}

def _save_topics(data: dict) -> None:
    _TOPICS_FILE.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

class _TopicBody(BaseModel):
    topic: str

class _CommentBody(BaseModel):
    comment: str

@app.get("/api/meetings/topic/{meeting_type}")
def api_get_topic(meeting_type: str):
    from fastapi import HTTPException
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Ugyldig møtetype")
    return {"topic": _load_topics().get(meeting_type, "")}

@app.post("/api/meetings/topic/{meeting_type}")
def api_set_topic(meeting_type: str, body: _TopicBody):
    from fastapi import HTTPException
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Ugyldig møtetype")
    data = _load_topics()
    data[meeting_type] = body.topic.strip()
    _save_topics(data)
    return {"ok": True}

@app.get("/api/meetings/comment/{meeting_type}/{date}")
def api_get_comment(meeting_type: str, date: str):
    import re as _re
    from fastapi import HTTPException
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Ugyldig møtetype")
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Ugyldig dato")
    p = _COMMENTS_DIR / meeting_type / f"{date}.txt"
    return {"comment": p.read_text(encoding="utf-8").strip() if p.exists() else ""}

@app.post("/api/meetings/comment/{meeting_type}/{date}")
def api_set_comment(meeting_type: str, date: str, body: _CommentBody):
    import re as _re
    from fastapi import HTTPException
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Ugyldig møtetype")
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Ugyldig dato")
    p = _COMMENTS_DIR / meeting_type / f"{date}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.comment.strip(), encoding="utf-8")
    return {"ok": True}


# ── Meeting run / status ───────────────────────────────────────────────────────
import subprocess as _sp

_MEETING_STATUS: dict = {
    "reflection": {"running": False, "progress": 0, "round": 0, "max_rounds": 6,
                   "step": "", "log": [], "started_at": None, "source": None},
    "dev":        {"running": False, "progress": 0, "round": 0, "max_rounds": 6,
                   "step": "", "log": [], "started_at": None, "source": None},
}
_MEETING_PROCS: dict = {}

_NIGHTJOB_STATUS: dict = {
    "running": False, "episodes": 0, "compressed": 0,
    "step": "", "log": [], "started_at": None, "finished_at": None, "error": None,
}
_NIGHTJOB_PROC = None


async def _stream_nightjob_proc(proc) -> None:
    import re as _re
    st = _NIGHTJOB_STATUS
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            st["log"].append(line)
            if len(st["log"]) > 60:
                st["log"].pop(0)
            m_batch = _re.search(r"Komprimerer batch.*\((\d+) interaksjon", line)
            m_ep    = _re.search(r"Episode (\d+) lagret", line)
            m_done  = _re.search(r"Nattjobb ferdig: (\d+) episoder laget, (\d+) interaksjon", line)
            if m_done:
                st["episodes"]   = int(m_done.group(1))
                st["compressed"] = int(m_done.group(2))
                st["step"] = f"{st['episodes']} episode(r) laget, {st['compressed']} interaksjoner komprimert"
            elif m_ep:
                st["step"] = f"Episode {m_ep.group(1)} lagret…"
            elif m_batch:
                st["step"] = f"Komprimerer {m_batch.group(1)} interaksjoner…"
            elif line.startswith("===") or "ferdig" in line.lower():
                st["step"] = line
        await proc.wait()
        st["error"] = None if proc.returncode == 0 else f"Exitkode {proc.returncode}"
    except Exception as exc:
        st["log"].append(f"[feil: {exc}]")
        st["error"] = str(exc)
    finally:
        st["running"] = False
        st["finished_at"] = datetime.now().isoformat()


def _parse_meeting_round(line: str):
    m = re.search(r"Runde (\d+)/(\d+)", line)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


async def _stream_meeting_proc(key: str, proc) -> None:
    st = _MEETING_STATUS[key]
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            st["log"].append(line)
            if len(st["log"]) > 60:
                st["log"].pop(0)
            rounds = _parse_meeting_round(line)
            if rounds:
                st["round"], st["max_rounds"] = rounds
                st["progress"] = int(rounds[0] / rounds[1] * 90)
                st["step"] = line
            elif "ferdig" in line.lower() or line.startswith("==="):
                st["step"] = line
        await proc.wait()
        if proc.returncode == 0:
            st["progress"] = 100
    except Exception as exc:
        st["log"].append(f"[feil: {exc}]")
    finally:
        st["running"] = False


def _detect_systemd_meeting(key: str, svc: str) -> None:
    st = _MEETING_STATUS[key]
    try:
        r = _sp.run(["systemctl", "is-active", f"{svc}.service"],
                    capture_output=True, text=True, timeout=2)
        is_active = r.stdout.strip() == "active"
        if is_active and not st["running"]:
            st.update({"running": True, "source": "timer",
                       "started_at": st["started_at"] or datetime.now().isoformat()})
        elif not is_active and st["running"] and st["source"] == "timer":
            st["running"] = False
    except Exception:
        pass

    if st["running"] and st["source"] == "timer":
        try:
            jr = _sp.run(
                ["journalctl", "-u", f"{svc}.service", "-n", "30",
                 "--no-pager", "--output=cat"],
                capture_output=True, text=True, timeout=3,
            )
            lines = [ln for ln in jr.stdout.splitlines() if ln.strip()]
            if lines:
                st["log"] = lines[-15:]
                for line in reversed(lines):
                    rounds = _parse_meeting_round(line)
                    if rounds:
                        st["round"] = rounds[0]
                        st["max_rounds"] = rounds[1]
                        st["progress"] = int(rounds[0] / rounds[1] * 90)
                        st["step"] = line
                        break
        except Exception:
            pass


def _load_env_file(path: str, env: dict) -> None:
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                env.setdefault(k.strip(), v.strip())
    except Exception:
        pass


@app.get("/api/meetings/status")
async def api_meetings_status():
    _detect_systemd_meeting("reflection", "kaare-reflection")
    _detect_systemd_meeting("dev", "kaare-dev-meeting")
    return {"reflection": _MEETING_STATUS["reflection"], "dev": _MEETING_STATUS["dev"]}


@app.post("/api/reflections/start")
async def api_reflections_start():
    st = _MEETING_STATUS["reflection"]
    if st["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        _MEETING_LOCK.touch()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "/kaare/kaare_reflection_runner.py",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare", env=env,
        )
        _MEETING_PROCS["reflection"] = proc
        st.update({"running": True, "progress": 0, "round": 0, "step": "Starter…",
                   "log": [], "started_at": datetime.now().isoformat(), "source": "manual"})
        asyncio.create_task(_stream_meeting_proc("reflection", proc))
        return {"status": "started"}
    except Exception as exc:
        _MEETING_LOCK.unlink(missing_ok=True)
        return {"status": "error", "detail": str(exc)}


@app.post("/api/dev-meetings/start")
async def api_dev_meetings_start():
    st = _MEETING_STATUS["dev"]
    if st["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        _MEETING_LOCK.touch()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "/kaare/kaare_dev_meeting.py",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare", env=env,
        )
        _MEETING_PROCS["dev"] = proc
        st.update({"running": True, "progress": 0, "round": 0, "step": "Starter…",
                   "log": [], "started_at": datetime.now().isoformat(), "source": "manual"})
        asyncio.create_task(_stream_meeting_proc("dev", proc))
        return {"status": "started"}
    except Exception as exc:
        _MEETING_LOCK.unlink(missing_ok=True)
        return {"status": "error", "detail": str(exc)}


@app.post("/api/memory/compress")
async def api_memory_compress():
    global _NIGHTJOB_PROC
    if _NIGHTJOB_STATUS["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "/kaare/kaare_nightjob.py",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare", env=env,
        )
        _NIGHTJOB_PROC = proc
        _NIGHTJOB_STATUS.update({
            "running": True, "episodes": 0, "compressed": 0,
            "step": "Starter…", "log": [],
            "started_at": datetime.now().isoformat(), "finished_at": None, "error": None,
        })
        asyncio.create_task(_stream_nightjob_proc(proc))
        return {"status": "started"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def _last_episode_ts() -> str | None:
    try:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect("/kaare/state/memory/interactions.db")
        row = conn.execute("SELECT MAX(ts_created) FROM episodes").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


@app.get("/api/memory/compress/status")
async def api_memory_compress_status():
    return {**_NIGHTJOB_STATUS, "last_episode_ts": _last_episode_ts()}


@app.get("/api/agent_messages")
def api_agent_messages(limit: int = 50):
    from kaare_core.memory.long_term import get_ltm
    return get_ltm().get_agent_messages(limit=limit)


# --- METRICS_MIDDLEWARE_START ---

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
# --- METRICS_MIDDLEWARE_END ---
# --- METRICS_ENDPOINTS_START ---
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
    """Siste N tool-kall + aktive timere. Brukes av admin Tools-fane."""
    from kaare_core.tools.timer_service import get_active_timers
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
# --- METRICS_ENDPOINTS_END ---

# --- MEMORY STORE / MEMORY LLM ---------------------------------
# Hvor memory-konteineren kjører (korttidsminne + modell)
MEMORY_LLM_BASE = os.getenv("MEMORY_LLM_BASE", "http://127.0.0.1:11434")
MEMORY_LLM_MODEL = os.getenv("MEMORY_LLM_MODEL", "qwen3:8b")
MEMORY_LLM_TIMEOUT = float(os.getenv("MEMORY_LLM_TIMEOUT", "30"))

# Enkel fil-logg for å ha spor, uavhengig av hva memory-konteineren gjør
MEMORY_LOG_PATH = os.getenv(
    "MEMORY_LOG_PATH",
    "/kaare/logs/memory_events.jsonl",
)

class MemoryAddRequest(BaseModel):
    """
    Ett minne-element (korttids-/langtidskandidat).
    scope: f.eks. 'ha', 'gui', 'system'
    user_id: hvem minnet tilhører (eller 'default' for globalt)
    """
    text: str
    scope: str = "default"
    user_id: str = "default"
    metadata: dict | None = None  # valgfritt, f.eks. room, device, tags

class MemoryQueryRequest(BaseModel):
    """
    Spørring mot memory-modulen.
    query: naturlig språk-spørsmål eller nøkkelord
    limit: hvor mange treff memory-konteineren maks skal returnere
    """
    query: str
    scope: str = "default"
    user_id: str = "default"
    limit: int = 20

def _append_memory_log(line: dict) -> None:
    """
    Robust file-logg. Feil her skal aldri knekke API-et.
    """
    try:
        os.makedirs(os.path.dirname(MEMORY_LOG_PATH), exist_ok=True)
        with open(MEMORY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass

@app.post("/api/memory/add")
async def api_memory_add(req: MemoryAddRequest):
    """
    Sender et minne-element til memory-konteineren (korttidsminne).
    Logger samtidig en enkel linje til disk.

    Tanken er at memory-konteineren etter hvert:
    - holder korttidsminne i RAM (3–4 GB)
    - selv bestemmer hva som skal ned i SQL/vektor-DB (langtidsminne)
    """
    t0 = time.perf_counter()

    payload = {
        "model": MEMORY_LLM_MODEL,
        "mode": "add",          # fri streng: memory-konteineren kan bruke denne
        "scope": req.scope,
        "user_id": req.user_id,
        "text": req.text,
        "metadata": req.metadata or {},
    }

    try:
        async with httpx.AsyncClient(timeout=MEMORY_LLM_TIMEOUT) as client:
            r = await client.post(
                f"{MEMORY_LLM_BASE}/api/memory/add",
                json=payload,
            )
            data = r.json() if r.status_code < 300 else {
                "ok": False,
                "error": r.text,
            }
    except Exception as e:
        data = {"ok": False, "error": f"memory_llm_add_failed: {e}"}

        dt_ms = round((time.perf_counter() - t0) * 1000)

        _append_memory_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": "add",
            "scope": req.scope,
            "user_id": req.user_id,
            "latency_ms": dt_ms,
        }
    )

    return {
        "ok": True,
        "latency_ms": dt_ms,
        "result": data,
    }

@app.post("/api/memory/query")
async def api_memory_query(req: MemoryQueryRequest):
    """
    Spør memory-konteineren om relevante minner/kontekst.
    Denne kan kalles av:
    - Kåre selv før han spør LLM
    - Vaktmester-Kåre
    - HA-bridge osv.
    """
    t0 = time.perf_counter()

    payload = {
        "model": MEMORY_LLM_MODEL,
        "mode": "query",
        "scope": req.scope,
        "user_id": req.user_id,
        "query": req.query,
        "limit": req.limit,
    }

    try:
        async with httpx.AsyncClient(timeout=MEMORY_LLM_TIMEOUT) as client:
            r = await client.post(
                f"{MEMORY_LLM_BASE}/api/memory/query",
                json=payload,
            )
            data = r.json() if r.status_code < 300 else {
                "ok": False,
                "error": r.text,
            }
    except Exception as e:
        dt_ms = round((time.perf_counter() - t0) * 1000)
        _append_memory_log(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "op": "query",
                "scope": req.scope,
                "user_id": req.user_id,
                "latency_ms": dt_ms,
                "query_preview": req.query[:80],
                "error": str(e),
            }
        )
        return {"ok": False, "error": f"memory_llm_query_failed: {e}"}

    dt_ms = round((time.perf_counter() - t0) * 1000)

    _append_memory_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": "query",
            "scope": req.scope,
            "user_id": req.user_id,
            "latency_ms": dt_ms,
            "query_preview": req.query[:80],
        }
    )

    return {
        "ok": True,
        "latency_ms": dt_ms,
        "result": data,
    }
# --- /MEMORY STORE / MEMORY LLM ---------------------------------


# --- LLM relay (Kåre hoved-LLM) -----------------------------------

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
# --- /LLM relay ---------------------------------------------------

# --- Cloud LLM (NVIDIA) -------------------------------------------

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

# --- /Cloud LLM ---------------------------------------------------


@app.post("/api/intent_to_ha")
async def api_intent_to_ha(req: dict):
    return {"ok": False, "error": "intent_to_ha_removed", "message": "Bruk Kåre direkte via /api/generate."}

async def exec_ha_direct(entity_id: str, action: str) -> dict:
    """
    Direktekall til HA-gateway med kjent entity_id og action.
    Brukes når vi allerede vet hva vi vil gjøre (reparasjon, kontekstoppløsning).
    Typisk bruk: reparasjon og kontekstoppløsning.
    """
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


