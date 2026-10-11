import asyncio
import base64
import json
import logging as _logging
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
)
from pydantic import BaseModel

import kaare_core.app_state as app_state
from adapters.frigate_adapter import get_cameras as _get_cameras
from adapters.mqtt_adapter import get_status as mqtt_status
from kaare_core import config_store
from kaare_core.audit import read_recent as _audit_read
from kaare_core.config import CAPABILITY_MAP_PATH as _CAPMAP_PATH
from kaare_core.config import get_model, get_service
from kaare_core.config import get_service as _svc
from kaare_core.domain.frigate_responder import (
    _cfg,
    _write_analysis_log,
    analyze_event,
    load_camera_config,
)
from kaare_core.memory.memory_embed_models import active_model
from kaare_core.normalcy import (
    add_correction as _add_correction,
)
from kaare_core.normalcy import (
    delete_correction as _delete_correction,
)
from kaare_core.normalcy import (
    get_recent_anomalies as _get_anomalies,
)
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.users.auth import require_auth as _require_auth
from kaare_core.vpn import create_client, delete_client, list_clients

router = APIRouter()

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_SERVICES_PATH = Path("/kaare/configs/services.yaml")
_LLM_PATH = Path("/kaare/configs/llm.yaml")
_FRIGATE_CAMERAS_PATH = Path("/kaare/configs/frigate_cameras.yaml")

_RESTARTABLE_SERVICES = {
    "kaare": "kaare.service",
    "gateway": "kaare_ha_gateway.service",
    "semantic_embed": "kaare-semantic-embed.service",
    "embedding": "kaare-embedding.service",
    "argus": "kaare-argus.service",
    "voice": "kaare-voice-bridge.service",
    "frontend": "kaare-frontend.service",
    "ha-log-bridge": "kaare-ha-log-bridge.service",
}

_IN_DOCKER = os.path.exists("/.dockerenv")

# Maps _RESTARTABLE_SERVICES keys to the actual docker-compose service names
# (docker-compose.yml). Container *names* depend on the compose project
# name, which is whatever directory the user cloned into — matching on the
# com.docker.compose.service label instead is robust regardless of that.
_DOCKER_COMPOSE_SERVICE = {
    "kaare": "kaare-api",
    "gateway": "kaare-ha-gateway",
    "semantic_embed": "kaare-semantic-embed",
    "embedding": "kaare-embedding",
    "argus": "kaare-argus",
    "voice": "kaare-voice",
    "frontend": "kaare-frontend",
    "ha-log-bridge": "kaare-ha-log-bridge",
}

_docker_http_client: httpx.Client | None = None


def _docker_socket_client() -> httpx.Client:
    """Lazy singleton talking to the Docker Engine API over the mounted
    docker.sock. Plain REST over a Unix socket — deliberately not the
    `docker` SDK, which isn't a dependency of this project and would need
    an image rebuild to add for what's really just two simple calls."""
    global _docker_http_client
    if _docker_http_client is None:
        _docker_http_client = httpx.Client(
            transport=httpx.HTTPTransport(uds="/var/run/docker.sock"),
            base_url="http://docker",
            timeout=10.0,
        )
    return _docker_http_client


def _docker_find_container(compose_service: str) -> dict | None:
    """The container's Docker API JSON for a compose service, matched via
    its com.docker.compose.service label, or None if it was never created
    (e.g. gated behind a COMPOSE_PROFILES that isn't active)."""
    r = _docker_socket_client().get(
        "/containers/json",
        params={
            "all": "true",
            "filters": json.dumps({"label": [f"com.docker.compose.service={compose_service}"]}),
        },
    )
    r.raise_for_status()
    containers = r.json()
    return containers[0] if containers else None


def _docker_service_state(service_key: str) -> tuple[str, str | None]:
    """(state, reason) for a _RESTARTABLE_SERVICES key in Docker mode.
    state is one of: running | stopped | not_created | unavailable."""
    compose_service = _DOCKER_COMPOSE_SERVICE.get(service_key)
    if not compose_service:
        return "unavailable", f"No Docker mapping for '{service_key}'"
    try:
        container = _docker_find_container(compose_service)
    except Exception as e:
        return "unavailable", f"Docker socket unreachable: {e}"
    if container is None:
        return "not_created", None
    return ("running" if container.get("State") == "running" else "stopped"), None


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
            return {
                "ok": False,
                "passed": 0,
                "failed": 0,
                "total": 0,
                "failures": [],
                "error": f"rc={result.returncode} stderr={result.stderr[:300]!r}",
            }
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "failures": [],
            "error": "Timed out (>60s)",
        }
    except Exception as e:
        return {"ok": False, "passed": 0, "failed": 0, "total": 0, "failures": [], "error": str(e)}


@router.get("/api/settings/cameras")
async def api_get_cameras(_u=Depends(_require_admin)):
    """
    Returns camera config merged with live Frigate camera+label discovery.
    Cameras known to Frigate but not in config appear with analyze: false defaults.
    """

    cfg = _cfg()
    saved_cameras: dict = cfg.get("cameras", {})
    roles: dict = cfg.get("roles", {})
    enabled: bool = cfg.get("enabled", True)

    available_labels: list[str] = []
    frigate_cameras: list[str] = []
    try:
        frigate_cam_list = await _get_cameras()
        frigate_cameras = [c["api_name"] for c in frigate_cam_list]
    except Exception:
        pass

    svc_cam_names: dict = {}
    try:
        svc_cfg = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text()) or {}
        frigate_url = (svc_cfg.get("frigate") or {}).get("url", "http://localhost:5000")
        svc_cam_names = (svc_cfg.get("frigate") or {}).get("camera_names") or {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{frigate_url}/api/config")
            fcfg = r.json()
            objects = fcfg.get("objects", {}) or {}
            available_labels = objects.get("track", [])
    except Exception:
        available_labels = ["person", "car", "postnord", "deer", "dog", "cat"]

    merged_cameras = {}
    all_cam_ids = list(saved_cameras.keys()) + [
        c for c in frigate_cameras if c not in saved_cameras
    ]
    for cam_id in all_cam_ids:
        if cam_id in saved_cameras:
            merged_cameras[cam_id] = saved_cameras[cam_id]
        else:
            merged_cameras[cam_id] = {
                "display_name": svc_cam_names.get(cam_id, cam_id),
                "role": "road_facing",
                "labels": {
                    lbl: {"analyze": False, "min_confidence": 0.65, "announce": False}
                    for lbl in available_labels
                },
            }
        merged_cameras[cam_id]["_id"] = cam_id

    away_mode_defaults = {
        "nighttime_start": 22,
        "nighttime_end": 6,
        "person_confidence_high": 0.6,
        "unknown_night_high": True,
    }
    away_mode = {**away_mode_defaults, **(cfg.get("away_mode") or {})}

    return {
        "enabled": enabled,
        "cameras": list(merged_cameras.values()),
        "roles": roles,
        "available_labels": available_labels,
        "storage": cfg.get("storage", {}),
        "away_mode": away_mode,
    }


@router.get("/api/settings/cameras/_storage_usage")
async def api_get_cameras_storage_usage(_u=Depends(_require_admin)):
    """Returns actual disk usage for camera snapshots and analysis log in MB."""
    snap_dir = Path("/kaare/state/frigate_snapshots")
    log_file = Path("/kaare/logs/frigate_analysis.log")
    try:
        snap_mb = round(
            sum(f.stat().st_size for f in snap_dir.iterdir() if f.is_file()) / 1_048_576, 1
        )
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
    return any(pat in text for pat in _FAILED_ANALYSIS_PATTERNS)


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
        result.append(
            {
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
            }
        )

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
        raise HTTPException(500, f"Analysis failed: {e}") from e

    if not result:
        argus_log.error(
            "retry_analysis: no result for event %s (no snapshot or VLM unreachable)", req.event_id
        )
        raise HTTPException(500, "Analysis failed — no snapshot available or VLM unreachable")

    analysis_text = result.get("analysis", "").strip()

    cfg = _cfg()
    cam_cfg = cfg.get("cameras", {}).get(req.camera, {})
    entry = {
        "ts": datetime.now(UTC).isoformat(),
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
        raw = config_store.load_for_update(_FRIGATE_CAMERAS_PATH)
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    raw["enabled"] = bool(payload.get("enabled", True))
    config_store.write_yaml(_FRIGATE_CAMERAS_PATH, raw, "gui:put_cameras_global")

    load_camera_config()
    return {"ok": True, "enabled": raw["enabled"]}


@router.put("/api/settings/cameras/_storage")
async def api_put_cameras_storage(payload: dict, _u=Depends(_require_admin)):
    """Update storage size limits. Values are clamped: 0=disabled, snapshots 100–10000 MB, log 10–1000 MB."""
    try:
        raw = config_store.load_for_update(_FRIGATE_CAMERAS_PATH)
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    storage = raw.setdefault("storage", {})

    if "snapshots_max_mb" in payload:
        val = int(payload["snapshots_max_mb"])
        storage["snapshots_max_mb"] = 0 if val == 0 else max(100, min(val, 10_000))

    if "log_max_mb" in payload:
        val = int(payload["log_max_mb"])
        storage["log_max_mb"] = 0 if val == 0 else max(10, min(val, 1_000))

    config_store.write_yaml(_FRIGATE_CAMERAS_PATH, raw, "gui:put_cameras_storage")

    load_camera_config()
    return {"ok": True, "storage": storage}


@router.put("/api/settings/cameras/_away_mode")
async def api_put_cameras_away_mode(payload: dict, _u=Depends(_require_admin)):
    """Update away-mode threat assessment settings for Frigate events."""
    try:
        raw = config_store.load_for_update(_FRIGATE_CAMERAS_PATH)
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    am = raw.setdefault("away_mode", {})

    if "nighttime_start" in payload:
        am["nighttime_start"] = max(0, min(int(payload["nighttime_start"]), 23))
    if "nighttime_end" in payload:
        am["nighttime_end"] = max(0, min(int(payload["nighttime_end"]), 23))
    if "person_confidence_high" in payload:
        am["person_confidence_high"] = max(0.0, min(float(payload["person_confidence_high"]), 1.0))
    if "unknown_night_high" in payload:
        am["unknown_night_high"] = bool(payload["unknown_night_high"])

    config_store.write_yaml(_FRIGATE_CAMERAS_PATH, raw, "gui:put_cameras_away_mode")

    load_camera_config()
    return {"ok": True, "away_mode": am}


@router.put("/api/settings/cameras/{camera_id}")
async def api_put_camera(camera_id: str, payload: dict, _u=Depends(_require_admin)):
    """Update config for one camera. Writes to frigate_cameras.yaml and hot-reloads."""
    try:
        raw = config_store.load_for_update(_FRIGATE_CAMERAS_PATH)
    except Exception:
        raw = {"enabled": True, "cameras": {}, "roles": {}}

    if "cameras" not in raw:
        raw["cameras"] = {}

    # Remove internal _id field before saving
    payload.pop("_id", None)
    raw["cameras"][camera_id] = payload

    config_store.write_yaml(_FRIGATE_CAMERAS_PATH, raw, "gui:put_camera")

    load_camera_config()
    return {"ok": True, "camera_id": camera_id}


class VpnCreateRequest(BaseModel):
    username: str
    device_name: str


@router.get("/api/vpn/clients")
async def api_vpn_list(payload: dict = Depends(_require_admin)):
    """Lists all WireGuard VPN clients."""

    try:
        return list_clients()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/vpn/clients", status_code=201)
async def api_vpn_create(req: VpnCreateRequest, payload: dict = Depends(_require_admin)):
    """Creates a new WireGuard client. Returns config text for QR rendering."""

    try:
        return create_client(req.username, req.device_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/api/vpn/clients/{client_name}")
async def api_vpn_delete(client_name: str, payload: dict = Depends(_require_admin)):
    """Removes a WireGuard client by name."""

    try:
        delete_client(client_name)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        data = config_store.load_for_update(_SETTINGS_PATH)
        data.setdefault("vpn", {}).update(payload)
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_vpn_settings")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
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

    _embed_device = _svc_cfg.get("embedding", {}).get("device", "CPU")
    _embed_enabled = _cap_cfg.get("services", {}).get("embedding", {}).get("enabled", True)
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

    _mem_model = active_model()
    _semembed_desc = (
        f"{_mem_model['dim']}-dim semantic memory embedding ({_mem_model['key']})"
        if _memory_enabled
        else "semantic memory embedding (disabled)"
    )

    return [
        {
            "key": "kaare_api",
            "name": "Kåre",
            "description": "Main orchestrator API",
            "color": "#646cff",
            "check_url": get_service("internal", "kaare_api") + "/",
        },
        {
            "key": "ha_gateway",
            "name": "HA Gateway",
            "description": "Home Assistant command executor",
            "color": "#facc15",
            "check_url": get_service("internal", "ha_gateway") + "/",
        },
        {
            "key": "semantic_embed",
            "name": "Semantic Embed",
            "description": _semembed_desc,
            "color": "#60a5fa",
            "check_url": get_service("internal", "semantic_embed") + "/",
        },
        {
            "key": "voice_bridge",
            "name": "Voice Bridge",
            "description": "Piper TTS + Whisper STT",
            "color": "#a78bfa",
            "check_url": get_service("internal", "voice_bridge") + "/",
        },
        {
            "key": "embedding",
            "name": "Embedding",
            "description": _embed_desc,
            "color": "#c2f8da",
            "check_url": get_service("ollama", "embed") + "/health",
        },
        {
            "key": "qdrant",
            "name": "Qdrant",
            "description": _qdrant_desc,
            "color": "#16a34a",
            "check_url": get_service("storage", "qdrant") + "/readyz",
        },
        {
            "key": "argus",
            "name": "Argus",
            "description": "System log monitor and alerter",
            "color": "#fb7185",
            "check_url": "file:///kaare/state/argus/report.json:600",
        },
        {
            "key": "jing_svc",
            "name": "Jing",
            "description": "Fast inner voice service",
            "color": "#e879f9",
            "check_url": "file:///kaare/state/jing_thoughts.txt:600",
        },
        {
            "key": "jang_svc",
            "name": "Jang",
            "description": "Slow inner voice service",
            "color": "#f472b6",
            "check_url": "file:///kaare/state/inner_thoughts.txt:2100",
        },
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
        _embed_model = _svc.get("embedding", {}).get("hf_model") or get_model("embed")
        _embed_device = _svc.get("embedding", {}).get("device", "CPU")
    except Exception:
        _embed_model = get_model("embed")
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
        {
            "key": "llm_kare",
            "name": "Kåre",
            "model": _model("kare"),
            "platform": _platform("default"),
            "color": "#646cff",
            "check_url": _check_url_for("default", "kare"),
        },
        {
            "key": "llm_miss_kare",
            "name": "Miss Kåre / Mechanic",
            "model": _model("miss_kare"),
            "platform": _platform("miss_kare"),
            "color": "#fde047",
            "check_url": _check_url_for("miss_kare"),
        },
        {
            "key": "llm_library",
            "name": "Library",
            "model": _model("library"),
            "platform": _platform("library"),
            "color": "#4ade80",
            "check_url": _check_url_for("library"),
        },
        {
            "key": "llm_jing",
            "name": "Jing",
            "model": Path(_svc.get("jing", {}).get("model_path", "")).name or "unknown",
            "platform": f"{_svc.get('inner_voices', {}).get('node_label', 'Local')} ({_svc.get('jing', {}).get('provider', 'openvino')})",
            "color": "#e879f9",
            "check_url": "file:///kaare/state/jing_thoughts.txt:600",
        },
        {
            "key": "llm_jang",
            "name": "Jang",
            "model": Path(_svc.get("jang", {}).get("model_path", "")).name or "unknown",
            "platform": f"{_svc.get('inner_voices', {}).get('node_label', 'Local')} ({_svc.get('jang', {}).get('provider', 'openvino')})",
            "color": "#f472b6",
            "check_url": "file:///kaare/state/inner_thoughts.txt:2100",
        },
        {
            "key": "whisper",
            "name": "Whisper STT",
            "model": "nb-whisper-large",
            "platform": "Voice Bridge",
            "color": "#a78bfa",
            "check_url": get_service("internal", "voice_bridge") + "/",
        },
        {
            "key": "piper",
            "name": "Piper TTS",
            "model": Path(
                _svc.get("voice", {}).get("tts", {}).get("tts_models", {}).get("nb")
                or _svc.get("voice", {}).get("tts", {}).get("voice", "")
            ).stem
            or "no_NO-talesyntese-medium",
            "platform": "Voice Bridge",
            "color": "#a78bfa",
            "check_url": get_service("internal", "voice_bridge") + "/",
        },
        {
            "key": "bge_m3",
            "name": _embed_model.split("/")[-1],
            "model": _embed_model,
            "platform": f"Embedding ({_embed_device})",
            "color": "#4ade80",
            "check_url": get_service("ollama", "embed") + "/health",
        },
    ]


@router.get("/api/system/gpus")
async def api_get_gpus(_u=Depends(_require_auth)):
    """List available GPUs via nvidia-smi. Returns empty list if not available."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        gpus = []
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 3:
                    gpus.append(
                        {
                            "id": int(parts[0]),
                            "name": parts[1],
                            "vram_gb": round(int(parts[2]) / 1024, 1),
                        }
                    )
        return {"gpus": gpus}
    except Exception:
        return {"gpus": []}


@router.get("/api/system/hardware")
async def api_get_hardware(refresh: bool = False, _u=Depends(_require_auth)):
    """Detect host hardware (CPU, RAM, GPU, NPU) and cache result in state/hardware.json."""
    return await asyncio.to_thread(_detect_hardware, refresh)


def _detect_hardware(refresh: bool) -> dict:
    """Blocking hardware probe (/proc, sysfs, nvidia-smi) — runs in a worker thread."""

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
        model_lines = [
            ln.split(":", 1)[1].strip()
            for ln in cpuinfo.splitlines()
            if ln.startswith("model name")
        ]
        cores = cpuinfo.count("processor\t:")
        result["cpu"] = {
            "model": model_lines[0] if model_lines else platform.processor() or "unknown",
            "cores": cores or (os.cpu_count() or 0),
        }
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
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 3:
                    result["gpus"].append(
                        {
                            "type": "nvidia",
                            "id": int(parts[0]),
                            "name": parts[1],
                            "vram_gb": round(int(parts[2]) / 1024, 1),
                        }
                    )
    except Exception:
        pass

    # AMD GPU (rocm-smi)
    if not result["gpus"]:
        try:
            r = subprocess.run(
                ["rocm-smi", "--showproductname", "--csv"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                for i, line in enumerate(r.stdout.strip().splitlines()):
                    if line and not line.lower().startswith("device"):
                        result["gpus"].append(
                            {"type": "amd", "id": i, "name": line.strip(), "vram_gb": None}
                        )
        except Exception:
            pass

    # Intel GPU (iGPU or discrete Arc, via DRM subsystem). Always runs — an Intel iGPU
    # commonly coexists with a discrete NVIDIA/AMD card, so it must not be gated on
    # result["gpus"] being empty like the NVIDIA/AMD checks above.
    try:
        dri_path = "/sys/class/drm"
        if os.path.exists(dri_path):
            seen_pci = set()
            for entry in sorted(os.listdir(dri_path)):
                if not re.fullmatch(r"card\d+", entry):
                    continue
                device_link = f"{dri_path}/{entry}/device"
                vendor_path = f"{device_link}/vendor"
                if not os.path.exists(vendor_path):
                    continue
                with open(vendor_path) as f:
                    vendor = f.read().strip()
                if vendor != "0x8086":  # Intel PCI vendor ID
                    continue
                pci_addr = os.path.basename(os.path.realpath(device_link))
                if pci_addr in seen_pci:
                    continue
                seen_pci.add(pci_addr)

                # Bus "00" is the platform bus the CPU sits on -> integrated GPU.
                # Any other bus is a discrete card (e.g. Intel Arc).
                bus = pci_addr.split(":")[1] if pci_addr.count(":") >= 1 else "00"
                is_integrated = bus == "00"
                gpu_type = "intel-igpu" if is_integrated else "intel-arc"
                name = "Intel iGPU" if is_integrated else "Intel Arc GPU"
                try:
                    r = subprocess.run(
                        ["lspci", "-mm", "-s", pci_addr],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        fields = shlex.split(r.stdout.strip())
                        if len(fields) >= 4:
                            name = f"{fields[3]} (iGPU)" if is_integrated else fields[3]
                except Exception:
                    pass

                result["gpus"].append(
                    {"type": gpu_type, "id": len(result["gpus"]), "name": name, "vram_gb": None}
                )
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
async def api_restart_service(
    service_key: str, background_tasks: BackgroundTasks, _u=Depends(_require_admin)
):
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

        return restart_service_blocking(service_key)

    # Bare-metal / systemd path
    if service_key == "kaare":

        def _self_restart():
            time.sleep(0.6)
            subprocess.run(
                ["sudo", "/bin/systemctl", "restart", unit], capture_output=True, timeout=15
            )

        background_tasks.add_task(_self_restart)
        return {"ok": True, "unit": unit}

    return restart_service_blocking(service_key)


def restart_service_blocking(service_key: str) -> dict:
    """Restart one sibling service (not the API itself) — Docker container or systemd unit.

    Blocks until the restart has completed (up to ~30 s); call it from a worker thread when
    used inside async code. Also used by the semantic-memory model switch (P126)."""
    unit = _RESTARTABLE_SERVICES.get(service_key)
    if unit is None or service_key == "kaare":
        return {"ok": False, "error": f"Not restartable this way: {service_key}"}

    if _IN_DOCKER:
        compose_service = _DOCKER_COMPOSE_SERVICE.get(service_key)
        if not compose_service:
            return {"ok": False, "docker": True, "error": f"No Docker mapping for '{service_key}'"}
        try:
            container = _docker_find_container(compose_service)
            if container is None:
                return {
                    "ok": False,
                    "docker": True,
                    "error": f"Container for '{compose_service}' doesn't exist — not part of the active distribution profile?",
                }
            # Docker waits up to t=10s for a graceful stop before force-killing,
            # and the HTTP call doesn't return until the whole restart (stop +
            # start) completes — needs headroom above the client's default
            # timeout, not just the stop grace period itself.
            r = _docker_socket_client().post(
                f"/containers/{container['Id']}/restart",
                params={"t": 10},
                timeout=30.0,
            )
            r.raise_for_status()
            return {"ok": True, "unit": unit, "docker": True}
        except Exception as e:
            return {"ok": False, "docker": True, "error": f"Docker restart failed: {e}"}

    try:
        result = subprocess.run(
            ["sudo", "/bin/systemctl", "restart", unit],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "systemctl failed"}
        return {"ok": True, "unit": unit}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/admin/settings/rollback")
async def api_settings_rollback(_u=Depends(_require_admin)):
    """Restore all *.yaml files in configs/ from configs_default/. The current configs/ is snapshotted first
    (configs/.history/_snapshots/<ts>_pre-reset-to-default), so a reset can be undone by hand (P134)."""
    defaults_dir = config_store.DEFAULT_DIR
    configs_dir = config_store.CONFIG_DIR
    restored, errors = [], []
    try:
        snap = config_store.snapshot("pre-reset-to-default", configs_dir)
    except Exception as e:
        return {"ok": False, "restored": [], "errors": [f"snapshot failed, nothing reset: {e}"]}
    for src in sorted(defaults_dir.glob("*.yaml")):
        dst = configs_dir / src.name
        try:
            config_store.write_bytes(dst, src.read_bytes(), "reset-to-default")
            restored.append(src.name)
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return {"ok": len(errors) == 0, "restored": restored, "errors": errors, "snapshot": str(snap)}


@router.get("/api/admin/services")
async def api_admin_services(_u=Depends(_require_auth)):
    """Return list of restartable services with current status."""
    statuses = {}
    if _IN_DOCKER:
        # In Docker each service is a separate container — can't use systemctl,
        # check real container state over the docker.sock instead.
        for key, unit in _RESTARTABLE_SERVICES.items():
            state, reason = _docker_service_state(key)
            statuses[key] = {
                "unit": unit,
                "active": state == "running",
                "docker": True,
                "state": state,
                "reason": reason,
            }
        return statuses

    for key, unit in _RESTARTABLE_SERVICES.items():
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=3,
            )
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
        rest = url[len("ollama-model://") :]
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
                    return any(
                        av == model_name or av.startswith(model_name + ":") for av in available
                    )
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
async def api_system_overview(_u=Depends(_require_admin)):
    """Returns live status of all system services and LLM/ML models."""
    services = _build_service_catalog()
    models = _build_model_catalog()

    svc_results, mdl_results = await asyncio.gather(
        asyncio.gather(*[_check_url(s["check_url"]) for s in services]),
        asyncio.gather(*[_check_url(m["check_url"]) for m in models]),
    )

    return {
        "services": [
            {**s, "online": ok, "check_url": None}
            for s, ok in zip(services, svc_results, strict=False)
        ],
        "models": [
            {**m, "online": ok, "check_url": None}
            for m, ok in zip(models, mdl_results, strict=False)
        ],
    }


# ── Normalcy / anomaly event feed ─────────────────────────────────────────────


class _CorrectionBody(BaseModel):
    source_key: str
    hour_bucket: str
    weekday: str
    verdict: str  # "normal" | "suspicious"
    comment: str = ""


class _CorrectionKeyBody(BaseModel):
    source_key: str
    hour_bucket: str
    weekday: str


@router.get("/api/normalcy/events")
async def api_normalcy_events(
    days: int = 7,
    min_score: float = 0.5,
    min_confidence: float = 0.0,
    limit: int = 200,
    label: str = "",
    u=Depends(_require_admin),
):
    """Return recent Frigate events with high deviation scores for admin review."""
    try:
        svc = _svc("notifications") or {}
        cameras: list[str] = svc.get("normalcy_cameras") or []
        labels = [label.strip()] if label.strip() else None
        events = _get_anomalies(
            days=min(days, 30),
            min_score=max(0.0, min(1.0, min_score)),
            min_confidence=max(0.0, min_confidence),
            limit=min(limit, 1000),
            cameras=cameras if cameras else None,
            labels=labels,
        )
        return {"ok": True, "events": events}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.post("/api/normalcy/correct")
async def api_normalcy_correct(body: _CorrectionBody, u=Depends(_require_admin)):
    """Record an admin correction for a normalcy event (normal/suspicious verdict)."""
    if body.verdict not in ("normal", "suspicious"):
        raise HTTPException(400, "verdict must be 'normal' or 'suspicious'")
    try:
        username = u.get("username", "admin") if isinstance(u, dict) else "admin"
        _add_correction(
            body.source_key,
            body.hour_bucket,
            body.weekday,
            body.verdict,
            body.comment,
            by=username,
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.delete("/api/normalcy/correct")
async def api_normalcy_uncorrect(body: _CorrectionKeyBody, u=Depends(_require_admin)):
    """Remove an admin correction (undo), returning the event to pending review."""
    try:
        removed = _delete_correction(body.source_key, body.hour_bucket, body.weekday)
        return {"ok": True, "removed": removed}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.get("/api/normalcy/snapshot/{event_id}")
async def api_normalcy_snapshot(event_id: str, u=Depends(_require_admin)):
    """Proxy a Frigate event snapshot to the frontend."""
    try:
        svc_cfg = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text()) or {}
        frigate_url = (svc_cfg.get("frigate") or {}).get("url", "http://localhost:5000")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{frigate_url}/api/events/{event_id}/snapshot.jpg",
                params={"crop": 1, "quality": 70},
            )
        if r.status_code == 404:
            raise HTTPException(404, "snapshot not found")
        if r.status_code != 200:
            raise HTTPException(502, "frigate error")
        return Response(content=r.content, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)) from e
