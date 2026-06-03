import asyncio
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import base64
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

import kaare_core.app_state as app_state
from kaare_core.audit import read_recent as _audit_read
from kaare_core.config import get_model, get_service
from kaare_core.users.auth import require_admin as _require_admin, require_auth as _require_auth

router = APIRouter()

_SETTINGS_PATH  = Path("/kaare/configs/settings.yaml")
_SERVICES_PATH  = Path("/kaare/configs/services.yaml")
_LLM_PATH       = Path("/kaare/configs/llm.yaml")
_CAPMAP_PATH    = Path("/kaare/capability_map.yaml")
_FRIGATE_CAMERAS_PATH = Path("/kaare/configs/frigate_cameras.yaml")

_RESTARTABLE_SERVICES = {
    "kaare":          "kaare.service",
    "gateway":        "kaare_ha_gateway.service",
    "semantic_embed": "kaare-semantic-embed.service",
    "embedding":      "kaare-embedding.service",
    "argus":     "kaare-argus.service",
    "voice":          "kaare-voice-bridge.service",
    "frontend":       "kaare-frontend.service",
    "ha-log-bridge":  "kaare-ha-log-bridge.service",
}

_IN_DOCKER = os.path.exists("/.dockerenv")


@router.get("/api/health_check")
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


@router.get("/api/run_tests")
async def api_run_tests(_u=Depends(_require_admin)):
    """Run pytest tests/ via system python3 and return structured pass/fail results."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, "/kaare/scripts/run_tests.py"],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": "/kaare"},
            ),
        )
        if not result.stdout.strip():
            return {"ok": False, "passed": 0, "failed": 0, "total": 0, "failures": [],
                    "error": f"rc={result.returncode} stderr={result.stderr[:300]!r}"}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "passed": 0, "failed": 0, "total": 0, "failures": [], "error": "Timed out (>60s)"}
    except Exception as e:
        return {"ok": False, "passed": 0, "failed": 0, "total": 0, "failures": [], "error": str(e)}


@router.get("/api/settings/cameras")
async def api_get_cameras(_u=Depends(_require_admin)):
    """
    Returns camera config merged with live Frigate camera+label discovery.
    Cameras known to Frigate but not in config appear with analyze: false defaults.
    """
    from kaare_core.domain.frigate_responder import _cfg

    cfg = _cfg()
    saved_cameras: dict = cfg.get("cameras", {})
    roles: dict = cfg.get("roles", {})
    enabled: bool = cfg.get("enabled", True)

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
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{frigate_url}/api/config")
            fcfg = r.json()
            objects = fcfg.get("objects", {}) or {}
            available_labels = objects.get("track", [])
    except Exception:
        available_labels = ["person", "car", "postnord", "deer", "dog", "cat"]

    merged_cameras = {}
    all_cam_ids = list(saved_cameras.keys()) + [c for c in frigate_cameras if c not in saved_cameras]
    for cam_id in all_cam_ids:
        if cam_id in saved_cameras:
            merged_cameras[cam_id] = saved_cameras[cam_id]
        else:
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


@router.get("/api/settings/cameras/_storage_usage")
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


_FAILED_ANALYSIS_PATTERNS = ["ikke kontakt", "kontakt med noen"]


def _is_failed_analysis(text: str) -> bool:
    if not text or len(text.strip()) < 20:
        return True
    for pat in _FAILED_ANALYSIS_PATTERNS:
        if pat in text:
            return True
    return False


@router.get("/api/settings/cameras/_analysis_log")
async def api_get_analysis_log(limit: int = 40, _u=Depends(_require_admin)):
    """Returns recent Frigate analysis log entries, newest first, deduplicated by event_id."""
    log_file = Path("/kaare/logs/frigate_analysis.log")
    snap_dir = Path("/kaare/state/frigate_snapshots")

    if not log_file.exists():
        return {"entries": []}

    try:
        lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return {"entries": []}

    seen: dict[str, dict] = {}
    for line in lines:
        try:
            entry = json.loads(line)
            eid = entry.get("event_id", "")
            seen[eid] = entry
        except Exception:
            continue

    entries = sorted(seen.values(), key=lambda e: e.get("ts", ""), reverse=True)[:limit]

    result = []
    for e in entries:
        eid = e.get("event_id", "")
        analysis = e.get("analysis", "")
        result.append({
            "ts": e.get("ts", ""),
            "camera": e.get("camera", ""),
            "display_name": e.get("display_name") or e.get("camera", ""),
            "role": e.get("role", ""),
            "label": e.get("label", ""),
            "score": e.get("score", 0),
            "top_score": e.get("top_score", e.get("score", 0)),
            "duration": e.get("duration", 0),
            "sub_label": e.get("sub_label"),
            "zones": e.get("zones", []),
            "event_id": eid,
            "analysis": analysis,
            "failed": _is_failed_analysis(analysis),
            "has_snapshot": bool(eid and (snap_dir / f"{eid}.jpg").is_file()),
        })

    return {"entries": result}


class RetryAnalysisRequest(BaseModel):
    event_id: str
    camera: str
    label: str
    score: float
    top_score: float = 0.0
    duration: float = 0.0
    sub_label: str | None = None
    sub_label_score: float | None = None
    zones: list[str] = []


@router.post("/api/settings/cameras/_retry_analysis")
async def api_retry_analysis(req: RetryAnalysisRequest, _u=Depends(_require_admin)):
    """Re-runs VLM analysis for a given event. Uses local snapshot if available."""
    import logging as _logging
    from kaare_core.domain.frigate_responder import analyze_event, _write_analysis_log, _cfg

    argus_log = _logging.getLogger("frigate_responder")
    snap_dir = Path("/kaare/state/frigate_snapshots")
    snap_path = snap_dir / f"{req.event_id}.jpg"

    img_b64_override: str | None = None
    if snap_path.is_file():
        try:
            img_b64_override = base64.b64encode(snap_path.read_bytes()).decode()
        except Exception as e:
            argus_log.error("retry_analysis: could not read local snapshot %s: %s", snap_path, e)

    event_dict = {
        "event_id": req.event_id,
        "camera": req.camera,
        "label": req.label,
        "score": req.score,
        "top_score": req.top_score or req.score,
        "duration": req.duration,
        "sub_label": req.sub_label,
        "sub_label_score": req.sub_label_score,
        "zones": req.zones,
    }

    try:
        result = await analyze_event(event_dict, img_b64_override=img_b64_override)
    except Exception as e:
        argus_log.error("retry_analysis: analyze_event raised for %s: %s", req.event_id, e)
        raise HTTPException(500, f"Analysis failed: {e}")

    if not result:
        argus_log.error("retry_analysis: no result for event %s (no snapshot or VLM unreachable)", req.event_id)
        raise HTTPException(500, "Analysis failed — no snapshot available or VLM unreachable")

    analysis_text = result.get("analysis", "").strip()

    cfg = _cfg()
    cam_cfg = cfg.get("cameras", {}).get(req.camera, {})
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "camera": req.camera,
        "display_name": cam_cfg.get("display_name") or req.camera,
        "role": cam_cfg.get("role", ""),
        "label": req.label,
        "score": req.score,
        "top_score": req.top_score or req.score,
        "duration": req.duration,
        "sub_label": req.sub_label,
        "zones": req.zones,
        "event_id": req.event_id,
        "analysis": analysis_text,
    }
    _write_analysis_log(entry)

    return {"ok": True, "analysis": analysis_text, "failed": _is_failed_analysis(analysis_text)}


@router.put("/api/settings/cameras/_global")
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


@router.put("/api/settings/cameras/_storage")
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


@router.put("/api/settings/cameras/{camera_id}")
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


class VpnCreateRequest(BaseModel):
    username: str
    device_name: str


@router.get("/api/vpn/clients")
async def api_vpn_list(payload: dict = Depends(_require_admin)):
    """Lists all WireGuard VPN clients."""
    from kaare_core.vpn import list_clients
    try:
        return list_clients()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/vpn/clients", status_code=201)
async def api_vpn_create(req: VpnCreateRequest, payload: dict = Depends(_require_admin)):
    """Creates a new WireGuard client. Returns config text for QR rendering."""
    from kaare_core.vpn import create_client
    try:
        return create_client(req.username, req.device_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/vpn/clients/{client_name}")
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


@router.get("/api/settings/vpn")
async def api_get_vpn_settings(_u=Depends(_require_admin)):
    """Return VPN endpoint config from settings.yaml."""
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    vpn = data.get("vpn", {})
    return {"duckdns_host": vpn.get("duckdns_host", ""), "wg_port": int(vpn.get("wg_port", 51820))}


@router.put("/api/settings/vpn")
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


@router.get("/api/system_status")
def api_system_status(_u=Depends(_require_auth)):
    """Return active module status. Domain must be enabled AND configured to show as active."""
    domains = app_state.CAPABILITY_MAP.get("domains", {})

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
    """System services (systemd processes). Descriptions derived from services.yaml + capability_map.yaml."""
    try:
        _svc_cfg = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        _svc_cfg = {}
    try:
        _cap_cfg = yaml.safe_load(_CAPMAP_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        _cap_cfg = {}

    _embed_device   = _svc_cfg.get("embedding", {}).get("device", "CPU")
    _embed_enabled  = _cap_cfg.get("services", {}).get("embedding", {}).get("enabled", True)
    _memory_enabled = _svc_cfg.get("memory_embed", {}).get("enabled", True)

    _embed_uses = ["wiki"]
    if _embed_enabled:
        _embed_uses.append("events")
    _embed_desc = f"BGE-M3 {' & '.join(_embed_uses) or 'indexing'} ({_embed_device})"

    _qdrant_parts = ["wiki"]
    if _memory_enabled:
        _qdrant_parts.append("memory")
    if _embed_enabled:
        _qdrant_parts.append("events")
    _qdrant_desc = "Vector database — " + (", ".join(_qdrant_parts) if _qdrant_parts else "—")

    _semembed_desc = (
        "384-dim semantic memory embedding" if _memory_enabled
        else "384-dim embedding (memory disabled)"
    )

    return [
        {"key": "kaare_api",      "name": "Kåre",           "description": "Main orchestrator API",
         "color": "#646cff",      "check_url": get_service("internal", "kaare_api") + "/"},
        {"key": "ha_gateway",     "name": "HA Gateway",     "description": "Home Assistant command executor",
         "color": "#facc15",      "check_url": get_service("internal", "ha_gateway") + "/"},
        {"key": "semantic_embed", "name": "Semantic Embed", "description": _semembed_desc,
         "color": "#60a5fa",      "check_url": get_service("internal", "semantic_embed") + "/"},
        {"key": "voice_bridge",   "name": "Voice Bridge",   "description": "Piper TTS + Whisper STT",
         "color": "#a78bfa",      "check_url": get_service("internal", "voice_bridge") + "/"},
        {"key": "embedding",      "name": "Embedding",      "description": _embed_desc,
         "color": "#c2f8da",      "check_url": get_service("ollama", "embed") + "/health"},
        {"key": "qdrant",         "name": "Qdrant",         "description": _qdrant_desc,
         "color": "#16a34a",      "check_url": get_service("storage", "qdrant") + "/readyz"},
        {"key": "argus",          "name": "Argus",          "description": "System log monitor and alerter",
         "color": "#fb7185",      "check_url": "file:///kaare/state/argus/report.json:600"},
        {"key": "jing_svc",       "name": "Jing",           "description": "Fast inner voice service",
         "color": "#e879f9",      "check_url": "file:///kaare/state/jing_thoughts.txt:600"},
        {"key": "jang_svc",       "name": "Jang",           "description": "Slow inner voice service",
         "color": "#f472b6",      "check_url": "file:///kaare/state/inner_thoughts.txt:2100"},
    ]


def _build_model_catalog() -> list[dict]:
    """LLM/ML models being served. Model names and platforms derived from llm.yaml + models.yaml.
    Inner voice models and Whisper are checked via file age / voice-bridge."""
    try:
        _llm = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        _llm = {}
    try:
        _svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        _embed_model  = _svc.get("embedding", {}).get("hf_model") or get_model("embed")
        _embed_device = _svc.get("embedding", {}).get("device", "CPU")
    except Exception:
        _embed_model  = get_model("embed")
        _embed_device = "CPU"

    def _platform(llm_key: str) -> str:
        s = _llm.get(llm_key, {})
        provider = s.get("provider", "ollama")
        if provider == "vllm":
            return "vLLM"
        if provider == "openai":
            return "OpenAI-compat"
        if provider not in ("ollama", "openvino"):
            return "Cloud"
        base_url = s.get("base_url", "")
        if "ollama:11434" in base_url or not base_url:
            return "Ollama (builtin)"
        host = base_url.replace("http://", "").replace("https://", "").split(":")[0]
        return f"Ollama ({host})"

    def _model(model_key: str) -> str:
        try:
            model_role = _llm.get(model_key, {}).get("model_role", model_key)
            return get_model(model_role)
        except Exception:
            return get_model(model_key)

    def _check_url_for(llm_key: str, model_key: str | None = None) -> str | None:
        s = _llm.get(llm_key, {})
        provider = s.get("provider", "ollama")
        base = (s.get("base_url") or "http://ollama:11434").rstrip("/")
        if provider == "vllm":
            return f"{base}/health"
        if provider == "openai":
            return f"{base}/v1/models"
        if provider not in ("ollama", "openvino"):
            return None  # cloud providers — no local reachability check
        host_port = base.replace("http://", "").replace("https://", "")
        return f"ollama-model://{host_port}|{_model(model_key or llm_key)}"

    return [
        {"key": "llm_kare",      "name": "Kåre",               "model": _model("kare"),
         "platform": _platform("default"),    "color": "#646cff",
         "check_url": _check_url_for("default", "kare")},
        {"key": "llm_miss_kare", "name": "Miss Kåre / Mechanic", "model": _model("miss_kare"),
         "platform": _platform("miss_kare"), "color": "#fde047",
         "check_url": _check_url_for("miss_kare")},
        {"key": "llm_library",   "name": "Library",            "model": _model("library"),
         "platform": _platform("library"),   "color": "#4ade80",
         "check_url": _check_url_for("library")},
        {"key": "llm_jing",      "name": "Jing",                "model": "Qwen3-0.6B",
         "platform": f"{_svc.get('inner_voices', {}).get('node_label', 'Local')} ({_svc.get('jing', {}).get('provider', 'openvino')})",
         "color": "#e879f9",
         "check_url": "file:///kaare/state/jing_thoughts.txt:600"},
        {"key": "llm_jang",      "name": "Jang",                "model": "Qwen3-4B",
         "platform": f"{_svc.get('inner_voices', {}).get('node_label', 'Local')} ({_svc.get('jang', {}).get('provider', 'openvino')})",
         "color": "#f472b6",
         "check_url": "file:///kaare/state/inner_thoughts.txt:2100"},
        {"key": "whisper",       "name": "Whisper STT",         "model": "nb-whisper-large",
         "platform": "Voice Bridge",         "color": "#a78bfa",
         "check_url": get_service("internal", "voice_bridge") + "/"},
        {"key": "piper",         "name": "Piper TTS",
         "model": Path(_svc.get("voice", {}).get("tts", {}).get("tts_models", {}).get("nb")
                       or _svc.get("voice", {}).get("tts", {}).get("voice", "")).stem or "no_NO-talesyntese-medium",
         "platform": "Voice Bridge",         "color": "#a78bfa",
         "check_url": get_service("internal", "voice_bridge") + "/"},
        {"key": "bge_m3",        "name": _embed_model.split("/")[-1], "model": _embed_model,
         "platform": f"Embedding ({_embed_device})",  "color": "#4ade80",
         "check_url": get_service("ollama", "embed") + "/health"},
    ]


@router.get("/api/system/gpus")
async def api_get_gpus(_u=Depends(_require_auth)):
    """List available GPUs via nvidia-smi. Returns empty list if not available."""
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


@router.get("/api/system/hardware")
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


@router.post("/api/admin/restart/{service_key}")
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
        return {"ok": False, "docker": True, "error": f"In Docker: restart '{service_key}' with 'docker compose restart {service_key}'"}

    # Bare-metal / systemd path
    if service_key == "kaare":
        def _self_restart():
            time.sleep(0.6)
            subprocess.run(["sudo", "/bin/systemctl", "restart", unit], capture_output=True)
        background_tasks.add_task(_self_restart)
        return {"ok": True, "unit": unit}

    try:
        result = subprocess.run(
            ["sudo", "/bin/systemctl", "restart", unit],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "systemctl failed"}
        return {"ok": True, "unit": unit}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/admin/settings/rollback")
async def api_settings_rollback(_u=Depends(_require_admin)):
    """Restore all *.yaml files in configs/ from configs_default/."""
    defaults_dir = Path("/kaare/configs_default")
    configs_dir  = Path("/kaare/configs")
    restored, errors = [], []
    for src in sorted(defaults_dir.glob("*.yaml")):
        dst = configs_dir / src.name
        try:
            dst.write_bytes(src.read_bytes())
            restored.append(src.name)
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return {"ok": len(errors) == 0, "restored": restored, "errors": errors}


# ── Config snapshots ──────────────────────────────────────────────────────────

_CONFIGS_DIR    = Path("/kaare/configs")
_SNAPSHOTS_DIR  = Path("/kaare/backups/configs")
_MAX_SNAPSHOTS  = 10
_SNAPSHOT_ID_RE = re.compile(r"^\d{8}_\d{6}$")


class SnapshotSaveRequest(BaseModel):
    name: str


def _list_snapshots() -> list[dict]:
    if not _SNAPSHOTS_DIR.exists():
        return []
    result = []
    for d in sorted(_SNAPSHOTS_DIR.iterdir(), reverse=True):
        meta_path = d / "meta.json"
        if d.is_dir() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                result.append({"id": d.name, **meta})
            except Exception:
                pass
    return result


@router.post("/api/admin/config-snapshot")
async def api_save_config_snapshot(payload: SnapshotSaveRequest, _u=Depends(_require_admin)):
    existing = _list_snapshots()
    if len(existing) >= _MAX_SNAPSHOTS:
        return {"ok": False, "error": "max_reached", "count": len(existing)}

    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    files_saved = []
    for src in sorted(_CONFIGS_DIR.glob("*.yaml")):
        dst = snapshot_dir / src.name
        dst.write_bytes(src.read_bytes())
        files_saved.append(src.name)

    meta = {
        "name": payload.name.strip()[:80],
        "created": datetime.now(timezone.utc).isoformat(),
        "files": files_saved,
    }
    (snapshot_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "id": snapshot_id, "file_count": len(files_saved)}


@router.get("/api/admin/config-snapshots")
async def api_list_config_snapshots(_u=Depends(_require_admin)):
    return {"snapshots": _list_snapshots()}


@router.post("/api/admin/config-snapshot/{snapshot_id}/restore")
async def api_restore_config_snapshot(snapshot_id: str, _u=Depends(_require_admin)):
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise HTTPException(status_code=400, detail="Invalid snapshot ID")
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    if not snapshot_dir.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    restored, errors = [], []
    for src in sorted(snapshot_dir.glob("*.yaml")):
        dst = _CONFIGS_DIR / src.name
        try:
            dst.write_bytes(src.read_bytes())
            restored.append(src.name)
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return {"ok": len(errors) == 0, "restored": restored, "errors": errors}


@router.delete("/api/admin/config-snapshot/{snapshot_id}")
async def api_delete_config_snapshot(snapshot_id: str, _u=Depends(_require_admin)):
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise HTTPException(status_code=400, detail="Invalid snapshot ID")
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    if not snapshot_dir.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    shutil.rmtree(snapshot_dir)
    return {"ok": True}


@router.get("/api/admin/config-snapshot/{snapshot_id}/export")
async def api_export_config_snapshot(snapshot_id: str, _u=Depends(_require_admin)):
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise HTTPException(status_code=400, detail="Invalid snapshot ID")
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    if not snapshot_dir.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    meta_path = snapshot_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        name_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", meta.get("name", snapshot_id))[:40]
    except Exception:
        name_slug = snapshot_id

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(snapshot_dir.glob("*.yaml")):
            zf.write(f, arcname=f.name)

    filename = f"ktsb-config-{name_slug}-{snapshot_id}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/admin/config/export")
async def api_export_current_config(_u=Depends(_require_admin)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(_CONFIGS_DIR.glob("*.yaml")):
            zf.write(f, arcname=f.name)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"ktsb-config-current-{ts}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/admin/config-snapshot/import")
async def api_import_config_snapshot(
    name: str = Form(...),
    file: UploadFile = File(...),
    _u=Depends(_require_admin),
):
    if not (file.filename or "").endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files accepted")

    existing = _list_snapshots()
    if len(existing) >= _MAX_SNAPSHOTS:
        return {"ok": False, "error": "max_reached", "count": len(existing)}

    content = await file.read()

    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    files_saved = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for member in zf.namelist():
                fname = Path(member).name
                # Only plain *.yaml files — no subdirectories, no path traversal
                if not fname.endswith(".yaml") or "/" in member or "\\" in member or fname != member:
                    continue
                (snapshot_dir / fname).write_bytes(zf.read(member))
                files_saved.append(fname)
    except zipfile.BadZipFile:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Invalid zip file")

    if not files_saved:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return {"ok": False, "error": "no_yaml_files"}

    meta = {
        "name": name.strip()[:80],
        "created": datetime.now(timezone.utc).isoformat(),
        "files": files_saved,
        "imported": True,
    }
    (snapshot_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "id": snapshot_id, "file_count": len(files_saved)}


@router.get("/api/admin/services")
async def api_admin_services(_u=Depends(_require_auth)):
    """Return list of restartable services with current status."""
    statuses = {}
    if _IN_DOCKER:
        # In Docker each service is a separate container — can't use systemctl.
        for key, unit in _RESTARTABLE_SERVICES.items():
            statuses[key] = {"unit": unit, "active": True, "docker": True}
        return statuses

    for key, unit in _RESTARTABLE_SERVICES.items():
        try:
            r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3)
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


@router.get("/api/audit/recent")
def api_audit_recent(limit: int = 200, payload: dict = Depends(_require_admin)):
    """Return the last N audit log entries (newest-first). Requires admin role."""
    return {"entries": _audit_read(limit=min(limit, 500))}


@router.get("/api/system/overview")
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
