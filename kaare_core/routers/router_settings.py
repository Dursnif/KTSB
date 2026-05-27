import asyncio
import subprocess
from pathlib import Path
import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException
import kaare_core.app_state as app_state
from kaare_core.users.auth import require_auth as _require_auth, require_admin as _require_admin
from kaare_core.config import get_model, reload_capability_services

router = APIRouter()

_SETTINGS_PATH   = Path("/kaare/configs/settings.yaml")
_LLM_PATH        = Path("/kaare/configs/llm.yaml")
_MODELS_PATH     = Path("/kaare/configs/models.yaml")
_SERVICES_PATH   = Path("/kaare/configs/services.yaml")
_MQTT_ENV_PATH   = Path("/kaare/configs/mqtt.env")
_HA_TOKEN_PATH   = Path("/kaare/configs/ha_token.env")
_KARE_HA_PATH    = Path("/kaare/configs/kare_ha.env")
_BRAVE_ENV_PATH  = Path("/kaare/configs/kare_llm.env")
_NVIDIA_ENV_PATH = Path("/kaare/configs/nvidia.env")
_LLM_KEYS_PATH   = Path("/kaare/configs/llm_keys.env")
_ALIASES_PATH    = Path("/kaare/configs/aliases.yaml")
_NODES_PATH      = Path("/kaare/configs/nodes.yaml")
_PLEX_ENV_PATH   = Path("/kaare/configs/plex.env")
_TRUSTED_PATH    = Path("/kaare/configs/trusted_sources.yaml")
_WEATHER_ENV_PATH     = Path("/kaare/configs/weather.env")
_LEDER_PRESET_DIR     = Path("/kaare/configs/meeting_leder")
_CAPABILITY_MAP_PATH  = Path("/kaare/capability_map.yaml")
_PERSONALITY_CORE_CUSTOM_PATH   = Path("/kaare/configs/personality_core_custom.md")
_PERSONALITY_CORE_STANDARD_PATH = Path("/kaare/configs/personality_core.md")
_MEETING_ROLE_PS_CUSTOM = Path("/kaare/configs/meeting_role_pettersmart_custom.md")
_MEETING_ROLE_MK_CUSTOM = Path("/kaare/configs/meeting_role_miss_kare_custom.md")
_PS_AGENT_DIR = Path("/kaare/kaare_core/agents/pettersmart")
_MK_AGENT_DIR = Path("/kaare/kaare_core/agents/miss_kare")

_EDITABLE_LLM_ROLES   = {"default", "miss_kare", "pettersmart", "library", "fallback", "cloud", "image_edit"}
_AGENT_TOGGLEABLE     = {"miss_kare", "pettersmart", "library", "fallback", "cloud", "image_edit"}
_OLLAMA_OPTION_KEYS   = {"num_ctx", "num_predict", "temperature", "presence_penalty", "top_k", "top_p"}
_CLOUD_OPTION_KEYS    = {"temperature", "top_p", "max_tokens"}
_IMAGE_OPTION_KEYS    = {"num_inference_steps", "guidance_scale", "true_cfg_scale", "response_format", "enabled"}
_VLLM_OPTION_KEYS     = {"max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty"}
_VLLM_DOCKER_KEYS     = {"max_model_len", "kv_cache_dtype", "gpu_memory_utilization", "max_num_seqs", "gpu_id"}
_OLLAMA_ENV_KEYS      = {"num_threads", "num_parallel", "max_loaded_models", "flash_attention", "kv_cache_type"}

_WEATHER_PROVIDERS              = {"met.no", "open-meteo", "openweathermap", "weatherapi"}
_VALID_LEDER_DEV_PRESETS        = {"standard", "streng", "utforskende", "egendefinert"}
_VALID_LEDER_REFLECTION_PRESETS = {"standard", "analytisk", "utfordrende", "egendefinert"}
_VALID_CONTRIBUTOR_MODES        = {"all", "selected", "admin_only"}
_VALID_PERSONALITY_MODES        = ["minimal", "letvekt", "standard", "full", "komplett", "egendefinert"]
_VALID_PS_ROLES                 = {"undersøker", "kritiker", "analytiker", "egendefinert"}
_VALID_MK_ROLES                 = {"empatisk", "analytiker", "utfordrende", "egendefinert"}

_PERSONALITY_CORE_BY_LANG = {
    "nb": Path("/kaare/configs/personality_core.md"),
    "en": Path("/kaare/configs/personality_core_en.md"),
    "de": Path("/kaare/configs/personality_core_de.md"),
}


def _reload_agent_enabled() -> None:
    try:
        data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
        for r in _AGENT_TOGGLEABLE:
            app_state._AGENT_ENABLED[r] = bool(data.get(r, {}).get("enabled", True))
    except Exception:
        pass


def _read_env_key(path: Path, key: str) -> str:
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


def _get_personality_default(lang: str) -> str:
    path = _PERSONALITY_CORE_BY_LANG.get(lang, _PERSONALITY_CORE_BY_LANG["en"])
    if path.exists():
        return path.read_text(encoding="utf-8")
    return _PERSONALITY_CORE_STANDARD_PATH.read_text(encoding="utf-8") if _PERSONALITY_CORE_STANDARD_PATH.exists() else ""


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


def _load_reflection_config() -> tuple[bool, int]:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text()).get("kare_reflection", {})
        return bool(cfg.get("enabled", False)), int(cfg.get("interval_seconds", 600))
    except Exception:
        return False, 600


_reload_agent_enabled()


@router.get("/api/settings")
def api_get_settings():
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    loc = data.get("location") or data.get("lokasjon", {})
    return {
        "location": loc,
        "log_level": data.get("log_level", "INFO"),
    }


@router.put("/api/settings/location")
async def api_put_location(payload: dict):
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


@router.get("/api/settings/language")
async def api_get_language():
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    return {
        "language": data.get("language", "nb"),
        "kare_language": data.get("kare_language") or data.get("language", "nb"),
    }


@router.put("/api/settings/language")
async def api_put_language(payload: dict):
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


@router.get("/api/settings/llm")
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
            # Non-Ollama text LLM: top-level temperature/top_p/max_tokens
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


@router.put("/api/settings/llm/{role}")
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
    if "model" in payload and payload["model"]:
        mdata = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
        mdata[s.get("model_role", role)] = payload["model"]
        _MODELS_PATH.write_text(yaml.dump(mdata, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    if role == "image_edit" and "model_edit" in payload and payload["model_edit"]:
        mdata = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
        mdata[s.get("model_role_edit", "image_edit_edit")] = payload["model_edit"]
        _MODELS_PATH.write_text(yaml.dump(mdata, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


@router.post("/api/settings/llm/{role}/restart_docker")
async def api_restart_vllm_docker(role: str, _u=Depends(_require_admin)):
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


@router.post("/api/settings/llm/discover_ollama")
async def api_discover_ollama(_u=Depends(_require_admin)):
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
    found: list[dict] = []

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


@router.get("/api/settings/models")
def api_get_models(_u=Depends(_require_auth)):
    return yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}


@router.put("/api/settings/models")
async def api_put_models(payload: dict, _u=Depends(_require_admin)):
    allowed = {"kare", "miss_kare", "library", "embed", "cloud"}
    bad = set(payload) - allowed
    if bad:
        raise HTTPException(400, f"Unknown model roles: {bad}")
    data = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
    data.update(payload)
    _MODELS_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True, "models": data}


@router.get("/api/settings/services")
def api_get_services(_u=Depends(_require_auth)):
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    ha = data.get("home_assistant", {})
    mqtt = data.get("mqtt", {})
    frigate = data.get("frigate", {})
    plex = data.get("media", {}).get("plex", {})
    emb = data.get("embedding", {})
    voice_stt = data.get("voice", {}).get("stt", {})
    cap = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
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


@router.put("/api/settings/services/ha")
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


def _write_mqtt_env_creds(username: str, password: str) -> None:
    lines = []
    if _MQTT_ENV_PATH.exists():
        for line in _MQTT_ENV_PATH.read_text(encoding="utf-8").splitlines():
            k = line.split("=")[0].strip()
            if k not in ("MQTT_USER", "MQTT_PASSWORD"):
                lines.append(line)
    lines.append(f"MQTT_USER={username}")
    lines.append(f"MQTT_PASSWORD={password}")
    _MQTT_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.put("/api/settings/services/mqtt")
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
        _write_mqtt_env_creds(
            payload.get("username", existing.get("MQTT_USER", "")),
            payload.get("password", existing.get("MQTT_PASSWORD", "")),
        )
    return {"ok": True}


@router.put("/api/settings/services/frigate")
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
            cap = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
            cap.setdefault("domains", {}).setdefault("frigate", {})["enabled"] = bool(payload["enabled"])
            _CAPABILITY_MAP_PATH.write_text(
                yaml.dump(cap, allow_unicode=True, default_flow_style=False), encoding="utf-8"
            )
            app_state.CAPABILITY_MAP = cap
        except Exception:
            pass
    return {"ok": True}


@router.put("/api/settings/services/plex")
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


@router.put("/api/settings/services/embedding")
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


@router.put("/api/settings/services/memory-embed")
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


@router.put("/api/settings/services/voice")
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


@router.get("/api/settings/ha-token")
def api_get_ha_token(_u=Depends(_require_auth)):
    tok = _read_env_key(_HA_TOKEN_PATH, "HA_TOKEN")
    return {"is_set": bool(tok), "masked": _mask_token(tok)}


@router.put("/api/settings/ha-token")
async def api_put_ha_token(payload: dict, _u=Depends(_require_admin)):
    tok = payload.get("token", "").strip()
    if not tok:
        raise HTTPException(400, "Token cannot be empty")
    _write_env_key(_HA_TOKEN_PATH, "HA_TOKEN", tok)
    return {"ok": True}


@router.get("/api/settings/ha-bridge")
def api_get_ha_bridge(_u=Depends(_require_auth)):
    return {
        "log_url": _read_env_key(_KARE_HA_PATH, "KARE_LOG_URL"),
        "timeout": _read_env_key(_KARE_HA_PATH, "KARE_HA_TIMEOUT"),
        "allowed_actions": _read_env_key(_KARE_HA_PATH, "KARE_ALLOWED_ACTIONS"),
    }


@router.put("/api/settings/ha-bridge")
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


@router.get("/api/settings/secrets")
def api_get_secrets(_u=Depends(_require_auth)):
    brave = _read_env_key(_BRAVE_ENV_PATH, "BRAVE_API_KEY")
    nvidia = _read_env_key(_NVIDIA_ENV_PATH, "NVIDIA_API_KEY")
    return {
        "brave":  {"is_set": bool(brave),  "masked": _mask_token(brave)},
        "nvidia": {"is_set": bool(nvidia), "masked": _mask_token(nvidia)},
    }


@router.put("/api/settings/secrets/{name}")
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


@router.get("/api/settings/weather")
async def api_get_weather(_u=Depends(_require_admin)):
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


@router.put("/api/settings/weather")
async def api_put_weather(payload: dict, _u=Depends(_require_admin)):
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


@router.get("/api/settings/websearch")
async def api_get_websearch(_u=Depends(_require_admin)):
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


@router.put("/api/settings/websearch")
async def api_put_websearch(payload: dict, _u=Depends(_require_admin)):
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


@router.get("/api/settings/reflection")
async def api_get_reflection(_u=Depends(_require_admin)):
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


@router.put("/api/settings/reflection")
async def api_put_reflection(payload: dict, _u=Depends(_require_admin)):
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
        app_state._REFLECTION_ENABLED, app_state._JANG_INTERVAL_S = _load_reflection_config()
    except Exception as e:
        raise HTTPException(500, f"Could not write settings: {e}")
    return {"ok": True}


@router.get("/api/settings/dev-meeting")
async def api_get_dev_meeting(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    cfg = data.get("dev_meeting", {})
    preset = cfg.get("leder_preset", "standard")
    default_preset = _leder_preset_text("dev", preset if preset != "egendefinert" else "standard")
    custom_text = _leder_preset_text("dev", "egendefinert")
    return {
        "max_rounds":           int(cfg.get("max_rounds", 6)),
        "max_invest_rounds":    int(cfg.get("max_invest_rounds", 5)),
        "kare_max_tokens":      int(cfg.get("kare_max_tokens", 2500)),
        "kare_invest_tokens":   int(cfg.get("kare_invest_tokens", 1000)),
        "leder_preset":         preset,
        "leder_preset_default": default_preset,
        "leder_preset_custom":  custom_text,
    }


@router.put("/api/settings/dev-meeting")
async def api_put_dev_meeting(payload: dict, _u=Depends(_require_admin)):
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


@router.get("/api/settings/kare")
async def api_get_kare_settings(_u=Depends(_require_admin)):
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


@router.put("/api/settings/kare")
async def api_put_kare_settings(payload: dict, _u=Depends(_require_admin)):
    ps = payload.get("personality_self")
    contributor_mode = None
    allowed: list = []
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


@router.get("/api/settings/trusted-sources")
async def api_get_trusted_sources(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        return data.get("sources", {})
    except Exception as e:
        raise HTTPException(500, f"Could not read trusted_sources.yaml: {e}")


@router.put("/api/settings/trusted-sources")
async def api_put_trusted_sources(payload: dict, _u=Depends(_require_admin)):
    for cat_key, entries in payload.items():
        if not isinstance(entries, list):
            raise HTTPException(400, f"Category '{cat_key}' must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or "domain" not in entry:
                raise HTTPException(400, "Each entry must have a 'domain' field")
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


@router.get("/api/settings/plex-token")
def api_get_plex_token(_u=Depends(_require_auth)):
    tok = _read_env_key(_PLEX_ENV_PATH, "PLEX_TOKEN")
    return {"is_set": bool(tok), "masked": _mask_token(tok)}


@router.put("/api/settings/plex-token")
async def api_put_plex_token(payload: dict, _u=Depends(_require_admin)):
    tok = payload.get("token", "").strip()
    if not tok:
        raise HTTPException(400, "Token cannot be empty")
    _write_env_key(_PLEX_ENV_PATH, "PLEX_TOKEN", tok)
    return {"ok": True, "restart_required": True}


@router.get("/api/settings/aliases")
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


@router.put("/api/settings/aliases")
async def api_put_aliases(payload: dict, _u=Depends(_require_admin)):
    allowed = {"aliases", "rooms", "room_entities"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown section in payload")
    data = yaml.safe_load(_ALIASES_PATH.read_text(encoding="utf-8")) or {}
    for section in allowed:
        if section in payload:
            data[section] = payload[section]
    _ALIASES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    app_state.ALIASES = data.get("aliases", {}) or {}
    return {"ok": True}


@router.get("/api/settings/nodes")
def api_get_nodes(_u=Depends(_require_admin)):
    data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
    return {"nodes": data.get("nodes", {})}


@router.put("/api/settings/nodes")
async def api_put_nodes(payload: dict, _u=Depends(_require_admin)):
    if "nodes" not in payload:
        raise HTTPException(400, "Missing 'nodes' key in payload")
    data = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8")) or {}
    data["nodes"] = payload["nodes"]
    _NODES_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return {"ok": True}


@router.get("/api/settings/capabilities")
def api_get_capabilities(_u=Depends(_require_admin)):
    data = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
    return {
        "domains": data.get("domains", {}),
        "distribution_profile": data.get("distribution_profile", ""),
        "services": data.get("services", {}),
    }


@router.put("/api/settings/capabilities")
async def api_put_capabilities(payload: dict, _u=Depends(_require_admin)):
    allowed = {"domains", "distribution_profile", "services"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown key in payload")
    data = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
    if "domains" in payload:
        data["domains"] = payload["domains"]
    if "distribution_profile" in payload:
        data["distribution_profile"] = payload["distribution_profile"]
    if "services" in payload:
        data["services"] = payload["services"]
    _CAPABILITY_MAP_PATH.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    app_state.CAPABILITY_MAP = data
    reload_capability_services()
    return {"ok": True}


@router.get("/api/onboarding/status")
def api_onboarding_status(_u=Depends(_require_admin)):
    from kaare_core.users.store import list_users as _list_users
    steps = []
    try:
        settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        lok = settings.get("lokasjon", settings)
        lat = lok.get("lat", 0) or 0
        lon = lok.get("lon", 0) or 0
        location_ok = float(lat) != 0.0 and float(lon) != 0.0
    except Exception:
        location_ok = False
    steps.append({"id": "location", "label": "Lokasjon satt", "complete": location_ok})
    try:
        users = _list_users()
        has_user = any(u.get("username") != "admin" for u in users)
    except Exception:
        has_user = False
    steps.append({"id": "user", "label": "Bruker opprettet", "complete": has_user})
    try:
        cap = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
        profile_ok = bool(cap.get("distribution_profile", ""))
    except Exception:
        profile_ok = False
    steps.append({"id": "distribution", "label": "Distribusjonsprofil valgt", "complete": profile_ok})
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        ha_set = bool(svc.get("home_assistant", {}).get("url", ""))
        frigate_set = bool(svc.get("frigate", {}).get("url", ""))
        plex_set = bool(svc.get("media", {}).get("plex", {}).get("url", ""))
        mqtt_set = bool(svc.get("mqtt", {}).get("host", ""))
    except Exception:
        ha_set = frigate_set = plex_set = mqtt_set = False
    optional_hints = [
        {"id": "ha",      "label": "Home Assistant URL", "set": ha_set},
        {"id": "mqtt",    "label": "MQTT Broker",         "set": mqtt_set},
        {"id": "frigate", "label": "Frigate URL",          "set": frigate_set},
        {"id": "plex",    "label": "Plex Server URL",      "set": plex_set},
    ]
    complete = all(s["complete"] for s in steps)
    return {"complete": complete, "steps": steps, "optional_hints": optional_hints}


@router.post("/api/settings/test-connection")
async def api_test_connection(payload: dict, _u=Depends(_require_auth)):
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            return {"ok": r.status_code < 500, "status_code": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/admin/tool_permissions")
async def api_get_tool_permissions(_u=Depends(_require_admin)):
    from kaare_core.config import get_tool_permissions
    return get_tool_permissions()


@router.put("/api/admin/tool_permissions")
async def api_put_tool_permissions(data: dict, _u=Depends(_require_admin)):
    from kaare_core.config import save_tool_permissions
    try:
        save_tool_permissions(data)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/settings/agent_tools")
async def api_get_agent_tools(_u=Depends(_require_admin)):
    from kaare_core.config import get_tool_permissions
    return get_tool_permissions().get("agent_tools", {})


@router.put("/api/settings/agent_tools")
async def api_put_agent_tools(data: dict, _u=Depends(_require_admin)):
    from kaare_core.config import get_tool_permissions, save_tool_permissions
    try:
        current = get_tool_permissions()
        current["agent_tools"] = data
        save_tool_permissions(current)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/settings/meeting-roles")
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
        "pettersmart":         ps_role,
        "pettersmart_custom":  ps_custom,
        "pettersmart_default": ps_default,
        "miss_kare":           mk_role,
        "miss_kare_custom":    mk_custom,
        "miss_kare_default":   mk_default,
    }


@router.put("/api/settings/meeting-roles")
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
