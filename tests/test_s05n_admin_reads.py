"""DT04-S05n (security, kaare-d7 2026-11-17): a logged-in child/teen read the house's topology and token stubs through GET
routes that only had require_auth — service URLs, LLM config, masked HA/Plex tokens, secrets, hardware, service states —
and could make the server GET any URL through POST /api/settings/test-connection (SSRF into the house network).

- A: those reads are admin only.
- B: /api/settings/services stays readable for every user, but a non-admin gets only {"voice": {"stt_enabled": …}}
  (allow-list, fail closed) — the user Home page needs exactly that.
- C: test-connection is admin only; for admin it still reaches GUI-configured addresses — local, LAN and external
  (rule 27: a barrier never blocks what the user configured).
- Guard: every GET route in kaare_core/routers/*.py and kaare_api.py is require_admin unless it is on ALLOWED below with a
  reason. A new non-admin GET route turns this test red until someone decides it belongs on the list.

Red runs never execute a real handler against the house: the guard is static (AST); B reads tmp files; C uses a fake
HTTP client; the 403 tests run only once the lock exists (the lock refuses before the handler)."""

import ast
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.routers import router_media, router_settings, router_system, router_users

ROOT = Path("/kaare")
ROUTE_FILES = [*sorted((ROOT / "kaare_core" / "routers").glob("*.py")), ROOT / "kaare_api.py"]
ADMIN_DEPS = {"require_admin", "_require_admin"}

# (file, path) → why a non-admin may call it. Anything else must be require_admin.
ALLOWED = {
    # public by nature (login screen, health)
    ("router_settings.py", "/api/settings/language"): "public: GUI language before login",
    ("router_settings.py", "/api/settings/assistant_name"): "public: login screen shows the name",
    ("kaare_api.py", "/"): "public: health",
    ("kaare_api.py", "/api/show"): "public: health ({'status': 'ok'})",
    ("kaare_api.py", "/openapi.json"): "off unless switched on in the GUI (R6 L1)",
    ("kaare_api.py", "/docs"): "off unless switched on in the GUI (R6 L1)",
    ("kaare_api.py", "/redoc"): "off unless switched on in the GUI (R6 L1)",
    # the caller's own data
    ("router_users.py", "/api/auth/me"): "own account",
    ("router_users.py", "/api/ping"): "own presence",
    ("kaare_api.py", "/api/chat_history"): "own chat",
    ("kaare_api.py", "/api/pending_notifications"): "own notifications",
    ("kaare_api.py", "/api/miss_kare/comment"): "own chat (user Home page)",
    ("router_frigate_user.py", "/api/frigate_user/questions/mine"): "own camera questions",
    ("router_frigate_user.py", "/api/frigate_user/questions/mine/history"): "own camera answers",
    # self-or-admin checked inside the handler
    ("router_users.py", "/api/users/{username}/flags"): "inline self/admin",
    ("router_users.py", "/api/users/{username}/frigate"): "inline self/admin/guardian",
    ("router_users.py", "/api/users/{username}/summary"): "inline self/admin/parent",
    ("router_users.py", "/api/users/{username}/household-visible"): "inline self/admin",
    ("router_users.py", "/api/users/{username}/unlock"): "inline self/admin",
    ("router_meetings.py", "/api/reflections/{username}"): "inline _own_or_admin",
    # owner-locked pictures (DT04 point 11)
    ("router_media.py", "/api/image/{image_id}"): "owner lock",
    ("router_media.py", "/api/card/{card_id}"): "owner lock",
    ("router_media.py", "/api/frigate_snapshot/{event_id:path}"): "owner lock",
    # machine callers with their own token
    ("router_settings.py", "/api/inner-voices/context"): "inner voices Bearer push token",
    ("router_update.py", "/api/update/alive"): "service token (updater)",
    # B: allow-listed body for non-admin
    ("router_settings.py", "/api/settings/services"): "non-admin gets only voice.stt_enabled (user Home page)",
}


def _get_routes():
    out = []
    for f in ROUTE_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        prefix = ""
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and getattr(n.value.func, "id", "") == "APIRouter"):
                for kw in n.value.keywords:
                    if kw.arg == "prefix":
                        prefix = ast.literal_eval(kw.value)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in n.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "get"
                        and d.args and isinstance(d.args[0], ast.Constant)):
                    deps = {ast.unparse(c.args[0]) for c in ast.walk(n.args)
                            if isinstance(c, ast.Call) and ast.unparse(c.func) == "Depends" and c.args}
                    out.append((f.name, prefix + d.args[0].value, n.lineno, deps))
    return out


def test_every_get_route_is_admin_or_explicitly_allowed():
    routes = _get_routes()
    assert len(routes) > 100  # the parser really found the routes
    open_ = [f"{f}:{line} {path}" for f, path, line, deps in routes
             if not (deps & ADMIN_DEPS) and (f, path) not in ALLOWED]
    assert not open_, "GET routes open to non-admin users without a decision:\n" + "\n".join(open_)


def test_allow_list_has_no_stale_rows():
    seen = {(f, p) for f, p, _, _ in _get_routes()}
    assert not (set(ALLOWED) - seen), set(ALLOWED) - seen


# ── live checks (TestClient) ─────────────────────────────────────────────────

LOCKED = [
    "/api/ollama/models/default",
    "/api/ollama/pull/status/default",
    "/api/settings/ollama_source",
    "/api/settings/llm",
    "/api/settings/models",
    "/api/settings/piper/models",
    "/api/settings/piper/download/job1",
    "/api/settings/ha-token",
    "/api/settings/ha-bridge",
    "/api/settings/secrets",
    "/api/settings/plex-token",
    "/api/settings/plex/status",
    "/api/system_status",
    "/api/system/gpus",
    "/api/system/hardware",
    "/api/admin/services",
    "/api/personalities",
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)  # users.db stays untouched
    app = FastAPI()
    for r in (router_settings, router_system, router_media, router_users):
        app.include_router(r.router)
    return TestClient(app)


def _hdr(role, user="s05n_user"):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


@pytest.mark.parametrize("role", ["teen", "child", "adult", "young_adult"])
@pytest.mark.parametrize("path", LOCKED)
def test_non_admin_is_refused_before_the_handler(client, path, role):
    assert client.get(path, headers=_hdr(role)).status_code == 403, path


@pytest.mark.parametrize("path", LOCKED)
def test_no_token_is_refused(client, path):
    assert client.get(path).status_code in (401, 403), path


# B — services: full body for admin, only stt_enabled for everyone else

@pytest.fixture
def services_files(tmp_path, monkeypatch):
    svc = tmp_path / "services.yaml"
    svc.write_text(yaml.safe_dump({
        "home_assistant": {"url": "http://192.0.2.10:8123"},
        "mqtt": {"host": "192.0.2.11"},
        "frigate": {"url": "http://192.0.2.12:5000"},
        "media": {"plex": {"url": "http://192.0.2.13:32400"}},
        "voice": {"stt": {"enabled": False, "backend": "openvino"}},
    }), encoding="utf-8")
    cap = tmp_path / "capability_map.yaml"
    cap.write_text("domains: {}\n", encoding="utf-8")
    monkeypatch.setattr(router_settings, "_SERVICES_PATH", svc)
    monkeypatch.setattr(router_settings, "_CAPABILITY_MAP_PATH", cap)


@pytest.mark.parametrize("role", ["teen", "child", "adult", "young_adult"])
def test_services_non_admin_gets_only_stt_enabled(client, services_files, role):
    r = client.get("/api/settings/services", headers=_hdr(role))
    assert r.status_code == 200
    assert r.json() == {"voice": {"stt_enabled": False}}
    assert "192.0.2" not in r.text


def test_services_admin_gets_everything(client, services_files):
    body = client.get("/api/settings/services", headers=_hdr("admin")).json()
    assert body["home_assistant"]["url"] == "http://192.0.2.10:8123"
    assert body["voice"]["stt_enabled"] is False and body["voice"]["stt_backend"] == "openvino"


# C — test-connection: admin only; admin still reaches local, LAN and external addresses (rule 27)

class _FakeResp:
    status_code = 200


@pytest.fixture
def fake_http(monkeypatch):
    seen = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **k):
            seen.append(url)
            return _FakeResp()

    monkeypatch.setattr(router_settings.httpx, "AsyncClient", _Client)
    return seen


@pytest.mark.parametrize("role", ["teen", "child", "adult", "young_adult"])
def test_test_connection_refuses_non_admin_and_fetches_nothing(client, fake_http, role):
    r = client.post("/api/settings/test-connection", json={"url": "http://192.168.0.1/"}, headers=_hdr(role))
    assert r.status_code == 403
    assert fake_http == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8123",          # local (same machine)
    "http://localhost:11434",         # local by name
    "http://192.168.1.23:8123",       # another machine in the LAN
    "http://10.0.0.5:5000",           # VPN / other private range
    "https://api.example.com/v1",     # external
])
def test_test_connection_admin_reaches_configured_addresses(client, fake_http, url):
    r = client.post("/api/settings/test-connection", json={"url": url}, headers=_hdr("admin"))
    assert r.status_code == 200 and r.json() == {"ok": True, "status_code": 200}
    assert fake_http == [url]
