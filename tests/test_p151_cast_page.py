"""P151 phase 2: KTSB pages on Cast screens (DashCast) and putting the screen back the way it was."""

import sys
import types

import pytest

from kaare_core.voice import cast_sender as cs


class FakeCast:
    def __init__(self, script):
        self.script = script
        self.calls: list = []
        self.socket_client = types.SimpleNamespace(is_connected=True)
        self.media_controller = types.SimpleNamespace(status=None)
        self._advance()

    def _advance(self):
        snap = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        self.status = types.SimpleNamespace(
            is_stand_by=snap.get("standby"), is_active_input=None, display_name=snap.get("app"),
            app_id=snap.get("app_id"), volume_level=0.1, volume_muted=False)
        self.media_controller.status = types.SimpleNamespace(player_state=None, title=None, content_id=None)

    def wait(self, timeout=None):
        pass

    def quit_app(self):
        self.calls.append(("quit",))

    def start_app(self, app_id, force_launch=False):
        self.calls.append(("start_app", app_id))

    def register_handler(self, h):
        pass


@pytest.fixture
def fake(monkeypatch):
    loads: list = []

    class Dash:
        def load_url(self, url, force=False, reload_seconds=0, callback_function=None):
            loads.append((url, force))

    mod = types.ModuleType("pychromecast.controllers.dashcast")
    mod.DashCastController = Dash
    monkeypatch.setitem(sys.modules, "pychromecast.controllers.dashcast", mod)

    def make(script):
        cast = FakeCast(script)
        monkeypatch.setattr(cs, "_casts", {"10.0.0.4": cast})
        monkeypatch.setattr(cs, "_POLL_S", 0.01)
        monkeypatch.setattr(cs, "WAIT_PICTURE_S", 0.2)
        orig = cs._snapshot

        def snapshot(c):
            out = orig(c)
            c._advance()
            return out

        monkeypatch.setattr(cs, "_snapshot", snapshot)
        return cast, loads

    return make


def test_show_url_http_page_quits_first_and_forces(fake):
    """BUG 2026-11-16 (P139 ## Bugs, T34 recipe test): an http:// page in DashCast's https frame is blocked silently.
    An http page must go top-level (force) — and DashCast must be started fresh, since a forced page replaces
    DashCast's own receiver and a second load_url would be ignored."""
    cast, loads = fake([{"app": "DashCast", "app_id": "84912283"}, {"app": "DashCast", "app_id": "84912283"}])
    out = cs.show_url("10.0.0.4", "http://192.168.1.2:8011/screen/tv?s=abc")
    assert out["ok"]
    assert cast.calls[0] == ("quit",)
    assert loads == [("http://192.168.1.2:8011/screen/tv?s=abc", True)]


def test_show_url_https_page_keeps_the_frame(fake):
    cast, loads = fake([{"app": None}, {"app": "DashCast", "app_id": "84912283"}])
    assert cs.show_url("10.0.0.4", "https://example.com")["ok"]
    assert loads == [("https://example.com", False)] and ("quit",) not in cast.calls


def test_restore_standby_screen_asks_for_standby(fake):
    cast, _ = fake([{"app": "DashCast", "app_id": "84912283"}, {"app": None}])
    out = cs.restore("10.0.0.4", {"standby": True, "app_id": None})
    assert ("quit",) in cast.calls and out["needs_standby"] is True


def test_restore_relaunches_the_app_that_ran_before(fake):
    cast, _ = fake([{"app": "DashCast", "app_id": "84912283"}, {"app": None}, {"app": "Plex", "app_id": "9AC194DC"}])
    out = cs.restore("10.0.0.4", {"standby": False, "app_id": "9AC194DC", "app": "Plex"})
    assert cast.calls[:2] == [("quit",), ("start_app", "9AC194DC")]
    assert out["needs_standby"] is False and out["relaunched"] == "Plex"


def test_restore_idle_screen_just_quits(fake):
    cast, _ = fake([{"app": "DashCast", "app_id": "84912283"}, {"app": "Backdrop", "app_id": "E8C28D3C"}])
    out = cs.restore("10.0.0.4", {"standby": False, "app_id": "E8C28D3C", "app": "Backdrop"})
    assert cast.calls == [("quit",)] and out["needs_standby"] is False and not out.get("relaunched")


def test_restore_leaves_a_screen_someone_else_took_over(fake):
    """If the user started something else meanwhile, KTSB's timer must not close it."""
    cast, _ = fake([{"app": "YouTube", "app_id": "233637DE"}])
    out = cs.restore("10.0.0.4", {"standby": True, "app_id": None})
    assert cast.calls == [] and out["skipped"]
