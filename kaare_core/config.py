import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from kaare_core import config_migrations, config_store
from kaare_core.tools.i18n import get_lang

_log = logging.getLogger(__name__)

_IN_DOCKER = os.path.exists("/.dockerenv")
CONFIG_DIR = Path("/kaare/configs")
_DEFAULT_DIR = Path("/kaare/configs_default")
# GUI-written (distribution profile, domains/services on/off). Lives on the configs volume so GUI
# changes survive a Docker upgrade (P127); the repo-root path is where installs before P127 kept it.
CAPABILITY_MAP_PATH = CONFIG_DIR / "capability_map.yaml"
_LEGACY_CAPABILITY_MAP_PATH = Path("/kaare/capability_map.yaml")


def ensure_config_file(target: Path, legacy: Path | None = None) -> Path:
    """Return configs/<name>, creating it if missing: a copy of `legacy` (where an install before P127
    kept it — keeps the user's settings) if that exists, else the shipped template from configs_default/.
    Copies only — the old file is never removed here. Readers call this themselves (a process may start before
    the migration has run). Atomic and locked through the config store, so processes starting at once never see
    a half-copied file (P134). Never raises."""
    if target.exists():
        return target
    src = legacy if legacy is not None and legacy.exists() else _DEFAULT_DIR / target.name
    return config_store.ensure_file(target, src)


def _migrate_or_wait_in_docker() -> None:
    """Docker: every KTSB container imports this module before it reads any config, but only the main image runs the
    migration from its entrypoint. Run the same runner here — the first process migrates, every other one waits on
    the migration lock and then finds nothing to do — so voice/embedding never read a file mid-upgrade (P134 R9).
    Bare metal migrates from kaare.service's ExecStartPre drop-in instead. Never raises."""
    if not _IN_DOCKER:
        return
    try:
        config_migrations.run(CONFIG_DIR, _DEFAULT_DIR)
    except Exception:
        _log.exception("config migration at import failed — continuing with the files as they are")


_migrate_or_wait_in_docker()


def ensure_capability_map() -> Path:
    return ensure_config_file(CAPABILITY_MAP_PATH, _LEGACY_CAPABILITY_MAP_PATH)


ensure_capability_map()


def _load_required(name: str) -> dict:
    """A config file every process needs at import. Missing → FileNotFoundError, as before; read through the
    config store so a file being replaced by another container is retried, not read as empty (P134)."""
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(path)
    return config_store.read_yaml(path)


MODELS: dict = _load_required("models.yaml")
_LLM_CFG: dict = _load_required("llm.yaml")  # agent_pool merged below, once the helper is defined
_SERVICES: dict = _load_required("services.yaml")
_SETTINGS: dict = _load_required("settings.yaml")

_TOOL_PERMISSIONS: dict = {}
_CAPABILITY_SERVICES: dict = {}


def _load_tool_permissions() -> None:
    global _TOOL_PERMISSIONS
    p = CONFIG_DIR / "tool_permissions.yaml"
    try:
        _TOOL_PERMISSIONS = config_store.read_yaml(p)
    except Exception:
        # Unreadable: keep what this process already has rather than running with no permissions table.
        _log.exception("tool_permissions.yaml unreadable — keeping the loaded permissions")
    # Runs on every load — process startup (call moved to end of this file, after
    # get_tools_for_role() is defined) and every hot-reload (save_tool_permissions(),
    # reload_config()). Never let a check failure break config loading itself.
    try:
        mismatches = validate_tool_permissions_consistency() + verify_always_included_survives(
            "adult"
        )
        if mismatches:
            _report_tool_consistency_alert(mismatches)
    except Exception:
        _log.exception("_load_tool_permissions: consistency check failed")


def _load_capability_services() -> None:
    global _CAPABILITY_SERVICES
    try:
        data = config_store.read_yaml(CAPABILITY_MAP_PATH)
        _CAPABILITY_SERVICES = data.get("services", {})
    except Exception:
        _log.exception("capability_map.yaml unreadable — keeping the loaded services")


def reload_capability_services() -> None:
    """Hot-reload the services section from capability_map.yaml."""
    _load_capability_services()


def reload_config() -> list[str]:
    """Hot-reload all YAML caches in this module. Called by /api/reload."""
    global MODELS, _LLM_CFG, _SERVICES, _SETTINGS
    reloaded = []
    for filename, setter in [
        ("models.yaml", lambda v: _set_models(v)),
        ("llm.yaml", lambda v: _set_llm(v)),
        ("services.yaml", lambda v: _set_services(v)),
        ("settings.yaml", lambda v: _set_settings(v)),
        ("tool_permissions.yaml", lambda _: _load_tool_permissions()),
    ]:
        try:
            data = config_store.read_yaml(CONFIG_DIR / filename)
            setter(data)
            reloaded.append(filename)
        except Exception:
            pass
    return reloaded


def _set_models(v: dict) -> None:
    global MODELS
    MODELS = v


def _set_llm(v: dict) -> None:
    global _LLM_CFG
    _LLM_CFG = apply_agent_pool(v)


# Per-role/per-call options a sub-agent-pool member may still set for itself. Only
# output length: everything else (num_ctx, sampling, num_thread, …) is shared, because a
# differing load-time option makes Ollama reload the model (P111 Phase 1).
POOL_ROLE_OVERRIDABLE = ("num_predict",)


def apply_agent_pool(raw: dict) -> dict:
    """Merge llm.yaml's shared `agent_pool:` block (options + think) into every role listed
    in `agent_pool.roles`. A member keeps its own base_url/container/timeout/enabled and may
    override only POOL_ROLE_OVERRIDABLE. Members are tagged `agent_pool: True`."""
    pool = (raw or {}).get("agent_pool") or {}
    members = pool.get("roles") or []
    if not members:
        return raw
    out = dict(raw)
    shared_opts = pool.get("options") or {}
    for role in members:
        cfg = raw.get(role)
        if not isinstance(cfg, dict) or cfg.get("share_with"):
            continue
        own = cfg.get("options") or {}
        merged = dict(cfg)
        merged["options"] = {
            **shared_opts,
            **{k: own[k] for k in POOL_ROLE_OVERRIDABLE if k in own},
        }
        if "think" in pool:
            merged["think"] = pool["think"]
        merged["agent_pool"] = True
        out[role] = merged
    return out


def pool_call_options(cfg: dict, overrides: dict | None = None) -> dict:
    """Options for one call. For an agent-pool role, per-call overrides are limited to
    POOL_ROLE_OVERRIDABLE on top of the shared options; for any other role, given overrides
    replace the configured options as they always have (call_llm_chat's original contract)."""
    base = dict(cfg.get("options") or {})
    if overrides is None:
        return base
    if cfg.get("agent_pool"):
        return {**base, **{k: v for k, v in overrides.items() if k in POOL_ROLE_OVERRIDABLE}}
    return dict(overrides)


def _set_services(v: dict) -> None:
    global _SERVICES
    _SERVICES = v


def _set_settings(v: dict) -> None:
    global _SETTINGS
    _SETTINGS = v


def get_settings() -> dict:
    return _SETTINGS


def get_model(role: str) -> str:
    """Return the Ollama model name for a logical role (kare, miss_kare, library, embed, cloud)."""
    return MODELS[role]


def get_llm_config(section: str) -> dict:
    """Return effective llm.yaml config for a role. Resolves share_with transparently."""
    cfg = _LLM_CFG[section]
    shared = cfg.get("share_with")
    if not shared:
        return cfg
    base = dict(_LLM_CFG[shared])
    base["share_with"] = shared
    base["enabled"] = cfg.get("enabled", True)
    return base


def get_known_containers() -> list[str]:
    """Distinct Docker container names referenced across all llm.yaml roles (share_with
    resolved, vllm_docker.container handled). Single source of truth for which containers
    exist on this install — never hardcode container names in code or GUI; read this
    instead, so renaming a container in llm.yaml is the only change needed anywhere."""
    seen: list[str] = []
    for role in _LLM_CFG:
        if not isinstance(_LLM_CFG.get(role), dict):
            continue  # not a role (a stray top-level value must not take the whole API down)
        cfg = get_llm_config(role)
        container = cfg.get("container") or cfg.get("vllm_docker", {}).get("container")
        if container and container not in seen:
            seen.append(container)
    return seen


def get_vllm_gpu_ids() -> set[int]:
    """GPU indices (nvidia-smi numbering) that host a vLLM container, from llm.yaml
    `vllm_docker.gpu_id` on every role whose provider is vllm. Empty when no role uses vLLM."""
    ids: set[int] = set()
    for role in _LLM_CFG:
        cfg = get_llm_config(role)
        gpu_id = (cfg.get("vllm_docker") or {}).get("gpu_id")
        if cfg.get("provider") == "vllm" and gpu_id is not None:
            try:
                ids.add(int(gpu_id))
            except (TypeError, ValueError):
                pass
    return ids


_tz_cache: dict = {"mtime": None, "tz": None}


def get_local_tz() -> ZoneInfo:
    """The household timezone set in the GUI (settings.yaml location.timezone; legacy
    lokasjon.tidssone as fallback; UTC when unset). Every module uses this one function.

    Follows a GUI change at once, in every process (API, Argus, night jobs): the file's mtime is
    checked on each call (a cheap stat) and the timezone is re-read only when the file changed —
    saving the location in the GUI does not reload the cached config by itself."""
    path = CONFIG_DIR / "settings.yaml"
    try:
        mtime = path.stat().st_mtime
        if mtime != _tz_cache["mtime"] or _tz_cache["tz"] is None:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            loc = data.get("location") or data.get("lokasjon") or {}
            _tz_cache["tz"] = ZoneInfo(loc.get("timezone") or loc.get("tidssone") or "UTC")
            _tz_cache["mtime"] = mtime
        return _tz_cache["tz"]
    except Exception:
        return _tz_cache["tz"] or ZoneInfo("UTC")


LOG_FLUSH_DEFAULT_SECONDS = 2.0
LOG_FLUSH_MAX_SECONDS = 30.0
_log_flush_cache: dict = {"mtime": None, "seconds": LOG_FLUSH_DEFAULT_SECONDS}


def get_log_flush_seconds() -> float:
    """How often buffered logs are written to disk (settings.yaml logging.flush_interval_seconds,
    set in the GUI under Settings → General → Logs and data; P131). 0 = write every line at once.
    Clamped to 0–30 s. Follows a GUI change at once in every process — same mtime check as
    get_local_tz()."""
    path = CONFIG_DIR / "settings.yaml"
    try:
        mtime = path.stat().st_mtime
        if mtime != _log_flush_cache["mtime"]:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = (data.get("logging") or {}).get(
                "flush_interval_seconds", LOG_FLUSH_DEFAULT_SECONDS
            )
            _log_flush_cache["seconds"] = min(max(float(raw), 0.0), LOG_FLUSH_MAX_SECONDS)
            _log_flush_cache["mtime"] = mtime
    except Exception:
        pass
    return _log_flush_cache["seconds"]


def now_local() -> datetime:
    """The household's «now» — an aware datetime in the GUI timezone (N16). Use this for every time
    or date a person or the model sees, and for clock-time scheduling («at 07:00»). Never the
    machine clock: in Docker that is UTC. Durations use time.monotonic(); stored timestamps use
    datetime.now(timezone.utc)."""
    return datetime.now(get_local_tz())


def today_local() -> str:
    """Today's date in the household timezone, ISO format (YYYY-MM-DD)."""
    return now_local().date().isoformat()


def local_from_ts(ts: float) -> datetime:
    """A Unix timestamp as an aware datetime in the household timezone."""
    return datetime.fromtimestamp(ts, get_local_tz())


_home_loc_cache: dict = {"mtime": None, "loc": None}


def get_home_location() -> dict:
    """The household's place set in the GUI: {"city": str, "country": str} from settings.yaml
    location (legacy lokasjon.sted/land as fallback; empty strings when unset). Same mtime check as
    get_local_tz(), so a GUI change is picked up at once without a reload."""
    path = CONFIG_DIR / "settings.yaml"
    try:
        mtime = path.stat().st_mtime
        if mtime != _home_loc_cache["mtime"] or _home_loc_cache["loc"] is None:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            loc = data.get("location") or data.get("lokasjon") or {}
            _home_loc_cache["loc"] = {
                "city": str(loc.get("city") or loc.get("sted") or "").strip(),
                "country": str(loc.get("country") or loc.get("land") or "").strip(),
            }
            _home_loc_cache["mtime"] = mtime
        return dict(_home_loc_cache["loc"])
    except Exception:
        return dict(_home_loc_cache["loc"] or {"city": "", "country": ""})


_DEFAULT_TRUSTED_PROXIES = ["caddy", "127.0.0.1", "::1"]
_net_cache: dict = {"mtime": None, "net": None}


# Fallback when no local subnet is set (P127): every 192.168.x.y home network, not one house's /24.
# Not 172.16.0.0/12 (Docker Desktop's NAT gateway would make every client «home») and not 10.0.0.0/8
# (collides with the VPN subnet). The onboarding suggests the user's own /24 instead.
DEFAULT_LOCAL_SUBNET = "192.168.0.0/16"


def get_network_settings() -> dict:
    """Network / access settings set in the GUI (Settings → General → Network and access, Expert):
    {"local_subnets": [..], "vpn_subnet": str, "trusted_proxies": [..], "api_docs": bool} from
    settings.yaml `network:`. Same mtime check as get_local_tz(), so a GUI change applies at once.

    trusted_proxies: the only peers whose X-Forwarded-For is believed (rule 27 — a reverse proxy the
    user configured is trusted, nobody else). Default: the bundled Caddy (compose service name) and
    loopback (a proxy on the same host). api_docs: /docs, /redoc, /openapi.json on (default off)."""
    path = CONFIG_DIR / "settings.yaml"
    try:
        mtime = path.stat().st_mtime
        if mtime != _net_cache["mtime"] or _net_cache["net"] is None:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            net = data.get("network") or {}
            subnets = net.get("local_subnets") or [net.get("local_subnet") or DEFAULT_LOCAL_SUBNET]
            if isinstance(subnets, str):
                subnets = [subnets]
            proxies = net.get("trusted_proxies")
            if proxies is None:
                proxies = list(_DEFAULT_TRUSTED_PROXIES)
            elif isinstance(proxies, str):
                proxies = [proxies]
            _net_cache["net"] = {
                "local_subnets": [str(x).strip() for x in subnets if str(x).strip()],
                "vpn_subnet": str(net.get("vpn_subnet") or "10.0.0.0/24").strip(),
                "trusted_proxies": [str(x).strip() for x in proxies if str(x).strip()],
                "api_docs": bool(net.get("api_docs", False)),
            }
            _net_cache["mtime"] = mtime
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _net_cache["net"].items()}
    except Exception:
        cached = _net_cache["net"]
        if cached:
            return {k: (list(v) if isinstance(v, list) else v) for k, v in cached.items()}
        return {
            "local_subnets": [DEFAULT_LOCAL_SUBNET],
            "vpn_subnet": "10.0.0.0/24",
            "trusted_proxies": list(_DEFAULT_TRUSTED_PROXIES),
            "api_docs": False,
        }


def get_qdrant_api_key(write: bool = True) -> str | None:
    """Return Qdrant API key from configs/qdrant.env.

    Uses read-only key for search operations (write=False) and the full key
    for upsert/delete/collection management (write=True).
    Returns None if qdrant.env is absent — client operates without auth.
    """
    env_path = CONFIG_DIR / "qdrant.env"
    try:
        keys: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            keys[k.strip()] = v.strip()
        if write:
            return keys.get("QDRANT__SERVICE__API_KEY") or None
        return (
            keys.get("QDRANT__SERVICE__READ_ONLY_API_KEY")
            or keys.get("QDRANT__SERVICE__API_KEY")
            or None
        )
    except OSError:
        return None


def get_service(section: str, key: str | None = None):
    """
    Return a services.yaml section or a specific key within it.

    Examples:
        get_service("home_assistant")           → {"url": ..., "timeout": ...}
        get_service("home_assistant", "url")    → "http://..."
        get_service("internal", "ha_gateway")  → "http://127.0.0.1:8002"
        get_service("storage", "qdrant")       → "http://127.0.0.1:6333"
    """
    sec = _SERVICES.get(section, {})
    if key is None:
        return sec
    return sec.get(key)


def _read_yaml_live(name: str) -> dict:
    """configs/<name>, re-read only when the file changed (mtime_ns + inode + size — a cheap stat per call).

    For settings the GUI writes straight to disk without a config reload — same idea as get_local_tz().
    Returns {} if the file is missing; an unreadable file keeps the last good value instead of caching {} (P134).
    Shared object — never mutate the result."""
    data = config_store.read_yaml_cached(CONFIG_DIR / name, copy_result=False)
    return data if isinstance(data, dict) else {}


def get_capability_map() -> dict:
    """The GUI-written capability map, re-read when the file changes (P127)."""
    return _read_yaml_live(CAPABILITY_MAP_PATH.name)


DEFAULT_MODELS_DIR = "/kaare/models"


def get_models_dir() -> Path:
    """Root folder for model files KTSB downloads from the GUI (settings.yaml models_dir,
    GUI: Settings → LLM/Model → Model storage). Each feature uses its own sub-folder."""
    return Path(_read_yaml_live("settings.yaml").get("models_dir") or DEFAULT_MODELS_DIR)


# P133: how Kåre's memory drawers reach the prompt. One accessor, always current (GUI writes
# settings.yaml directly). Defaults = the shipped behaviour (rule 24/25).
MEMORY_PROMPT_DEFAULTS = {
    "relevance_enabled": True,  # «Relevant nå» block — off without an embedding service anyway
    "top_k": 3,  # D12
    "min_score": 0.50,  # D12 — measured 2026-10-09 (BGE-M3): hits ≥ 0.50, noise ≤ 0.50
    "echo_min_score": 0.60,  # a new row this close to a resolved conclusion is an echo (C2)
    "lessons_max_chars": 1500,  # lessons drawer, always prose (D3/D7)
    "lessons_write_max_chars": 600,  # one lesson is one rule, not a story (N25)
    "core_facts_max_chars": 1500,  # top-ranked world facts + resolved conclusions as prose (D3)
    "index_max_chars": 2000,  # title index of kare notes + other world facts (D2)
    "resolve_roles": ["admin"],  # who may mark a topic resolved (D11)
}


def get_memory_prompt_config() -> dict:
    """settings.yaml `memory_prompt:` over MEMORY_PROMPT_DEFAULTS; bad values fall back."""
    raw = dict(_read_yaml_live("settings.yaml").get("memory_prompt") or {})
    cfg = dict(MEMORY_PROMPT_DEFAULTS)
    for key, default in MEMORY_PROMPT_DEFAULTS.items():
        if key not in raw:
            continue
        val = raw[key]
        try:
            if isinstance(default, bool):
                cfg[key] = bool(val)
            elif isinstance(default, int):
                cfg[key] = int(val) if int(val) > 0 else default
            elif isinstance(default, float):
                cfg[key] = float(val) if 0.0 < float(val) < 1.0 else default
            elif isinstance(default, list):
                cfg[key] = [str(v) for v in val] if isinstance(val, list) else default
        except (TypeError, ValueError):
            cfg[key] = default
    return cfg


def get_embedding_config() -> dict:
    """The embedding section of services.yaml (BGE-M3 service), always current."""
    return dict(_read_yaml_live("services.yaml").get("embedding") or {})


def get_memory_embed_config() -> dict:
    """The memory_embed section of services.yaml (semantic memory), always current."""
    return dict(_read_yaml_live("services.yaml").get("memory_embed") or {})


def get_ssh_nodes() -> dict:
    """Return ssh_nodes.yaml as dict with keys 'local' and 'nodes'.

    'local': {sudo_enabled: bool}
    'nodes': {id: {label, host, user, port, ssh_key, node_type, sudo_enabled, sudo_commands}}
    Falls back to empty nodes if the file is missing or unreadable.
    """
    p = CONFIG_DIR / "ssh_nodes.yaml"
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    return {
        "local": data.get("local", {"sudo_enabled": False}),
        "nodes": data.get("nodes") or {},
    }


def get_tool_permissions() -> dict:
    """Return the raw tool_permissions config (always_included + roles dict)."""
    return _TOOL_PERMISSIONS


def is_agent_tool_enabled(agent: str, tool: str, default: bool = True) -> bool:
    """Check if a tool is enabled for a given agent (reads agent_tools in tool_permissions.yaml)."""
    return _TOOL_PERMISSIONS.get("agent_tools", {}).get(agent, {}).get(tool, default)


def is_embedding_enabled() -> bool:
    """Return False if embedding is explicitly disabled in services.yaml."""
    return bool(_SERVICES.get("embedding", {}).get("enabled", True))


def is_service_enabled(service_name: str) -> bool:
    """Return True if the named service is enabled in capability_map.yaml services: section.

    Defaults to True if the service is not listed (opt-out semantics).
    Services: embedding, agents, voice, jing, jang.
    """
    return bool(_CAPABILITY_SERVICES.get(service_name, {}).get("enabled", True))


def is_stt_enabled() -> bool:
    """Return False if STT/Whisper is explicitly disabled in services.yaml."""
    return bool(_SERVICES.get("voice", {}).get("stt", {}).get("enabled", True))


def save_tool_permissions(data: dict) -> None:
    """Write new tool_permissions.yaml and hot-reload the cache."""
    config_store.write_yaml(CONFIG_DIR / "tool_permissions.yaml", data, "gui:tool_permissions")
    _load_tool_permissions()


def filter_tools_by_model(tools: list, size_b: float) -> list:
    """
    Remove tools the current model is too small to use reliably.

    Hard rule: models under 9B get no tools — they can't handle tool-calling
    schemas reliably and the JSON overhead inflates the prompt unnecessarily.
    9B (e.g. qwen3.5-abliterated Q4_K_M, 6.6GB) is the minimum for tool use.

    size_b=999.0 is used for vLLM/cloud to skip filtering entirely.
    """
    if size_b < 9.0:
        _log.info("filter_tools_by_model: model=%.2fB < 9B — no tools", size_b)
        return []
    # PLC0415: circular: config is imported by tools.definitions
    from kaare_core.tools.definitions import TOOL_MODEL_TIERS  # noqa: PLC0415

    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    result = []
    removed = []
    for tool in tools:
        name: str = tool["function"]["name"]
        if name in always_names:
            result.append(tool)
            continue
        min_size = TOOL_MODEL_TIERS.get(name, 0.0)
        if size_b >= min_size:
            result.append(tool)
        else:
            removed.append(name)
    if removed:
        _log.info("filter_tools_by_model: removed %s (model=%.1fB)", removed, size_b)
    return result


def filter_tools_by_tier_ceiling(tools: list, max_tier: float) -> list:
    """
    Restrict tools to those at or below a given capability tier ceiling.

    Unlike filter_tools_by_model(), there is no "under 9B = zero tools" cutoff —
    this is for the fallback model's own degraded-mode token budget (num_ctx: 8192
    on port 11445), not a capability check. always_included tools always pass.
    """
    # PLC0415: circular: config is imported by tools.definitions
    from kaare_core.tools.definitions import TOOL_MODEL_TIERS  # noqa: PLC0415

    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    result = []
    removed = []
    for tool in tools:
        name: str = tool["function"]["name"]
        if name in always_names:
            result.append(tool)
            continue
        min_size = TOOL_MODEL_TIERS.get(name, 0.0)
        if min_size <= max_tier:
            result.append(tool)
        else:
            removed.append(name)
    if removed:
        _log.info("filter_tools_by_tier_ceiling: removed %s (max_tier=%.1f)", removed, max_tier)
    return result


# Tools that read shared logs holding other users' text: the think cache (prompt + reply, no
# user_id) and Jing/Jang's inner thoughts (fed by the Argus digest, which quotes the start of
# every user message). Admin only, whatever tool_permissions.yaml says — also enforced in
# kaare_core/tools/executor_memory.py.
ADMIN_ONLY_TOOLS: frozenset[str] = frozenset(
    {"thought_history", "les_tankehistorikk", "inner_thoughts", "les_indre_tanker"}
)


def get_tools_for_role(role: str, user_id: str = "") -> list:
    """
    Return the filtered tool list for the given user role and language.

    Reads tool_permissions.yaml. The always_included tools are always appended.
    library_online in a role's list upgrades the library tool to include the online action.
    admin (or roles with "all") receives the full list, but capability guards still apply:
    - cloud.enabled=false  → library tool loses the online action
    - agents service off   → library tool removed, søk_nett added as fallback
    """
    # PLC0415: circular: config is imported by tools.definitions
    from kaare_core.tools.definitions import get_library_no_online, get_tools  # noqa: PLC0415

    lang = get_lang(user_id) if user_id else "nb"
    all_tools = get_tools(lang)
    lib_no_online = get_library_no_online(lang)

    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    roles_cfg: dict = _TOOL_PERMISSIONS.get("roles", {})
    if (
        role in set(_TOOL_PERMISSIONS.get("no_always_included", ["household", "stranger"]))
        or role not in roles_cfg
    ):
        always_names = (
            set()
        )  # knowledge boundary for the system identities and unknown roles (P120)
    # Unknown role → the most restricted list, never a broader one (fail closed, P120).
    role_list: list[str] = roles_cfg.get(role, roles_cfg.get("stranger", []))

    if "all" in role_list:
        candidate = list(all_tools)
    else:
        allowed: set[str] = set(role_list)
        has_library_online = "library_online" in allowed
        candidate = []
        for tool in all_tools:
            name: str = tool["function"]["name"]
            if name in always_names:
                candidate.append(tool)
            elif name == "library":
                if "library" in allowed:
                    candidate.append(tool if has_library_online else lib_no_online)
            elif name in allowed:
                candidate.append(tool)

    # Capability guards — applied regardless of role
    cloud_enabled = _LLM_CFG.get("cloud", {}).get("enabled", True)
    agents_enabled = is_service_enabled("agents")

    result: list = []
    had_library = False
    has_søk_nett = any(t["function"]["name"] == "web_search" for t in candidate)

    for tool in candidate:
        name: str = tool["function"]["name"]
        if name == "library":
            had_library = True
            if not agents_enabled:
                continue  # library needs agents; søk_nett added below as fallback
            result.append(tool if cloud_enabled else lib_no_online)
        else:
            result.append(tool)

    # If agents off and role had library but not søk_nett → add direct web search
    if had_library and not agents_enabled and not has_søk_nett:
        for tool in get_tools(lang):
            if tool["function"]["name"] == "web_search":
                result.append(tool)
                break

    if role != "admin":
        result = [t for t in result if t["function"]["name"] not in ADMIN_ONLY_TOOLS]
    return result


def _report_tool_consistency_alert(mismatches: list[str]) -> None:
    """
    Write a CRITICAL-severity JSON line to route_decisions.log so Argus picks it up
    on its next poll and surfaces it on the admin dashboard.

    Cannot import kaare_api._route_log() here — kaare_api.py imports FROM
    kaare_core.config, so the reverse import would be circular. Writes the same JSON
    shape directly instead (same "known file, known shape" idiom _route_log() uses).
    """
    _log.critical("tool_permissions consistency check failed: %s", mismatches)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "source": "kaare",
        "subsystem": "routing",
        "stage": "tool_consistency_mismatch",
        "error": True,
        "mismatches": mismatches,
    }
    try:
        with open("/kaare/logs/route_decisions.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        _log.exception("_report_tool_consistency_alert: failed to write route_decisions.log")


def validate_tool_permissions_consistency() -> list[str]:
    """
    Compare tool names defined in kaare_core.tools.definitions against every name
    referenced in tool_permissions.yaml's always_included and role lists.

    Returns a list of human-readable mismatch descriptions; empty list means fully
    consistent. Flags only config → dead-definition drift (a role list or
    always_included still names a tool that no longer exists in definitions.py — a
    stale rename, the exact P83 symptom that made Kåre lose its always_included
    tools). Deliberately does NOT flag the reverse direction (a tool defined but never
    referenced in any role list): most of those are intentionally admin-only, reachable
    only through the "admin: [all]" bypass and never meant to appear in a role list at
    all (e.g. ssh_command, restart_docker_container) — firing CRITICAL on all of those
    every single reload would be permanent noise, not a signal, and would train the
    admin to ignore this alert channel. (Confirmed empirically 2026-08-01: this
    direction produced 10 hits against the live config, only 2 of which — write_reflex,
    household — were real gaps, per the Decisions section.)

    agent_tools: is deliberately excluded from the comparison — those keys belong to
    Mechanic's own separate internal tool registry (utforsk, inspiser, hukommelse, ...),
    not this namespace. Confirmed empirically 2026-08-01: comparing them against
    definitions.py produced 8 false "orphaned config" hits.
    """
    # PLC0415: circular: config is imported by tools.definitions
    from kaare_core.tools.definitions import get_tools  # noqa: PLC0415

    try:
        defined_names = {t["function"]["name"] for t in get_tools("nb")}
    except Exception:
        _log.critical("validate_tool_permissions_consistency: failed to load tool definitions")
        return ["failed to load kaare_core.tools.definitions.get_tools()"]

    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    roles_cfg: dict = _TOOL_PERMISSIONS.get("roles", {})
    referenced_names: set[str] = set(always_names)
    for role_list in roles_cfg.values():
        for name in role_list:
            # "all" = admin bypass keyword, "library_online" = upgrade flag, not a tool
            if name not in ("all", "library_online"):
                referenced_names.add(name)

    orphaned_config = sorted(referenced_names - defined_names)
    if orphaned_config:
        return [
            f"tool_permissions.yaml references tools not defined in definitions.py: {orphaned_config}"
        ]
    return []


def verify_always_included_survives(role: str = "adult") -> list[str]:
    """
    Confirm every configured always_included tool actually survives
    get_tools_for_role() filtering for a real, non-bypass role.

    Tests against "adult" by design, not "admin" — admin skips the role filter
    entirely via the "all" shortcut and could never catch a bug in the filter path
    itself. Catches the exact bug shape that caused Kåre to report having no tools
    active: an always_included name silently dropped due to a
    definitions.py/tool_permissions.yaml mismatch.
    """
    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    if role != "admin":
        always_names -= ADMIN_ONLY_TOOLS  # removed on purpose for every role but admin
    if not always_names:
        return []
    try:
        result_names = {t["function"]["name"] for t in get_tools_for_role(role)}
    except Exception:
        _log.critical("verify_always_included_survives: get_tools_for_role(%r) raised", role)
        return [f"get_tools_for_role({role!r}) raised an exception"]

    missing = sorted(always_names - result_names)
    if missing:
        return [f"always_included tools missing from get_tools_for_role({role!r}): {missing}"]
    return []


# Runs consistency checks once at first import (process startup) — the calls to
# _load_tool_permissions() must sit here, after get_tools_for_role() and the two
# validators above are all defined. Calling it at its old position near the top of
# this file (before get_tools_for_role() existed) would raise NameError on import.
_load_tool_permissions()
_load_capability_services()
_LLM_CFG = apply_agent_pool(_LLM_CFG)
