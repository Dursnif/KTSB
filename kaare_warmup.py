#!/usr/bin/env python3
"""
kaare_warmup.py – Laster alle LLM-modeller inn i GPU/RAM ved oppstart.
Kjøres som systemd oneshot-tjeneste. Blokkerer til alle modeller er klare.

Laster parallelt der modellene bruker separate GPUer:
  - GPU 1 (Blackwell): Kåre 27B
  - GPU 0 (5060 Ti):   Miss Kåre 9B + Agents 8B (begge passer)
  - CPU:               Embedding 8B
"""

import asyncio
import logging
import sys
import time

import httpx

sys.path.insert(0, "/kaare")
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, get_service as _svc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("warmup")

def _ctx(section: str) -> int:
    return _llm(section)["options"]["num_ctx"]

MODELS = [
    {
        "name":    "Kåre 27B (Blackwell GPU 1)",
        "base":    _svc("ollama", "kare"),
        "model":   _cfg_model("kare"),
        "timeout": 600,
        "type":    "generate",
        "gpu":     True,
        "options": {"num_ctx": _ctx("default"), "num_predict": 1},
    },
    {
        "name":    "Miss Kåre 9B (5060 Ti GPU 0)",
        "base":    _svc("ollama", "miss_kare"),
        "model":   _cfg_model("miss_kare"),
        "timeout": 180,
        "type":    "generate",
        "gpu":     True,
        "options": {"num_ctx": _ctx("miss_kare"), "num_predict": 1},
    },
    {
        "name":    "Frøken Library 8B (5060 Ti GPU 0)",
        "base":    _svc("ollama", "library"),
        "model":   _cfg_model("library"),
        "timeout": 180,
        "type":    "generate",
        "gpu":     True,
        "options": {"num_ctx": _ctx("library"), "num_predict": 1},
    },
    # BGE-M3 embedding service loads on startup and stays warm — no warmup needed.
]

CONTAINER_WAIT_TIMEOUT = 120   # sekunder å vente på at en container er oppe
CONTAINER_POLL_INTERVAL = 3


async def _wait_for_container(base: str, name: str) -> bool:
    """Venter til /api/tags svarer. Returnerer True hvis klar."""
    deadline = time.monotonic() + CONTAINER_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base}/api/tags")
                if r.status_code == 200:
                    log.info("[%s] container oppe", name)
                    return True
        except Exception:
            pass
        await asyncio.sleep(CONTAINER_POLL_INTERVAL)
    log.error("[%s] container ikke oppe etter %ds", name, CONTAINER_WAIT_TIMEOUT)
    return False


async def _load_model(m: dict) -> bool:
    """Sender en minimal forespørsel for å laste modellen inn i minne."""
    name  = m["name"]
    base  = m["base"]
    model = m["model"]
    timeout = m["timeout"]

    if not await _wait_for_container(base, name):
        return False

    log.info("[%s] laster %s …", name, model)
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if m["type"] == "embed":
                r = await client.post(f"{base}/api/embed", json={
                    "model": model,
                    "input": "oppvarming",
                })
            else:
                # Tom prompt med keep_alive=-1 = last inn, ikke generer
                # num_ctx MÅ matche det inference-kall bruker – ellers laster Ollama modellen på nytt
                payload = {
                    "model":      model,
                    "prompt":     "",
                    "keep_alive": -1,
                    "stream":     False,
                }
                if m.get("options"):
                    payload["options"] = m["options"]
                r = await client.post(f"{base}/api/generate", json=payload)
            r.raise_for_status()
        elapsed = time.monotonic() - t0

        if m.get("gpu"):
            vram = await _check_vram(base, model, name)
            if vram == 0:
                log.error(
                    "[%s] ADVARSEL: modellen kjører på CPU (size_vram=0)! "
                    "GPU ikke klar ved oppstart – restart containeren manuelt.",
                    name,
                )
                return False
            log.info("[%s] klar på %.1fs (VRAM: %.1f GiB)", name, elapsed, vram / 1e9)
        else:
            log.info("[%s] klar på %.1fs", name, elapsed)
        return True
    except Exception as e:
        elapsed = time.monotonic() - t0
        log.error("[%s] feilet etter %.1fs: %s", name, elapsed, e)
        return False


async def _check_vram(base: str, model: str, name: str) -> int:
    """Returnerer size_vram for modellen via /api/ps. 0 betyr CPU."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/api/ps")
            r.raise_for_status()
            for m in r.json().get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    return m.get("size_vram", 0)
    except Exception as e:
        log.warning("[%s] kunne ikke sjekke VRAM: %s", name, e)
    return 0


async def main() -> None:
    log.info("=== Kåre warmup starter – laster %d modeller parallelt ===", len(MODELS))
    t0 = time.monotonic()

    results = await asyncio.gather(*[_load_model(m) for m in MODELS])

    total = time.monotonic() - t0
    ok    = sum(results)
    log.info("=== Warmup ferdig på %.1fs – %d/%d modeller klare ===", total, ok, len(MODELS))

    if ok < len(MODELS):
        log.warning("Ikke alle modeller lastet – systemet kan være ustabilt")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
