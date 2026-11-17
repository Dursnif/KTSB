"""DT04-S05r / S05t — a rollback puts configs/ back as they were before the update, says what it did, and tells an
admin's own choice apart from a failed update.

S05r (observed 2026-11-17): «Gå tilbake til v1.10.3» swapped images, compose and .env back but left configs/ as v1.11
had migrated them; v1.10.3 read `cors_origins: ["same-host"]` literally and its GUI hung behind CORS. The updater now
keeps its own copy of configs/ (without .history) next to the previous bundle at the moment it switches, and puts it
back on every rollback, before the old services start. state/ (the data) is never touched by a rollback.
"""

import importlib.util
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

UPDATER_PATH = Path(__file__).resolve().parent.parent / "services" / "updater" / "updater.py"
TOKEN = "test-service-token"
OLD_SETTINGS = "# mine\ncors_origins:\n  - \"*\"\n"
MIGRATED = "# mine\ncors_origins:\n  - \"same-host\"\nscreens:\n  enabled: true\n"


@pytest.fixture
def upd(tmp_path, monkeypatch):
    home = tmp_path / "ktsb"
    for d in ("configs", "state", "logs", "docker"):
        (home / d).mkdir(parents=True)
    (home / "docker" / "Caddyfile").write_text("old caddy\n")
    (home / "docker-compose.yml").write_text("name: kaare\n# old\n")
    (home / ".env").write_text("KTSB_VERSION=v1.10.3\n")
    (home / "configs" / "service_token.env").write_text(f"KAARE_SERVICE_TOKEN={TOKEN}\n")
    (home / "configs" / "settings.yaml").write_text(OLD_SETTINGS)
    (home / "configs" / ".history" / "settings.yaml").mkdir(parents=True)
    (home / "configs" / ".history" / "settings.yaml" / "old.yaml").write_text("history\n")
    (home / "state" / "notes.json").write_text("before\n")
    monkeypatch.setenv("KTSB_HOME", str(home))
    spec = importlib.util.spec_from_file_location("ktsb_updater_s05r", UPDATER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "SOCK_PATH", tmp_path / "sock" / "updater.sock")
    return mod


def _mock(upd, monkeypatch, fail_health_of=None):
    """Docker/cosign/network mocked. The new version's first start «migrates» configs and the user writes data."""
    calls = []

    def staged(rel):
        root = upd.STAGE_DIR / "x" / "b"
        (root / "docker").mkdir(parents=True)
        (root / "docker-compose.yml").write_text("name: kaare\n# new\n")
        (root / "VERSION").write_text(rel["version"] + "\n")
        return root

    def start():
        calls.append("start")
        if upd.current_version() == "v1.11.0":  # the new version migrates on start
            (upd.HOME / "configs" / "settings.yaml").write_text(MIGRATED)
            (upd.HOME / "configs" / "new_in_v111.yaml").write_text("x: 1\n")
            (upd.HOME / "state" / "notes.json").write_text("written after the update\n")
        else:  # the old version starts: what does it see?
            calls.append("old start saw: " + (upd.HOME / "configs" / "settings.yaml").read_text())

    def health(v):
        calls.append(f"health {v}")
        if v == fail_health_of:
            raise upd.UpdateError("API never answered")

    monkeypatch.setattr(upd, "preflight", lambda v: {"version": v})
    monkeypatch.setattr(upd, "download_and_stage", staged)
    monkeypatch.setattr(upd, "services_except_self", lambda: ["kaare-api"])
    monkeypatch.setattr(upd, "compose", lambda *a, **k: "")
    monkeypatch.setattr(upd, "own_images", list)
    monkeypatch.setattr(upd, "start_services", start)
    monkeypatch.setattr(upd, "health", health)
    monkeypatch.setattr(upd, "self_update_if_needed", lambda v: calls.append("self_update"))
    return calls


def test_manual_rollback_restores_configs_before_the_old_version_starts(upd, monkeypatch):
    calls = _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")
    assert (upd.HOME / "configs" / "settings.yaml").read_text() == MIGRATED
    upd.do_manual_rollback()
    assert (upd.HOME / "configs" / "settings.yaml").read_text() == OLD_SETTINGS
    assert "old start saw: " + OLD_SETTINGS in calls  # restored BEFORE the old services started
    st = upd.read_state()
    assert st["result"] == "rolled_back" and st["configs_restored"] == "restored"


def test_rollback_never_touches_state_and_keeps_history(upd, monkeypatch):
    _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")
    upd.do_manual_rollback()
    assert (upd.HOME / "state" / "notes.json").read_text() == "written after the update\n"
    assert (upd.HOME / "configs" / ".history" / "settings.yaml" / "old.yaml").read_text() == "history\n"
    assert not (upd.PREV_DIR / "configs" / ".history").exists()  # the copy skips the history folder


def test_automatic_rollback_restores_configs_too(upd, monkeypatch):
    _mock(upd, monkeypatch, fail_health_of="v1.11.0")
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["result"] == "rolled_back"
    assert (upd.HOME / "configs" / "settings.yaml").read_text() == OLD_SETTINGS
    assert st["configs_restored"] == "restored"


def test_previous_without_a_config_copy_is_said_not_hidden(upd, monkeypatch):
    """A previous version kept by an older updater has no config copy: roll back anyway, and say so."""
    _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")
    shutil.rmtree(upd.PREV_DIR / "configs")
    upd.do_manual_rollback()
    st = upd.read_state()
    assert st["result"] == "rolled_back" and st["configs_restored"] == "missing"
    assert (upd.HOME / "configs" / "settings.yaml").read_text() == MIGRATED


def _snap(upd, stamp, tag, settings):
    d = upd.HOME / "configs" / ".history" / "_snapshots" / f"{stamp}_{tag}"
    d.mkdir(parents=True)
    (d / "settings.yaml").write_text(settings)
    return d


def test_previous_from_an_older_updater_falls_back_to_the_first_pre_migrate_snapshot(upd, monkeypatch):
    """Every v1.10.x install updates with the v1.10.x updater, which keeps no config copy. The new version's migration
    snapshots configs/ before it changes anything — the first such snapshot after the switch is exactly what the old
    version left. An older snapshot (before the switch) and a later one (after another migration) are not used."""
    _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")
    shutil.rmtree(upd.PREV_DIR / "configs")
    switch = upd.PREV_DIR.stat().st_mtime
    os.utime(upd.PREV_DIR, (switch, switch))
    _snap(upd, "20000101T000000000000Z", "pre-migrate-v9", "too old\n")
    after = datetime.fromtimestamp(switch + 30, UTC).strftime("%Y%m%dT%H%M%S%fZ")
    later = datetime.fromtimestamp(switch + 3600, UTC).strftime("%Y%m%dT%H%M%S%fZ")
    _snap(upd, after, "pre-migrate-v13", OLD_SETTINGS)
    _snap(upd, later, "pre-migrate-v13", "after a restore\n")
    _snap(upd, datetime.fromtimestamp(switch + 10, UTC).strftime("%Y%m%dT%H%M%S%fZ"), "pre-reset", "not a migration\n")
    upd.do_manual_rollback()
    st = upd.read_state()
    assert st["configs_restored"] == "restored_from_premigrate"
    assert (upd.HOME / "configs" / "settings.yaml").read_text() == OLD_SETTINGS
    assert after in (upd.HOME / "logs" / "audit.log").read_text()  # the source is logged


def test_failed_config_restore_is_reported(upd, monkeypatch):
    _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(upd, "_restore_configs", broken)
    upd.do_manual_rollback()
    st = upd.read_state()
    assert st["configs_restored"].startswith("failed")
    assert "config_restore_failed" in (upd.HOME / "logs" / "audit.log").read_text()


def test_admin_rollback_and_failed_update_are_told_apart(upd, monkeypatch):
    """S05t: «requested by an admin» was shown as «the update failed». Codes for the GUI, never raw English."""
    _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")
    upd.do_manual_rollback()
    st = upd.read_state()
    assert st["rollback_kind"] == "admin" and st["reason_code"] == "admin_request"
    assert st["left_version"] == "v1.11.0" and st["target"] == "v1.10.3"
    assert st["from_version"] == "v1.10.3"  # unchanged: a second rollback must still aim at the kept previous


def test_automatic_rollback_carries_the_failed_step(upd, monkeypatch):
    _mock(upd, monkeypatch, fail_health_of="v1.11.0")
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["rollback_kind"] == "auto" and st["reason_code"] == "step_failed" and st["failed_step"] == "health"


def test_check_cache_is_cleared_after_a_rollback(upd, monkeypatch):
    _mock(upd, monkeypatch)
    upd.do_update("v1.11.0")
    upd._check_cache.update(at=10**12, result={"current": "v1.11.0", "available": False}, test=False)
    upd.do_manual_rollback()
    assert upd._check_cache["result"] is None
