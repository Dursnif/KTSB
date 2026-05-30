#!/usr/bin/env python3
"""
kaare_warmup.py – Pre-loads all Ollama models with keep_warm=true into GPU/RAM.

Reads llm.yaml at runtime — no hardcoded model names or hardware references.
Runs as a systemd oneshot service. Exits 1 if any warm model fails to load.

Grouping logic: roles sharing the same base_url are loaded sequentially
(Ollama processes one model at a time per instance). Roles on different
Ollama instances are loaded in parallel.
"""

import asyncio
import logging
import sys
import time

import httpx
import yaml

sys.path.insert(0, "/kaare")
from kaare_core.config import get_model as _get_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("warmup")

_LLM_YAML = "/kaare/configs/llm.yaml"

CONTAINER_WAIT_TIMEOUT = 120
CONTAINER_POLL_INTERVAL = 3

_OLLAMA_PROVIDERS = {"ollama", "openvino"}


def _build_warmup_list() -> list[dict]:
    """Read llm.yaml and return all roles with keep_warm=true and an Ollama provider."""
    try:
        with open(_LLM_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        log.error("Could not read llm.yaml: %s", e)
        return []

    warmup: list[dict] = []
    for role, s in cfg.items():
        if not isinstance(s, dict):
            continue
        if not s.get("keep_warm", False):
            continue
        provider = s.get("provider", "ollama")
        if provider not in _OLLAMA_PROVIDERS:
            log.debug("[%s] keep_warm=true but provider=%s — skipping (not Ollama)", role, provider)
            continue
        base_url = s.get("base_url", "")
        if not base_url:
            log.warning("[%s] keep_warm=true but no base_url — skipping", role)
            continue
        model_role = s.get("model_role", role)
        try:
            model = _get_model(model_role)
        except Exception:
            model = ""
        if not model:
            log.warning("[%s] keep_warm=true but no model configured — skipping", role)
            continue
        num_ctx = (s.get("options") or {}).get("num_ctx")
        raw_timeout = s.get("timeout", 300)
        timeout = max(float(raw_timeout), 300.0) if isinstance(raw_timeout, (int, float)) else 300.0
        warmup.append({
            "role":    role,
            "name":    f"{role} ({model})",
            "base":    base_url,
            "model":   model,
            "timeout": timeout,
            "num_ctx": num_ctx,
        })
    return warmup


async def _wait_for_container(base: str, name: str) -> bool:
    deadline = time.monotonic() + CONTAINER_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base}/api/tags")
                if r.status_code == 200:
                    log.info("[%s] Ollama ready", name)
                    return True
        except Exception:
            pass
        await asyncio.sleep(CONTAINER_POLL_INTERVAL)
    log.error("[%s] Ollama not reachable after %ds", name, CONTAINER_WAIT_TIMEOUT)
    return False


async def _check_vram(base: str, model: str, name: str) -> int:
    """Return size_vram bytes for a loaded model via /api/ps. 0 = CPU."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/api/ps")
            r.raise_for_status()
            for m in r.json().get("models", []):
                if m.get("name", "").startswith(model.split(":")[0]):
                    return m.get("size_vram", 0)
    except Exception as e:
        log.warning("[%s] Could not check VRAM: %s", name, e)
    return 0


async def _load_model(m: dict) -> bool:
    name    = m["name"]
    base    = m["base"]
    model   = m["model"]
    timeout = m["timeout"]

    if not await _wait_for_container(base, name):
        return False

    log.info("[%s] Loading %s ...", name, model)
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            opts: dict = {"num_predict": 1}
            if m.get("num_ctx"):
                opts["num_ctx"] = m["num_ctx"]
            r = await client.post(f"{base}/api/generate", json={
                "model":      model,
                "prompt":     "",
                "keep_alive": -1,
                "stream":     False,
                "options":    opts,
            })
            r.raise_for_status()
        elapsed = time.monotonic() - t0
        vram = await _check_vram(base, model, name)
        if vram == 0:
            log.warning("[%s] Running on CPU (size_vram=0) — GPU unavailable or VRAM full? (%.1fs)", name, elapsed)
        else:
            log.info("[%s] Ready in %.1fs (VRAM: %.1f GiB)", name, elapsed, vram / 1e9)
        return True
    except Exception as e:
        elapsed = time.monotonic() - t0
        log.error("[%s] Failed after %.1fs: %s", name, elapsed, e)
        return False


async def _load_group(group: list[dict]) -> list[bool]:
    """Load models for one Ollama instance sequentially."""
    results = []
    for m in group:
        results.append(await _load_model(m))
    return results


async def main() -> None:
    models = _build_warmup_list()
    if not models:
        log.info("No models with keep_warm=true — nothing to warm up.")
        return

    # Group by base_url: parallel across instances, sequential within each
    by_base: dict[str, list[dict]] = {}
    for m in models:
        by_base.setdefault(m["base"], []).append(m)

    log.info(
        "=== Kåre warmup: %d model(s) across %d Ollama instance(s) ===",
        len(models), len(by_base),
    )
    t0 = time.monotonic()

    all_results = await asyncio.gather(*[_load_group(g) for g in by_base.values()])
    flat = [r for group in all_results for r in group]

    total = time.monotonic() - t0
    ok = sum(flat)
    log.info("=== Warmup done in %.1fs — %d/%d models ready ===", total, ok, len(models))

    if ok < len(models):
        log.warning("Not all models loaded — system may be unstable")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
