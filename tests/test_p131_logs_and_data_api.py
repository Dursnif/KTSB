"""Settings → General → Logs and data (P131 Phase 4): API round trip, validation, live pickup."""

import asyncio

import pytest
import yaml
from fastapi import HTTPException

from kaare_core import config
from kaare_core.routers import router_settings as rs


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "settings.yaml"
    p.write_text(
        "kare_language: nb\ndata_retention:\n  episodes_retention_days: 90\n", encoding="utf-8"
    )
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_log_flush_cache", {"mtime": None, "seconds": 2.0})
    return p


def _get():
    return asyncio.run(rs.api_get_logs_and_data(_u=None))


def _put(payload):
    return asyncio.run(rs.api_put_logs_and_data(payload, _u=None))


def test_defaults_and_existing_values(settings_file):
    got = _get()
    assert got["flush_interval_seconds"] == 2.0
    assert got["retention"] == {
        "interactions_retention_days": 0,
        "episodes_retention_days": 90,
        "think_cache_retention_days": 30,
    }
    assert got["recommended"]["flush_min"] == 2 and got["recommended"]["flush_max"] == 10


def test_put_saves_and_flush_interval_is_picked_up_without_restart(settings_file):
    got = _put({"flush_interval_seconds": 5, "retention": {"think_cache_retention_days": 14}})
    assert got["flush_interval_seconds"] == 5.0
    assert config.get_log_flush_seconds() == 5.0  # the accessor log_buffer uses, read live
    data = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert data["logging"]["flush_interval_seconds"] == 5.0
    assert data["data_retention"] == {
        "episodes_retention_days": 90,
        "think_cache_retention_days": 14,
    }
    assert data["kare_language"] == "nb"  # other settings untouched


@pytest.mark.parametrize(
    "payload",
    [
        {"flush_interval_seconds": -1},
        {"flush_interval_seconds": 31},
        {"flush_interval_seconds": "fast"},
        {"retention": {"episodes_retention_days": -3}},
        {"retention": {"episodes_retention_days": "a week"}},
        {"retention": {"passwords_retention_days": 3}},
    ],
)
def test_invalid_values_are_refused_and_nothing_is_written(settings_file, payload):
    before = settings_file.read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as err:
        _put(payload)
    assert err.value.status_code == 400
    assert settings_file.read_text(encoding="utf-8") == before


def test_zero_means_immediate_writes(settings_file):
    assert _put({"flush_interval_seconds": 0})["flush_interval_seconds"] == 0.0
    assert config.get_log_flush_seconds() == 0.0


def test_accessor_clamps_hand_edited_values(settings_file):
    settings_file.write_text("logging:\n  flush_interval_seconds: 999\n", encoding="utf-8")
    assert config.get_log_flush_seconds() == config.LOG_FLUSH_MAX_SECONDS
