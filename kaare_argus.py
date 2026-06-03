#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kaare_argus.py  v3.0  –  Async logg-daemon
-------------------------------------------------
Tails 7 interne logfiler → normaliserer → indekserer i Qdrant (port 6333).
Embedding via BGE-M3 (1024-dim, NPU, port 11446).
Beholder stall-deteksjon og statistikkrapport fra v0.4.

Logfiler som overvåkes:
  /kaare/logs/route_decisions.log    (routing-pipeline)
  /kaare/logs/llm_calls.log          (LLM-kall)
  /kaare/logs/kaare_ha_gateway.log   (HA-gateway handlinger)
  /kaare/logs/kaare_tts_route.log    (STT/TTS)
  /kaare/logs/metrics_requests.log   (API-trafikk)
  /kaare/logs/frigate_mqtt.log       (Frigate MQTT-hendelser)
  /kaare/logs/ha_events.log          (HA tilstandsendringer)
"""

import asyncio
import hashlib
import json
import logging
import sys
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, "/kaare")
import yaml
from kaare_core.config import get_service as _svc, get_qdrant_api_key as _qdrant_key
from kaare_core.tools.i18n import t, get_lang

# ──────────────────────────────────────────────────────────────────────────────
# Konfigurasjon
# ──────────────────────────────────────────────────────────────────────────────

def _load_local_tz() -> ZoneInfo:
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
        loc = cfg.get("location") or cfg.get("lokasjon", {})
        return ZoneInfo(loc.get("timezone") or loc.get("tidssone", "Europe/Oslo"))
    except Exception:
        return ZoneInfo("Europe/Oslo")


def _load_face_events_cfg() -> tuple[int, int]:
    """Returns (session_timeout_seconds, retention_hours)."""
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
        fe = cfg.get("face_events", {})
        return int(fe.get("session_timeout_seconds", 60)), int(fe.get("retention_hours", 48))
    except Exception:
        return 60, 48


def _load_camera_names() -> dict[str, str]:
    """Returns API name → friendly name map from services.yaml."""
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text())
        return cfg.get("frigate", {}).get("camera_names", {})
    except Exception:
        return {}


def _load_embedding_enabled() -> bool:
    try:
        cfg = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text())
        return bool(cfg.get("embedding", {}).get("enabled", True))
    except Exception:
        return True


LOCAL_TZ             = _load_local_tz()
QDRANT_URL           = _svc("storage", "qdrant")
EMBED_URL            = _svc("ollama", "embed") + "/api/embed"
ARGUS_COLLECTION = "argus_events"
VECTOR_DIM           = 1024

FACE_SESSION_TIMEOUT_S, FACE_EVENTS_RETENTION_H = _load_face_events_cfg()
_CAMERA_NAMES: dict[str, str] = _load_camera_names()
EMBEDDING_ENABLED: bool = _load_embedding_enabled()
POLL_INTERVAL     = 5.0    # sekunder mellom fil-sjekk
REPORT_INTERVAL   = 60.0   # sekunder mellom rapport-skriving
BATCH_SIZE        = 50     # maks dokumenter per Qdrant-upsert
MAX_ACTIVE_RIDS   = 20_000
RID_TIMEOUT_SECS  = 30
RAW_MAX_LEN       = 2000   # tegn – avkorter store JSON-linjer i 'raw'-feltet

STATE_PATH        = Path("/kaare/state/argus/state.json")
REPORT_PATH       = Path("/kaare/state/argus/report.json")
DIGEST_PATH       = Path("/kaare/state/argus/digest.txt")
FACE_EVENTS_PATH  = Path("/kaare/state/argus/face_events.txt")
IDS_DIR           = Path("/kaare/state/argus/ids")  # én .txt per måned med doc_ids
DIGEST_MAX        = 200  # events kept in rolling digest for Jing

# Logfiler med kilde-tag og standard subsystem
LOG_SOURCES = [
    {"path": Path("/kaare/logs/route_decisions.log"),   "source": "kaare",            "subsystem": "routing"},
    {"path": Path("/kaare/logs/llm_calls.log"),         "source": "kaare-llm",        "subsystem": "llm"},
    {"path": Path("/kaare/logs/kaare_ha_gateway.log"),  "source": "kaare-ha-gateway", "subsystem": "ha"},
    {"path": Path("/kaare/logs/kaare_tts_route.log"),   "source": "kaare-tts",        "subsystem": "stt"},
    {"path": Path("/kaare/logs/metrics_requests.log"),  "source": "kaare-metrics",    "subsystem": "metrics"},
    {"path": Path("/kaare/logs/frigate_mqtt.log"),      "source": "frigate-mqtt",     "subsystem": "frigate"},
    {"path": Path("/kaare/logs/ha_events.log"),         "source": "ha-events",        "subsystem": "home"},
]

# Stages som betyr at HA-pipelinen faktisk er i gang (brukes til stall-deteksjon)
PROGRESS_STAGES = {
    "intent_to_ha_in", "intent_parsed", "alias_lookup",
    "alias_applied", "ha_gateway_return", "intent_to_ha_return", "ha_handled_done",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("argus")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ──────────────────────────────────────────────────────────────────────────────
# Hjelpefunksjoner
# ──────────────────────────────────────────────────────────────────────────────

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(ts: str) -> datetime | None:
    """Parser ISO-8601 og 'YYYY-MM-DD HH:MM:SS'-format til datetime (UTC)."""
    if not ts:
        return None
    try:
        s = str(ts).strip()
        # "2025-12-07 00:21:57" → "2025-12-07T00:21:57"
        if len(s) >= 19 and s[10] == " ":
            s = s[:10] + "T" + s[11:]
        # Trailing Z → +00:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def doc_id(source: str, raw_line: str) -> str:
    """Lager en stabil, unik ID basert på kilden og innholdet i linjen."""
    h = hashlib.sha1(raw_line.encode("utf-8", errors="replace")).hexdigest()[:14]
    return f"{source}-{h}"


def file_signature(path: Path) -> str | None:
    """Returnerer 'dev:inode' – endres hvis filen er rotert/erstattet."""
    try:
        st = path.stat()
        return f"{st.st_dev}:{st.st_ino}"
    except Exception:
        return None


def safe_load_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def safe_write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def inc(d: dict, key: str, n: int = 1):
    d[key] = d.get(key, 0) + n


def write_digest(path: Path, lines: list[str]) -> None:
    """Write rolling event digest to file for Jing to read."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        log.warning("write_digest error: %s", exc)


def record_doc_ids(doc_ids: list[str], period: str) -> None:
    """Appender doc_ids til /kaare/state/argus/ids/YYYY-MM.txt."""
    if not doc_ids:
        return
    try:
        IDS_DIR.mkdir(parents=True, exist_ok=True)
        with (IDS_DIR / f"{period}.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(doc_ids) + "\n")
    except Exception as exc:
        log.warning("record_doc_ids feil: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Støyfilter – linjer som IKKE skal indekseres
# ──────────────────────────────────────────────────────────────────────────────

def is_noise(ev: dict, source: str) -> bool:
    """Returner True hvis linjen skal droppes (ikke sendes til Qdrant)."""
    if source == "kaare-metrics":
        # Heartbeats og favicon
        if ev.get("endpoint") in ("/", "/favicon.ico"):
            return True

    if source == "kaare":
        # fastpath-miss alene er ikke interessant
        if ev.get("stage") == "fastpath_check" and ev.get("hit") is False:
            return True

    if source == "kaare-ha-gateway":
        event = ev.get("event", "")
        # Alias-oppdatering ved oppstart er støy
        if event == "aliases_updated":
            return True
        # "forward" er pre-action-logg – vi beholder bare "forward_done"
        if event == "forward":
            return True

    if source == "frigate-mqtt":
        # detection_update = same object confirmed frame-by-frame — pure noise.
        # Exception: keep updates where a face has been recognized (sub_label set).
        if ev.get("stage") == "detection_update" and not ev.get("sub_label"):
            return True

    if source == "ha-events":
        stage = ev.get("stage", "")
        if stage == "state_changed":
            entity_id = ev.get("entity_id", "")
            domain = entity_id.split(".")[0] if "." in entity_id else ""
            # Drop continuously updating numeric domains — not useful for Argus
            if domain in {"sensor", "weather", "sun", "zone", "update", "number", "select", "text"}:
                return True
            # Drop attribute-only updates where state value did not change
            if ev.get("from") == ev.get("to"):
                return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Meldings-generator – lager lesbar tekst for hvert event
# ──────────────────────────────────────────────────────────────────────────────

def make_message(ev: dict, source: str) -> str:
    """Returnerer en kort, menneskelig lesbar oppsummering av eventet."""
    stage = ev.get("stage") or ev.get("event") or ""

    if source == "kaare-llm":
        model   = ev.get("model", "?")
        latency = ev.get("latency_ms", "?")
        status  = ev.get("status", "?")
        return f"LLM: {model} {latency}ms status={status}"

    if source == "kaare-metrics":
        endpoint = ev.get("endpoint", "?")
        status   = ev.get("status", "?")
        duration = ev.get("duration_ms", "?")
        return f"API: {endpoint} {status} ({duration}ms)"

    if source == "kaare-ha-gateway":
        if stage == "forward_done":
            result = ev.get("result", "?")
            # Hent entity_id(er) fra HA-svaret hvis tilgjengelig
            details = (ev.get("mini_kaareha_resp") or {}).get("details", [])
            entities = ", ".join(
                d.get("entity_id", "") for d in details if d.get("entity_id")
            )
            if entities:
                return f"HA: {result} — {entities}"
            return f"HA gateway: {result} rid={ev.get('rid', '')}"
        return f"HA gateway: {stage}"

    if source == "ha-events":
        if stage == "state_changed":
            entity = ev.get("entity_id", "?")
            fr     = ev.get("from", "?")
            to     = ev.get("to", "?")
            user   = ev.get("user_id", "")
            suffix = f" (bruker: {user})" if user else ""
            return f"HA: {entity} {fr} → {to}{suffix}"
        if stage == "call_service":
            domain  = ev.get("domain", "?")
            service = ev.get("service", "?")
            entity  = ev.get("entity_id", "")
            if entity:
                return f"HA service: {domain}.{service} → {entity}"
            return f"HA service: {domain}.{service}"
        return f"HA: {stage}"

    if source == "kaare-tts":
        if stage == "infer_done":
            return f"STT: ferdig {ev.get('latency_ms','?')}ms ({ev.get('language','?')})"
        if stage == "device_failed":
            err = str(ev.get("error", ""))[:80]
            return f"STT FEIL: {ev.get('device','?')} — {err}"
        return f"STT: {stage}"

    if source == "frigate-mqtt":
        cam_raw = ev.get("camera", "?")
        cam = _CAMERA_NAMES.get(cam_raw, cam_raw)
        lbl = ev.get("label", "?")
        pct = int(float(ev.get("score", 0)) * 100)
        name = ev.get("sub_label")
        if name:
            sl_score = ev.get("sub_label_score")
            sl_pct = f" {int(float(sl_score) * 100)}%" if sl_score is not None else ""
            return t("argus_face_event", get_lang("global"), name=name, pct=sl_pct, cam=cam, label=lbl, score=pct)
        return t("argus_motion_event", get_lang("global"), label=lbl, cam=cam, pct=pct)

    # route_decisions.log (source == "kaare")
    if stage == "ha_handled_done":
        return f"HA: {ev.get('action','?')} {ev.get('entity_id','?')}"
    if stage == "ha_gateway_return":
        ok = "OK" if ev.get("status") == "ok" else "FEIL"
        return f"HA gateway: {ok} {ev.get('rid', '')}"
    if stage == "fastpath_match":
        return f"Fastpath: {ev.get('route','?')}"
    if stage == "intent_parsed":
        return f"Intent: {ev.get('intent','?')}"
    if stage == "routing.stalled":
        return f"STALL: rid {ev.get('rid','?')} stoppet etter {ev.get('stage','?')}"
    if stage == "generate_in":
        preview = ev.get("prompt_preview", "")[:60]
        return f"Inn: {preview}"

    return f"{source}: {stage or '?'}"


def get_level(ev: dict) -> str:
    """info / warning / error basert på event-innhold."""
    if ev.get("error"):
        return "error"
    stage = ev.get("stage") or ev.get("event") or ""
    if stage == "device_failed":
        return "error"
    http_status = ev.get("status")
    if isinstance(http_status, int) and http_status >= 400:
        return "warning"
    return "info"


# ──────────────────────────────────────────────────────────────────────────────
# Face-event aggregering
# Frigate kan sende mange deteksjoner per sekund av samme person.
# Vi aggregerer: hendelser innenfor FACE_SESSION_TIMEOUT_S sekunder = én sesjon.
# Én sesjon = én linje i face_events.txt.
# ──────────────────────────────────────────────────────────────────────────────

# {name: {name, label, first_ts, last_ts, cameras, count, max_score}}
_face_sessions: dict[str, dict] = {}


def _friendly_cam(api_name: str) -> str:
    return _CAMERA_NAMES.get(api_name, api_name)


def _flush_face_session(name: str, session: dict) -> None:
    """Write a completed face session as one line to face_events.txt."""
    first_dt = parse_iso(session["first_ts"]) or datetime.now(timezone.utc)
    last_dt  = parse_iso(session["last_ts"])  or first_dt

    first_local = first_dt.astimezone(LOCAL_TZ)
    last_local  = last_dt.astimezone(LOCAL_TZ)

    first_str = first_local.strftime("%Y-%m-%d %H:%M")
    gap_s = (last_dt - first_dt).total_seconds()
    if first_local.date() == last_local.date() and gap_s >= 30:
        time_str = f"{first_str}→{last_local.strftime('%H:%M')}"
    else:
        time_str = first_str

    cameras   = session["cameras"]
    cam_strs  = ", ".join(_friendly_cam(c) for c in cameras)
    n_cams    = len(cameras)
    pct       = int(session["max_score"] * 100)

    lang       = get_lang("global")
    label_type = t("argus_label_person" if session["label"] == "person" else "argus_label_vehicle", lang)
    cam_word   = t("argus_cam_word_s" if n_cams == 1 else "argus_cam_word_p", lang, n=n_cams)
    line       = t("argus_face_session", lang, time=time_str, label=label_type, name=name, cam_word=cam_word, cams=cam_strs, pct=pct) + "\n"

    try:
        FACE_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FACE_EVENTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
        log.debug("face_events flush: %s", line.strip())
    except Exception as exc:
        log.warning("face_events write error: %s", exc)

    _face_sessions.pop(name, None)


def process_face_event(ev: dict) -> None:
    """Update in-memory face sessions from a Frigate event with sub_label set."""
    name = ev.get("sub_label")
    if not name:
        return

    camera = ev.get("camera", "?")
    score  = float(ev.get("sub_label_score") or ev.get("score") or 0)
    label  = ev.get("label", "person")
    ts_raw = ev.get("ts", "")
    dt     = parse_iso(str(ts_raw)) if ts_raw else datetime.now(timezone.utc)

    session = _face_sessions.get(name)
    if session is not None:
        last_dt = parse_iso(session["last_ts"]) or dt
        gap = (dt - last_dt).total_seconds()
        if gap <= FACE_SESSION_TIMEOUT_S:
            session["last_ts"] = dt.isoformat()
            if camera not in session["cameras"]:
                session["cameras"].append(camera)
            session["count"] += 1
            session["max_score"] = max(session["max_score"], score)
            return
        # Gap exceeded — flush old, fall through to start new
        _flush_face_session(name, session)

    _face_sessions[name] = {
        "name":     name,
        "label":    label,
        "first_ts": dt.isoformat(),
        "last_ts":  dt.isoformat(),
        "cameras":  [camera],
        "count":    1,
        "max_score": score,
    }


def flush_stale_face_sessions() -> None:
    """Flush sessions not updated in more than FACE_SESSION_TIMEOUT_S seconds."""
    now = datetime.now(timezone.utc)
    for name in list(_face_sessions):
        session = _face_sessions.get(name)
        if session is None:
            continue
        last_dt = parse_iso(session["last_ts"]) or now
        if (now - last_dt).total_seconds() > FACE_SESSION_TIMEOUT_S:
            _flush_face_session(name, session)


def trim_face_events_file() -> None:
    """Remove entries older than FACE_EVENTS_RETENTION_H hours."""
    if not FACE_EVENTS_PATH.exists():
        return
    try:
        cutoff = datetime.now(LOCAL_TZ) - timedelta(hours=FACE_EVENTS_RETENTION_H)
        lines  = FACE_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
        kept   = []
        for line in lines:
            if not line.strip():
                continue
            try:
                # Parse "[2026-05-07 14:28→14:29] ..." or "[2026-05-07 14:28] ..."
                inner = line[1 : line.index("]")]
                ts_part = inner.split("→")[0].strip()
                dt = datetime.strptime(ts_part, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
                if dt >= cutoff:
                    kept.append(line)
            except Exception:
                kept.append(line)  # keep lines we can't parse
        content = "\n".join(kept) + ("\n" if kept else "")
        FACE_EVENTS_PATH.write_text(content, encoding="utf-8")
    except Exception as exc:
        log.warning("face_events trim error: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Qdrant-klient og collection-init
# ──────────────────────────────────────────────────────────────────────────────

_qdrant: QdrantClient | None = None


def _qdrant_client() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=QDRANT_URL, api_key=_qdrant_key(write=True))
    return _qdrant


def ensure_argus_collection() -> None:
    try:
        client = _qdrant_client()
        existing = [c.name for c in client.get_collections().collections]
        if ARGUS_COLLECTION not in existing:
            client.create_collection(
                collection_name=ARGUS_COLLECTION,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            log.info("Qdrant-kolleksjon '%s' opprettet.", ARGUS_COLLECTION)
        else:
            log.info("Qdrant-kolleksjon '%s' klar.", ARGUS_COLLECTION)
    except Exception as exc:
        log.error("Feil ved init av Qdrant-kolleksjon: %s", exc)


async def qdrant_upsert_batch(http: httpx.AsyncClient, docs: list[dict]) -> int:
    """Embed og upsert en batch dokumenter til Qdrant. Returnerer antall vellykket indeksert."""
    if not docs or not EMBEDDING_ENABLED:
        return 0
    try:
        texts = [d["fields"]["message"] for d in docs]
        emb_r = await http.post(EMBED_URL, json={"model": "bge-m3", "input": texts}, timeout=30.0)
        emb_r.raise_for_status()
        dense_vecs = emb_r.json()["embeddings"]  # list[list[float]], 1024-dim each
    except Exception as exc:
        log.warning("Embed-kall feil: %s", exc)
        return 0
    try:
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, doc["id"])),
                vector=vec,
                payload=doc["fields"],
            )
            for doc, vec in zip(docs, dense_vecs)
        ]
        _qdrant_client().upsert(collection_name=ARGUS_COLLECTION, points=points)
        return len(points)
    except Exception as exc:
        log.warning("Qdrant upsert feil: %s", exc)
        return 0


# ──────────────────────────────────────────────────────────────────────────────
# Fil-tailing – leser nye linjer siden sist
# ──────────────────────────────────────────────────────────────────────────────

def read_new_lines(path: Path, file_state: dict) -> tuple[list[str], dict]:
    """
    Leser nye linjer fra path siden forrige kjøring.
    file_state: {"file_id": str|None, "offset": int}
    Returnerer (nye linjer, oppdatert file_state).
    """
    if not path.exists():
        return [], file_state

    cur_id = file_signature(path)
    offset = int(file_state.get("offset", 0))

    # Fil er rotert/erstattet – start fra toppen
    if file_state.get("file_id") != cur_id:
        offset = 0
        file_state = {"file_id": cur_id, "offset": 0}

    # Fil er blitt kortere (truncert) – start fra toppen
    try:
        if path.stat().st_size < offset:
            offset = 0
            file_state = {"file_id": cur_id, "offset": 0}
    except Exception:
        return [], file_state

    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for raw in f:
                raw = raw.rstrip("\n\r")
                if raw.strip():
                    lines.append(raw)
            file_state = {"file_id": cur_id, "offset": f.tell()}
    except Exception as exc:
        log.warning("Les %s: %s", path.name, exc)

    return lines, file_state


# ──────────────────────────────────────────────────────────────────────────────
# Stall-deteksjon (fra v0.4, kun for route_decisions.log)
# ──────────────────────────────────────────────────────────────────────────────

def update_stall_tracking(ev: dict, active_rids: dict, counters: dict) -> None:
    rid   = ev.get("rid")
    stage = ev.get("stage")
    ts_raw = ev.get("ts")
    if not rid or not stage or not ts_raw:
        return

    dt = parse_iso(str(ts_raw))
    if dt is None:
        return

    r = active_rids.get(rid)
    if not r:
        r = {"first_ts": dt.isoformat(), "last_ts": dt.isoformat(),
             "closed": False, "progress": False}
        active_rids[rid] = r
    else:
        r["last_ts"] = dt.isoformat()

    if stage in PROGRESS_STAGES:
        r["progress"] = True

    if stage == "fastpath_check":
        if ev.get("hit") is True:
            inc(counters, "fastpath.hit")
        elif ev.get("hit") is False:
            inc(counters, "fastpath.miss")

    if stage == "ha_gateway_return":
        if ev.get("status") != "ok" or ev.get("error"):
            inc(counters, "ha.gateway_error")
        else:
            inc(counters, "ha.gateway_ok")
        r["closed"] = True

    if stage == "ha_handled_done":
        inc(counters, "rid.closed_success")
        r["closed"] = True


def expire_stalled_rids(active_rids: dict, counters: dict) -> None:
    """Rydder opp lukkede og tidsutgåtte RIDs; teller stalls."""
    now = datetime.now(timezone.utc)

    for rid in list(active_rids):
        rr = active_rids.get(rid)
        if not rr:
            continue
        if rr.get("closed"):
            active_rids.pop(rid, None)
            continue
        last_dt = parse_iso(rr.get("last_ts", "")) or now
        if (now - last_dt).total_seconds() > RID_TIMEOUT_SECS:
            if rr.get("progress"):
                inc(counters, "routing.stalled")
            active_rids.pop(rid, None)

    # Sikkerhetskap – fjern eldste hvis for mange aktive
    if len(active_rids) > MAX_ACTIVE_RIDS:
        items = sorted(
            active_rids.items(),
            key=lambda x: parse_iso(x[1].get("last_ts", "")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        keep = {rid for rid, _ in items[:MAX_ACTIVE_RIDS]}
        for rid in list(active_rids):
            if rid not in keep:
                active_rids.pop(rid, None)


def build_report(counters: dict, active_rids: dict) -> dict:
    success      = int(counters.get("rid.closed_success", 0))
    gw_err       = int(counters.get("ha.gateway_error", 0))
    stalled      = int(counters.get("routing.stalled", 0))
    fp_hit       = int(counters.get("fastpath.hit", 0))
    fp_miss      = int(counters.get("fastpath.miss", 0))
    fp_total     = fp_hit + fp_miss
    closed_total = success + gw_err + stalled

    return {
        "timestamp":  utc_now_iso(),
        "counters":   counters,
        "active_rids": len(active_rids),
        "rates": {
            "rid.success_rate":  round(success / closed_total, 3) if closed_total else None,
            "rid.stall_rate":    round(stalled / closed_total, 3) if closed_total else None,
            "rid.error_rate":    round(gw_err  / closed_total, 3) if closed_total else None,
            "fastpath.hit_rate": round(fp_hit  / fp_total,     3) if fp_total     else None,
        },
        "qdrant": {
            "indexed":       int(counters.get("qdrant.indexed", 0)),
            "dropped_noise": int(counters.get("qdrant.dropped_noise", 0)),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Hoved-daemon-løkke
# ──────────────────────────────────────────────────────────────────────────────

async def daemon():
    state = safe_load_json(STATE_PATH)

    # Migrasjon fra v0.4-format (hadde "route_log" nøkkel)
    if "route_log" in state and "files" not in state:
        old = state.pop("route_log", {})
        route_key = str(LOG_SOURCES[0]["path"])
        state["files"] = {route_key: old}

    if "files" not in state:
        state["files"] = {}
    for src in LOG_SOURCES:
        key = str(src["path"])
        if key not in state["files"]:
            state["files"][key] = {"file_id": None, "offset": 0}

    if "active_rids" not in state or not isinstance(state["active_rids"], dict):
        state["active_rids"] = {}
    if "counters" not in state or not isinstance(state["counters"], dict):
        state["counters"] = {}

    counters    = state["counters"]
    active_rids = state["active_rids"]

    digest_buffer: deque = deque(maxlen=DIGEST_MAX)
    last_report = 0.0

    async with httpx.AsyncClient() as client:
        log.info("Argus v3.0 startet — overvåker %d logfiler", len(LOG_SOURCES))
        if not EMBEDDING_ENABLED:
            log.info("Embedding disabled — Qdrant indexing skipped.")

        while True:
            batch: list[dict] = []
            # period → [doc_id, ...] — for ID-tracking per måned
            period_ids: dict[str, list[str]] = {}

            for src in LOG_SOURCES:
                path      = src["path"]
                source    = src["source"]
                subsystem = src["subsystem"]
                key       = str(path)

                lines, new_fstate = read_new_lines(path, state["files"][key])
                state["files"][key] = new_fstate

                for raw_line in lines:
                    try:
                        ev = json.loads(raw_line)
                    except Exception:
                        continue

                    # Støyfilter
                    if is_noise(ev, source):
                        inc(counters, "qdrant.dropped_noise")
                        continue

                    # Face aggregation — runs before Qdrant indexing
                    if source == "frigate-mqtt" and ev.get("sub_label"):
                        process_face_event(ev)

                    # Stall-tracking (kun route_decisions.log)
                    if source == "kaare":
                        update_stall_tracking(ev, active_rids, counters)

                    # Normalisering til felles event-schema
                    ts_raw = ev.get("ts", "")
                    dt     = parse_iso(str(ts_raw)) if ts_raw else None
                    ts_iso = dt.isoformat() if dt else utc_now_iso()
                    period = ts_iso[:7]  # "YYYY-MM"

                    did = doc_id(source, raw_line)
                    doc = {
                        "id": did,
                        "fields": {
                            "message":    make_message(ev, source),
                            "raw":        raw_line[:RAW_MAX_LEN],
                            "source":     source,
                            "subsystem":  ev.get("subsystem") or subsystem,
                            "event_type": ev.get("stage") or ev.get("event") or "",
                            "level":      get_level(ev),
                            "ts":         ts_iso,
                            "rid":        ev.get("rid") or "",
                        },
                    }
                    batch.append(doc)
                    local_ts = (dt or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
                    digest_buffer.append(
                        f"{local_ts.strftime('%m-%d %H:%M')} | {doc['fields']['source']} | {doc['fields']['message']}"
                    )
                    period_ids.setdefault(period, []).append(did)

                    if len(batch) >= BATCH_SIZE:
                        n = await qdrant_upsert_batch(client, batch)
                        if n:
                            inc(counters, "qdrant.indexed", n)
                            for p, ids in period_ids.items():
                                record_doc_ids(ids, p)
                        batch.clear()
                        period_ids.clear()

            # Post eventuell rest-batch
            if batch:
                n = await qdrant_upsert_batch(client, batch)
                if n:
                    inc(counters, "qdrant.indexed", n)
                    for p, ids in period_ids.items():
                        record_doc_ids(ids, p)

            # Rydd stalled RIDs
            expire_stalled_rids(active_rids, counters)

            # Flush face sessions that have been silent for > timeout
            flush_stale_face_sessions()

            # Skriv rapport og lagre tilstand
            now = asyncio.get_event_loop().time()
            if now - last_report >= REPORT_INTERVAL:
                report = build_report(counters, active_rids)
                safe_write_json(REPORT_PATH, report)
                state["counters"]    = counters
                state["active_rids"] = active_rids
                safe_write_json(STATE_PATH, state)
                if digest_buffer:
                    write_digest(DIGEST_PATH, list(digest_buffer))
                trim_face_events_file()
                last_report = now
                log.info(
                    "Rapport: %d indeksert / %d droppet / %d aktive RIDs",
                    int(counters.get("qdrant.indexed", 0)),
                    int(counters.get("qdrant.dropped_noise", 0)),
                    len(active_rids),
                )

            await asyncio.sleep(POLL_INTERVAL)


def main():
    ensure_argus_collection()
    try:
        asyncio.run(daemon())
    except KeyboardInterrupt:
        log.info("Argus stoppet.")


if __name__ == "__main__":
    main()
