"""Every HTTP route on the API (8000), the HA gateway (8002) and the voice bridge (8011) has a
lock — or is on the explicit OPEN list below with the reason it may stay open (P120).

Reads the source with `ast` (nothing is imported or started), so a new endpoint without a lock
fails `pytest` the day it is written. A lock is one of:
- a `Depends(...)` on a known auth dependency in the route's signature, or
- a call in the route body to a gate that decides per request (`identify_caller`,
  `_node_or_service`, `_pin_gate`).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "kaare_api.py",
    "kaare_ha_gateway.py",
    "services/voice/voice_bridge.py",
    "services/voice/voice_manager.py",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kaare_core/routers").glob("*.py")),
]

AUTH_DEPENDENCIES = {
    "require_admin",
    "_require_admin",
    "require_auth",
    "_require_auth",
    "require_image_auth",
    "_require_image_auth",
    "_require_service",
    "_require_user",
}
BODY_GATES = {"identify_caller", "_identify_caller", "_node_or_service", "_pin_gate"}

# (file, METHOD, path): why it may stay open
OPEN = {
    (
        "kaare_api.py",
        "GET",
        "/openapi.json",
    ): "API docs — 404 unless the GUI switch network.api_docs is on (DT03 R6 L1)",
    (
        "kaare_api.py",
        "GET",
        "/docs",
    ): "API docs — 404 unless the GUI switch network.api_docs is on (DT03 R6 L1)",
    (
        "kaare_api.py",
        "GET",
        "/redoc",
    ): "API docs — 404 unless the GUI switch network.api_docs is on (DT03 R6 L1)",
    ("kaare_api.py", "GET", "/"): "liveness text only",
    ("kaare_api.py", "GET", "/api/show"): "static Ollama-format probe used by the LLM adapter",
    ("kaare_ha_gateway.py", "GET", "/"): "liveness text only",
    ("kaare_ha_gateway.py", "GET", "/health"): "health check for the system overview",
    (
        "services/voice/voice_bridge.py",
        "GET",
        "/audio/{filename}",
    ): "ESP32/Chromecast fetch TTS files; names are random uuid4 and the files are temporary",
    ("services/voice/voice_bridge.py", "GET", "/health"): "health check",
    (
        "kaare_core/routers/router_settings.py",
        "GET",
        "/api/settings/language",
    ): "GUI language before login",
    (
        "kaare_core/routers/router_settings.py",
        "GET",
        "/api/settings/assistant_name",
    ): "login page shows the name",
    (
        "kaare_core/routers/router_settings.py",
        "POST",
        "/api/inner-voices/push",
    ): "Jing/Jang push — checks its own shared token (hmac) in the body/header",
    ("kaare_core/routers/router_users.py", "POST", "/auth/login"): "login itself (rate-limited)",
    (
        "kaare_core/routers/router_users.py",
        "POST",
        "/users/recover",
    ): "seed-phrase recovery (rate-limited)",
}

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "websocket"}


def _routes():
    for rel in FILES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in HTTP_METHODS
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    yield rel, dec.func.attr.upper(), dec.args[0].value, node


def _is_locked(fn) -> bool:
    sig_names = {n.id for n in ast.walk(fn.args) if isinstance(n, ast.Name)}
    sig_names |= {n.attr for n in ast.walk(fn.args) if isinstance(n, ast.Attribute)}
    body_calls = {
        c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    return bool(sig_names & AUTH_DEPENDENCIES or body_calls & BODY_GATES)


def test_every_route_is_locked_or_explicitly_open():
    unlocked = [
        f"{rel}: {method} {path}"
        for rel, method, path, fn in _routes()
        if not _is_locked(fn) and (rel, method, path) not in OPEN
    ]
    assert not unlocked, (
        "Routes without a lock (add a dependency, or an OPEN entry with a reason):\n"
        + "\n".join(unlocked)
    )


def test_open_list_has_no_stale_entries():
    present = {(rel, method, path) for rel, method, path, _ in _routes()}
    stale = [k for k in OPEN if k not in present]
    assert not stale, f"OPEN entries for routes that no longer exist: {stale}"


def test_removed_routes_stay_removed():
    present = {(method, path) for _, method, path, _ in _routes()}
    for gone in [
        ("POST", "/api/ask_llm"),
        ("POST", "/api/ask_cloud"),
        ("POST", "/api/intent_to_ha"),
        ("POST", "/api/memory/add"),
        ("POST", "/api/memory/query"),
        ("POST", "/api/argus_delta"),
        ("POST", "/command"),
        ("POST", "/push-aliases"),
        ("GET", "/gui"),
        ("POST", "/api/voice/trigger/{node_id}"),
        ("WEBSOCKET", "/ws/voice"),
    ]:
        assert gone not in present, gone
