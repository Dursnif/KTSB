"""
Check that every Ollama-backed LLM role points at a model that is actually pulled (P116 B13).

Agent-pool roles (Miss Kåre, Mechanic, Miss Library, fallback) overflow onto the other pool
instance when their own is busy (model_lock.lock_agent_pool), so their model must exist on
every instance in the pool — not only on the role's home port.

Before this, a role whose model was missing in Ollama simply failed at call time with no hint
to the user — a typical fresh Docker install trap. The check uses get_llm_config(), so roles
that share another role's instance (share_with / agent pool) are resolved to the URL they
really use, not a stale base_url in the YAML.
"""

import asyncio
import logging

import httpx

import kaare_core.config as _config
from kaare_core import model_lock
from kaare_core.config import get_llm_config, get_model

logger = logging.getLogger(__name__)

_last_result: dict[str, dict] = {}


def _ollama_roles() -> dict[str, dict]:
    """role → {"base_url", "model"} for enabled Ollama roles (resolved config)."""
    out: dict[str, dict] = {}
    # read at call time — reload_config() rebinds the module-level dict
    for role in list((_config._LLM_CFG or {}).keys()):
        try:
            cfg = get_llm_config(role)
        except Exception:
            continue
        if (
            not isinstance(cfg, dict)
            or cfg.get("provider") != "ollama"
            or cfg.get("enabled") is False
        ):
            continue
        base_url = (cfg.get("base_url") or "").rstrip("/")
        model = get_model(cfg.get("model_role", role)) if cfg.get("model_role", role) else ""
        if base_url and model:
            out[role] = {"base_url": base_url, "model": model, "urls": _instance_urls(base_url)}
    return out


def _instance_urls(base_url: str) -> list[str]:
    """Every instance a role can land on: its own base_url, plus the other pool instances when
    it belongs to the agent pool (the same overflow lock_agent_pool does)."""
    return model_lock.pool_order(base_url)


def _has_model(pulled: set[str], model: str) -> bool:
    if model in pulled:
        return True
    return bool(":" not in model and f"{model}:latest" in pulled)


async def check_ollama_role_models() -> dict[str, dict]:
    """role → {"model", "base_url", "status": "ok" | "missing" | "unreachable"}. Never raises."""
    global _last_result
    roles = _ollama_roles()
    pulled_by_url: dict[str, set[str] | None] = {}

    async def _tags(url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{url}/api/tags")
                r.raise_for_status()
                pulled_by_url[url] = {m.get("name", "") for m in r.json().get("models", [])}
        except Exception:
            pulled_by_url[url] = None

    await asyncio.gather(*(_tags(u) for u in {u for r in roles.values() for u in r["urls"]}))
    result = {}
    for role, info in roles.items():
        missing_on = [
            u
            for u in info["urls"]
            if pulled_by_url.get(u) is not None and not _has_model(pulled_by_url[u], info["model"])
        ]
        unreachable = [u for u in info["urls"] if pulled_by_url.get(u) is None]
        if missing_on:
            status, where = "missing", missing_on
        elif unreachable:
            status, where = "unreachable", unreachable
        else:
            status, where = "ok", []
        result[role] = {
            "model": info["model"],
            "base_url": ", ".join(where) or info["base_url"],
            "status": status,
        }
    _last_result = result
    return result


async def log_missing_models() -> None:
    """Startup hook: log a warning for each Ollama role whose model is not pulled."""
    try:
        for role, info in (await check_ollama_role_models()).items():
            if info["status"] == "missing":
                logger.warning(
                    "LLM role '%s': model '%s' is not pulled on %s — calls for this role will fail "
                    "until it is downloaded (`ollama pull %s`)",
                    role,
                    info["model"],
                    info["base_url"],
                    info["model"],
                )
    except Exception as e:
        logger.warning("LLM model check failed: %s", e)
