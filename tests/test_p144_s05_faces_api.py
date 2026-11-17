"""/api/frigate_user/* (P144-s05), admin only, fail closed. Frigate's library and users.db are temporary."""

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.frigate_user import faces
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import store

LIBRARY = {"Person A": ["a1.webp"], "Person B": ["b1.webp", "b2.webp"], "Person C": [], "train": ["t.webp"]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    state = {"library": dict(LIBRARY), "down": False}

    async def fake_library_json(_base):
        if state["down"]:
            raise OSError("frigate down")
        return state["library"]

    async def fake_image(name, file):
        return b"IMG:" + f"{name}/{file}".encode(), "image/webp"

    monkeypatch.setattr(rfu, "_fetch_library_json", fake_library_json)
    monkeypatch.setattr(rfu, "get_service", lambda svc, key: {"url": "http://frigate.test", "timeout": 5}.get(key))
    monkeypatch.setattr(rfu, "_fetch_image", fake_image)
    store.init_db()
    store.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    store.create_user(username="mum", display_name="Mum", role="adult", pin="482915")
    store.update_user("mum", frigate_faces=["Person A"])
    app = FastAPI()
    app.include_router(rfu.router)
    c = TestClient(app)
    c.state = state
    c.cfg = tmp_path / "frigate_faces.yaml"
    return c


def _hdr(user, role):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}



@pytest.mark.parametrize("method,path", [
    ("get", "/api/frigate_user/faces"),
    ("put", "/api/frigate_user/faces/Person A"),
    ("put", "/api/frigate_user/settings"),
    ("get", "/api/frigate_user/faces/Person A/thumb"),
])
@pytest.mark.parametrize("who", [None, ("mum", "adult"), ("kid", "child")])
def test_non_admin_is_refused(client, method, path, who):
    headers = _hdr(*who) if who else {}
    body = {"class": "household", "attributes": {}} if path.endswith("A") else {"unclassified_name_days": 3}
    r = getattr(client, method)(path, headers=headers, **({"json": body} if method == "put" else {}))
    assert r.status_code in (401, 403)
    assert not client.cfg.exists()


def test_list_joins_library_classification_and_linked_user(client):
    client.cfg.write_text(yaml.safe_dump({"faces": {"Person B": {"class": "safe_guest"}}}), encoding="utf-8")
    v = client.get("/api/frigate_user/faces", headers=_hdr("boss", "admin")).json()
    by = {f["name"]: f for f in v["faces"]}
    assert set(by) == {"Person A", "Person B", "Person C"}  # Frigate's «train» folder is not a person
    assert by["Person A"]["linked_user"] == {"username": "mum", "display_name": "Mum"}
    assert by["Person B"]["class"] == "safe_guest" and by["Person B"]["linked_user"] is None
    assert by["Person B"]["images"] == 2
    assert v["unclassified_count"] == 2
    assert v["unclassified_name_days"] == 7
    assert v["error"] == ""
    assert set(v["attribute_values"]) == {"beard", "glasses", "age_band", "headwear_often"}


def test_list_keeps_configured_faces_when_frigate_is_down(client):
    client.cfg.write_text(yaml.safe_dump({"faces": {"Person B": {"class": "household"}}}), encoding="utf-8")
    client.state.update(down=True)
    v = client.get("/api/frigate_user/faces", headers=_hdr("boss", "admin")).json()
    assert v["error"] == "unreachable"
    assert [f["name"] for f in v["faces"]] == ["Person B"]
    assert v["faces"][0]["in_library"] is False


def test_put_classifies_and_returns_row(client):
    r = client.put("/api/frigate_user/faces/Person C", headers=_hdr("boss", "admin"),
                   json={"class": "not_welcome", "attributes": {"beard": "yes", "age_band": "adult"}})
    assert r.status_code == 200, r.text
    assert r.json()["class"] == "not_welcome" and r.json()["attributes"]["beard"] == "yes"
    assert faces.classify("Person C") == "not_welcome"


@pytest.mark.parametrize("body", [
    {"class": "friend", "attributes": {}},
    {"class": "household", "attributes": {"beard": "maybe"}},
    {"class": "household", "attributes": {"eye_colour": "blue"}},
    {"class": "household", "attributes": "beard"},
    {"attributes": {}},
])
def test_put_rejects_bad_values(client, body):
    r = client.put("/api/frigate_user/faces/Person A", headers=_hdr("boss", "admin"), json=body)
    assert r.status_code in (400, 422)
    assert not client.cfg.exists()


def test_put_unknown_face_is_404(client):
    r = client.put("/api/frigate_user/faces/Person Z", headers=_hdr("boss", "admin"),
                   json={"class": "household", "attributes": {}})
    assert r.status_code == 404
    assert not client.cfg.exists()


def test_put_settings(client):
    h = _hdr("boss", "admin")
    assert client.put("/api/frigate_user/settings", headers=h, json={"unclassified_name_days": 3}).json() == {
        "unclassified_name_days": 3
    }
    assert client.put("/api/frigate_user/settings", headers=h, json={"unclassified_name_days": 500}).status_code == 400


def test_thumb_only_for_library_faces_and_file_chosen_by_server(client):
    h = _hdr("boss", "admin")
    r = client.get("/api/frigate_user/faces/Person B/thumb", headers=h)
    assert r.status_code == 200 and r.content == b"IMG:Person B/b1.webp"
    assert client.get("/api/frigate_user/faces/Person C/thumb", headers=h).status_code == 404  # no images
    assert client.get("/api/frigate_user/faces/Person Z/thumb", headers=h).status_code == 404
    assert client.get("/api/frigate_user/faces/train/thumb", headers=h).status_code == 404
