"""Onboarding status labels: English in the API (rule 8), translated by step id in the GUI (DT04-R2k, DT04-s06 item 1)."""

import json
from pathlib import Path

import pytest

import kaare_core.routers.router_settings as rs

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def status(tmp_path, monkeypatch):
    (tmp_path / "settings.yaml").write_text("location: {lat: 0, lon: 0}\n", encoding="utf-8")
    (tmp_path / "services.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "cap.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(rs, "_SERVICES_PATH", tmp_path / "services.yaml")
    monkeypatch.setattr(rs, "_CAPABILITY_MAP_PATH", tmp_path / "cap.yaml")
    monkeypatch.setattr(rs, "_list_users", lambda: [{"username": "admin"}])
    return rs.api_onboarding_status(_u=None)


def test_api_labels_are_english(status):
    labels = {s["id"]: s["label"] for s in status["steps"]}
    assert labels == {
        "location": "Location set",
        "user": "User created",
        "distribution": "Distribution profile chosen",
    }


def test_every_step_id_has_a_gui_label_in_all_languages(status):
    for lang in ("nb", "en", "de"):
        tree = json.loads((ROOT / "frontend/src/locales" / f"{lang}.json").read_text(encoding="utf-8"))
        for step in status["steps"]:
            assert isinstance(tree["dashboard"]["onboarding_steps"].get(step["id"]), str), (lang, step["id"])


def test_dashboard_translates_steps_by_id():
    src = (ROOT / "frontend/src/pages/admin/Dashboard.tsx").read_text(encoding="utf-8")
    assert "dashboard.onboarding_steps.${s.id}" in src
    assert "map(s => s.label)" not in src
