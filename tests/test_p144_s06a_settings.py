"""P144-s06a: the Frigate user's switch + settings (rule 25): template off and generic, accessor defaults and clamps,
the migration copies a missing file, and the admin-only API. Never touches the live configs/."""

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kaare_core import config_migrations
from kaare_core.frigate_user import settings as fsettings
from kaare_core.routers import router_frigate_user
from kaare_core.users.auth import require_admin

TEMPLATE = Path("/kaare/configs_default/frigate_user.yaml")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "frigate_user.yaml"
    monkeypatch.setattr(fsettings, "CONFIG_PATH", path)
    return path


def test_template_is_on_and_generic():
    data = yaml.safe_load(TEMPLATE.read_text())
    assert data["enabled"] is True  # DT04 R0.6b: ships on (heavy modules off); was False
    assert data["episode_gap_s"] == 120 and data["visit_gap_s"] == 1800
    assert data["l0_min_face_px"] == 10000
    c = data["conclude"]
    assert c["idle_gate_s"] == 30 and c["urgent_classes"] == ["not_welcome"]
    assert c["urgent_when_away_person"] is True and c["urgent_labels"] == ["fire", "smoke"]
    assert c["max_attempts"] == 3 and c["poll_s"] == 30


def test_defaults_without_file(cfg):
    s = fsettings.get_settings()
    assert s["enabled"] is False
    assert fsettings.enabled() is False
    assert fsettings.episode_gap_s() == 120 and fsettings.visit_gap_s() == 1800
    assert fsettings.l0_min_face_px() == 10000
    assert fsettings.conclude()["idle_gate_s"] == 30


def test_bad_values_fall_back_and_clamp(cfg):
    cfg.write_text("enabled: maybe\nepisode_gap_s: -5\nvisit_gap_s: abc\nl0_min_face_px: 99999999\n"
                   "conclude: {idle_gate_s: 5000, urgent_classes: [not_welcome, bogus]}\n")
    assert fsettings.enabled() is False
    assert fsettings.episode_gap_s() == fsettings.LIMITS["episode_gap_s"][0]
    assert fsettings.visit_gap_s() == 1800
    assert fsettings.l0_min_face_px() == fsettings.LIMITS["l0_min_face_px"][1]
    c = fsettings.conclude()
    assert c["idle_gate_s"] == fsettings.LIMITS["conclude.idle_gate_s"][1]
    assert c["urgent_classes"] == ["not_welcome"]


def test_broken_file_never_raises(cfg):
    cfg.write_text("{{{ not yaml")
    assert fsettings.get_settings()["enabled"] is False


def test_update_validates_and_writes(cfg):
    out = fsettings.update({"enabled": True, "episode_gap_s": 90, "conclude": {"idle_gate_s": 60}})
    assert out["enabled"] is True and out["episode_gap_s"] == 90 and out["conclude"]["idle_gate_s"] == 60
    assert fsettings.enabled() is True
    with pytest.raises(ValueError):
        fsettings.update({"episode_gap_s": 0})
    with pytest.raises(ValueError):
        fsettings.update({"nonsense": 1})
    with pytest.raises(ValueError):
        fsettings.update({"enabled": "yes"})


def test_migration_copies_missing_file(tmp_path):
    defaults = tmp_path / "defaults"
    config = tmp_path / "configs"
    defaults.mkdir()
    config.mkdir()
    (defaults / "frigate_user.yaml").write_text(TEMPLATE.read_text())
    missing = config_migrations._missing_files(config, defaults)
    assert (config / "frigate_user.yaml", defaults / "frigate_user.yaml") in missing


# ── API ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setattr(router_frigate_user, "_audit", lambda *a, **k: None)  # never the live audit.log
    app = FastAPI()
    app.include_router(router_frigate_user.router)
    return app, TestClient(app)


def test_api_refuses_without_admin(client):
    _app, c = client
    assert c.get("/api/frigate_user/pipe").status_code in (401, 403)
    assert c.put("/api/frigate_user/pipe", json={"enabled": True}).status_code in (401, 403)


def test_api_get_put(client):
    app, c = client
    app.dependency_overrides[require_admin] = lambda: {"sub": "admin_x", "role": "admin"}
    r = c.get("/api/frigate_user/pipe")
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = c.put("/api/frigate_user/pipe", json={"enabled": True, "l0_min_face_px": 12000})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["l0_min_face_px"] == 12000
    assert c.put("/api/frigate_user/pipe", json={"visit_gap_s": -1}).status_code == 400
