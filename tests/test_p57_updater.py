"""P57 — in-app updater (services/updater/updater.py) and the API side (router_update).

The updater module computes its paths from KTSB_HOME at import, so each test loads a fresh copy pointed at a
temporary install folder — nothing here touches /kaare or the Docker engine.
"""

import fcntl
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kaare_core.routers import router_update

UPDATER_PATH = Path(__file__).resolve().parent.parent / "services" / "updater" / "updater.py"
TOKEN = "test-service-token"


@pytest.fixture
def upd(tmp_path, monkeypatch):
    home = tmp_path / "ktsb"
    (home / "configs").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "logs").mkdir()
    (home / "docker").mkdir()
    (home / "docker" / "Caddyfile").write_text("old caddy\n")
    (home / "docker-compose.yml").write_text("name: kaare\n# old\n")
    (home / "setup.sh").write_text("#!/bin/sh\n# old\n")
    (home / ".env").write_text("# my settings\nCOMPOSE_PROFILES=medium\nKTSB_VERSION=v1.10.0\nKAARE_DOMAIN=x\n")
    (home / "configs" / "service_token.env").write_text(f"KAARE_SERVICE_TOKEN={TOKEN}\n")
    monkeypatch.setenv("KTSB_HOME", str(home))
    spec = importlib.util.spec_from_file_location("ktsb_updater_under_test", UPDATER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "SOCK_PATH", tmp_path / "sock" / "updater.sock")
    return mod


# ── versions, .env ─────────────────────────────────────────────────────────────


def test_parse_version(upd):
    assert upd.parse_version("v1.10.2") == (1, 10, 2)
    assert upd.parse_version("v1.10.10") > upd.parse_version("v1.10.9")
    for bad in ("1.10.2", "v1.10", "v1.10.2-rc1", "latest", "", "v1.10.2; rm -rf /"):
        assert upd.parse_version(bad) is None


def test_env_value_replaced_and_rest_kept(upd):
    upd.set_env_value("KTSB_VERSION", "v1.11.0")
    text = (upd.HOME / ".env").read_text()
    assert "KTSB_VERSION=v1.11.0" in text and "KTSB_VERSION=v1.10.0" not in text
    assert "# my settings" in text and "KAARE_DOMAIN=x" in text
    upd.set_env_value("NEW_KEY", "1")
    assert upd.read_env_file()["NEW_KEY"] == "1"
    assert upd.current_version() == "v1.11.0"


def test_commented_key_is_not_treated_as_set(upd):
    (upd.HOME / ".env").write_text("# KTSB_VERSION=v9.9.9\n")
    assert upd.current_version() == ""
    upd.set_env_value("KTSB_VERSION", "v1.11.0")
    assert (upd.HOME / ".env").read_text().splitlines() == ["# KTSB_VERSION=v9.9.9", "KTSB_VERSION=v1.11.0"]


# ── trust anchors ──────────────────────────────────────────────────────────────


def test_signer_identity_only_accepts_tag_builds_of_our_workflow(upd):
    rx = re.compile(upd.SIGNER_IDENTITY)
    assert rx.match("https://github.com/Dursnif/KTSB/.github/workflows/build.yml@refs/tags/v1.11.0")
    assert rx.match("https://github.com/dursnif/ktsb/.github/workflows/build.yml@refs/tags/v1.11.0")
    for bad in (
        "https://github.com/Dursnif/KTSB/.github/workflows/build.yml@refs/heads/main",
        "https://github.com/Dursnif/KTSB/.github/workflows/other.yml@refs/tags/v1.11.0",
        "https://github.com/evil/KTSB/.github/workflows/build.yml@refs/tags/v1.11.0",
        "https://github.com/Dursnif/KTSB-fork/.github/workflows/build.yml@refs/tags/v1.11.0",
    ):
        assert not rx.match(bad), bad


def test_bundle_checksum(upd, tmp_path, monkeypatch):
    monkeypatch.setattr(upd, "_cosign", lambda *a, **k: "")
    tarball = tmp_path / "ktsb-docker-v1.11.0.tar.gz"
    tarball.write_bytes(b"bundle")
    good = hashlib.sha256(b"bundle").hexdigest()
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"{good}  {tarball.name}\n")
    upd.verify_bundle(tarball, tmp_path / "sig.json", sums)  # no exception
    sums.write_text(f"{'0' * 64}  {tarball.name}\n")
    with pytest.raises(upd.UpdateError, match="checksum"):
        upd.verify_bundle(tarball, tmp_path / "sig.json", sums)
    sums.write_text(f"{good}  something-else.tar.gz\n")
    with pytest.raises(upd.UpdateError, match="missing"):
        upd.verify_bundle(tarball, tmp_path / "sig.json", sums)


def test_bundle_signature_failure_stops_before_checksum(upd, tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise upd.UpdateError("cosign verify-blob failed")

    monkeypatch.setattr(upd, "_cosign", refuse)
    with pytest.raises(upd.UpdateError, match="cosign"):
        upd.verify_bundle(tmp_path / "x.tar.gz", tmp_path / "sig.json", tmp_path / "SHA256SUMS")


def test_verify_image_needs_exactly_one_digest(upd, monkeypatch):
    one = json.dumps([{"critical": {"image": {"docker-manifest-digest": "sha256:abc"}}}])
    monkeypatch.setattr(upd, "_cosign", lambda *a, **k: one)
    assert upd.verify_image("ghcr.io/dursnif/kaare:v1.11.0") == "sha256:abc"
    two = json.dumps(
        [
            {"critical": {"image": {"docker-manifest-digest": "sha256:abc"}}},
            {"critical": {"image": {"docker-manifest-digest": "sha256:def"}}},
        ]
    )
    monkeypatch.setattr(upd, "_cosign", lambda *a, **k: two)
    with pytest.raises(upd.UpdateError):
        upd.verify_image("ghcr.io/dursnif/kaare:v1.11.0")


# ── files: keep previous, swap, restore ────────────────────────────────────────


def test_previous_roundtrip_leaves_user_files_alone(upd, tmp_path):
    (upd.HOME / "docker-compose.override.yml").write_text("mine\n")
    upd.save_previous()
    new = tmp_path / "new"
    (new / "docker").mkdir(parents=True)
    (new / "docker" / "Caddyfile").write_text("new caddy\n")
    (new / "docker-compose.yml").write_text("name: kaare\n# new\n")
    (new / "VERSION").write_text("v1.11.0\n")
    upd._copy_bundle(new, upd.HOME)
    upd.set_env_value("KTSB_VERSION", "v1.11.0")
    assert "# new" in (upd.HOME / "docker-compose.yml").read_text()
    assert (upd.HOME / "docker" / "Caddyfile").read_text() == "new caddy\n"
    upd.restore_previous()
    assert "# old" in (upd.HOME / "docker-compose.yml").read_text()
    assert (upd.HOME / "docker" / "Caddyfile").read_text() == "old caddy\n"
    assert upd.current_version() == "v1.10.0"
    assert (upd.HOME / "docker-compose.override.yml").read_text() == "mine\n"


# ── the update flow (docker/cosign/network mocked) ─────────────────────────────


def _mock_flow(upd, monkeypatch, fail_at=None, calls=None, fail_always=False):
    calls = calls if calls is not None else []

    def maybe_fail(name):
        calls.append(name)
        if name == fail_at and (fail_always or calls.count(name) == 1):
            raise upd.UpdateError(f"{name} broke")

    def staged(rel):
        maybe_fail("download")
        root = upd.STAGE_DIR / "x" / "b"
        (root / "docker").mkdir(parents=True)
        (root / "docker-compose.yml").write_text("name: kaare\n# new\n")
        (root / "VERSION").write_text(rel["version"] + "\n")
        return root

    monkeypatch.setattr(upd, "preflight", lambda v: maybe_fail("preflight") or {"version": v})
    monkeypatch.setattr(upd, "download_and_stage", staged)
    monkeypatch.setattr(upd, "services_except_self", lambda: ["kaare-api"])
    monkeypatch.setattr(upd, "compose", lambda *a, **k: maybe_fail("compose " + a[0]) or "")
    monkeypatch.setattr(upd, "own_images", lambda: ["ghcr.io/dursnif/kaare:v1.11.0"])
    monkeypatch.setattr(upd, "verify_image", lambda ref: maybe_fail("verify_image") or "sha256:good")
    monkeypatch.setattr(upd, "local_digests", lambda ref: {"sha256:good"})
    monkeypatch.setattr(upd, "start_services", lambda: maybe_fail("start"))
    monkeypatch.setattr(upd, "health", lambda v: maybe_fail(f"health {v}"))
    monkeypatch.setattr(upd, "self_update_if_needed", lambda v: calls.append("self_update"))
    return calls


def test_update_success(upd, monkeypatch):
    calls = _mock_flow(upd, monkeypatch)
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["phase"] == "done" and st["result"] == "updated"
    assert upd.current_version() == "v1.11.0"
    assert "# new" in (upd.HOME / "docker-compose.yml").read_text()
    assert calls[-1] == "self_update"
    audit = (upd.HOME / "logs" / "audit.log").read_text()
    assert "update_started" in audit and "update_done" in audit


def test_failure_before_switch_changes_nothing(upd, monkeypatch):
    calls = _mock_flow(upd, monkeypatch, fail_at="download")
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["phase"] == "failed" and "download broke" in st["error"]
    assert upd.current_version() == "v1.10.0"
    assert "start" not in calls


@pytest.mark.parametrize("fail_at", ["compose pull", "verify_image", "start", "health v1.11.0"])
def test_failure_after_switch_rolls_back(upd, monkeypatch, fail_at):
    calls = _mock_flow(upd, monkeypatch, fail_at=fail_at)
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["phase"] == "rolled_back", st
    assert upd.current_version() == "v1.10.0"
    assert "# old" in (upd.HOME / "docker-compose.yml").read_text()
    assert "health v1.10.0" in calls  # the old version was checked after the rollback
    assert "self_update" not in calls


def test_failed_rollback_is_reported_not_hidden(upd, monkeypatch):
    _mock_flow(upd, monkeypatch, fail_at="start", fail_always=True)
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["phase"] == "failed" and st["result"] == "rollback_failed"
    assert "rollback also failed" in st["error"]
    assert "rollback_failed" in (upd.HOME / "logs" / "audit.log").read_text()


def test_unsigned_image_is_refused(upd, monkeypatch):
    _mock_flow(upd, monkeypatch)
    monkeypatch.setattr(upd, "local_digests", lambda ref: {"sha256:tampered"})
    upd.do_update("v1.11.0")
    st = upd.read_state()
    assert st["phase"] == "rolled_back" and "not the signed one" in st["error"]


def test_restart_mid_update_rolls_back(upd, monkeypatch):
    calls = _mock_flow(upd, monkeypatch)
    monkeypatch.setattr(upd, "repair_temp_named", lambda: None)
    upd.save_previous()
    upd.set_env_value("KTSB_VERSION", "v1.11.0")
    upd.write_state(phase="start", from_version="v1.10.0", target="v1.11.0")
    upd.resume_after_restart()
    assert upd.read_state()["phase"] == "rolled_back"
    assert upd.current_version() == "v1.10.0"
    assert "start" in calls


def test_restart_after_self_update_confirms_done(upd, monkeypatch):
    monkeypatch.setattr(upd, "repair_temp_named", lambda: None)
    upd.write_state(phase="self_update", from_version="v1.10.0", target="v1.11.0")
    upd.resume_after_restart()
    assert upd.read_state()["phase"] == "done"


def test_stale_night_lock_file_does_not_block_but_a_held_one_does(upd):
    lock = upd.NIGHT_RUN_LOCK
    lock.write_text('{"pid": 1}')  # left on disk by last night's run
    assert upd.night_sequence_running() is False
    with open(lock, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert upd.night_sequence_running() is True
    assert upd.night_sequence_running() is False
    upd.MEETING_LOCK.write_text("")
    assert upd.night_sequence_running() is True


# ── socket commands ────────────────────────────────────────────────────────────


def test_requests_without_the_token_are_refused(upd):
    assert upd.handle({"cmd": "status"}) == {"ok": False, "error": "unauthorized"}
    assert upd.handle({"cmd": "status", "token": "wrong"})["error"] == "unauthorized"
    (upd.TOKEN_PATH).write_text("")
    assert upd.handle({"cmd": "status", "token": ""})["error"] == "unauthorized"  # no token configured → closed


def test_update_command_validates_and_never_takes_an_image(upd, monkeypatch):
    started = []
    monkeypatch.setattr(upd, "_start_job", lambda fn, *a: started.append(a) or True)
    assert upd.handle({"cmd": "update", "token": TOKEN, "version": "ghcr.io/evil/x:1"})["error"] == "invalid version"
    assert upd.handle({"cmd": "update", "token": TOKEN, "version": "v1.11.0", "image": "evil"})["ok"]
    assert started == [("v1.11.0",)]
    assert upd.handle({"cmd": "nope", "token": TOKEN})["error"] == "unknown command"


def test_status_and_rollback_availability(upd, monkeypatch):
    monkeypatch.setattr(upd, "install_problem", lambda: "")
    st = upd.handle({"cmd": "status", "token": TOKEN})
    assert st["ok"] and st["current"] == "v1.10.0" and st["can_rollback"] is False
    assert upd.handle({"cmd": "rollback", "token": TOKEN})["error"] == "nothing to roll back to"


def test_check_compares_with_running_version(upd, monkeypatch):
    rel = {"version": "v1.11.0", "published": "", "notes": "n", "url": "u", "assets": {}}
    monkeypatch.setattr(upd, "list_releases", lambda: [rel])
    assert upd.check(force=True)["available"] is True
    upd.set_env_value("KTSB_VERSION", "v1.11.0")
    assert upd.check(force=True)["available"] is False


def test_unconfigured_install_says_why(upd, monkeypatch):
    monkeypatch.delenv("KTSB_HOME")
    assert upd.install_problem() == "no_home"
    assert "setup" in upd._PROBLEMS["no_home"]


# ── API side ───────────────────────────────────────────────────────────────────


def test_alive_requires_service_token():
    app = FastAPI()
    app.include_router(router_update.router)
    client = TestClient(app)
    assert client.get("/api/update/alive").status_code == 401
    assert client.get("/api/update/alive", headers={"X-Kaare-Service-Token": "wrong"}).status_code == 401


def test_admin_endpoints_require_admin():
    app = FastAPI()
    app.include_router(router_update.router)
    client = TestClient(app)
    assert client.get("/api/update/status").status_code in (401, 403)
    assert client.post("/api/update/start", json={"version": "v1.11.0"}).status_code in (401, 403)
    assert client.post("/api/update/rollback").status_code in (401, 403)
