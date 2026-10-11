import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import re as _re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from pathlib import Path as _Path

import httpx
import httpx as _httpx
import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

import kaare_core.app_state as app_state
from adapters import llm_adapter as _llm
from adapters import lovdata_adapter
from adapters.display_adapter import send_display
from adapters.llm_adapter import reload_config as _reload_llm
from adapters.llm_adapter import reload_config as _reload_llm_config
from adapters.web_fetch import (
    SOURCE_TYPES,
    TRUST_LEVELS,
    normalize_domain,
    normalize_entry,
    open_web_enabled,
)
from adapters.web_fetch import setting as web_fetch_setting
from adapters.web_search_adapter import load_websearch_config, paid_calls_today
from adapters.web_search_adapter import reload_config as reload_websearch_config
from kaare_core import config_store
from kaare_core.aio_files import append_text
from kaare_core.background import spawn
from kaare_core.client_ip import client_ip as _client_ip
from kaare_core.config import CAPABILITY_MAP_PATH as _CAPABILITY_MAP_PATH
from kaare_core.config import (
    DEFAULT_LOCAL_SUBNET,
    LOG_FLUSH_DEFAULT_SECONDS,
    LOG_FLUSH_MAX_SECONDS,
    POOL_ROLE_OVERRIDABLE,
    apply_agent_pool,
    get_local_tz,
    get_log_flush_seconds,
    get_memory_prompt_config,
    get_model,
    get_network_settings,
    get_service,
    get_ssh_nodes,
    get_tool_permissions,
    now_local,
    reload_capability_services,
    reload_config,
    save_tool_permissions,
)
from kaare_core.config import reload_config as reload_core_config
from kaare_core.dev_slot.config import get_dev_slot_config_from
from kaare_core.llm_model_check import check_ollama_role_models
from kaare_core.memory import relevance as _relevance
from kaare_core.service_auth import service_headers
from kaare_core.tools.i18n import get_lang as _get_lang
from kaare_core.tools.source_confidence import caution as source_caution
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.users.auth import require_auth as _require_auth
from kaare_core.users.store import list_users as _list_users
from kaare_core.voice.media_host import detect_lan_address, in_container
from kaare_core.voice.tts_models import PIPER_PRESETS, piper_dir, piper_hf_paths, piper_local_path
from kaare_night_runner import DEFAULT_START_TIME, load_start_time, parse_start_time, read_last_slot

router = APIRouter()
logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_LLM_PATH = Path("/kaare/configs/llm.yaml")
_MODELS_PATH = Path("/kaare/configs/models.yaml")
_SERVICES_PATH = Path("/kaare/configs/services.yaml")
_MQTT_ENV_PATH = Path("/kaare/configs/mqtt.env")
_HA_TOKEN_PATH = Path("/kaare/configs/ha_token.env")
_KARE_HA_PATH = Path("/kaare/configs/kare_ha.env")
_BRAVE_ENV_PATH = Path("/kaare/configs/kare_llm.env")
_NVIDIA_ENV_PATH = Path("/kaare/configs/nvidia.env")
_LLM_KEYS_PATH = Path("/kaare/configs/llm_keys.env")
_ALIASES_PATH = Path("/kaare/configs/aliases.yaml")
_NODES_PATH = Path("/kaare/configs/nodes.yaml")
_PLEX_ENV_PATH = Path("/kaare/configs/plex.env")
_TRUSTED_PATH = Path("/kaare/configs/trusted_sources.yaml")
_WEATHER_ENV_PATH = Path("/kaare/configs/weather.env")
_LEDER_PRESET_DIR = Path("/kaare/configs/meeting_leder")
_PERSONALITY_CORE_CUSTOM_PATH = Path("/kaare/configs/personality_core_custom.md")
_PERSONALITY_CORE_STANDARD_PATH = Path("/kaare/configs/personality_core.md")
_MEETING_ROLE_PS_CUSTOM = Path("/kaare/configs/meeting_role_mechanic_custom.md")
_PS_AGENT_DIR = Path("/kaare/kaare_core/agents/mechanic")

_EDITABLE_LLM_ROLES = {
    "default",
    "miss_kare",
    "mechanic",
    "library",
    "fallback",
    "cloud",
    "image_edit",
    "reason_freely",
}
_AGENT_TOGGLEABLE = {"miss_kare", "mechanic", "library", "fallback", "cloud", "image_edit"}
_OLLAMA_OPTION_KEYS = {
    "num_ctx",
    "num_predict",
    "temperature",
    "presence_penalty",
    "top_k",
    "top_p",
}
_CLOUD_OPTION_KEYS = {"temperature", "top_p", "max_tokens"}
_IMAGE_OPTION_KEYS = {
    "num_inference_steps",
    "guidance_scale",
    "true_cfg_scale",
    "response_format",
    "enabled",
}
_VLLM_OPTION_KEYS = {
    "max_tokens",
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "top_k",
    "min_p",
}
_VLLM_DOCKER_KEYS = {
    "max_model_len",
    "kv_cache_dtype",
    "gpu_memory_utilization",
    "max_num_seqs",
    "gpu_id",
    "enable_prompt_tokens_details",
}
_OLLAMA_ENV_KEYS = {
    "num_threads",
    "num_parallel",
    "max_loaded_models",
    "flash_attention",
    "kv_cache_type",
}

_WEATHER_PROVIDERS = {"met.no", "open-meteo", "openweathermap", "weatherapi", "pirateweather"}
_VALID_LEDER_DEV_PRESETS = {"standard", "streng", "utforskende", "egendefinert"}
_VALID_LEDER_REFLECTION_PRESETS = {"standard", "analytisk", "utfordrende", "egendefinert"}
_VALID_CONTRIBUTOR_MODES = {"all", "selected", "admin_only"}
_VALID_PERSONALITY_MODES = ["minimal", "letvekt", "standard", "full", "komplett", "egendefinert"]
_VALID_PS_ROLES = {"undersøker", "kritiker", "analytiker", "egendefinert"}

_PERSONALITY_CORE_BY_LANG = {
    "nb": Path("/kaare/configs/personality_core.md"),
    "en": Path("/kaare/configs/personality_core_en.md"),
    "de": Path("/kaare/configs/personality_core_de.md"),
}

# In-memory warmup state per role (reset on restart, which is acceptable)
_warmup_status: dict[str, dict] = {}
_warmup_tasks: dict[str, "asyncio.Task[None]"] = {}

# vLLM docker restart state
_vllm_restart_status: dict[str, dict] = {}
_vllm_restart_tasks: dict[str, "asyncio.Task[None]"] = {}

# Maps llm.yaml vllm_docker keys → CLI flags passed to vllm serve
_VLLM_ARG_MAP = {
    "max_model_len": "--max-model-len",
    "kv_cache_dtype": "--kv-cache-dtype",
    "gpu_memory_utilization": "--gpu-memory-utilization",
    "max_num_seqs": "--max-num-seqs",
}

# Boolean vllm_docker keys → CLI flags with no value (present = on, absent = off)
_VLLM_BOOL_FLAG_MAP = {
    "enable_prompt_tokens_details": "--enable-prompt-tokens-details",
}

# Readiness wait after re-creating the vLLM container (model load + CUDA graph capture).
# Overridable per role via llm.yaml vllm_docker.startup_timeout.
_VLLM_STARTUP_TIMEOUT_S = 900
_VLLM_READY_POLL_S = 5
_DOCKER_DEFAULT_SHM = 67108864  # 64 MB — docker run's default --shm-size


def _docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _container_state(container: str) -> dict:
    """Return docker inspect State (+ RestartCount) for a container, or {} if it doesn't exist."""
    r = _docker(["inspect", container], timeout=10)
    if r.returncode != 0:
        return {}
    info = json.loads(r.stdout)[0]
    state = dict(info.get("State") or {})
    state["RestartCount"] = info.get("RestartCount", 0)
    return state


async def _wait_vllm_ready(container: str, base_url: str, timeout_s: int) -> tuple[bool, str]:
    """Poll until vLLM answers /v1/models. Fails fast if the container exits or crash-loops."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    url = base_url.rstrip("/") + "/v1/models"
    initial = await loop.run_in_executor(None, _container_state, container)
    base_restarts = initial.get("RestartCount", 0)
    async with httpx.AsyncClient(timeout=5.0) as client:
        while loop.time() < deadline:
            state = await loop.run_in_executor(None, _container_state, container)
            if not state:
                return False, "container disappeared"
            if (
                state.get("Restarting")
                or state.get("RestartCount", 0) > base_restarts
                or not state.get("Running")
            ):
                logs = await loop.run_in_executor(
                    None, lambda: _docker(["logs", "--tail", "15", container], timeout=10)
                )
                tail = (logs.stdout + logs.stderr).strip()[-600:]
                return False, f"container exited (code {state.get('ExitCode')}): {tail}"
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return True, ""
            except httpx.HTTPError:
                pass
            await asyncio.sleep(_VLLM_READY_POLL_S)
    return False, f"not ready after {timeout_s}s"


def _build_vllm_run_cmd(
    info: dict, container: str, vllm_cfg: dict, image_env: list[str]
) -> list[str]:
    """Build the `docker run` argv that re-creates a vLLM container from its inspect data,
    with the dynamic flags replaced by the values in llm.yaml vllm_docker.

    image_env is the image's own Config.Env — only variables added on top of it are carried
    over, so an image upgrade isn't overridden by stale PATH/CUDA values from the old one."""
    image = info["Config"]["Image"]
    old_cmd = list(info["Config"].get("Cmd") or [])
    # The effective entrypoint must be carried over: images like vllm/vllm-openai ship
    # ["vllm","serve"], while containers created with --entrypoint vllm keep "serve" in Cmd.
    # Dropping it gave "vllm serve serve …" and a crash loop (BUG-log 2026-10-04).
    entrypoint = list(info["Config"].get("Entrypoint") or [])
    env_vars = [e for e in (info["Config"].get("Env") or []) if e not in set(image_env)]
    host_cfg = info["HostConfig"]

    # Replace dynamic args in the command with values from vllm_cfg
    new_cmd = list(old_cmd)
    replaced: set[str] = set()
    i = 0
    while i < len(new_cmd):
        for cfg_key, cli_flag in _VLLM_ARG_MAP.items():
            if new_cmd[i] == cli_flag and i + 1 < len(new_cmd) and cfg_key in vllm_cfg:
                new_cmd[i + 1] = str(vllm_cfg[cfg_key])
                replaced.add(cli_flag)
        i += 1
    # Append any flags that were not already in the original command
    for cfg_key, cli_flag in _VLLM_ARG_MAP.items():
        if cli_flag not in replaced and cfg_key in vllm_cfg:
            new_cmd.extend([cli_flag, str(vllm_cfg[cfg_key])])

    # Boolean flags (present/absent, no value) — e.g. --enable-prompt-tokens-details
    for cfg_key, cli_flag in _VLLM_BOOL_FLAG_MAP.items():
        if cfg_key not in vllm_cfg:
            continue
        if vllm_cfg[cfg_key] and cli_flag not in new_cmd:
            new_cmd.append(cli_flag)
        elif not vllm_cfg[cfg_key] and cli_flag in new_cmd:
            new_cmd.remove(cli_flag)

    # Docker run argv: same mounts/ports/env/entrypoint as the old container
    gpu_id = vllm_cfg.get("gpu_id", 0)
    docker_run = [
        "docker",
        "run",
        "-d",
        "--name",
        container,
        "--restart",
        "unless-stopped",
        "--gpus",
        f"device={gpu_id}",
    ]
    for bind in host_cfg.get("Binds") or []:
        docker_run.extend(["-v", bind])
    for cport, bindings in (host_cfg.get("PortBindings") or {}).items():
        for b in bindings or []:
            host_port = b.get("HostPort", "")
            host_ip = b.get("HostIp", "")
            # keep the bind address (e.g. 127.0.0.1) — dropping it re-published the model
            # port to the whole LAN on every GUI restart (P120)
            prefix = f"{host_ip}:" if host_ip else ""
            docker_run.extend(["-p", f"{prefix}{host_port}:{cport.split('/')[0]}"])
    for env in env_vars:
        docker_run.extend(["-e", env])
    if host_cfg.get("IpcMode") == "host":
        docker_run.extend(["--ipc", "host"])
    shm = host_cfg.get("ShmSize") or 0
    if shm and shm != _DOCKER_DEFAULT_SHM:
        docker_run.extend(["--shm-size", str(shm)])
    if entrypoint:
        docker_run.extend(["--entrypoint", entrypoint[0]])
    docker_run.append(image)
    docker_run.extend(entrypoint[1:])
    docker_run.extend(new_cmd)
    return docker_run


async def _run_vllm_restart(role: str, container: str, vllm_cfg: dict, base_url: str) -> None:
    """Re-create a vLLM Docker container with updated args from llm.yaml.

    The old container is parked as '<name>-prev' (not removed) until the new one answers
    /v1/models; if it never does, the new one is removed and the old one restored.
    """
    loop = asyncio.get_running_loop()
    prev_name = f"{container}-prev"
    print(f"[vLLM restart] task started for {container}")
    _vllm_restart_status[role] = {"status": "inspecting"}
    try:
        # 1. Inspect current container to get static args, volumes, ports, image
        inspect_raw = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "inspect", container],
                capture_output=True,
                text=True,
                timeout=10,
            ),
        )
        print(f"[vLLM restart] inspect returncode={inspect_raw.returncode}")
        if inspect_raw.returncode != 0:
            detail = inspect_raw.stderr.strip()[:300]
            print(f"[vLLM restart] inspect failed: {detail}")
            _vllm_restart_status[role] = {
                "status": "error",
                "detail": f"Container '{container}' not found: {detail}",
            }
            return

        if await loop.run_in_executor(None, _container_state, prev_name):
            _vllm_restart_status[role] = {
                "status": "error",
                "detail": f"'{prev_name}' already exists from an earlier restart — remove or rename it first",
            }
            return

        info = json.loads(inspect_raw.stdout)[0]
        img_raw = await loop.run_in_executor(
            None, lambda: _docker(["image", "inspect", info["Image"]], timeout=10)
        )
        image_env = (
            (json.loads(img_raw.stdout)[0]["Config"].get("Env") or [])
            if img_raw.returncode == 0
            else []
        )
        docker_run = _build_vllm_run_cmd(info, container, vllm_cfg, image_env)
        print(f"[vLLM restart] docker run cmd: {' '.join(docker_run)}")

        # 2. Stop the old container and park it as <name>-prev (kept for rollback)
        print(f"[vLLM restart] stopping {container}")
        _vllm_restart_status[role] = {"status": "stopping"}
        await loop.run_in_executor(
            None, lambda: _docker(["stop", "-t", "30", container], timeout=60)
        )
        renamed = await loop.run_in_executor(
            None, lambda: _docker(["rename", container, prev_name], timeout=10)
        )
        if renamed.returncode != 0:
            detail = renamed.stderr.strip()[:300]
            print(f"[vLLM restart] rename to {prev_name} failed: {detail}")
            await loop.run_in_executor(None, lambda: _docker(["start", container], timeout=30))
            _vllm_restart_status[role] = {
                "status": "error",
                "detail": f"rename failed, old container restarted: {detail}",
            }
            return
        print(f"[vLLM restart] parked old container as {prev_name}")

        # 3. Start the new container and wait until it actually serves requests
        _vllm_restart_status[role] = {"status": "starting"}
        result = await loop.run_in_executor(
            None, lambda: subprocess.run(docker_run, capture_output=True, text=True, timeout=30)
        )
        if result.returncode != 0:
            ok, detail = False, f"docker run failed: {result.stderr.strip()[:300]}"
        else:
            _vllm_restart_status[role] = {"status": "loading"}
            timeout_s = int(vllm_cfg.get("startup_timeout", _VLLM_STARTUP_TIMEOUT_S))
            ok, detail = await _wait_vllm_ready(container, base_url, timeout_s)

        if ok:
            await loop.run_in_executor(None, lambda: _docker(["rm", prev_name], timeout=30))
            print(f"[vLLM restart] {container} ready, removed {prev_name}")
            _vllm_restart_status[role] = {"status": "ready"}
            return

        # 4. Rollback: drop the new container (created by this task) and restore the old one
        print(f"[vLLM restart] new {container} failed ({detail}) — rolling back to {prev_name}")
        _vllm_restart_status[role] = {"status": "rolling_back", "detail": detail}
        await loop.run_in_executor(None, lambda: _docker(["rm", "-f", container], timeout=60))
        await loop.run_in_executor(
            None, lambda: _docker(["rename", prev_name, container], timeout=10)
        )
        await loop.run_in_executor(None, lambda: _docker(["start", container], timeout=30))
        restored, _ = await _wait_vllm_ready(container, base_url, _VLLM_STARTUP_TIMEOUT_S)
        print(
            f"[vLLM restart] rollback of {container} {'ready' if restored else 'did NOT come up'}"
        )
        _vllm_restart_status[role] = {
            "status": "error",
            "detail": detail,
            "rolled_back": True,
            "restored": restored,
        }
    except Exception as exc:
        print(f"[vLLM restart] exception for {container}: {exc}")
        _vllm_restart_status[role] = {"status": "error", "detail": str(exc)[:300]}


def _reload_llm_and_core() -> None:
    """Re-read llm.yaml + models.yaml in the running process so a GUI save applies at once (rule 25).
    Call it after the LAST write of a request — reloading before models.yaml is written left the old
    model in use until a restart (DT03 R2 2.9)."""
    try:
        reload_core_config()
        _reload_llm_config()
    except Exception as e:
        logger.warning("config reload after LLM save failed: %s", e)


def _model_used_elsewhere(
    llm_data: dict, models_data: dict, role: str, base_url: str, model: str
) -> bool:
    """True when another enabled role on the same Ollama instance still runs `model` (after the
    models.yaml update). Roles that share_with another role, or share a model_role key, resolve
    through that role — so roles that follow this role's change do not count. Unloading a model
    another role uses evicted the shared 9B (Docker: the main agent's model) — DT03 R2 2.9."""

    def _norm(u: str) -> str:
        return (u or "").strip().rstrip("/").lower()

    target_url = _norm(base_url)
    for name, cfg in llm_data.items():
        if name == role or not isinstance(cfg, dict) or cfg.get("enabled", True) is False:
            continue
        src = llm_data.get(cfg["share_with"], {}) if cfg.get("share_with") else cfg
        src_name = cfg.get("share_with") or name
        if not isinstance(src, dict) or _norm(src.get("base_url", "")) != target_url:
            continue
        if models_data.get(src.get("model_role", src_name)) == model:
            return True
    return False


async def _unload_ollama_model(base_url: str, model: str) -> None:
    """Evict a model from VRAM (keep_alive=0). Fire-and-forget — never raises."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            await c.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "",
                    "keep_alive": 0,
                },
            )
        print(f"[unload] unloaded old model {model} from {base_url}")
    except Exception as e:
        print(f"[unload] failed to unload old model {model} from {base_url}: {e}")


async def _run_ollama_warmup(
    role: str, base_url: str, model: str, num_ctx: int, keep_warm: bool = True
) -> None:
    """Load an Ollama model into VRAM after a config change or pull.

    keep_warm=True  → send keep_alive=-1 (model stays in VRAM indefinitely)
    keep_warm=False → omit keep_alive (Ollama default: 5-min idle TTL)
    """
    _warmup_status[role] = {"status": "waiting", "model": model}
    ready = False
    for _ in range(40):  # max 120 s (40 × 3 s)
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{base_url}/api/tags")
                if r.status_code == 200:
                    ready = True
                    break
        except Exception:
            pass
        await asyncio.sleep(3)
    if not ready:
        _warmup_status[role] = {"status": "error", "model": model}
        return
    _warmup_status[role] = {"status": "loading", "model": model}
    try:
        payload: dict = {
            "model": model,
            "prompt": "",
            "stream": False,
            "options": {"num_ctx": num_ctx, "num_predict": 1},
        }
        if keep_warm:
            payload["keep_alive"] = -1
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(f"{base_url}/api/generate", json=payload)
            r.raise_for_status()
        # Check whether model actually loaded into VRAM or fell back to CPU
        vram_bytes = await _get_model_vram(base_url, model)
        if vram_bytes == 0:
            _warmup_status[role] = {"status": "warning_cpu", "model": model, "vram_bytes": 0}
        else:
            _warmup_status[role] = {"status": "done", "model": model, "vram_bytes": vram_bytes}
    except Exception as e:
        _warmup_status[role] = {"status": "error", "model": model, "detail": str(e)[:120]}


async def _get_model_vram(base_url: str, model: str) -> int:
    """Return size_vram bytes for a loaded Ollama model. 0 means CPU."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{base_url}/api/ps")
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    if m.get("name", "").startswith(model.split(":")[0]):
                        return m.get("size_vram", 0)
    except Exception:
        pass
    return 0


def _start_warmup_task(
    role: str, base_url: str, model: str, num_ctx: int, keep_warm: bool = True
) -> None:
    if role in _warmup_tasks and not _warmup_tasks[role].done():
        _warmup_tasks[role].cancel()
    _warmup_tasks[role] = asyncio.create_task(
        _run_ollama_warmup(role, base_url, model, num_ctx, keep_warm=keep_warm)
    )


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
    config_store.set_env_keys(path, {key: value}, f"gui:env:{key}")


def _mask_token(tok: str) -> str:
    return (tok[:8] + "..." + tok[-6:]) if len(tok) > 14 else ("***" if tok else "")


def _write_mqtt_env(username: str, password: str) -> None:
    with config_store.edit_text(_MQTT_ENV_PATH, "gui:mqtt-credentials", mode=0o640) as doc:
        lines = [
            line
            for line in doc.text.splitlines()
            if line.split("=")[0].strip() not in ("MQTT_USER", "MQTT_PASSWORD")
        ]
        lines.append(f"MQTT_USER={username}")
        lines.append(f"MQTT_PASSWORD={password}")
        doc.text = "\n".join(lines) + "\n"


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
    return (
        _PERSONALITY_CORE_STANDARD_PATH.read_text(encoding="utf-8")
        if _PERSONALITY_CORE_STANDARD_PATH.exists()
        else ""
    )


def _ps_preset_file(role: str, lang: str = "nb") -> Path:
    suffix = "" if lang == "nb" else f"_{lang}"
    if role in ("undersøker", "kritiker", "analytiker"):
        path = _PS_AGENT_DIR / f"personlighet_{role}{suffix}.md"
        if path.exists():
            return path
        return _PS_AGENT_DIR / f"personlighet_{role}.md"
    path = _PS_AGENT_DIR / f"personlighet{suffix}.md"
    if path.exists():
        return path
    return _PS_AGENT_DIR / "personlighet.md"


def _load_reflection_config() -> tuple[bool, int]:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text()).get("kare_reflection", {})
        return bool(cfg.get("enabled", False)), int(cfg.get("interval_seconds", 600))
    except Exception:
        return False, 600


_reload_agent_enabled()


@router.get("/api/settings")
def api_get_settings(_u=Depends(_require_admin)):
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
async def api_put_location(payload: dict, _u=Depends(_require_admin)):
    allowed = {"city", "postal_code", "country", "lat", "lon", "timezone"}
    if not all(k in allowed for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        data.setdefault("location", {}).update(payload)
        # Remove legacy Norwegian key if present
        data.pop("lokasjon", None)
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_location")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    return {"ok": True, "location": data["location"]}


_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


@router.get("/api/settings/network")
async def api_get_network(_u=Depends(_require_admin)):
    return get_network_settings()


@router.put("/api/settings/network")
async def api_put_network(payload: dict, _u=Depends(_require_admin)):
    """Network and access (Settings → General, Expert): local subnets, VPN subnet, trusted reverse
    proxies (only they may set X-Forwarded-For — rule 27), API docs switch. Live, no restart."""
    allowed = {"local_subnets", "vpn_subnet", "trusted_proxies", "api_docs"}
    if not set(payload) <= allowed:
        raise HTTPException(400, "Unknown field in payload")
    clean: dict = {}
    if "local_subnets" in payload:
        subnets = [str(x).strip() for x in (payload["local_subnets"] or []) if str(x).strip()]
        for sn in subnets:
            try:
                ipaddress.ip_network(sn, strict=False)
            except ValueError as exc:
                raise HTTPException(400, f"Not a subnet: {sn}") from exc
        clean["local_subnets"] = subnets
    if "vpn_subnet" in payload:
        vpn = str(payload["vpn_subnet"] or "").strip()
        try:
            ipaddress.ip_network(vpn, strict=False)
        except ValueError as exc:
            raise HTTPException(400, f"Not a subnet: {vpn}") from exc
        clean["vpn_subnet"] = vpn
    if "trusted_proxies" in payload:
        proxies = [str(x).strip() for x in (payload["trusted_proxies"] or []) if str(x).strip()]
        for px in proxies:
            try:
                net = ipaddress.ip_network(px, strict=False)
                if net.prefixlen == 0:
                    raise HTTPException(
                        400, f"{px} would trust every address — list the proxy itself"
                    )
            except ValueError as exc:
                if not _HOSTNAME_RE.match(px):
                    raise HTTPException(400, f"Not an address, subnet or host name: {px}") from exc
        clean["trusted_proxies"] = proxies
    if "api_docs" in payload:
        clean["api_docs"] = bool(payload["api_docs"])
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        data.setdefault("network", {}).update(clean)
        data["network"].pop("local_subnet", None)  # legacy single-subnet key
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_network")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    return {"ok": True, **get_network_settings()}


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
async def api_put_language(payload: dict, _u=Depends(_require_admin)):
    gui_allowed = {"nb", "en", "de"}
    lang = payload.get("language")
    kare_lang = payload.get("kare_language")
    if lang is not None and lang not in gui_allowed:
        raise HTTPException(400, f"GUI language must be one of: {', '.join(sorted(gui_allowed))}")
    if kare_lang is not None and (not isinstance(kare_lang, str) or not kare_lang.strip()):
        raise HTTPException(400, "kare_language must be a non-empty string")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        if lang is not None:
            data["language"] = lang
        if kare_lang is not None:
            data["kare_language"] = kare_lang.strip()
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_language")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    try:
        _reload_llm()
    except Exception:
        pass
    return {
        "ok": True,
        "language": data.get("language"),
        "kare_language": data.get("kare_language"),
    }


@router.get("/api/settings/llm")
def api_get_llm(_u=Depends(_require_auth)):
    raw = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    # Agent-pool members show the effective (shared) options/think — P111 Phase 1
    data = apply_agent_pool(raw)
    pool_roles = list((raw.get("agent_pool") or {}).get("roles") or [])
    result: dict = {}
    for role in _EDITABLE_LLM_ROLES:
        if role not in data:
            continue
        s = data[role]
        # Resolve share_with: build entry from parent config so all fields are available
        share_with = s.get("share_with")
        if share_with and share_with in data:
            resolved = dict(data[share_with])
            resolved["share_with"] = share_with
            resolved["enabled"] = s.get("enabled", True)
            s = resolved
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
            entry["keep_warm"] = bool(s.get("keep_warm", False))
            entry["options"] = {
                k: v for k, v in (s.get("options") or {}).items() if k in _OLLAMA_OPTION_KEYS
            }
            entry["gpu_id"] = s.get("gpu_id")
            entry["ollama_env"] = {
                k: v for k, v in (s.get("ollama_env") or {}).items() if k in _OLLAMA_ENV_KEYS
            }
        elif provider == "vllm":
            entry["think"] = s.get("think")
            entry["options"] = {
                k: v for k, v in (s.get("options") or {}).items() if k in _VLLM_OPTION_KEYS
            }
            entry["vllm_docker"] = {
                k: v for k, v in (s.get("vllm_docker") or {}).items() if k in _VLLM_DOCKER_KEYS
            }
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
            tok = _read_env_key(_LLM_KEYS_PATH, api_key_env) or _read_env_key(
                _NVIDIA_ENV_PATH, api_key_env
            )
            entry["api_key_set"] = bool(tok)
            entry["api_key_masked"] = _mask_token(tok)
        else:
            # Non-Ollama text LLM: top-level temperature/top_p/max_tokens
            for k in _CLOUD_OPTION_KEYS:
                if k in s:
                    entry[k] = s[k]
            api_key_env = s.get("api_key_env", f"{role.upper()}_API_KEY")
            entry["api_key_env"] = api_key_env
            tok = _read_env_key(_LLM_KEYS_PATH, api_key_env) or _read_env_key(
                _NVIDIA_ENV_PATH, api_key_env
            )
            entry["api_key_set"] = bool(tok)
            entry["api_key_masked"] = _mask_token(tok)
        entry["share_with"] = s.get("share_with")
        if role in pool_roles:
            entry["agent_pool_roles"] = pool_roles
            # Per-role keys inside the pool (today only num_predict — it does not reload the
            # model), so a future "agent settings" GUI knows what it may edit per agent.
            entry["agent_pool_role_keys"] = list(POOL_ROLE_OVERRIDABLE)
        result[role] = entry
    return result


@router.put("/api/settings/llm/{role}")
async def api_put_llm_role(role: str, payload: dict, _u=Depends(_require_admin)):
    if role not in _EDITABLE_LLM_ROLES:
        raise HTTPException(400, f"Unknown role: {role}")
    data = config_store.load_for_update(_LLM_PATH)
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    s = data[role]
    pool = data.get("agent_pool") or {}
    if (
        role in (pool.get("roles") or [])
        and not s.get("share_with")
        and (s.get("provider", "ollama") == "ollama")
    ):
        # Agent-pool member: think + every option except the per-role ones are shared by the
        # whole pool, so they are written to agent_pool:, never to this role (P111 Phase 1).
        payload = dict(payload)
        if "think" in payload:
            if payload["think"] is None:
                pool.pop("think", None)
            else:
                pool["think"] = payload["think"]
            del payload["think"]
        if isinstance(payload.get("options"), dict):
            own = {}
            pool.setdefault("options", {})
            for k, v in payload["options"].items():
                if k not in _OLLAMA_OPTION_KEYS:
                    continue
                if k in POOL_ROLE_OVERRIDABLE:
                    own[k] = v
                else:
                    pool["options"][k] = v
            payload["options"] = own
        data["agent_pool"] = pool
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
        if payload.get("api_key"):
            env_var = s.get("api_key_env", "IMAGE_EDIT_API_KEY")
            _write_env_key(_LLM_KEYS_PATH, env_var, payload["api_key"])
    elif provider != "ollama":
        for k in _CLOUD_OPTION_KEYS:
            if k in payload:
                s[k] = payload[k]
        # Write API key to env file if provided
        if payload.get("api_key"):
            env_var = s.get("api_key_env", f"{role.upper()}_API_KEY")
            target = _NVIDIA_ENV_PATH if role == "cloud" else _LLM_KEYS_PATH
            _write_env_key(target, env_var, payload["api_key"])
    if provider == "ollama" and "container" in payload:
        if payload["container"] is None or payload["container"] == "":
            s.pop("container", None)
        else:
            # Validate: only alphanumeric, dash, underscore — no shell injection
            if _re.fullmatch(r"[a-zA-Z0-9_\-]+", str(payload["container"])):
                s["container"] = str(payload["container"])
    if provider == "ollama" and "gpu_id" in payload:
        if payload["gpu_id"] is None:
            s.pop("gpu_id", None)
        else:
            s["gpu_id"] = int(payload["gpu_id"])
    if provider == "ollama" and "keep_warm" in payload:
        s["keep_warm"] = bool(payload["keep_warm"])
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
    if "share_with" in payload:
        if payload["share_with"]:
            # Toggle ON: strip all fields — only share_with + enabled remain
            data[role] = {
                "share_with": str(payload["share_with"]),
                "enabled": s.get("enabled", True),
            }
        else:
            # Toggle OFF: remove share_with flag (payload fields already set above)
            s.pop("share_with", None)
    config_store.write_yaml(_LLM_PATH, data, "gui:put_llm_role")
    _reload_agent_enabled()
    warmup_started = False
    if payload.get("model"):
        mdata = config_store.load_for_update(_MODELS_PATH)
        model_role_key = s.get("model_role", role)
        old_model = mdata.get(model_role_key, "")
        new_model = payload["model"]
        mdata[model_role_key] = new_model
        config_store.write_yaml(_MODELS_PATH, mdata, "gui:put_llm_role")
        # Auto-warmup whenever an Ollama model is saved (load into VRAM if needed)
        if provider == "ollama" and new_model and s.get("base_url"):
            if (
                old_model
                and old_model != new_model
                and not _model_used_elsewhere(data, mdata, role, s["base_url"], old_model)
            ):
                spawn(_unload_ollama_model(s["base_url"], old_model))
            num_ctx = (apply_agent_pool(data).get(role, {}).get("options") or {}).get(
                "num_ctx", 8192
            )
            _start_warmup_task(
                role, s["base_url"], new_model, num_ctx, keep_warm=bool(s.get("keep_warm", False))
            )
            warmup_started = True
    elif provider == "ollama" and payload.get("keep_warm") and s.get("base_url"):
        # keep_warm just enabled — pre-load the current model
        current_model = get_model(s.get("model_role", role))
        if current_model:
            num_ctx = (apply_agent_pool(data).get(role, {}).get("options") or {}).get(
                "num_ctx", 8192
            )
            _start_warmup_task(role, s["base_url"], current_model, num_ctx, keep_warm=True)
            warmup_started = True
    if role == "image_edit" and "model_edit" in payload and payload["model_edit"]:
        mdata = config_store.load_for_update(_MODELS_PATH)
        mdata[s.get("model_role_edit", "image_edit_edit")] = payload["model_edit"]
        config_store.write_yaml(_MODELS_PATH, mdata, "gui:put_llm_role")
    # Shared agent-pool options and models.yaml are read per call — reload after the last write so
    # a GUI change reaches every pool member without a service restart (P111 Phase 1, DT03 2.9).
    _reload_llm_and_core()
    return {"ok": True, "warmup_started": warmup_started}


@router.post("/api/settings/llm/{role}/restart_docker")
async def api_restart_vllm_docker(role: str, _u=Depends(_require_admin)):
    settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    if not settings.get("allow_docker_control", False):
        raise HTTPException(403, "Docker control is disabled in settings")
    if not _docker_socket_ok():
        raise HTTPException(503, "Docker socket not available")
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    s = data[role]
    provider = s.get("provider", "ollama")
    if provider != "vllm":
        raise HTTPException(400, f"Role {role} uses provider={provider}, not vllm")
    vllm_cfg = s.get("vllm_docker", {})
    container = vllm_cfg.get("container") or f"vllm-{role}"
    if role in _vllm_restart_tasks and not _vllm_restart_tasks[role].done():
        return {"ok": False, "error": "Restart already in progress", "container": container}
    base_url = s.get("base_url")
    if not base_url:
        raise HTTPException(400, f"Role {role} has no base_url in llm.yaml")
    _vllm_restart_tasks[role] = asyncio.create_task(
        _run_vllm_restart(role, container, vllm_cfg, base_url)
    )
    return {"ok": True, "container": container, "status": "restarting"}


@router.get("/api/settings/llm/{role}/restart_docker_status")
async def api_restart_docker_status(role: str, _u=Depends(_require_admin)):
    return _vllm_restart_status.get(role, {"status": "idle"})


@router.post("/api/settings/llm/{role}/warmup")
async def api_trigger_warmup(role: str, payload: dict, _u=Depends(_require_admin)):
    """Manually trigger Ollama warmup for a role (e.g. after a model pull)."""
    if role not in _EDITABLE_LLM_ROLES:
        raise HTTPException(400, f"Unknown role: {role}")
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    s = data[role]
    provider = s.get("provider", "ollama")
    if provider != "ollama":
        raise HTTPException(400, f"Role {role} uses provider={provider}, not ollama")
    base_url = s.get("base_url", "")
    if not base_url:
        raise HTTPException(400, f"No base_url configured for role {role}")
    num_ctx = (apply_agent_pool(data).get(role, {}).get("options") or {}).get("num_ctx", 8192)
    model = payload.get("model") or get_model(s.get("model_role", role))
    if not model:
        raise HTTPException(400, "No model configured for this role")
    _start_warmup_task(role, base_url, model, num_ctx, keep_warm=bool(s.get("keep_warm", False)))
    return {"ok": True, "model": model}


@router.get("/api/settings/llm/{role}/warmup_status")
async def api_get_warmup_status(role: str, _u=Depends(_require_admin)):
    """Return current warmup status for a role."""
    return _warmup_status.get(role, {"status": "idle"})


@router.post("/api/settings/llm/discover_ollama")
async def api_discover_ollama(_u=Depends(_require_admin)):
    subnets = get_network_settings()["local_subnets"]
    # Use first non-Docker-bridge subnet for Ollama host scan
    subnet_str = next(
        (
            s
            for s in subnets
            if not ipaddress.ip_network(s, strict=False).overlaps(
                ipaddress.ip_network("172.16.0.0/12")
            )
        ),
        "",
    )
    candidates: list[str] = [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://host.docker.internal:11434",
        "http://host-gateway:11434",
    ]
    try:
        network = ipaddress.IPv4Network(subnet_str, strict=False)
        if network.prefixlen < 24:
            # A wide subnet (the default 192.168.0.0/16) is 65 000 probes — scan only this machine's /24 (P127)
            own = detect_lan_address()
            network = (
                ipaddress.IPv4Network(f"{own}/24", strict=False)
                if own and ipaddress.ip_address(own) in network
                else None
            )
        for host in network.hosts() if network else []:
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


# ---------------------------------------------------------------------------
# Docker-kontroll og VRAM-oversikt
# ---------------------------------------------------------------------------


def _docker_socket_ok() -> bool:
    """Check if Docker socket is accessible (mounted in container or native)."""

    return os.access("/var/run/docker.sock", os.R_OK)


@router.get("/api/settings/night-sequence")
async def api_get_night_sequence(_u=Depends(_require_admin)):
    """Night sequence start time (N17) — read by kaare_night_runner.py every poll, no restart."""
    hour, minute = load_start_time(_SETTINGS_PATH)
    return {
        "start_time": f"{hour:02d}:{minute:02d}",
        "default": DEFAULT_START_TIME,
        "timezone": str(get_local_tz()),
        "last_slot": read_last_slot(),
    }


@router.put("/api/settings/night-sequence")
async def api_put_night_sequence(payload: dict, _u=Depends(_require_admin)):
    parsed = parse_start_time(payload.get("start_time"))
    if parsed is None:
        raise HTTPException(400, "start_time must be HH:MM (24 h)")
    value = f"{parsed[0]:02d}:{parsed[1]:02d}"
    settings = config_store.load_for_update(_SETTINGS_PATH)
    night = settings.get("night_sequence")
    settings["night_sequence"] = {**(night if isinstance(night, dict) else {}), "start_time": value}
    config_store.write_yaml(_SETTINGS_PATH, settings, "gui:put_night_sequence")
    return {"ok": True, "start_time": value}


# ── Logs and data (P131): log write interval + data retention ─────────────────
_RETENTION_KEYS = (
    "interactions_retention_days",
    "episodes_retention_days",
    "think_cache_retention_days",
)
_RETENTION_DEFAULTS = {
    "interactions_retention_days": 0,
    "episodes_retention_days": 0,
    "think_cache_retention_days": 30,
}
# Shown as amber warnings in the GUI card — one source for the thresholds.
_LOGS_RECOMMENDED = {"flush_min": 2, "flush_max": 10, "retention_warn_below_days": 7}


@router.get("/api/settings/logs-and-data")
async def api_get_logs_and_data(_u=Depends(_require_admin)):
    """Settings → General → Logs and data (Expert). flush_interval_seconds is read live by
    kaare_core.log_buffer in every process; retention is applied by the night sequence."""
    settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    retention = settings.get("data_retention") or {}
    return {
        "flush_interval_seconds": get_log_flush_seconds(),
        "retention": {k: int(retention.get(k, _RETENTION_DEFAULTS[k])) for k in _RETENTION_KEYS},
        "defaults": {
            "flush_interval_seconds": LOG_FLUSH_DEFAULT_SECONDS,
            "retention": _RETENTION_DEFAULTS,
        },
        "limits": {"flush_max": LOG_FLUSH_MAX_SECONDS},
        "recommended": _LOGS_RECOMMENDED,
    }


@router.put("/api/settings/logs-and-data")
async def api_put_logs_and_data(payload: dict, _u=Depends(_require_admin)):
    settings = config_store.load_for_update(_SETTINGS_PATH)
    if "flush_interval_seconds" in payload:
        try:
            secs = float(payload["flush_interval_seconds"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "flush_interval_seconds must be a number") from exc
        if not 0 <= secs <= LOG_FLUSH_MAX_SECONDS:
            raise HTTPException(400, f"flush_interval_seconds must be 0–{LOG_FLUSH_MAX_SECONDS:g}")
        logging_cfg = settings.get("logging")
        settings["logging"] = {
            **(logging_cfg if isinstance(logging_cfg, dict) else {}),
            "flush_interval_seconds": secs,
        }
    retention_in = payload.get("retention") or {}
    if retention_in:
        current = settings.get("data_retention")
        retention = dict(current) if isinstance(current, dict) else {}
        for key, value in retention_in.items():
            if key not in _RETENTION_KEYS:
                raise HTTPException(400, f"unknown retention field: {key}")
            try:
                days = int(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"{key} must be a whole number of days") from exc
            if days < 0:
                raise HTTPException(400, f"{key} must be 0 (keep forever) or more")
            retention[key] = days
        settings["data_retention"] = retention
    config_store.write_yaml(_SETTINGS_PATH, settings, "gui:put_logs_and_data")
    return await api_get_logs_and_data(_u=_u)


@router.get("/api/settings/system/docker_control")
async def api_get_docker_control(_u=Depends(_require_admin)):
    """Return allow_docker_control flag and Docker socket availability."""
    settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return {
        "allow_docker_control": bool(settings.get("allow_docker_control", False)),
        "socket_available": _docker_socket_ok(),
    }


@router.put("/api/settings/system/docker_control")
async def api_put_docker_control(payload: dict, _u=Depends(_require_admin)):
    """Toggle allow_docker_control in settings.yaml."""
    if "allow_docker_control" not in payload:
        raise HTTPException(400, "Missing field: allow_docker_control")
    settings = config_store.load_for_update(_SETTINGS_PATH)
    settings["allow_docker_control"] = bool(payload["allow_docker_control"])
    config_store.write_yaml(_SETTINGS_PATH, settings, "gui:put_docker_control")
    return {"ok": True, "allow_docker_control": settings["allow_docker_control"]}


@router.get("/api/settings/llm/discover-containers")
async def api_discover_containers(_u=Depends(_require_admin)):
    """List running Docker containers with their mapped ports (for Ollama role assignment)."""
    if not _docker_socket_ok():
        return {"containers": [], "error": "Docker socket not available"}
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            name = parts[0]
            ports_str = parts[1] if len(parts) > 1 else ""
            # Parse port mappings like "0.0.0.0:11447->11434/tcp"
            mapped_ports = [int(m.group(1)) for m in re.finditer(r":(\d+)->\d+", ports_str)]
            containers.append({"name": name, "ports": mapped_ports})
        return {"containers": containers}
    except Exception as e:
        return {"containers": [], "error": str(e)[:120]}


@router.get("/api/settings/llm/model_check")
async def api_llm_model_check(_u=Depends(_require_admin)):
    """Per Ollama role: is its model pulled? {role: {model, base_url, status: ok|missing|unreachable}} (P116 B13)."""
    return await check_ollama_role_models()


@router.get("/api/settings/llm/vram_overview")
async def api_get_vram_overview(_u=Depends(_require_admin)):
    """Return VRAM usage for all loaded Ollama models across all configured instances."""
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    # Collect unique Ollama base_urls
    seen_urls: set[str] = set()
    url_to_roles: dict[str, list[str]] = {}
    for role, cfg in data.items():
        if cfg.get("provider") == "ollama" and cfg.get("base_url"):
            url = cfg["base_url"]
            seen_urls.add(url)
            url_to_roles.setdefault(url, []).append(role)

    entries: list[dict] = []

    async def _fetch_ps(url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{url}/api/ps")
                if r.status_code != 200:
                    return
                for m in r.json().get("models", []):
                    model_name = m.get("name", "")
                    vram_bytes = m.get("size_vram", 0)
                    # Find which role(s) this model belongs to
                    roles_here = url_to_roles.get(url, [])
                    matched_role = roles_here[0] if len(roles_here) == 1 else "?"
                    for role_candidate in roles_here:
                        cfg = data.get(role_candidate, {})
                        mr = cfg.get("model_role", role_candidate)
                        mdata = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
                        configured_model = mdata.get(mr, "")
                        if configured_model and model_name.startswith(
                            configured_model.split(":")[0]
                        ):
                            matched_role = role_candidate
                            break
                    entries.append(
                        {
                            "role": matched_role,
                            "model": model_name,
                            "vram_bytes": vram_bytes,
                            "on_cpu": vram_bytes == 0,
                            "base_url": url,
                        }
                    )
        except Exception:
            pass

    await asyncio.gather(*[_fetch_ps(u) for u in seen_urls])
    return {"entries": entries}


@router.post("/api/settings/llm/{role}/restart_ollama")
async def api_restart_ollama(role: str, _u=Depends(_require_admin)):
    """Restart an Ollama Docker container and trigger warmup for the given role."""
    settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    if not settings.get("allow_docker_control", False):
        raise HTTPException(403, "Docker control is disabled in settings")
    if not _docker_socket_ok():
        raise HTTPException(503, "Docker socket not available")
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    cfg = data[role]
    if cfg.get("provider") != "ollama":
        raise HTTPException(400, f"Role {role} is not an Ollama role")
    container = cfg.get("container")
    if not container:
        raise HTTPException(400, f"No container configured for role {role}")
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "restart", container],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip(), "container": container}
        # Kick off warmup automatically after restart
        base_url = cfg.get("base_url", "")
        num_ctx = cfg.get("options", {}).get("num_ctx", 8192)
        mdata = yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}
        model = mdata.get(cfg.get("model_role", role), "")
        if base_url and model:
            _start_warmup_task(role, base_url, model, num_ctx)
        return {"ok": True, "container": container, "warmup_started": bool(base_url and model)}
    except Exception as e:
        return {"ok": False, "error": str(e), "container": container}


@router.get("/api/settings/models")
def api_get_models(_u=Depends(_require_auth)):
    return yaml.safe_load(_MODELS_PATH.read_text(encoding="utf-8")) or {}


@router.put("/api/settings/models")
async def api_put_models(payload: dict, _u=Depends(_require_admin)):
    allowed = {"kare", "miss_kare", "library", "embed", "cloud"}
    bad = set(payload) - allowed
    if bad:
        raise HTTPException(400, f"Unknown model roles: {bad}")
    data = config_store.load_for_update(_MODELS_PATH)
    data.update(payload)
    config_store.write_yaml(_MODELS_PATH, data, "gui:put_models")
    _reload_llm_and_core()  # apply at once (rule 25) — before, a model change needed a restart
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
    ha_enabled = cap.get("domains", {}).get("home_assistant", {}).get("enabled", False)
    return {
        "home_assistant": {
            "url": ha.get("url", ""),
            "timeout": ha.get("timeout", 5),
            "enabled": bool(ha_enabled),
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
            "device": emb.get("device", "NPU"),
            "hf_model": emb.get("hf_model", "BAAI/bge-m3"),
            "model_path": emb.get("model_path", ""),
            "emb_enabled": emb.get("enabled", True),
        },
        "memory_embed": {
            "enabled": bool(data.get("memory_embed", {}).get("enabled", False)),
            "model_dir": data.get("memory_embed", {}).get("model_dir", ""),
        },
        "voice": {
            "stt_backend": voice_stt.get("backend", "openvino"),
            "faster_whisper_model": voice_stt.get("faster_whisper_model", "large-v3"),
            "compute_type": voice_stt.get("faster_whisper_compute_type", "int8"),
            "cpu_threads": int(voice_stt.get("cpu_threads", 0) or 0),
            "stt_enabled": voice_stt.get("enabled", True),
            "model_dir": voice_stt.get("model_dir", ""),
        },
    }


def _set_domain_enabled(domain: str, enabled: bool) -> None:
    """Turn a capability_map domain (home_assistant, frigate, …) on/off and update the live copy."""
    cap = config_store.load_for_update(_CAPABILITY_MAP_PATH)
    cap.setdefault("domains", {}).setdefault(domain, {})["enabled"] = bool(enabled)
    config_store.write_yaml(_CAPABILITY_MAP_PATH, cap, "gui:set_domain_enabled")
    app_state.CAPABILITY_MAP = cap


async def _reload_ha_gateway() -> bool:
    """Ask the HA gateway to re-read its HA URL/token (P127): without it a new token from the GUI or the
    onboarding only took effect after a gateway restart. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                f"{get_service('internal', 'ha_gateway')}/api/reload", headers=service_headers()
            )
        return r.status_code == 200
    except Exception as exc:
        logger.warning("HA gateway reload failed: %s", exc)
        return False


@router.put("/api/settings/services/ha")
async def api_put_services_ha(payload: dict, _u=Depends(_require_admin)):
    if not all(k in {"url", "timeout", "enabled"} for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    enabled = payload.pop("enabled", None)
    data = config_store.load_for_update(_SERVICES_PATH)
    data.setdefault("home_assistant", {}).update(payload)
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_ha")
    if enabled is not None:
        _set_domain_enabled("home_assistant", bool(enabled))
    # Keep legacy ha_url in settings.yaml in sync
    if "url" in payload:
        sdata = config_store.load_for_update(_SETTINGS_PATH)
        if "ha_url" in sdata:
            sdata["ha_url"] = payload["url"]
            config_store.write_yaml(_SETTINGS_PATH, sdata, "gui:put_services_ha")
    if "url" in payload:
        await _reload_ha_gateway()
    return {"ok": True}


def _write_mqtt_env_creds(username: str, password: str) -> None:
    with config_store.edit_text(_MQTT_ENV_PATH, "gui:mqtt-credentials", mode=0o640) as doc:
        lines = [
            line
            for line in doc.text.splitlines()
            if line.split("=")[0].strip() not in ("MQTT_USER", "MQTT_PASSWORD")
        ]
        lines.append(f"MQTT_USER={username}")
        lines.append(f"MQTT_PASSWORD={password}")
        doc.text = "\n".join(lines) + "\n"


@router.put("/api/settings/services/mqtt")
async def api_put_services_mqtt(payload: dict, _u=Depends(_require_admin)):
    allowed = {
        "host",
        "port",
        "username",
        "password",
        "tls_enabled",
        "topic_prefix",
        "client_id",
        "reconnect_interval",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    data = config_store.load_for_update(_SERVICES_PATH)
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
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_mqtt")
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
    data = config_store.load_for_update(_SERVICES_PATH)
    frigate = data.setdefault("frigate", {})
    if "url" in payload:
        frigate["url"] = payload["url"]
    if "timeout" in payload:
        frigate["timeout"] = int(payload["timeout"])
    if "snapshot_timeout" in payload:
        frigate["snapshot_timeout"] = int(payload["snapshot_timeout"])
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_frigate")
    if "enabled" in payload:
        try:
            _set_domain_enabled("frigate", bool(payload["enabled"]))
        except Exception:
            pass
    return {"ok": True}


@router.put("/api/settings/services/plex")
async def api_put_services_plex(payload: dict, _u=Depends(_require_admin)):
    if not all(k in {"url", "timeout"} for k in payload):
        raise HTTPException(400, "Unknown field in payload")
    data = config_store.load_for_update(_SERVICES_PATH)
    media = data.setdefault("media", {})
    plex = media.setdefault("plex", {})
    if "url" in payload:
        plex["url"] = payload["url"]
    if "timeout" in payload:
        plex["timeout"] = int(payload["timeout"])
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_plex")
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
    data = config_store.load_for_update(_SERVICES_PATH)
    emb = data.setdefault("embedding", {})
    if "device" in payload:
        emb["device"] = payload["device"]
    if "hf_model" in payload:
        emb["hf_model"] = str(payload["hf_model"])
    if "model_path" in payload:
        emb["model_path"] = str(payload["model_path"])
    if "emb_enabled" in payload:
        emb["enabled"] = bool(payload["emb_enabled"])
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_embedding")
    return {"ok": True}


@router.put("/api/settings/services/memory-embed")
async def api_put_services_memory_embed(payload: dict, _u=Depends(_require_admin)):
    allowed = {"enabled", "model_dir"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    data = config_store.load_for_update(_SERVICES_PATH)
    me = data.setdefault("memory_embed", {})
    if "enabled" in payload:
        me["enabled"] = bool(payload["enabled"])
    if "model_dir" in payload:
        me["model_dir"] = str(payload["model_dir"])
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_memory_embed")
    return {"ok": True}


@router.put("/api/settings/services/voice")
async def api_put_services_voice(payload: dict, _u=Depends(_require_admin)):
    allowed = {
        "stt_backend",
        "faster_whisper_model",
        "compute_type",
        "stt_enabled",
        "model_dir",
        "cpu_threads",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown field in payload: {unknown}")
    valid_backends = {"openvino", "faster_whisper"}
    valid_compute_types = {"int8", "float16", "float32", "int8_float16"}
    if "stt_backend" in payload and payload["stt_backend"] not in valid_backends:
        raise HTTPException(400, f"stt_backend must be one of: {valid_backends}")
    if "compute_type" in payload and payload["compute_type"] not in valid_compute_types:
        raise HTTPException(400, f"compute_type must be one of: {valid_compute_types}")
    if "cpu_threads" in payload and (
        not isinstance(payload["cpu_threads"], int) or not 0 <= payload["cpu_threads"] <= 64
    ):
        raise HTTPException(400, "cpu_threads must be an integer 0–64 (0 = auto)")
    data = config_store.load_for_update(_SERVICES_PATH)
    stt = data.setdefault("voice", {}).setdefault("stt", {})
    if "stt_backend" in payload:
        stt["backend"] = payload["stt_backend"]
    if "faster_whisper_model" in payload:
        stt["faster_whisper_model"] = str(payload["faster_whisper_model"])
    if "compute_type" in payload:
        stt["faster_whisper_compute_type"] = payload["compute_type"]
    if "stt_enabled" in payload:
        stt["enabled"] = bool(payload["stt_enabled"])
    if "cpu_threads" in payload:
        stt["cpu_threads"] = payload["cpu_threads"]
    if "model_dir" in payload:
        stt["model_dir"] = str(payload["model_dir"])
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_services_voice")
    return {"ok": True}


# LAN address speakers fetch audio from (P127): a hostname or IP, no scheme or port.
_MEDIA_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")


def _valid_media_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_MEDIA_HOST_RE.match(host))


@router.get("/api/settings/voice/media-host")
def api_get_media_host(_u=Depends(_require_admin)):
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    container = in_container()
    return {
        "media_host": str((data.get("voice") or {}).get("media_host") or ""),
        # Inside a container the detected address is the container's own — useless to speakers
        "detected": "" if container else detect_lan_address(),
        "in_container": container,
    }


@router.put("/api/settings/voice/media-host")
async def api_put_media_host(payload: dict, _u=Depends(_require_admin)):
    if set(payload) != {"media_host"}:
        raise HTTPException(400, "Expected exactly: media_host")
    host = str(payload["media_host"] or "").strip()
    if host and not _valid_media_host(host):
        raise HTTPException(
            400, "media_host must be a hostname or IP address, without http:// or a port"
        )
    data = config_store.load_for_update(_SERVICES_PATH)
    data.setdefault("voice", {})["media_host"] = host
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_media_host")
    applied = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                f"{get_service('internal', 'voice_bridge')}/reload_media_host",
                headers=service_headers(),
            )
        applied = r.status_code == 200
    except Exception as exc:
        logger.info("Voice bridge not reachable for media host reload: %s", exc)
    return {"ok": True, "applied": applied}


# ---------------------------------------------------------------------------
# Piper TTS — model management
# ---------------------------------------------------------------------------

# Presets, folder and paths live in kaare_core/voice/tts_models.py (shared with the voice service).

_TIER_ORDER = {"x_low": 0, "low": 1, "medium": 2, "high": 3}

_piper_download_jobs: dict[str, dict] = {}  # job_id → {status, lang, model, error}


def _piper_active_models() -> dict[str, str]:
    """Read tts_models from services.yaml → {lang: path_str}."""
    try:
        data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        return data.get("voice", {}).get("tts", {}).get("tts_models", {})
    except Exception:
        return {}


@router.get("/api/settings/piper/models")
def api_get_piper_models(_u=Depends(_require_auth)):
    """Return preset list per language with downloaded/active state.

    Models may live at the nested HuggingFace path (new downloads) or at a
    flat path (manually placed / pre-existing install). The configured path in
    tts_models takes precedence when it matches this model's filename.
    """
    active = _piper_active_models()
    result: dict[str, list[dict]] = {}
    for lang, presets in PIPER_PRESETS.items():
        lang_active_path = active.get(lang, "")
        configured = Path(lang_active_path) if lang_active_path else None
        rows = []
        for p in presets:
            nested_path = piper_local_path(p["model"])
            # If configured path exists and filename matches this preset, use it
            configured_matches = (
                configured is not None and configured.exists() and configured.stem == p["model"]
            )
            actual_path = configured if configured_matches else nested_path
            downloaded = actual_path.exists()
            is_active = configured_matches
            rows.append(
                {
                    **p,
                    "local_path": str(actual_path),
                    "downloaded": downloaded,
                    "active": is_active,
                }
            )
        result[lang] = rows
    return result


async def _run_piper_download(job_id: str, model_name: str, lang: str) -> None:
    """Background task: download Piper model from HuggingFace using Python library."""
    _piper_download_jobs[job_id] = {
        "status": "running",
        "lang": lang,
        "model": model_name,
        "error": None,
    }
    onnx_path, json_path = piper_hf_paths(model_name)
    piper_dir().mkdir(parents=True, exist_ok=True)

    def _do_download() -> None:
        # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
        from huggingface_hub import hf_hub_download  # noqa: PLC0415

        for hf_path in (onnx_path, json_path):
            hf_hub_download(
                repo_id="rhasspy/piper-voices",
                filename=hf_path,
                local_dir=str(piper_dir()),
                local_dir_use_symlinks=False,
            )

    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(loop.run_in_executor(None, _do_download), timeout=600)
        _piper_download_jobs[job_id]["status"] = "done"
    except TimeoutError:
        _piper_download_jobs[job_id]["status"] = "error"
        _piper_download_jobs[job_id]["error"] = "Download timed out (10 min)"
    except Exception as exc:
        _piper_download_jobs[job_id]["status"] = "error"
        _piper_download_jobs[job_id]["error"] = str(exc)


@router.post("/api/settings/piper/download")
async def api_post_piper_download(
    payload: dict, background_tasks: BackgroundTasks, _u=Depends(_require_admin)
):
    lang = payload.get("lang", "").strip()
    model_name = payload.get("model_name", "").strip()
    if lang not in PIPER_PRESETS:
        raise HTTPException(400, f"Unknown lang: {lang}")
    known_models = {p["model"] for p in PIPER_PRESETS[lang]}
    if model_name not in known_models:
        raise HTTPException(400, f"Unknown model: {model_name}")
    job_id = _uuid.uuid4().hex[:12]
    background_tasks.add_task(_run_piper_download, job_id, model_name, lang)
    return {"job_id": job_id}


@router.get("/api/settings/piper/download/{job_id}")
def api_get_piper_download_status(job_id: str, _u=Depends(_require_auth)):
    job = _piper_download_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.put("/api/settings/piper/activate")
async def api_put_piper_activate(payload: dict, _u=Depends(_require_admin)):
    lang = payload.get("lang", "").strip()
    model_path = payload.get("model_path", "").strip()
    if lang not in PIPER_PRESETS:
        raise HTTPException(400, f"Unknown lang: {lang}")
    if not Path(model_path).exists():
        raise HTTPException(400, f"Model file not found: {model_path}")
    data = config_store.load_for_update(_SERVICES_PATH)
    data.setdefault("voice", {}).setdefault("tts", {}).setdefault("tts_models", {})[lang] = (
        model_path
    )
    config_store.write_yaml(_SERVICES_PATH, data, "gui:put_piper_activate")
    svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    bridge_url = svc.get("internal", {}).get("voice_bridge", "http://127.0.0.1:8011")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{bridge_url}/reload_tts_models", headers=service_headers())
    except Exception:
        pass  # non-fatal — model active after next bridge restart
    return {"ok": True, "lang": lang, "model_path": model_path}


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
    await _reload_ha_gateway()
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
        "brave": {"is_set": bool(brave), "masked": _mask_token(brave)},
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
    owm_key = _read_env_key(_WEATHER_ENV_PATH, "OPENWEATHERMAP_API_KEY")
    wapi_key = _read_env_key(_WEATHER_ENV_PATH, "WEATHERAPI_KEY")
    pirate_key = _read_env_key(_WEATHER_ENV_PATH, "PIRATEWEATHER_API_KEY")
    stormglass_key = _read_env_key(_WEATHER_ENV_PATH, "STORMGLASS_API_KEY")
    return {
        "provider": wcfg.get("provider", "met.no"),
        "forecast_days": int(wcfg.get("forecast_days", 2)),
        "show_feels_like": bool(wcfg.get("show_feels_like", False)),
        "show_uv_index": bool(wcfg.get("show_uv_index", False)),
        "show_sun_times": bool(wcfg.get("show_sun_times", False)),
        "show_alerts": bool(wcfg.get("show_alerts", True)),
        "show_air_quality": bool(wcfg.get("show_air_quality", False)),
        "use_ha_sensors": bool(wcfg.get("use_ha_sensors", False)),
        "ha_temp_entity": wcfg.get("ha_temp_entity", ""),
        "ha_wind_entity": wcfg.get("ha_wind_entity", ""),
        "ha_wind_gust_entity": wcfg.get("ha_wind_gust_entity", ""),
        "ha_wind_direction_entity": wcfg.get("ha_wind_direction_entity", ""),
        "ha_precip_entity": wcfg.get("ha_precip_entity", ""),
        "ha_precip_last_hour_entity": wcfg.get("ha_precip_last_hour_entity", ""),
        "ha_precip_today_entity": wcfg.get("ha_precip_today_entity", ""),
        "ha_humidity_entity": wcfg.get("ha_humidity_entity", ""),
        "ha_pressure_entity": wcfg.get("ha_pressure_entity", ""),
        "show_tides": bool(wcfg.get("show_tides", False)),
        "tide_provider": wcfg.get("tide_provider", "auto"),
        "use_camera_for_weather": bool(wcfg.get("use_camera_for_weather", False)),
        "weather_camera": wcfg.get("weather_camera", ""),
        "openweathermap_key_set": bool(owm_key),
        "openweathermap_key_masked": _mask_token(owm_key) if owm_key else "",
        "weatherapi_key_set": bool(wapi_key),
        "weatherapi_key_masked": _mask_token(wapi_key) if wapi_key else "",
        "pirateweather_key_set": bool(pirate_key),
        "pirateweather_key_masked": _mask_token(pirate_key) if pirate_key else "",
        "stormglass_key_set": bool(stormglass_key),
        "stormglass_key_masked": _mask_token(stormglass_key) if stormglass_key else "",
    }


@router.put("/api/settings/weather")
async def api_put_weather(payload: dict, _u=Depends(_require_admin)):
    provider = payload.get("provider", "met.no")
    if provider not in _WEATHER_PROVIDERS:
        raise HTTPException(
            400, f"Unknown provider: {provider}. Valid: {sorted(_WEATHER_PROVIDERS)}"
        )
    forecast_days = int(payload.get("forecast_days", 2))
    if not (1 <= forecast_days <= 7):
        raise HTTPException(400, "forecast_days must be 1–7")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        data["weather"] = {
            "provider": provider,
            "forecast_days": forecast_days,
            "show_feels_like": bool(payload.get("show_feels_like", False)),
            "show_uv_index": bool(payload.get("show_uv_index", False)),
            "show_sun_times": bool(payload.get("show_sun_times", False)),
            "show_alerts": bool(payload.get("show_alerts", True)),
            "show_air_quality": bool(payload.get("show_air_quality", False)),
            "use_ha_sensors": bool(payload.get("use_ha_sensors", False)),
            "ha_temp_entity": payload.get("ha_temp_entity", "").strip(),
            "ha_wind_entity": payload.get("ha_wind_entity", "").strip(),
            "ha_wind_gust_entity": payload.get("ha_wind_gust_entity", "").strip(),
            "ha_wind_direction_entity": payload.get("ha_wind_direction_entity", "").strip(),
            "ha_precip_entity": payload.get("ha_precip_entity", "").strip(),
            "ha_precip_last_hour_entity": payload.get("ha_precip_last_hour_entity", "").strip(),
            "ha_precip_today_entity": payload.get("ha_precip_today_entity", "").strip(),
            "ha_humidity_entity": payload.get("ha_humidity_entity", "").strip(),
            "ha_pressure_entity": payload.get("ha_pressure_entity", "").strip(),
            "show_tides": bool(payload.get("show_tides", False)),
            "tide_provider": payload.get("tide_provider", "auto"),
            "use_camera_for_weather": bool(payload.get("use_camera_for_weather", False)),
            "weather_camera": payload.get("weather_camera", "").strip(),
        }
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_weather")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    if key := payload.get("openweathermap_key", "").strip():
        _write_env_key(_WEATHER_ENV_PATH, "OPENWEATHERMAP_API_KEY", key)
    if key := payload.get("weatherapi_key", "").strip():
        _write_env_key(_WEATHER_ENV_PATH, "WEATHERAPI_KEY", key)
    if key := payload.get("pirateweather_key", "").strip():
        _write_env_key(_WEATHER_ENV_PATH, "PIRATEWEATHER_API_KEY", key)
    if key := payload.get("stormglass_key", "").strip():
        _write_env_key(_WEATHER_ENV_PATH, "STORMGLASS_API_KEY", key)
    return {"ok": True, "provider": provider, "forecast_days": forecast_days}


@router.get("/api/settings/websearch")
async def api_get_websearch(_u=Depends(_require_admin)):
    # Same reader as the search itself (one source, rule 25), plus the expert numbers with
    # their generic defaults so the GUI always shows the value in effect.
    cfg = load_websearch_config()
    for key in _WS_NUMERIC:
        cfg[key] = web_fetch_setting(key)
    cfg["open_web"] = open_web_enabled()
    cfg["caution"] = source_caution()
    cfg["paid_calls_today"] = paid_calls_today()
    return cfg


_WS_TEXT = {"provider", "fallback", "searxng_url", "brave_country", "brave_search_lang", "caution"}
_WS_INT = {"fetch_count", "max_results", "content_max"}
# Expert numbers read through adapters.web_fetch.setting() (P121 Phase 1).
_WS_NUMERIC = (
    "search_total_timeout_s",
    "fetch_timeout_s",
    "max_download_bytes",
    "per_host_interval_s",
    "page_cache_ttl_s",
    "page_cache_max_entries",
    "trusted_slots",
    "official_timeout_s",
    "background_timeout_s",
)
_WS_BOOL = {"open_web"}


@router.put("/api/settings/websearch")
async def api_put_websearch(payload: dict, _u=Depends(_require_admin)):
    payload.pop("paid_calls_today", None)  # read-only, sent back by the GUI form
    unknown = set(payload) - _WS_TEXT - _WS_INT - set(_WS_NUMERIC) - _WS_BOOL
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")
    if "open_web" in payload and not isinstance(payload["open_web"], bool):
        raise HTTPException(400, "open_web must be true or false")
    if payload.get("provider") not in (None, "searxng", "brave"):
        raise HTTPException(400, "provider must be 'searxng' or 'brave'")
    if payload.get("fallback") not in (None, "", "searxng", "brave"):
        raise HTTPException(400, "fallback must be '', 'searxng' or 'brave'")
    if payload.get("caution") not in (None, "careful", "balanced", "open"):
        raise HTTPException(400, "caution must be 'careful', 'balanced' or 'open'")
    for key in (_WS_INT | set(_WS_NUMERIC)) & set(payload):
        try:
            value = float(payload[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"{key} must be a number") from exc
        if value <= 0:
            raise HTTPException(400, f"{key} must be greater than 0")
        payload[key] = (
            int(value)
            if key in _WS_INT
            or key in ("max_download_bytes", "page_cache_max_entries", "trusted_slots")
            else value
        )
    try:
        data = config_store.load_for_update(_SERVICES_PATH)
        data.setdefault("web_search", {}).update(payload)
        config_store.write_yaml(_SERVICES_PATH, data, "gui:put_websearch")
    except Exception as e:
        raise HTTPException(500, f"Could not write services.yaml: {e}") from e
    try:
        # services.yaml is cached by kaare_core.config — reload it so the expert numbers apply now
        await asyncio.to_thread(reload_core_config)
        reload_websearch_config()
    except Exception as e:
        print(f"[settings] websearch reload after save failed: {e}")
    return {"ok": True}


# ── Norwegian law from Lovdata's open dataset (P123) ──────────────────────────

_law_refresh_task: asyncio.Task | None = None


@router.get("/api/settings/law")
async def api_get_law(_u=Depends(_require_admin)):
    cfg = lovdata_adapter.config()
    running = _law_refresh_task is not None and not _law_refresh_task.done()
    return {
        "enabled": cfg["enabled"],
        "status": lovdata_adapter.status(),
        "refreshing": running,
        "attribution": lovdata_adapter.ATTRIBUTION,
    }


@router.put("/api/settings/law")
async def api_put_law(payload: dict, _u=Depends(_require_admin)):
    if set(payload) - {"enabled"} or not isinstance(payload.get("enabled"), bool):
        raise HTTPException(400, 'Body must be {"enabled": true|false}')
    try:
        data = config_store.load_for_update(_SERVICES_PATH)
        data.setdefault("law", {}).setdefault("lovdata", {})["enabled"] = payload["enabled"]
        config_store.write_yaml(_SERVICES_PATH, data, "gui:put_law")
        await asyncio.to_thread(reload_core_config)
    except Exception as e:
        raise HTTPException(500, f"Could not write services.yaml: {e}") from e
    return {"ok": True, "enabled": payload["enabled"]}


@router.post("/api/settings/law/refresh")
async def api_refresh_law(_u=Depends(_require_admin)):
    """Download Lovdata's open datasets now and rebuild the index (in the background)."""
    global _law_refresh_task
    if _law_refresh_task is not None and not _law_refresh_task.done():
        return {"ok": True, "started": False, "refreshing": True}

    async def _run():
        try:
            await lovdata_adapter.refresh()
        except Exception as e:
            print(f"[settings] Lovdata refresh failed: {e}")

    _law_refresh_task = asyncio.create_task(_run())
    return {"ok": True, "started": True, "refreshing": True}


@router.get("/api/settings/reflection")
async def api_get_reflection(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    cfg = data.get("kare_reflection", {})
    preset = cfg.get("leder_preset", "standard")
    default_preset = _leder_preset_text(
        "reflection", preset if preset != "egendefinert" else "standard"
    )
    custom_text = _leder_preset_text("reflection", "egendefinert")
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "interval_seconds": int(cfg.get("interval_seconds", 600)),
        "max_rounds": int(cfg.get("max_rounds", 6)),
        "kare_max_tokens": int(cfg.get("kare_max_tokens", 8192)),
        "miss_kare_max_tokens": int(cfg.get("miss_kare_max_tokens", 1500)),
        "leder_preset": preset,
        "leder_preset_default": default_preset,
        "leder_preset_custom": custom_text,
    }


@router.put("/api/settings/reflection")
async def api_put_reflection(payload: dict, _u=Depends(_require_admin)):
    allowed = {
        "enabled",
        "interval_seconds",
        "max_rounds",
        "kare_max_tokens",
        "miss_kare_max_tokens",
        "leder_preset",
        "leder_preset_custom",
    }
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
        data = config_store.load_for_update(_SETTINGS_PATH)
        cfg_update = {k: v for k, v in payload.items() if k != "leder_preset_custom"}
        data.setdefault("kare_reflection", {}).update(cfg_update)
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_reflection")
        if "leder_preset_custom" in payload:
            config_store.write_text(
                _LEDER_PRESET_DIR / "reflection_egendefinert.md",
                str(payload["leder_preset_custom"]),
                "gui:put_reflection",
            )
        app_state._REFLECTION_ENABLED, app_state._JANG_INTERVAL_S = _load_reflection_config()
    except Exception as e:
        raise HTTPException(500, f"Could not write settings: {e}") from e
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
    slot = get_dev_slot_config_from(data)
    return {
        # P118 B5/B8: the dev slot — on/off + its settings (the GUI shows these).
        "enabled": bool(cfg.get("enabled", True)),
        "time_budget_minutes": slot["time_budget_minutes"],
        "topics_per_meeting": slot["topics_per_meeting"],
        "max_user_traces": slot["max_user_traces"],
        "round_cap_per_topic": slot["round_cap_per_topic"],
        # Old persona-meeting settings — kept readable, no longer shown in the GUI.
        "max_rounds": int(cfg.get("max_rounds", 6)),
        "max_invest_rounds": int(cfg.get("max_invest_rounds", 5)),
        "kare_max_tokens": int(cfg.get("kare_max_tokens", 2500)),
        "kare_invest_tokens": int(cfg.get("kare_invest_tokens", 1000)),
        "leder_preset": preset,
        "leder_preset_default": default_preset,
        "leder_preset_custom": custom_text,
    }


@router.put("/api/settings/dev-meeting")
async def api_put_dev_meeting(payload: dict, _u=Depends(_require_admin)):
    slot_keys = {
        "time_budget_minutes",
        "max_user_traces",
        "round_cap_per_topic",
        "topics_per_meeting",
    }
    allowed = {
        "max_rounds",
        "max_invest_rounds",
        "kare_max_tokens",
        "kare_invest_tokens",
        "leder_preset",
        "leder_preset_custom",
        "enabled",
    } | slot_keys
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")
    for key, (lo, hi) in {
        "time_budget_minutes": (1, 240),
        "max_user_traces": (0, 200),
        "round_cap_per_topic": (1, 12),
        "topics_per_meeting": (1, 50),
    }.items():
        if key in payload:
            try:
                val = int(payload[key])
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"{key} must be a whole number") from exc
            if not lo <= val <= hi:
                raise HTTPException(400, f"{key} must be between {lo} and {hi}")
    if "max_rounds" in payload and int(payload["max_rounds"]) < 2:
        raise HTTPException(400, "max_rounds must be at least 2")
    if "max_invest_rounds" in payload and int(payload["max_invest_rounds"]) < 1:
        raise HTTPException(400, "max_invest_rounds must be at least 1")
    if "leder_preset" in payload and payload["leder_preset"] not in _VALID_LEDER_DEV_PRESETS:
        raise HTTPException(400, f"leder_preset must be one of: {_VALID_LEDER_DEV_PRESETS}")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        cfg_update = {
            k: v for k, v in payload.items() if k != "leder_preset_custom" and k not in slot_keys
        }
        if "enabled" in cfg_update:
            cfg_update["enabled"] = bool(cfg_update["enabled"])
        data.setdefault("dev_meeting", {}).update(cfg_update)
        slot_update = {k: int(v) for k, v in payload.items() if k in slot_keys}
        if slot_update:
            data.setdefault("dev_slot", {}).update(slot_update)
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_dev_meeting")
        if "leder_preset_custom" in payload:
            config_store.write_text(
                _LEDER_PRESET_DIR / "dev_egendefinert.md",
                str(payload["leder_preset_custom"]),
                "gui:put_dev_meeting",
            )
    except Exception as e:
        raise HTTPException(500, f"Could not write settings: {e}") from e
    return {"ok": True}


@router.get("/api/settings/assistant_name")
async def api_get_assistant_name():
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    return {"assistant_name": data.get("assistant_name", "Kåre")}


@router.get("/api/settings/kare")
async def api_get_kare_settings(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    ps = data.get("personality_self", {})
    ar = data.get("automation_reflex", {})
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
        "personality_core_default": _get_personality_default(
            data.get("kare_language") or data.get("language", "nb")
        ),
        "personality_self": {
            "contributors": ps.get("contributors", "all"),
            "allowed_users": ps.get("allowed_users", []),
        },
        "automation_reflex": {
            "contributors": ar.get("contributors", "admin_only"),
            "allowed_users": ar.get("allowed_users", []),
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
    ar = payload.get("automation_reflex")
    reflex_contributor_mode = None
    reflex_allowed: list = []
    if ar is not None:
        reflex_contributor_mode = ar.get("contributors")
        if reflex_contributor_mode and reflex_contributor_mode not in _VALID_CONTRIBUTOR_MODES:
            raise HTTPException(400, f"contributors must be one of: {_VALID_CONTRIBUTOR_MODES}")
        reflex_allowed = ar.get("allowed_users", [])
        if not isinstance(reflex_allowed, list):
            raise HTTPException(400, "allowed_users must be a list")
    personality_mode = payload.get("personality_mode")
    if personality_mode and personality_mode not in _VALID_PERSONALITY_MODES:
        raise HTTPException(400, f"personality_mode must be one of: {_VALID_PERSONALITY_MODES}")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
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
        if ar is not None:
            data.setdefault("automation_reflex", {})
            if reflex_contributor_mode:
                data["automation_reflex"]["contributors"] = reflex_contributor_mode
            data["automation_reflex"]["allowed_users"] = reflex_allowed
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_kare_settings")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    if personality_mode == "egendefinert" and "personality_core_custom" in payload:
        try:
            config_store.write_text(
                _PERSONALITY_CORE_CUSTOM_PATH,
                str(payload["personality_core_custom"]),
                "gui:put_kare_settings",
            )
        except Exception as e:
            raise HTTPException(500, f"Could not write personality_core_custom.md: {e}") from e
    try:
        _llm.reload_config()
    except Exception:
        pass
    return {"ok": True}


# ── Memory drawers in the prompt (P133 C6, D11–D13) ─────────────────────────
# memory_prompt.* via config.get_memory_prompt_config(); the kare-list caps live under the
# existing kare_reminders block (lister.py / llm_adapter.py read them).
_MEMORY_PROMPT_RANGES = {
    "top_k": (1, 10),
    "lessons_max_chars": (200, 6000),
    "lessons_write_max_chars": (100, 4000),
    "core_facts_max_chars": (200, 8000),
    "index_max_chars": (200, 8000),
}
_MEMORY_SCORE_RANGES = {"min_score": (0.2, 0.95), "echo_min_score": (0.3, 0.98)}
_KARE_REMINDER_RANGES = {
    "injection_max_chars": (200, 8000),
    "note_prompt_chars": (60, 2000),
    "write_max_chars": (100, 4000),
    "max_entries": (10, 1000),
}
_KARE_REMINDER_DEFAULTS = {
    "injection_max_chars": 2000,
    "note_prompt_chars": 300,
    "write_max_chars": 600,
    "max_entries": 100,
}
_RESOLVE_ROLES = ("admin", "adult", "young_adult", "teen", "child")


@router.get("/api/settings/memory-prompt")
async def api_get_memory_prompt(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    kr = data.get("kare_reminders") or {}
    return {
        **get_memory_prompt_config(),
        "kare_reminders": {k: int(kr.get(k, v)) for k, v in _KARE_REMINDER_DEFAULTS.items()},
        "roles": list(_RESOLVE_ROLES),
        # Not stored: whether «Relevant nå» actually runs (switch on AND an embedder enabled).
        "relevance_active": _relevance.provider() is not None,
    }


@router.put("/api/settings/memory-prompt")
async def api_put_memory_prompt(payload: dict, _u=Depends(_require_admin)):
    update: dict = {}
    for key, (lo, hi) in _MEMORY_PROMPT_RANGES.items():
        if key in payload:
            try:
                val = int(payload[key])
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"{key} must be a whole number") from exc
            if not lo <= val <= hi:
                raise HTTPException(400, f"{key} must be between {lo} and {hi}")
            update[key] = val
    for key, (lo, hi) in _MEMORY_SCORE_RANGES.items():
        if key in payload:
            try:
                val = round(float(payload[key]), 2)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"{key} must be a number") from exc
            if not lo <= val <= hi:
                raise HTTPException(400, f"{key} must be between {lo} and {hi}")
            update[key] = val
    if "relevance_enabled" in payload:
        update["relevance_enabled"] = bool(payload["relevance_enabled"])
    if "resolve_roles" in payload:
        roles = payload["resolve_roles"]
        if not isinstance(roles, list) or any(r not in _RESOLVE_ROLES for r in roles):
            raise HTTPException(400, f"resolve_roles must be a list of: {_RESOLVE_ROLES}")
        update["resolve_roles"] = list(dict.fromkeys(roles))
    kr_update: dict = {}
    for key, (lo, hi) in _KARE_REMINDER_RANGES.items():
        if key in (payload.get("kare_reminders") or {}):
            try:
                val = int(payload["kare_reminders"][key])
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"kare_reminders.{key} must be a whole number") from exc
            if not lo <= val <= hi:
                raise HTTPException(400, f"kare_reminders.{key} must be between {lo} and {hi}")
            kr_update[key] = val
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        if update:
            data.setdefault("memory_prompt", {}).update(update)
        if kr_update:
            data.setdefault("kare_reminders", {}).update(kr_update)
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_memory_prompt")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    try:
        reload_core_config()  # lister reads kare_reminders via get_settings()
        _llm.reload_config()  # kare_reminders.injection_max_chars is read at config load
    except Exception:
        pass
    return {"ok": True}


@router.get("/api/settings/trusted-sources")
async def api_get_trusted_sources(_u=Depends(_require_admin)):
    # Entries come back in the current format (description/type/trust, P121 Phase 2), also when
    # the file still has the old Norwegian `beskrivelse` key.
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        return {
            cat: [normalize_entry(e) for e in (items or []) if isinstance(e, dict)]
            for cat, items in (data.get("sources") or {}).items()
        }
    except Exception as e:
        raise HTTPException(500, f"Could not read trusted_sources.yaml: {e}") from e


@router.put("/api/settings/trusted-sources")
async def api_put_trusted_sources(payload: dict, _u=Depends(_require_admin)):
    for cat_key, entries in payload.items():
        if not isinstance(entries, list):
            raise HTTPException(400, f"Category '{cat_key}' must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or "domain" not in entry:
                raise HTTPException(400, "Each entry must have a 'domain' field")
            # Bare hosts only (P121 Phase 0): "https://www.x.no/path" -> "x.no" — a path can't be
            # enforced by the fetcher. English keys + known type/trust values (Phase 2).
            if not normalize_domain(str(entry.get("domain", ""))):
                raise HTTPException(400, f"Invalid domain: '{entry.get('domain', '')}'")
            if entry.get("type") not in (None, "", *SOURCE_TYPES):
                raise HTTPException(400, f"Unknown source type: '{entry.get('type')}'")
            if entry.get("trust") not in (None, "", *TRUST_LEVELS):
                raise HTTPException(400, f"Unknown trust level: '{entry.get('trust')}'")
    payload = {cat: [normalize_entry(e) for e in entries] for cat, entries in payload.items()}
    try:
        existing = config_store.load_for_update(_TRUSTED_PATH)
        existing["sources"] = payload
        config_store.write_yaml(_TRUSTED_PATH, existing, "gui:put_trusted_sources")
    except Exception as e:
        raise HTTPException(500, f"Could not write trusted_sources.yaml: {e}") from e
    domain_count = sum(len(v) for v in payload.values())
    return {"ok": True, "categories": len(payload), "domains": domain_count}


@router.get("/api/settings/blocked-sources")
async def api_get_blocked_sources(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        return {"blocked": [str(d) for d in (data.get("blocked") or [])]}
    except Exception as e:
        raise HTTPException(500, f"Could not read trusted_sources.yaml: {e}") from e


@router.put("/api/settings/blocked-sources")
async def api_put_blocked_sources(payload: dict, _u=Depends(_require_admin)):
    """Domains web search and link reading never use (P121 Phase 2). Stored as bare hosts."""
    items = payload.get("blocked")
    if not isinstance(items, list):
        raise HTTPException(400, "'blocked' must be a list of domains")
    hosts = []
    for raw in items:
        host = normalize_domain(str(raw))
        if not host:
            raise HTTPException(400, f"Invalid domain: '{raw}'")
        if host not in hosts:
            hosts.append(host)
    try:
        existing = config_store.load_for_update(_TRUSTED_PATH)
        existing["blocked"] = hosts
        config_store.write_yaml(_TRUSTED_PATH, existing, "gui:put_blocked_sources")
    except Exception as e:
        raise HTTPException(500, f"Could not write trusted_sources.yaml: {e}") from e
    return {"ok": True, "blocked": hosts}


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
    data = config_store.load_for_update(_ALIASES_PATH)
    for section in allowed:
        if section in payload:
            data[section] = payload[section]
    config_store.write_yaml(_ALIASES_PATH, data, "gui:put_aliases")
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
    data = config_store.load_for_update(_NODES_PATH)
    data["nodes"] = payload["nodes"]
    config_store.write_yaml(_NODES_PATH, data, "gui:put_nodes")
    try:
        async with _httpx.AsyncClient(timeout=3) as _c:
            await _c.post(
                f"{get_service('internal', 'voice_bridge')}/reload_nodes", headers=service_headers()
            )
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/settings/nodes/{node_id}/test_display")
async def api_test_display_node(node_id: str, _u=Depends(_require_admin)):
    result = await send_display(node_id=node_id, text="Kåre display test", title="Kåre", duration=5)
    if result.get("ok"):
        return {"ok": True, "method": result.get("method")}
    raise HTTPException(500, result.get("error", "Unknown error"))


# ── SSH Nodes ──────────────────────────────────────────────────────────────────

_SSH_NODES_PATH = Path("/kaare/configs/ssh_nodes.yaml")


@router.get("/api/settings/ssh-nodes")
def api_get_ssh_nodes(_u=Depends(_require_admin)):
    return get_ssh_nodes()


@router.put("/api/settings/ssh-nodes")
async def api_put_ssh_nodes(payload: dict, _u=Depends(_require_admin)):
    data: dict = {}
    if _SSH_NODES_PATH.exists():
        try:
            data = config_store.load_for_update(_SSH_NODES_PATH)
        except Exception:
            data = {}
    if "local" in payload:
        data["local"] = payload["local"]
    if "nodes" in payload:
        data["nodes"] = payload["nodes"]
    config_store.write_yaml(_SSH_NODES_PATH, data, "gui:put_ssh_nodes")
    return {"ok": True}


@router.post("/api/settings/ssh-nodes/test")
async def api_test_ssh_node(payload: dict, _u=Depends(_require_admin)):
    host = payload.get("host", "").strip()
    user = payload.get("user", "root").strip()
    port = int(payload.get("port", 22))
    ssh_key = str(payload.get("ssh_key", "~/.ssh/id_ed25519")).replace("~", str(_Path.home()))

    if not host:
        raise HTTPException(400, "host is required")

    def _run_test() -> dict:
        start = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-i",
                    ssh_key,
                    "-F",
                    "/kaare/.ssh/config",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=6",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-p",
                    str(port),
                    f"{user}@{host}",
                    "echo ok",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            elapsed = int((time.monotonic() - start) * 1000)
            if result.returncode == 0 and "ok" in result.stdout:
                return {"ok": True, "latency_ms": elapsed}
            err = (result.stderr or result.stdout).strip()[:200]
            return {"ok": False, "latency_ms": elapsed, "error": err or "SSH returned non-zero"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "latency_ms": 10000, "error": "Connection timed out"}
        except Exception as e:
            return {"ok": False, "latency_ms": 0, "error": str(e)[:200]}

    return await asyncio.to_thread(_run_test)


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
    data = config_store.load_for_update(_CAPABILITY_MAP_PATH)
    if "domains" in payload:
        data["domains"] = payload["domains"]
    if "distribution_profile" in payload:
        data["distribution_profile"] = payload["distribution_profile"]
    if "services" in payload:
        data["services"] = payload["services"]
    config_store.write_yaml(_CAPABILITY_MAP_PATH, data, "gui:put_capabilities")
    app_state.CAPABILITY_MAP = data
    reload_capability_services()
    return {"ok": True}


def suggest_local_subnet(addr: str, vpn_subnet: str) -> str:
    """The /24 around a client address, if it can be a home LAN (P127). Never the Docker bridge (Docker
    Desktop's NAT shows every client as 172.x.0.1), the VPN, loopback, link-local or a public address."""
    try:
        ip = ipaddress.ip_address(addr)
        if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
            return ""
        if ip in ipaddress.ip_network("172.16.0.0/12"):
            return ""
        if ip in ipaddress.ip_network(vpn_subnet, strict=False):
            return ""
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except ValueError:
        return ""


@router.get("/api/onboarding/network-suggestion")
def api_onboarding_network_suggestion(request: Request, _u=Depends(_require_admin)):
    """Setup wizard: the local subnet to suggest from where the admin's browser connects. The browser
    adds its own guess (the address it opened KTSB on) when this one is empty."""
    net = get_network_settings()
    return {
        "suggested_subnet": suggest_local_subnet(_client_ip(request), net["vpn_subnet"]),
        "local_subnets": net["local_subnets"],
        "vpn_subnet": net["vpn_subnet"],
        "is_default": net["local_subnets"] == [DEFAULT_LOCAL_SUBNET],
    }


@router.get("/api/onboarding/status")
def api_onboarding_status(_u=Depends(_require_admin)):
    steps = []
    try:
        settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        # GUI saves write "location" and drop the legacy "lokasjon" block (P116 B19)
        lok = settings.get("location") or settings.get("lokasjon") or {}
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
    steps.append(
        {"id": "distribution", "label": "Distribusjonsprofil valgt", "complete": profile_ok}
    )
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
    return get_tool_permissions()


@router.put("/api/admin/tool_permissions")
async def api_put_tool_permissions(data: dict, _u=Depends(_require_admin)):
    try:
        save_tool_permissions(data)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/settings/agent_tools")
async def api_get_agent_tools(_u=Depends(_require_admin)):
    return get_tool_permissions().get("agent_tools", {})


@router.put("/api/settings/agent_tools")
async def api_put_agent_tools(data: dict, _u=Depends(_require_admin)):
    try:
        current = get_tool_permissions()
        current["agent_tools"] = data
        save_tool_permissions(current)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Meeting roles: Mechanic only. Miss Kåre has one locked personality (P115 Phase 1 / P99) —
# her role selector was removed; a stale "miss_kare" key in a PUT payload is ignored.
@router.get("/api/settings/meeting-roles")
async def api_get_meeting_roles(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    mr = data.get("meeting_roles", {})
    ps_role = mr.get("mechanic", "undersøker")
    ps_custom = (
        _MEETING_ROLE_PS_CUSTOM.read_text(encoding="utf-8")
        if _MEETING_ROLE_PS_CUSTOM.exists()
        else ""
    )
    ps_def_file = _ps_preset_file(
        ps_role if ps_role != "egendefinert" else "undersøker", lang=_get_lang()
    )
    ps_default = ps_def_file.read_text(encoding="utf-8") if ps_def_file.exists() else ""
    return {
        "mechanic": ps_role,
        "mechanic_custom": ps_custom,
        "mechanic_default": ps_default,
    }


@router.put("/api/settings/meeting-roles")
async def api_put_meeting_roles(payload: dict, _u=Depends(_require_admin)):
    ps_role = payload.get("mechanic")
    if ps_role and ps_role not in _VALID_PS_ROLES:
        raise HTTPException(400, f"mechanic role must be one of: {_VALID_PS_ROLES}")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        data.setdefault("meeting_roles", {})
        if ps_role:
            data["meeting_roles"]["mechanic"] = ps_role
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_meeting_roles")
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    if ps_role == "egendefinert" and "mechanic_custom" in payload:
        try:
            config_store.write_text(
                _MEETING_ROLE_PS_CUSTOM, str(payload["mechanic_custom"]), "gui:put_meeting_roles"
            )
        except Exception as e:
            raise HTTPException(500, f"Could not write mechanic custom: {e}") from e
    return {"ok": True}


@router.get("/api/settings/timers")
async def api_get_timer_settings(_u=Depends(_require_admin)):
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    return {"max_per_user": data.get("timers", {}).get("max_per_user", 20)}


@router.put("/api/settings/timers")
async def api_put_timer_settings(payload: dict, _u=Depends(_require_admin)):
    max_per_user = payload.get("max_per_user")
    if not isinstance(max_per_user, int) or max_per_user < 1 or max_per_user > 1000:
        raise HTTPException(400, "max_per_user must be an integer between 1 and 1000")
    try:
        data = config_store.load_for_update(_SETTINGS_PATH)
        data.setdefault("timers", {})["max_per_user"] = max_per_user
        config_store.write_yaml(_SETTINGS_PATH, data, "gui:put_timer_settings")

        await asyncio.to_thread(reload_config)
    except Exception as e:
        raise HTTPException(500, f"Could not write settings.yaml: {e}") from e
    return {"ok": True, "max_per_user": max_per_user}


# ---------------------------------------------------------------------------
# Inner voices (Jing + Jang)
# ---------------------------------------------------------------------------

_JING_THOUGHTS_PATH = Path("/kaare/state/jing_thoughts.txt")
_INNER_THOUGHTS_PATH = Path("/kaare/state/inner_thoughts.txt")
_VALID_PROVIDERS = {"openvino", "mlx", "cpu", "remote"}

# docker-compose.yml hardcodes PROVIDER=openvino as the build-arg for both
# kaare-jing and kaare-jang — only openvino-genai/optimum-intel are ever
# actually installed in the built image. Picking cpu/mlx in Docker mode
# crash-loops the container (missing torch/transformers or mlx_lm) with no
# GUI-visible error. "remote" needs no local provider at all, always safe.
_IN_DOCKER = os.path.exists("/.dockerenv")
_DOCKER_SAFE_PROVIDERS = {"openvino", "remote"}


@router.get("/api/settings/inner-voices")
async def api_get_inner_voices(_u=Depends(_require_admin)):
    svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    cfg = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return {
        "jing": {
            "provider": svc.get("jing", {}).get("provider", "openvino"),
            "model_path": svc.get("jing", {}).get("model_path", ""),
            "push_url": svc.get("jing", {}).get("push_url", ""),
            "interval_seconds": cfg.get("jing", {}).get("interval_seconds", 180),
            "max_tokens": cfg.get("jing", {}).get("max_tokens", 300),
        },
        "jang": {
            "provider": svc.get("jang", {}).get("provider", "openvino"),
            "model_path": svc.get("jang", {}).get("model_path", ""),
            "push_url": svc.get("jang", {}).get("push_url", ""),
            "interval_seconds": cfg.get("jang", {}).get("interval_seconds", 600),
            "max_tokens": cfg.get("jang", {}).get("max_tokens", 2048),
            "turns_back": cfg.get("jang", {}).get("turns_back", 3),
            "inner_thoughts_retention_hours": cfg.get("jang", {}).get(
                "inner_thoughts_retention_hours", 24
            ),
        },
        "node_label": svc.get("inner_voices", {}).get("node_label", "Local"),
        "push_token": svc.get("inner_voices", {}).get("push_token", ""),
    }


@router.put("/api/settings/inner-voices")
async def api_put_inner_voices(payload: dict, _u=Depends(_require_admin)):
    _svc_keys = {
        "jing_provider",
        "jing_model_path",
        "jing_push_url",
        "jang_provider",
        "jang_model_path",
        "jang_push_url",
        "node_label",
        "push_token",
        "generate_token",
    }
    unknown = set(payload) - _svc_keys
    if unknown:
        raise HTTPException(400, f"Unknown fields: {unknown}")

    svc = config_store.load_for_update(_SERVICES_PATH)
    jing_svc = svc.setdefault("jing", {})
    jang_svc = svc.setdefault("jang", {})
    iv_svc = svc.setdefault("inner_voices", {})

    for name, sub in (("jing", jing_svc), ("jang", jang_svc)):
        prov_key = f"{name}_provider"
        if prov_key in payload:
            if payload[prov_key] not in _VALID_PROVIDERS:
                raise HTTPException(400, f"{prov_key} must be one of: {_VALID_PROVIDERS}")
            if _IN_DOCKER and payload[prov_key] not in _DOCKER_SAFE_PROVIDERS:
                raise HTTPException(
                    400,
                    f"{prov_key}='{payload[prov_key]}' is not available in this Docker image "
                    f"(only built with openvino support) — the {name} container would "
                    f"crash-loop on restart. Rebuild the image with a different PROVIDER "
                    f"build-arg first if you need cpu/mlx, or use 'remote'/'openvino'.",
                )
            sub["provider"] = payload[prov_key]
        if f"{name}_model_path" in payload:
            sub["model_path"] = str(payload[f"{name}_model_path"])
        if f"{name}_push_url" in payload:
            sub["push_url"] = str(payload[f"{name}_push_url"])

    if "node_label" in payload:
        iv_svc["node_label"] = str(payload["node_label"])
    if payload.get("generate_token"):
        iv_svc["push_token"] = str(_uuid.uuid4())
    elif "push_token" in payload:
        iv_svc["push_token"] = str(payload["push_token"])

    config_store.write_yaml(_SERVICES_PATH, svc, "gui:put_inner_voices")
    return {"ok": True}


@router.post("/api/inner-voices/push")
async def api_inner_voices_push(payload: dict, request: Request):
    """Receive generated thoughts from a remote inner voice machine."""

    source = payload.get("source", "")
    content = (payload.get("content") or "").strip()

    # Auth: Bearer header (what push_client.py sends) or token field in the body. Only the
    # body was read before, so every real push got 401 (P116 B12).
    auth = request.headers.get("authorization", "")
    token = (
        auth[7:].strip() if auth.lower().startswith("bearer ") else str(payload.get("token") or "")
    )
    svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    expected = (svc.get("inner_voices", {}).get("push_token") or "").strip()
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing push token")

    if source not in ("jing", "jang"):
        raise HTTPException(400, "source must be 'jing' or 'jang'")
    if not content:
        raise HTTPException(400, "content is empty")

    if source == "jing":
        _JING_THOUGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = now_local().strftime("%H:%M")
        block = f"[Jing {timestamp}]\n{content}"
        await append_text(_JING_THOUGHTS_PATH, f"\n\n{block}")
    else:
        _INNER_THOUGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = now_local().strftime("%Y-%m-%d %H:%M")
        await append_text(_INNER_THOUGHTS_PATH, f"\n\n[Jang {timestamp}]\n{content}")

    return {"ok": True}


# ── Notification settings ──────────────────────────────────────────────────────

_NOTIF_DEFAULTS: dict = {
    "enabled": True,
    "normalcy_min_score": 0.5,
    "normalcy_min_confidence": 0.0,
    "normalcy_cameras": [],
}


@router.get("/api/settings/notifications")
async def api_get_notifications(_u=Depends(_require_admin)):
    """Return notification settings: master toggle, push entity, normalcy thresholds."""
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        notif = {**_NOTIF_DEFAULTS, **(svc.get("notifications") or {})}
        ha_notify_entity = (svc.get("home_assistant") or {}).get("ha_notify_entity", "")
        return {"ok": True, "notifications": notif, "ha_notify_entity": ha_notify_entity}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.put("/api/settings/notifications")
async def api_put_notifications(payload: dict, _u=Depends(_require_admin)):
    """Save notification settings."""
    try:
        svc = config_store.load_for_update(_SERVICES_PATH)

        notif = svc.setdefault("notifications", {})
        if "enabled" in payload:
            notif["enabled"] = bool(payload["enabled"])
        if "normalcy_min_score" in payload:
            notif["normalcy_min_score"] = float(payload["normalcy_min_score"])
        if "normalcy_min_confidence" in payload:
            notif["normalcy_min_confidence"] = float(payload["normalcy_min_confidence"])
        if "normalcy_cameras" in payload:
            notif["normalcy_cameras"] = list(payload["normalcy_cameras"])

        # ha_notify_entity lives under home_assistant
        if "ha_notify_entity" in payload:
            svc.setdefault("home_assistant", {})["ha_notify_entity"] = str(
                payload["ha_notify_entity"]
            )

        config_store.write_yaml(_SERVICES_PATH, svc, "gui:put_notifications")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e)) from e
