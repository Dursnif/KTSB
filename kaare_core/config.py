import yaml
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_DIR = Path("/kaare/configs")
_DEFAULT_DIR = Path("/kaare/configs_default")
_CAPABILITY_MAP_PATH = Path("/kaare/capability_map.yaml")

with open(CONFIG_DIR / "models.yaml", "r", encoding="utf-8") as f:
    MODELS: dict = yaml.safe_load(f)

with open(CONFIG_DIR / "llm.yaml", "r", encoding="utf-8") as f:
    _LLM_CFG: dict = yaml.safe_load(f)

with open(CONFIG_DIR / "services.yaml", "r", encoding="utf-8") as f:
    _SERVICES: dict = yaml.safe_load(f)

with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS: dict = yaml.safe_load(f)

_TOOL_PERMISSIONS: dict = {}
_CAPABILITY_SERVICES: dict = {}


def _load_tool_permissions() -> None:
    global _TOOL_PERMISSIONS
    p = CONFIG_DIR / "tool_permissions.yaml"
    try:
        _TOOL_PERMISSIONS = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        _TOOL_PERMISSIONS = {}


def _load_capability_services() -> None:
    global _CAPABILITY_SERVICES
    try:
        data = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
        _CAPABILITY_SERVICES = data.get("services", {})
    except Exception:
        _CAPABILITY_SERVICES = {}


_load_tool_permissions()
_load_capability_services()


def reload_capability_services() -> None:
    """Hot-reload the services section from capability_map.yaml."""
    _load_capability_services()


def reload_config() -> list[str]:
    """Hot-reload all YAML caches in this module. Called by /api/reload."""
    global MODELS, _LLM_CFG, _SERVICES, _SETTINGS
    reloaded = []
    for filename, setter in [
        ("models.yaml",          lambda v: _set_models(v)),
        ("llm.yaml",             lambda v: _set_llm(v)),
        ("services.yaml",        lambda v: _set_services(v)),
        ("settings.yaml",        lambda v: _set_settings(v)),
        ("tool_permissions.yaml", lambda _: _load_tool_permissions()),
    ]:
        try:
            data = yaml.safe_load((CONFIG_DIR / filename).read_text(encoding="utf-8"))
            setter(data)
            reloaded.append(filename)
        except Exception:
            pass
    return reloaded


def _set_models(v: dict) -> None:
    global MODELS; MODELS = v

def _set_llm(v: dict) -> None:
    global _LLM_CFG; _LLM_CFG = v

def _set_services(v: dict) -> None:
    global _SERVICES; _SERVICES = v

def _set_settings(v: dict) -> None:
    global _SETTINGS; _SETTINGS = v


def get_model(role: str) -> str:
    """Return the Ollama model name for a logical role (kare, miss_kare, library, embed, cloud)."""
    return MODELS[role]


def get_llm_config(section: str) -> dict:
    """Return the full llm.yaml section (options, timeout, base_url, …) for a role."""
    return _LLM_CFG[section]


def get_local_tz() -> ZoneInfo:
    """Return the configured local timezone as a ZoneInfo object (from settings.yaml location.timezone)."""
    try:
        loc = _SETTINGS.get("location") or _SETTINGS.get("lokasjon", {})
        tz_str = loc.get("timezone") or loc.get("tidssone", "UTC")
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("UTC")


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
    p = CONFIG_DIR / "tool_permissions.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    _load_tool_permissions()


def get_tools_for_role(role: str) -> list:
    """
    Return the filtered KAARE_TOOLS list for the given user role.

    Reads tool_permissions.yaml. The always_included tools are always appended.
    library_online in a role's list upgrades the library tool to include the online action.
    admin (or roles with "all") receives the full unfiltered list.
    """
    from kaare_core.tools.definitions import KAARE_TOOLS, LIBRARY_NO_ONLINE

    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    roles_cfg: dict = _TOOL_PERMISSIONS.get("roles", {})
    role_list: list[str] = roles_cfg.get(role, roles_cfg.get("adult", []))

    if "all" in role_list:
        return KAARE_TOOLS

    allowed: set[str] = set(role_list)
    has_library_online = "library_online" in allowed

    result = []
    for tool in KAARE_TOOLS:
        name: str = tool["function"]["name"]
        if name in always_names:
            result.append(tool)
        elif name == "library":
            if "library" in allowed:
                result.append(tool if has_library_online else LIBRARY_NO_ONLINE)
        elif name in allowed:
            result.append(tool)
    return result
