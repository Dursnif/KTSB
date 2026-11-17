"""J21 step 5e (P144 phase 5): the module panel «Moduler» — GET /api/frigate_user/modules (admin) shows every module's
state, its needs / greyed reason and the «dobbelt arbeid» warning, from `modules.status()`. Tmp config; nothing live."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.frigate_user import alerts, face_votes, modules
from kaare_core.frigate_user import settings as fsettings
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: false}\n")
    monkeypatch.setattr(modules, "camera_config", lambda: {"enabled": True})
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    ustore.create_user(username="mum", display_name="Mum", role="adult", pin="482915")
    frigate_consent.invalidate()
    app = FastAPI()
    app.include_router(rfu.router)
    yield TestClient(app)
    frigate_consent.invalidate()


def _hdr(user, role):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


@pytest.mark.parametrize("who", [None, ("mum", "adult")])
def test_the_panel_is_admin_only(client, who):
    r = client.get("/api/frigate_user/modules", headers=_hdr(*who) if who else {})
    assert r.status_code == (401 if who is None else 403)


def test_the_panel_shows_every_module_with_its_state(client):
    d = client.get("/api/frigate_user/modules", headers=_hdr("boss", "admin")).json()
    by = {m["n"]: m for m in d["modules"]}
    assert sorted(by) == list(range(8))
    assert by[0]["on"] and by[7]["on"] and by[0]["setting"] == "always"
    assert not by[1]["on"] and by[1]["replaced"] and by[4]["on"] and not by[2]["on"]  # m1 gone; Double Take off here
    assert d["warnings"] == []
    assert set(by[4]) >= {"n", "setting", "on", "why", "needs", "needs_missing", "switchable"}
    assert by[4]["needs"] == ["0", "3"] and by[5]["needs"] == ["0", "4|1"]


def test_switchable_marks_only_what_acts(client):
    d = client.get("/api/frigate_user/modules", headers=_hdr("boss", "admin")).json()
    # P144 6e: module 1 is replaced (L3 + module 4) — shown, never switched
    assert {m["n"] for m in d["modules"] if m["switchable"]} == {2, 4, 5, 6}  # DT04 R0.6b: m4–m6 act; was {2}


# ── module 7 and the acting switch ───────────────────────────────────────────────────────────────────────

def test_the_alert_is_sent_once_with_module_1_gone(monkeypatch):
    """P144 phase 6: the old responder is gone; module 7's own starter alone sends the away alert — exactly once."""
    sent = []

    async def m7(camera, label, score):
        sent.append(camera)

    monkeypatch.setattr(alerts, "maybe_notify_away", m7)
    asyncio.run(alerts.on_end_event({"event_id": "e1", "camera": "cam", "label": "person", "score": 0.9}))
    assert sent == ["cam"]


def test_m2_off_turns_double_take_votes_off(monkeypatch):

    monkeypatch.setattr(face_votes.settings, "enabled", lambda: True)
    monkeypatch.setattr(face_votes, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(modules, "is_on", lambda m: True)
    assert face_votes.enabled() is True
    monkeypatch.setattr(modules, "is_on", lambda m: m != 2)
    assert face_votes.enabled() is False
