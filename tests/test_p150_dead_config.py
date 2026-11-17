"""P150 phase 6a (2026-11-14, two yes): old settings nothing reads any more are gone — from the house, the templates
(so migrate_configs never puts them back) and the one API branch that still accepted one. Time used as a window or for
privacy stays; only the old deletion rules replaced by size limits go."""

import asyncio
from pathlib import Path

import yaml

from kaare_core.routers import router_system

TEMPLATES = Path("/kaare/configs_default")


def test_storage_api_no_longer_writes_the_old_analysis_log_limit(tmp_path, monkeypatch):
    p = tmp_path / "frigate_cameras.yaml"
    p.write_text(yaml.dump({"enabled": True, "storage": {"snapshots_max_mb": 500}}), encoding="utf-8")
    monkeypatch.setattr(router_system, "_FRIGATE_CAMERAS_PATH", p)
    monkeypatch.setattr(router_system, "load_camera_config", dict)
    asyncio.run(router_system.api_put_cameras_storage({"snapshots_max_mb": 600, "log_max_mb": 50}, _u=None))
    storage = yaml.safe_load(p.read_text(encoding="utf-8"))["storage"]
    assert storage == {"snapshots_max_mb": 600}


def test_templates_ship_none_of_the_dead_settings():
    s = yaml.safe_load((TEMPLATES / "settings.yaml").read_text(encoding="utf-8"))
    assert "face_events" not in s
    assert "history_days" not in s["stm"]
    assert not {"max_entries", "trim_margin"} & set(s["think_cache"])
    assert "think_cache_retention_days" not in s["data_retention"]
    assert "inner_thoughts_retention_hours" not in (s.get("jang") or {})
    cams = yaml.safe_load((TEMPLATES / "frigate_cameras.yaml").read_text(encoding="utf-8"))
    assert "log_max_mb" not in cams["storage"]
    fu = yaml.safe_load((TEMPLATES / "frigate_user.yaml").read_text(encoding="utf-8"))
    assert not {"event_detail_days", "hourly_days", "daily_days"} & set(fu)


def test_time_as_a_window_stays():
    s = yaml.safe_load((TEMPLATES / "settings.yaml").read_text(encoding="utf-8"))
    assert "response_max_chars" in s["think_cache"], "a live setting, not a deletion rule"
