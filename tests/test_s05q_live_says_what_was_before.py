"""DT04-S05q (thread 9 — the code knows, so the text says it): «live» ended with «the screen then goes back to how it was».
With a YouTube fireplace video earlier in the chat history, Kåre filled «how it was» with «the fireplace video carries on
where it was» — but nothing was playing (Cast app null). The bridge already snapshots the screen before KTSB takes it;
now it returns a small summary and the tool text names it. Never a promise that something «continues»."""

import ast
import asyncio
from pathlib import Path

import pytest

from kaare_core.tools import executor_camera as ec
from kaare_core.tools.i18n import t
from kaare_core.voice import cast_sender
from tests.test_p151_camera_live import bridge, run  # noqa: F401  (bridge is a fixture)

BRIDGE = Path("/kaare/services/voice/voice_bridge.py")


# ── cast_sender.before_summary: what the screen showed, from the snapshot ────

@pytest.mark.parametrize("snap,expected", [
    ({"app_id": None, "app": None, "standby": False}, {"kind": "idle", "app": None}),
    ({"app_id": "E8C28D3C", "app": "Backdrop", "standby": False}, {"kind": "idle", "app": None}),
    ({"app_id": None, "app": None, "standby": True}, {"kind": "standby", "app": None}),
    ({"app_id": "233637DE", "app": "YouTube", "standby": False, "state": "PLAYING"}, {"kind": "app", "app": "YouTube"}),
    ({"app_id": "84912283", "app": "DashCast", "standby": False}, {"kind": "idle", "app": None}),  # KTSB's own page
    ({}, {"kind": "unknown", "app": None}),
    (None, {"kind": "unknown", "app": None}),
])
def test_before_summary(snap, expected):
    assert cast_sender.before_summary(snap) == expected


# ── the tool text names the before-state, in every language ─────────────────

def _live(replies, before, lang="en"):
    replies["/screen/camera"] = {"ok": True, "showing": True, "minutes": 2.0, "before": before,
                                 "session": {"mode": "mse", "size": "640x360"}}
    return asyncio.run(ec._kamera({"_user_id": "u", "action": "live", "camera": "door", "screen": "node:tv_a"}, lang))


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
@pytest.mark.parametrize("before,key", [
    ({"kind": "idle", "app": None}, "cam_live_before_idle"),
    ({"kind": "standby", "app": None}, "cam_live_before_standby"),
    ({"kind": "unknown", "app": None}, "cam_live_before_unknown"),
    (None, "cam_live_before_unknown"),  # an older bridge without the field: honest «unknown», never «as it was»
])
def test_live_ok_names_the_before_state(bridge, lang, before, key):  # noqa: F811
    _, replies = bridge
    out = _live(replies, before, lang)
    text = t(key, lang, screen="tv_a")
    assert text != key and text in out, (lang, out)


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_live_ok_names_the_app_and_never_what_it_played(bridge, lang):  # noqa: F811
    _, replies = bridge
    out = _live(replies, {"kind": "app", "app": "YouTube"}, lang)
    text = t("cam_live_before_app", lang, screen="tv_a", app="YouTube")
    assert text != "cam_live_before_app" and text in out and "YouTube" in out


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_live_ok_has_no_vague_as_it_was(bridge, lang):  # noqa: F811
    _, replies = bridge
    out = _live(replies, {"kind": "idle", "app": None}, lang)
    for vague in ("slik den var", "how it was", "vorherigen Zustand"):
        assert vague not in out, (lang, out)


def test_bridge_returns_the_before_summary():
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "screen_camera")
    returns = [r for r in ast.walk(fn) if isinstance(r, ast.Return) and isinstance(r.value, ast.Dict)]
    keysets = [[k.value for k in r.value.keys if isinstance(k, ast.Constant)] for r in returns]
    started = [ks for ks in keysets if "session" in ks]  # the reply after a session was started
    assert started and all("before" in ks for ks in started), keysets
    assert "before_summary" in ast.unparse(fn)
