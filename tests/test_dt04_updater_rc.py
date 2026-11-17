"""DT04 R0.7 — the updater understands release candidates and can be told to offer them (opt-in, off by default).

- `vX.Y.Z-rcN` parses, and orders: rc1 < rc2 < the final release < the next patch's rc.
- By default only final releases are offered (GitHub `prerelease` and rc tags are skipped) — a stranger never sees a
  test version. With Settings → Maintenance «Include test versions» (settings.yaml `updates.include_test_versions`, Expert)
  the updater also lists signed prereleases. The updater reads the setting itself from the install's configs — it does
  not take it from a request.
- An install running an rc sees the final release without the setting.
"""

import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kaare_core.routers import router_update
from kaare_core.users.auth import require_admin
from tests.test_p57_updater import upd  # noqa: F401 — fixture


def _rel(tag, prerelease=False):
    names = (f"ktsb-docker-{tag}.tar.gz", f"ktsb-docker-{tag}.tar.gz.sigstore.json", "SHA256SUMS")
    return {"tag_name": tag, "prerelease": prerelease, "draft": False, "published_at": "", "body": "", "html_url": "",
            "assets": [{"name": n, "browser_download_url": f"https://x/{n}"} for n in names]}


@pytest.fixture
def gh(upd, monkeypatch):  # noqa: F811
    releases = [_rel("v1.11.1-rc1", prerelease=True), _rel("v1.11.0"), _rel("v1.11.0-rc2", prerelease=True),
                _rel("v1.10.3")]
    monkeypatch.setattr(upd, "http_get", lambda url, accept="": json.dumps(releases).encode())
    return upd


def _include_test(mod, on: bool):
    (mod.HOME / "configs" / "settings.yaml").write_text(yaml.safe_dump({"updates": {"include_test_versions": on}}))


# ── versions ───────────────────────────────────────────────────────────────────

def test_rc_tags_parse_and_order(upd):  # noqa: F811
    p = upd.parse_version
    assert p("v1.11.0-rc1") and p("v1.11.0-rc.2")
    assert p("v1.11.0-rc1") < p("v1.11.0-rc2") < p("v1.11.0") < p("v1.11.1-rc1") < p("v1.11.1")
    assert p("v1.10.10") > p("v1.10.9")
    for bad in ("v1.11.0-rc", "v1.11.0-beta1", "v1.11.0-rc1; rm -rf /", "1.11.0-rc1", "v1.11.0-RC1x"):
        assert p(bad) is None


# ── what is offered ────────────────────────────────────────────────────────────

def test_by_default_only_final_releases_are_listed(gh):
    assert [r["version"] for r in gh.list_releases()] == ["v1.11.0", "v1.10.3"]


def test_with_the_setting_signed_prereleases_are_listed_newest_first(gh):
    _include_test(gh, True)
    assert [r["version"] for r in gh.list_releases()] == ["v1.11.1-rc1", "v1.11.0", "v1.11.0-rc2", "v1.10.3"]


def test_a_stable_install_is_offered_the_next_rc_only_with_the_setting(gh):
    gh.set_env_value("KTSB_VERSION", "v1.11.0")
    assert gh.check(force=True)["available"] is False
    _include_test(gh, True)
    res = gh.check(force=True)
    assert res["available"] is True and res["latest"]["version"] == "v1.11.1-rc1"


def test_an_rc_install_is_offered_the_final_release_without_the_setting(gh):
    gh.set_env_value("KTSB_VERSION", "v1.11.0-rc2")
    assert gh.current_version() == "v1.11.0-rc2"
    res = gh.check(force=True)
    assert res["available"] is True and res["latest"]["version"] == "v1.11.0"


def test_turning_the_setting_on_is_seen_without_waiting_for_the_check_cache(gh):
    gh.set_env_value("KTSB_VERSION", "v1.11.0")
    assert gh.check()["available"] is False  # cached now
    _include_test(gh, True)
    assert gh.check()["available"] is True


def test_preflight_refuses_an_rc_while_the_setting_is_off(gh):
    with pytest.raises(gh.UpdateError):
        gh.preflight("v1.11.1-rc1")


def test_the_update_command_accepts_an_rc_tag(upd, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(upd, "_start_job", lambda fn, *a: started.append(a) or True)
    assert upd.handle({"cmd": "update", "token": "test-service-token", "version": "v1.11.1-rc1"})["ok"]
    assert started == [("v1.11.1-rc1",)]


# ── the GUI setting (API side) ──────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"language": "nb"}))
    monkeypatch.setattr(router_update, "_SETTINGS_PATH", path)
    app = FastAPI()
    app.include_router(router_update.router)
    app.dependency_overrides[require_admin] = lambda: {"sub": "admin_x", "role": "admin"}
    return TestClient(app), path


def test_setting_is_off_by_default_and_saved_from_the_gui(api):
    client, path = api
    assert client.get("/api/update/settings").json() == {"include_test_versions": False}
    assert client.put("/api/update/settings", json={"include_test_versions": True}).status_code == 200
    data = yaml.safe_load(path.read_text())
    assert data["updates"]["include_test_versions"] is True and data["language"] == "nb"
    assert client.get("/api/update/settings").json() == {"include_test_versions": True}


def test_setting_endpoints_require_admin():
    app = FastAPI()
    app.include_router(router_update.router)
    client = TestClient(app)
    assert client.get("/api/update/settings").status_code in (401, 403)
    assert client.put("/api/update/settings", json={"include_test_versions": True}).status_code in (401, 403)


def test_template_ships_the_setting_off():
    tpl = yaml.safe_load(Path("/kaare/configs_default/settings.yaml").read_text(encoding="utf-8"))
    assert tpl["updates"]["include_test_versions"] is False
