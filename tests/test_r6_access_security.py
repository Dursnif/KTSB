"""DT03 R6 — can others get in? Regression tests for the holes found on 2026-10-06.

H1  X-Forwarded-For is believed only from GUI-trusted proxies (rule 27: a proxy the user
    configured is trusted — nobody else), and a per-username limit stops PIN guessing however
    many addresses the attacker claims.
M1  the per-IP limit is wide enough that one person's typos behind a shared NAT address do not
    lock the whole house out.
H2  a token issued while the PIN must be changed reaches only PIN change and logout.
L1  /docs, /redoc, /openapi.json are off unless switched on in the GUI.
Also: the network context fails closed ('external'), the network settings endpoint validates.
"""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import kaare_api
import kaare_core.client_ip as cip
import kaare_core.users.auth as auth
from kaare_core.routers import router_settings, router_users

NET = {
    "local_subnets": ["192.168.0.0/24"],
    "vpn_subnet": "10.0.0.0/24",
    "trusted_proxies": ["caddy", "127.0.0.1", "::1"],
    "api_docs": False,
}


class _Req:
    def __init__(self, peer: str, xff: str = ""):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


@pytest.fixture
def net(monkeypatch):
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in NET.items()}
    monkeypatch.setattr(cip, "get_network_settings", lambda: cfg)
    monkeypatch.setattr(cip, "_resolve", lambda name: {"172.18.0.5"} if name == "caddy" else set())
    return cfg


# ── H1: client_ip ─────────────────────────────────────────────────────────────


def test_spoofed_header_from_untrusted_peer_is_ignored(net):
    # Docker Desktop: every LAN client arrives as the gateway 172.18.0.1 — not a trusted proxy
    assert cip.client_ip(_Req("172.18.0.1", "10.9.9.200")) == "172.18.0.1"
    assert cip.client_ip(_Req("192.168.0.77", "127.0.0.1")) == "192.168.0.77"


def test_bundled_caddy_by_service_name_is_trusted(net):
    assert cip.client_ip(_Req("172.18.0.5", "192.168.0.77")) == "192.168.0.77"


def test_rightmost_untrusted_hop_wins_so_a_client_cannot_prepend(net):
    # client sent "X-Forwarded-For: 1.2.3.4", Caddy appended the real peer 192.168.0.77
    assert cip.client_ip(_Req("172.18.0.5", "1.2.3.4, 192.168.0.77")) == "192.168.0.77"


def test_malformed_chain_from_trusted_proxy_falls_back_to_peer(net):
    assert cip.client_ip(_Req("127.0.0.1", "not-an-ip")) == "127.0.0.1"


def test_user_configured_proxy_on_another_machine_is_honoured(net):
    """Rule 27: a reverse proxy the user put in the GUI list (here nginx on a NAS) works."""
    assert cip.client_ip(_Req("192.168.0.50", "192.168.0.77")) == "192.168.0.50"
    net["trusted_proxies"].append("192.168.0.50")
    assert cip.client_ip(_Req("192.168.0.50", "192.168.0.77")) == "192.168.0.77"
    net["trusted_proxies"].append("10.20.0.0/16")
    assert cip.client_ip(_Req("10.20.3.4", "192.168.0.88")) == "192.168.0.88"


# ── H1 + M1: login limits ─────────────────────────────────────────────────────


@pytest.fixture
def login_client(monkeypatch, net):
    router_users._login_failures.clear()
    monkeypatch.setattr(router_users, "login", lambda u, p: None)  # every PIN wrong
    monkeypatch.setattr(router_users.store, "check_pin_expired", lambda u: False)
    app = FastAPI()
    app.include_router(router_users.router)
    yield app
    router_users._login_failures.clear()


def _post_login(app, peer, user, xff=""):
    c = TestClient(app, client=(peer, 50000))
    h = {"X-Forwarded-For": xff} if xff else {}
    return c.post(
        "/api/auth/login", json={"username": user, "pin": "000000"}, headers=h
    ).status_code


def test_rotating_fake_addresses_do_not_bypass_the_account_limit(login_client):
    codes = [_post_login(login_client, "172.18.0.1", "admin", xff=f"10.9.9.{i}") for i in range(8)]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429] * 3


def test_same_account_from_many_real_addresses_is_still_limited(login_client):
    codes = [_post_login(login_client, f"192.168.0.{10 + i}", "admin") for i in range(7)]
    assert codes[5:] == [429, 429]


def test_one_persons_typos_do_not_lock_out_the_house(login_client):
    for _ in range(6):
        _post_login(login_client, "172.18.0.1", "kid")  # kid is now locked
    assert _post_login(login_client, "172.18.0.1", "kid") == 429
    assert (
        _post_login(login_client, "172.18.0.1", "parent") == 401
    )  # same NAT address, still allowed


def test_per_ip_limit_stops_spraying_many_accounts(login_client):
    codes = [_post_login(login_client, "192.168.0.99", f"user{i}") for i in range(32)]
    assert codes[:30] == [401] * 30 and codes[30:] == [429, 429]


# ── H2: PIN change required ──────────────────────────────────────────────────


def test_pin_change_blocks_logic(monkeypatch):
    payload = {"sub": "admin", "role": "admin", "pin_change": True}
    monkeypatch.setattr(auth, "get_user", lambda u: {"must_change_pin": True})
    assert auth.pin_change_blocks("GET", "/api/settings", payload)
    assert auth.pin_change_blocks("PUT", "/api/users/someone_else/pin", payload)
    assert not auth.pin_change_blocks("PUT", "/api/users/admin/pin", payload)
    assert not auth.pin_change_blocks("POST", "/api/auth/logout", payload)
    assert not auth.pin_change_blocks("GET", "/api/settings", {"sub": "admin", "role": "admin"})
    monkeypatch.setattr(auth, "get_user", lambda u: {"must_change_pin": False})  # PIN changed
    assert not auth.pin_change_blocks("GET", "/api/settings", payload)

    def boom(_u):
        raise RuntimeError("db gone")

    monkeypatch.setattr(auth, "get_user", boom)
    assert auth.pin_change_blocks("GET", "/api/settings", payload)  # fail closed


def test_login_token_carries_pin_change_claim_only_when_required():
    assert (
        auth.decode_token(auth.create_token("a", "admin", pin_change=True)).get("pin_change")
        is True
    )
    assert "pin_change" not in auth.decode_token(auth.create_token("a", "admin"))


def test_gate_middleware_returns_403_until_pin_changed(monkeypatch):
    state = {"must": True}
    monkeypatch.setattr(
        kaare_api, "_pin_change_blocks", lambda m, p, pl: auth.pin_change_blocks(m, p, pl)
    )
    monkeypatch.setattr(auth, "get_user", lambda u: {"must_change_pin": state["must"]})
    app = FastAPI()
    app.add_middleware(kaare_api.PinChangeGateMiddleware)

    @app.get("/api/settings")
    def _s():
        return {"ok": True}

    @app.put("/api/users/admin/pin")
    def _p():
        return {"ok": True}

    c = TestClient(app)
    tok = auth.create_token("admin", "admin", pin_change=True)
    h = {"Authorization": f"Bearer {tok}"}
    r = c.get("/api/settings", headers=h)
    assert r.status_code == 403 and r.json()["pin_change_required"] is True
    assert c.get(f"/api/settings?token={tok}").status_code == 403  # query token too
    assert c.put("/api/users/admin/pin", headers=h).status_code == 200
    state["must"] = False
    assert c.get("/api/settings", headers=h).status_code == 200  # same token now free
    normal = {"Authorization": f"Bearer {auth.create_token('admin', 'admin')}"}
    assert c.get("/api/settings", headers=normal).status_code == 200


# ── L1: API docs ──────────────────────────────────────────────────────────────


def test_api_docs_off_by_default_and_switchable(monkeypatch):
    cfg = dict(NET)
    monkeypatch.setattr(kaare_api, "_get_network_settings", lambda: cfg)
    c = TestClient(kaare_api.app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404, path
    cfg["api_docs"] = True
    assert c.get("/openapi.json").status_code == 200
    assert c.get("/docs").status_code == 200


# ── network context + settings endpoint ──────────────────────────────────────


def test_network_context_fails_closed(monkeypatch):
    monkeypatch.setattr(auth, "get_network_settings", lambda: dict(NET))
    assert auth.get_network_context("not-an-ip") == "external"
    assert auth.get_network_context("192.168.0.20") == "local"
    assert auth.get_network_context("10.0.0.7") == "vpn"
    assert auth.get_network_context("8.8.8.8") == "external"


def test_network_settings_endpoint_validates(monkeypatch, tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("network:\n  local_subnet: 192.168.1.0/24\n", encoding="utf-8")
    monkeypatch.setattr(router_settings, "_SETTINGS_PATH", p)
    monkeypatch.setattr(router_settings, "get_network_settings", lambda: {"ok": "stub"})

    def put(body):
        return asyncio.run(router_settings.api_put_network(body, _u=None))

    for bad in (
        {"trusted_proxies": ["0.0.0.0/0"]},
        {"trusted_proxies": ["::/0"]},
        {"trusted_proxies": ["bad host!"]},
        {"local_subnets": ["300.1.1.0/24"]},
        {"vpn_subnet": "x"},
        {"unknown": 1},
    ):
        with pytest.raises(HTTPException):
            put(bad)
    put(
        {
            "trusted_proxies": ["caddy", "192.168.0.50", "10.20.0.0/16"],
            "local_subnets": ["192.168.0.0/24", "192.168.5.0/24"],
            "api_docs": True,
        }
    )
    text = p.read_text(encoding="utf-8")
    assert "192.168.0.50" in text and "api_docs: true" in text and "local_subnet:" not in text
