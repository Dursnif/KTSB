"""DT04-S05u — the first-start admin (PIN 1234) must never lock a new install out.

Observed on a fresh Docker install: the default admin was created with a temporary PIN that expired after 1 hour; a
stranger who first logged in later (model download, a break) got «Temporary PIN has expired. Ask an administrator for a
new one.» — and there is no other administrator. Now: the first-start PIN has no expiry, but works only from the local
network until it is changed (the «change PIN first» lock still applies). Temporary PINs an admin gives others keep their
expiry. An install that is already locked out is healed once at start, only when it is exactly that case.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.client_ip as cip
import kaare_core.users.auth as auth
from kaare_core.routers import router_users
from kaare_core.users import store

NET = {"local_subnets": ["192.168.0.0/24"], "vpn_subnet": "10.0.0.0/24", "trusted_proxies": [], "api_docs": False}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    return tmp_path / "users.db"


def _row(db, username="admin"):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
    finally:
        c.close()


def _sql(db, q, args=()):
    c = sqlite3.connect(db)
    try:
        c.execute(q, args)
        c.commit()
    finally:
        c.close()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(cip, "get_network_settings", lambda: NET)
    monkeypatch.setattr(auth, "get_network_settings", lambda: NET)
    router_users._login_failures.clear()

    def fake_login(u, p):
        return {"token": "t", "user": {"username": u}, "must_change_pin": True} if p == "1234" else None

    monkeypatch.setattr(router_users, "login", fake_login)
    app = FastAPI()
    app.include_router(router_users.router)
    yield app
    router_users._login_failures.clear()


def _login(app, peer):
    return TestClient(app, client=(peer, 50000)).post("/api/auth/login", json={"username": "admin", "pin": "1234"})


def test_first_start_admin_pin_has_no_expiry(db):
    row = _row(db)
    assert row["must_change_pin"] == 1 and row["pin_expires_at"] is None
    assert store.is_first_start_pin("admin") is True
    assert store.check_pin_expired("admin") is False


def test_first_start_admin_from_lan_two_hours_later_logs_in_and_must_change(client, db):
    _sql(db, "UPDATE users SET created_at=? WHERE username='admin'",
         ((datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds"),))
    r = _login(client, "192.168.0.77")
    assert r.status_code == 200, r.text
    assert r.json()["must_change_pin"] is True


@pytest.mark.parametrize("peer", ["10.0.0.5", "8.8.8.8"])
def test_first_start_admin_from_vpn_or_outside_is_refused_honestly(client, peer):
    r = _login(client, peer)
    assert r.status_code == 403
    assert "home network" in r.json()["detail"]


def test_admin_given_temporary_pin_still_expires(db):
    store.create_user(username="kid", display_name="Kid", role="child", pin="246813", must_change_pin=True)
    row = _row(db, "kid")
    assert row["pin_expires_at"] is not None
    assert store.is_first_start_pin("kid") is False
    _sql(db, "UPDATE users SET pin_expires_at=? WHERE username='kid'",
         ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds"),))
    assert store.check_pin_expired("kid") is True


def _lock_out_like_v1110(db):
    """What a v1.11.0 install looks like after the first hour: default admin, PIN 1234, expiry passed."""
    _sql(db, "UPDATE users SET pin_expires_at=? WHERE username='admin'",
         ((datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds"),))


def test_locked_out_first_admin_is_healed_at_start(db):
    _lock_out_like_v1110(db)
    assert store.check_pin_expired("admin") is True
    store.init_db()
    assert _row(db)["pin_expires_at"] is None
    assert store.check_pin_expired("admin") is False


def test_no_healing_when_the_pin_is_not_the_default(db):
    _lock_out_like_v1110(db)
    _sql(db, "UPDATE users SET pin_hash=? WHERE username='admin'", (store.hash_pin("975310"),))
    store.init_db()
    assert _row(db)["pin_expires_at"] is not None


def test_no_healing_when_another_admin_exists(db):
    _lock_out_like_v1110(db)
    store.create_user(username="boss", display_name="Boss", role="admin", pin="975310", must_change_pin=False)
    store.init_db()
    assert _row(db)["pin_expires_at"] is not None
