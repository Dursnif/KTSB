"""P144-s27b — «Uttrykk og gester» per person, with the s02 consent rule (2026-11-06): young adult / adult /
admin decide for themselves; child / teen their guardians, an admin when none. On by default («default skrudd
på … bedre at det skrues av der det ikke ønskes»). Tmp users.db only."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.routers import router_users
from kaare_core.users import frigate_consent, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(router_users, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    for name, role in [("boss", "admin"), ("mum", "adult"), ("ung", "young_adult"), ("kid", "child"),
                       ("orphan", "teen")]:
        store.create_user(username=name, display_name=name, role=role, pin="482915")
    store.update_user("mum", is_parent=True, managed_children=json.dumps(["kid"]))
    frigate_consent.invalidate()
    app = FastAPI()
    app.include_router(router_users.router)
    yield TestClient(app)
    frigate_consent.invalidate()


def _hdr(user):
    return {"Authorization": f"Bearer {auth.create_token(user, store.get_user(user)['role'])}"}


def _put(client, caller, target, on):
    return client.put(f"/api/users/{target}/frigate", json={"expressions": on}, headers=_hdr(caller))


def test_on_by_default_for_everyone(client):
    for u in store.list_users():
        assert u["frigate_expressions"] is True, u["username"]
    assert frigate_consent.expressions_off() == frozenset()


@pytest.mark.parametrize("who", ["mum", "ung", "boss"])
def test_self_deciding_roles_set_their_own(client, who):
    r = _put(client, who, who, False)
    assert r.status_code == 200 and r.json()["expressions"] is False
    assert store.get_user(who)["frigate_expressions"] is False
    assert store.get_user(who)["frigate_tracking"] is True, "the tracking consent is untouched"


def test_nobody_else_sets_an_adults(client):
    assert _put(client, "boss", "mum", False).status_code == 403
    assert store.get_user("mum")["frigate_expressions"] is True


def test_guardian_sets_the_childs_and_the_child_does_not(client):
    assert _put(client, "kid", "kid", False).status_code == 403
    assert _put(client, "mum", "kid", False).status_code == 200
    assert store.get_user("kid")["frigate_expressions"] is False


def test_admin_decides_for_a_child_without_guardian(client):
    assert _put(client, "boss", "orphan", False).status_code == 200


def test_the_view_carries_both_switches(client):
    r = client.get("/api/users/mum/frigate", headers=_hdr("mum")).json()
    assert r["tracking"] is True and r["expressions"] is True


def test_a_put_without_any_switch_is_refused(client):
    assert client.put("/api/users/mum/frigate", json={}, headers=_hdr("mum")).status_code == 422


def test_tracking_alone_still_works(client):
    r = client.put("/api/users/mum/frigate", json={"tracking": False}, headers=_hdr("mum"))
    assert r.status_code == 200 and r.json()["tracking"] is False and r.json()["expressions"] is True


def test_off_list_is_by_username(client):
    _put(client, "mum", "mum", False)
    frigate_consent.invalidate()
    assert frigate_consent.expressions_off() == frozenset({"mum"})


def test_a_store_error_fails_closed(client, monkeypatch):
    frigate_consent.invalidate()

    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(store, "list_expressions_off", boom)
    assert frigate_consent.expressions_off() is None
