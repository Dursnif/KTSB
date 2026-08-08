import json
import logging
import yaml
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

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
    # Runs on every load — process startup (call moved to end of this file, after
    # get_tools_for_role() is defined) and every hot-reload (save_tool_permissions(),
    # reload_config()). Never let a check failure break config loading itself.
    try:
        mismatches = validate_tool_permissions_consistency() + verify_always_included_survives("adult")
        if mismatches:
            _report_tool_consistency_alert(mismatches)
    except Exception:
        _log.exception("_load_tool_permissions: consistency check failed")


def _load_capability_services() -> None:
    global _CAPABILITY_SERVICES
    try:
        data = yaml.safe_load(_CAPABILITY_MAP_PATH.read_text(encoding="utf-8")) or {}
        _CAPABILITY_SERVICES = data.get("services", {})
    except Exception:
        _CAPABILITY_SERVICES = {}


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


def get_local_tz() -> ZoneInfo:
    """Return the configured local timezone as a ZoneInfo object (from settings.yaml location.timezone)."""
    try:
        loc = _SETTINGS.get("location") or _SETTINGS.get("lokasjon", {})
        tz_str = loc.get("timezone") or loc.get("tidssone", "UTC")
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("UTC")


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
        return keys.get("QDRANT__SERVICE__READ_ONLY_API_KEY") or keys.get("QDRANT__SERVICE__API_KEY") or None
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
    p = CONFIG_DIR / "tool_permissions.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
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
    from kaare_core.tools.definitions import TOOL_MODEL_TIERS
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
    from kaare_core.tools.definitions import TOOL_MODEL_TIERS
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


def get_tools_for_role(role: str, user_id: str = "") -> list:
    """
    Return the filtered tool list for the given user role and language.

    Reads tool_permissions.yaml. The always_included tools are always appended.
    library_online in a role's list upgrades the library tool to include the online action.
    admin (or roles with "all") receives the full list, but capability guards still apply:
    - cloud.enabled=false  → library tool loses the online action
    - agents service off   → library tool removed, søk_nett added as fallback
    """
    from kaare_core.tools.definitions import get_tools, get_library_no_online
    from kaare_core.tools.i18n import get_lang

    lang = get_lang(user_id) if user_id else "nb"
    all_tools = get_tools(lang)
    lib_no_online = get_library_no_online(lang)

    always_names: set[str] = set(_TOOL_PERMISSIONS.get("always_included", []))
    roles_cfg: dict = _TOOL_PERMISSIONS.get("roles", {})
    role_list: list[str] = roles_cfg.get(role, roles_cfg.get("adult", []))

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
    cloud_enabled  = _LLM_CFG.get("cloud", {}).get("enabled", True)
    agents_enabled = is_service_enabled("agents")

    result: list = []
    had_library  = False
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
        "ts": datetime.now(timezone.utc).isoformat(),
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
    from kaare_core.tools.definitions import get_tools

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
