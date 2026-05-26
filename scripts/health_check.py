#!/usr/bin/env python3
"""
scripts/health_check.py — Kåre system health check

Checks three layers:
  1. Module imports   — syntax + missing dependencies
  2. Config files     — valid YAML + required keys present
  3. Live services    — HTTP endpoints responding (skip with --no-live)

Usage:
  PYTHONPATH=/kaare /kaare/venv/bin/python scripts/health_check.py
  PYTHONPATH=/kaare /kaare/venv/bin/python scripts/health_check.py --no-live
"""

import argparse
import importlib
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import yaml

# ── ANSI colours ────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg: str)   -> str: return f"{GREEN}✓{RESET} {msg}"
def err(msg: str)  -> str: return f"{RED}✗{RESET} {msg}"
def warn(msg: str) -> str: return f"{YELLOW}~{RESET} {msg}"
def head(msg: str) -> str: return f"\n{BOLD}{CYAN}{msg}{RESET}"


# ── Modules to import-check ──────────────────────────────────────────────────
#
# Voice provider modules marked VOICE_VENV need pychromecast / pyatv /
# aioesphomeapi / async-upnp-client — those live in services/voice/venv/,
# not in the main venv. We skip them rather than count as errors.

VOICE_VENV = {
    # These modules import packages only installed in services/voice/venv/:
    # wyoming, pychromecast, pyatv, aioesphomeapi, async-upnp-client
    "kaare_core.voice.registry",          # imports all providers (including DLNA)
    "kaare_core.voice.wyoming_server",    # imports wyoming.*
    "kaare_core.voice.providers.chromecast",
    "kaare_core.voice.providers.airplay",
    "kaare_core.voice.providers.dlna",
    "kaare_core.voice.providers.esp32",
}

MODULES = [
    # Core infrastructure
    "kaare_core.config",
    "kaare_core.logging",
    "kaare_core.http_clients",
    "kaare_core.model_lock",
    "kaare_core.llm_fallback",
    "kaare_core.session_keys",
    "kaare_core.crypto",
    "kaare_core.image_store",
    "kaare_core.vpn",
    # Memory
    "kaare_core.memory.types",
    "kaare_core.memory.short_term",
    "kaare_core.memory.long_term",
    "kaare_core.memory.semantic_memory",
    # HA
    "kaare_core.ha.aliasing",
    "kaare_core.ha.clarification",
    "kaare_core.ha.fastpath",
    # Tools
    "kaare_core.tools.definitions",
    "kaare_core.tools.executor",
    "kaare_core.tools.shared_tools",
    "kaare_core.tools.timer_service",
    "kaare_core.tools.notisblokk",
    "kaare_core.tools.think_cache",
    "kaare_core.tools.lister",
    # Agents
    "kaare_core.agents.miss_kare.evaluator",
    "kaare_core.agents.miss_kare.stm",
    "kaare_core.agents.pettersmart.tools",
    # Domain
    "kaare_core.domain.decision",
    "kaare_core.domain.policy",
    "kaare_core.domain.frigate_responder",
    # Routers
    "kaare_core.routers.router_generate",
    "kaare_core.routers.router_intent",
    "kaare_core.routers.router_memory",
    "kaare_core.routers.router_users",
    # Users
    "kaare_core.users.auth",
    "kaare_core.users.store",
    "kaare_core.users.profile_manager",
    # Voice (base + registry always in main venv)
    "kaare_core.voice.base",
    "kaare_core.voice.registry",
    "kaare_core.voice.wyoming_server",
    "kaare_core.voice.providers.ha",
    "kaare_core.voice.providers.wyoming",
    "kaare_core.voice.providers.snapcast",
    # Voice providers that need the voice venv (skipped, not errors)
    "kaare_core.voice.providers.chromecast",
    "kaare_core.voice.providers.airplay",
    "kaare_core.voice.providers.dlna",
    "kaare_core.voice.providers.esp32",
    # Adapters
    "adapters.llm_adapter",
    "adapters.frigate_adapter",
    "adapters.mqtt_adapter",
    "adapters.plex_adapter",
    "adapters.web_search_adapter",
    "adapters.yr_adapter",
    "adapters.image_generation_adapter",
]


# ── Config validation rules ──────────────────────────────────────────────────
#
# Each entry: (filename, required_key_paths)
# A key path like "default.provider" means cfg["default"]["provider"] must exist.

CONFIG_RULES = [
    ("llm.yaml", [
        "default",
        "default.provider",
        "default.base_url",
        "default.model_role",
        "default.options",
        "cloud",
        "cloud.provider",
    ]),
    ("models.yaml", [
        "kare",
        "miss_kare",
        "library",
        "embed",
    ]),
    ("services.yaml", [
        "home_assistant",
        "internal",
        "storage",
        "embedding",
    ]),
    ("settings.yaml", [
        "assistant_name",
        "location",
    ]),
    ("aliases.yaml",          []),
    ("lang_normalize.yaml",   []),
    ("nodes.yaml",            []),
    ("radio_stations.yaml",   []),
    ("tool_permissions.yaml", []),
    ("trusted_sources.yaml",  []),
    ("voice_providers.yaml",  []),
    ("gateway_settings.yaml", []),
]


# ── Live service endpoints ────────────────────────────────────────────────────

def _build_services() -> list[tuple[str, str]]:
    """Build service URL list from services.yaml + llm.yaml.
    Works on AI-PC (localhost URLs) and in Docker (internal hostnames).
    Falls back to 127.0.0.1 defaults if configs are missing.
    """
    try:
        svc = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text()) or {}
    except Exception:
        svc = {}
    try:
        llm = yaml.safe_load(Path("/kaare/configs/llm.yaml").read_text()) or {}
    except Exception:
        llm = {}

    internal = svc.get("internal", {})
    ollama   = svc.get("ollama", {})
    storage  = svc.get("storage", {})

    def _u(val: str | None, fallback: str) -> str:
        return (val or fallback).rstrip("/")

    kaare_api    = _u(internal.get("kaare_api"),    "http://127.0.0.1:8000")
    ha_gateway   = _u(internal.get("ha_gateway"),   "http://127.0.0.1:8002")
    sem_embed    = _u(internal.get("semantic_embed"),"http://127.0.0.1:11500")
    agents       = _u(internal.get("agents"),        "http://127.0.0.1:11450")
    embed        = _u(ollama.get("embed"),            "http://127.0.0.1:11446")
    qdrant       = _u(storage.get("qdrant"),          "http://127.0.0.1:6333")
    ollama_kare  = _u(ollama.get("kare"),             "http://127.0.0.1:11434")
    ollama_miss  = _u(ollama.get("miss_kare"),        "http://127.0.0.1:11445")
    ollama_lib   = _u(ollama.get("library"),          "http://127.0.0.1:11447")

    mem_embed_enabled = svc.get("memory_embed", {}).get("enabled", False)
    bge_embed_enabled = svc.get("embedding", {}).get("enabled", True)

    services = [
        ("Main API",       kaare_api  + "/"),
        ("HA gateway",     ha_gateway + "/"),
        ("Agents server",  agents     + "/"),
        ("Qdrant",         qdrant     + "/"),
    ]
    if mem_embed_enabled:
        services.append(("Semantic embed", sem_embed + "/"))
    if bge_embed_enabled:
        services.append(("Embedding (BGE)", embed + "/health"))

    default_llm = llm.get("default", {})
    if default_llm.get("provider") == "vllm":
        vllm_url = _u(default_llm.get("base_url"), "http://127.0.0.1:11440")
        services.append(("vLLM", vllm_url + "/v1/models"))

    services.append(("Ollama (main)", ollama_kare + "/"))
    if ollama_miss != ollama_kare:
        services.append(("Ollama miss_kare", ollama_miss + "/"))
    if ollama_lib not in (ollama_kare, ollama_miss):
        services.append(("Ollama library", ollama_lib + "/"))

    return services


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_nested(data: dict, dotted_key: str):
    """Return value at 'a.b.c' path, or raise KeyError."""
    parts = dotted_key.split(".")
    cur = data
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            raise KeyError(dotted_key)
        cur = cur[p]
    return cur


def _check_url(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """GET url. Returns (success, detail)."""
    try:
        t0 = time.monotonic()
        with urllib.request.urlopen(url, timeout=timeout) as r:
            ms = int((time.monotonic() - t0) * 1000)
            return True, f"HTTP {r.status} ({ms} ms)"
    except urllib.error.HTTPError as e:
        if e.code < 500:
            ms = int((time.monotonic() - t0) * 1000)
            return True, f"HTTP {e.code} ({ms} ms)"
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


# ── Check functions ───────────────────────────────────────────────────────────

def check_imports() -> tuple[int, int, int, list[dict]]:
    """Returns (passed, failed, skipped, error_list)."""
    errors: list[dict] = []
    passed = failed = skipped = 0
    for mod in MODULES:
        if mod in VOICE_VENV:
            skipped += 1
            continue
        try:
            importlib.import_module(mod)
            passed += 1
        except Exception as e:
            errors.append({"name": mod, "detail": str(e).split("\n")[0]})
            failed += 1
    return passed, failed, skipped, errors


def check_configs() -> tuple[int, int, list[dict]]:
    """Returns (passed, failed, error_list)."""
    errors: list[dict] = []
    config_dir = Path("/kaare/configs")
    passed = failed = 0
    for filename, required_keys in CONFIG_RULES:
        path = config_dir / filename
        if not path.exists():
            errors.append({"name": filename, "detail": "file not found"})
            failed += 1
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append({"name": filename, "detail": f"YAML parse error: {e}"})
            failed += 1
            continue

        missing = []
        for key in required_keys:
            try:
                _get_nested(data, key)
            except KeyError:
                missing.append(key)

        if missing:
            errors.append({"name": filename, "detail": f"missing keys: {', '.join(missing)}"})
            failed += 1
        else:
            passed += 1
    return passed, failed, errors


def check_services() -> tuple[int, int, list[dict]]:
    """Returns (passed, failed, results_list)."""
    results: list[dict] = []
    passed = failed = 0
    for name, url in _build_services():
        success, detail = _check_url(url)
        results.append({"name": name, "url": url, "ok": success, "detail": detail})
        if success:
            passed += 1
        else:
            failed += 1
    return passed, failed, results


def _run_human(no_live: bool) -> int:
    """Human-readable terminal output."""
    print(f"\n{BOLD}{'=' * 52}{RESET}")
    print(f"{BOLD}  Kåre Health Check — {time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"{BOLD}{'=' * 52}{RESET}")

    print(head("[1/3] Module imports"))
    imp_ok, imp_err, imp_skip, imp_errors = check_imports()
    for e in imp_errors:
        print(f"  {err(e['name'])}  — {e['detail']}")
    for mod in MODULES:
        if mod not in VOICE_VENV and mod not in {e["name"] for e in imp_errors}:
            print(f"  {ok(mod)}")
    for mod in MODULES:
        if mod in VOICE_VENV:
            print(f"  {warn(mod + '  (voice-venv, skipped)')}")

    print(head("[2/3] Config files"))
    cfg_ok, cfg_err, cfg_errors = check_configs()
    err_names = {e["name"] for e in cfg_errors}
    for filename, required_keys in CONFIG_RULES:
        if filename in err_names:
            detail = next(e["detail"] for e in cfg_errors if e["name"] == filename)
            print(f"  {err(filename)}  — {detail}")
        else:
            extra = f"  ({len(required_keys)} key checks)" if required_keys else ""
            print(f"  {ok(filename)}{extra}")

    if no_live:
        svc_ok = svc_err = 0
        print(head("[3/3] Live services"))
        print(f"  {warn('Skipped (--no-live)')}")
    else:
        print(head("[3/3] Live services"))
        svc_ok, svc_err, svc_results = check_services()
        for r in svc_results:
            label = f"{r['name']:<22} {r['url']}"
            if r["ok"]:
                print(f"  {ok(label)}  — {r['detail']}")
            else:
                print(f"  {err(label)}  — {r['detail']}")

    total_err = imp_err + cfg_err + svc_err
    print(f"\n{BOLD}{'─' * 52}{RESET}")
    print(f"  Imports  : {GREEN}{imp_ok} ok{RESET}  {RED}{imp_err} failed{RESET}  {YELLOW}{imp_skip} skipped{RESET}")
    print(f"  Configs  : {GREEN}{cfg_ok} ok{RESET}  {RED}{cfg_err} failed{RESET}")
    if not no_live:
        print(f"  Services : {GREEN}{svc_ok} ok{RESET}  {RED}{svc_err} failed{RESET}")

    if total_err == 0:
        print(f"\n  {GREEN}{BOLD}All checks passed.{RESET}")
    else:
        print(f"\n  {RED}{BOLD}{total_err} error(s) found.{RESET}")
    print(f"{BOLD}{'=' * 52}{RESET}\n")
    return 1 if total_err > 0 else 0


def _run_json(no_live: bool) -> int:
    """Machine-readable JSON output for the API endpoint."""
    imp_ok, imp_err, imp_skip, imp_errors = check_imports()
    cfg_ok, cfg_err, cfg_errors           = check_configs()

    if no_live:
        svc_ok = svc_err = 0
        svc_results: list[dict] = []
    else:
        svc_ok, svc_err, svc_results = check_services()

    total_errors = imp_err + cfg_err + svc_err
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": total_errors == 0,
        "total_errors": total_errors,
        "imports": {
            "passed": imp_ok,
            "failed": imp_err,
            "skipped": imp_skip,
            "errors": imp_errors,
        },
        "configs": {
            "passed": cfg_ok,
            "failed": cfg_err,
            "errors": cfg_errors,
        },
        "services": {
            "passed": svc_ok,
            "failed": svc_err,
            "skipped": no_live,
            "results": svc_results,
        },
    }
    print(json.dumps(output))
    return 1 if total_errors > 0 else 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Kåre system health check")
    parser.add_argument("--no-live", action="store_true", help="Skip live service checks")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    if args.json:
        return _run_json(args.no_live)
    return _run_human(args.no_live)


if __name__ == "__main__":
    sys.exit(main())
