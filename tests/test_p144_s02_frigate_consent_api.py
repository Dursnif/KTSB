"""Who may set «Frigate may track me» (P144-s02), fail closed:
young adult / adult / admin — only themselves; child / teen — their guardians, or an admin when
no guardian is assigned. Face links are admin-only and one face belongs to one person."""

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
    for name, role in [
        ("boss", "admin"),
        ("mum", "adult"),
        ("dad", "adult"),
        ("ung", "young_adult"),
        ("kid", "child"),
        ("teen", "teen"),
        ("orphan", "child"),
    ]:
        store.create_user(username=name, display_name=name, role=role, pin="482915")
    store.update_user("mum", is_parent=True, managed_children=json.dumps(["kid", "teen"]))
    frigate_consent.invalidate()
    app = FastAPI()
    app.include_router(router_users.router)
    yield TestClient(app)
    frigate_consent.invalidate()


def _hdr(user, role=None):
    role = role or store.get_user(user)["role"]
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


def _put(client, caller, target, allowed, role=None):
    return client.put(
        f"/api/users/{target}/frigate", json={"tracking": allowed}, headers=_hdr(caller, role)
    )


def test_default_is_on_for_everyone(client):
    for u in store.list_users():
        assert u["frigate_tracking"] is True, u["username"]


@pytest.mark.parametrize("who", ["mum", "ung", "boss"])
def test_self_deciding_roles_set_their_own(client, who):
    assert _put(client, who, who, False).status_code == 200
    assert store.get_user(who)["frigate_tracking"] is False


@pytest.mark.parametrize("caller", ["boss", "dad", "kid"])
def test_nobody_else_sets_an_adults(client, caller):
    assert _put(client, caller, "mum", False).status_code == 403
    assert store.get_user("mum")["frigate_tracking"] is True


@pytest.mark.parametrize("child", ["kid", "teen"])
def test_guardian_sets_childs(client, child):
    assert _put(client, "mum", child, False).status_code == 200
    assert store.get_user(child)["frigate_tracking"] is False


@pytest.mark.parametrize("caller", ["kid", "dad", "boss", "ung"])
def test_child_with_guardian_only_guardian(client, caller):
    """The child itself, a non-guardian adult and even admin are refused once a guardian exists."""
    assert _put(client, caller, "kid", False).status_code == 403
    assert store.get_user("kid")["frigate_tracking"] is True


def test_child_without_guardian_admin_sets(client):
    assert _put(client, "dad", "orphan", False).status_code == 403
    assert _put(client, "orphan", "orphan", False).status_code == 403
    assert _put(client, "boss", "orphan", False).status_code == 200
    assert store.get_user("orphan")["frigate_tracking"] is False


def test_forged_admin_token_role_does_not_help(client):
    """The role in the token is not trusted for this decision — the DB role is."""
    assert _put(client, "dad", "orphan", False, role="admin").status_code == 403


def test_inactive_guardian_refused(client):
    store.update_user("mum", is_active=False)
    assert _put(client, "mum", "kid", False).status_code == 403


def test_unknown_target_and_system_identity(client):
    assert _put(client, "boss", "ghost", False).status_code in (403, 404)
    assert _put(client, "boss", "unknown_user", False).status_code == 403


def test_no_token(client):
    assert client.put("/api/users/mum/frigate", json={"tracking": False}).status_code in (401, 403)


def test_get_shows_who_decides(client):
    r = client.get("/api/users/kid/frigate", headers=_hdr("mum"))
    assert r.status_code == 200
    body = r.json()
    assert body["tracking"] is True and body["decided_by"] == "guardian" and body["can_edit"] is True
    r = client.get("/api/users/mum/frigate", headers=_hdr("boss"))
    assert r.json()["can_edit"] is False and r.json()["decided_by"] == "self"
    assert client.get("/api/users/mum/frigate", headers=_hdr("dad")).status_code == 403


def test_put_invalidates_strip_cache(client):
    store.update_user("mum", frigate_faces=["Mum"])
    assert not frigate_consent.is_untracked("Mum")
    _put(client, "mum", "mum", False)
    assert frigate_consent.is_untracked("Mum")


def test_face_link_admin_only_and_unique(client):
    r = client.put("/api/users/mum", json={"frigate_faces": ["Mum"]}, headers=_hdr("boss"))
    assert r.status_code == 200 and r.json()["frigate_faces"] == ["Mum"]
    r = client.put("/api/users/dad", json={"frigate_faces": ["mum"]}, headers=_hdr("boss"))
    assert r.status_code == 400
    r = client.put("/api/users/mum", json={"frigate_faces": ["Dad"]}, headers=_hdr("mum"))
    assert r.status_code == 403
    r = client.put("/api/users/mum", json={"frigate_faces": []}, headers=_hdr("boss"))
    assert r.json()["frigate_faces"] == []
