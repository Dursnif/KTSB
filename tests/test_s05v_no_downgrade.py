"""DT04-S05v — the update card offered «Update KTSB to v1.11.0» on an install running v1.11.1 (a downgrade).

The updater read KTSB_VERSION from .env; a value it cannot parse (a test tag, an empty value with images on :latest)
made `current` "" and every release counted as newer. Now the API passes the version its own image runs, and the
updater uses it when .env says nothing usable. If neither is known, nothing is offered and the answer says so.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from kaare_core.routers import router_update

UPDATER_PATH = Path(__file__).resolve().parent.parent / "services" / "updater" / "updater.py"
TOKEN = "test-service-token"
RELEASES = [{"version": "v1.11.0", "published": "x", "notes": "", "url": "u"},
            {"version": "v1.10.3", "published": "x", "notes": "", "url": "u"}]


def _upd(tmp_path, monkeypatch, env_version):
    home = tmp_path / "ktsb"
    (home / "configs").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "logs").mkdir()
    (home / ".env").write_text(f"KTSB_VERSION={env_version}\n")
    (home / "configs" / "service_token.env").write_text(f"KAARE_SERVICE_TOKEN={TOKEN}\n")
    monkeypatch.setenv("KTSB_HOME", str(home))
    spec = importlib.util.spec_from_file_location(f"ktsb_updater_s05v_{env_version or 'empty'}", UPDATER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "list_releases", lambda: list(RELEASES))
    return mod


@pytest.mark.parametrize("env_version", ["dt04-20261117g", ""])
def test_running_image_version_is_used_when_env_is_unreadable(tmp_path, monkeypatch, env_version):
    upd = _upd(tmp_path, monkeypatch, env_version)
    r = upd.handle({"cmd": "check", "token": TOKEN, "force": True, "running": "v1.11.1"})
    assert r["ok"] and r["current"] == "v1.11.1"
    assert r["available"] is False  # v1.11.0 is older — never offered


def test_unknown_running_version_offers_nothing_and_says_so(tmp_path, monkeypatch):
    upd = _upd(tmp_path, monkeypatch, "dt04-20261117g")
    r = upd.handle({"cmd": "check", "token": TOKEN, "force": True, "running": "dev"})
    assert r["ok"] and r["available"] is False and r.get("current_unknown") is True


def test_a_readable_env_still_decides(tmp_path, monkeypatch):
    upd = _upd(tmp_path, monkeypatch, "v1.10.3")
    r = upd.handle({"cmd": "check", "token": TOKEN, "force": True, "running": "v1.10.3"})
    assert r["current"] == "v1.10.3" and r["available"] is True and r["latest"]["version"] == "v1.11.0"


def test_the_api_passes_its_running_version(monkeypatch):
    sent = {}

    async def fake_ask(cmd, **fields):
        sent.update(cmd=cmd, **fields)
        return {"ok": True}

    monkeypatch.setattr(router_update, "_ask_updater", fake_ask)
    monkeypatch.setattr(router_update, "get_version", lambda: "v1.11.1")
    asyncio.run(router_update.api_update_check(router_update.CheckRequest(force=True), _u={}))
    assert sent == {"cmd": "check", "force": True, "running": "v1.11.1"}


def test_the_card_says_when_the_version_is_unknown():

    root = Path(__file__).resolve().parent.parent / "frontend" / "src"
    src = (root / "pages" / "admin" / "System.tsx").read_text(encoding="utf-8")
    assert "check.current_unknown" in src and "!check.current_unknown" in src  # never «up to date» when unknown
    for lang in ("nb", "en", "de"):
        assert json.loads((root / "locales" / f"{lang}.json").read_text(encoding="utf-8"))["system"]["update"]["current_unknown"]
