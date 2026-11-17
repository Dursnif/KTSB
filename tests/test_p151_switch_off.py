"""P151 D11: with `screens.enabled: false` (the shipped template value) the whole screen feature is inert — every
/screen route answers «off» / 404, no background job starts, and Kåre has no screen tool (main session kaare-d7,
2026-11-16: the bridge file ships with other plans' fixes, so the switch must really cover everything).

The bridge itself is not imported in tests (it loads speech models), so the routes are checked in its source."""

import ast
from pathlib import Path

import yaml

from kaare_core.tools import definitions as d
from kaare_core.voice import screen as sc

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "services/voice/voice_bridge.py"
# Allowed while off, with the reason: they only end/restore or report state — they never show anything.
ALLOWED_OFF = {"/screen/stop", "/screen/status", "/screen/active"}


def _screen_routes(tree: ast.Module) -> dict[str, ast.AsyncFunctionDef]:
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for dec in fn.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.args
                    and isinstance(dec.args[0], ast.Constant) and str(dec.args[0].value).startswith("/screen")):
                out[str(dec.args[0].value)] = fn
    return out


def _calls_enabled(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "enabled"
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "screen_core" for n in ast.walk(fn))


def gaps(source: str) -> list[str]:
    """Everything in the bridge source that would still act while the switch is off."""
    tree = ast.parse(source)
    found = [f"route {p} does not check the switch" for p, fn in sorted(_screen_routes(tree).items())
             if p not in ALLOWED_OFF and not _calls_enabled(fn)]
    found.extend(  # no screen job may start with the bridge
        f"startup hook {fn.name} starts a screen job" for fn in ast.walk(tree)
        if isinstance(fn, ast.AsyncFunctionDef) and "screen" in ast.unparse(fn)
        and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "on_event" for d in fn.decorator_list))
    return found


def test_every_screen_route_checks_the_switch_and_nothing_starts_at_boot():
    assert gaps(BRIDGE.read_text(encoding="utf-8")) == []


def test_gap_finder_sees_a_route_without_the_switch():
    """The checker itself must be able to go red (rule 28)."""
    bad = '@app.get("/screen/x")\nasync def f():\n    return 1\n@app.on_event("startup")\nasync def s():\n    spawn(_screen_expiry_loop())\n'
    assert gaps(bad) == ["route /screen/x does not check the switch", "startup hook s starts a screen job"]


def test_template_ships_the_feature_on():
    """Changed 2026-11-16 (s04, said to the owner): was «the template ships it off» while P151 was unfinished. Owner: P151
    is on for everyone in v1.11.0. The off-state tests in this file stay — they prove that a user who switches it OFF
    gets a fully inert feature."""
    tpl = yaml.safe_load((ROOT / "configs_default/settings.yaml").read_text(encoding="utf-8"))
    assert tpl["screens"]["enabled"] is True
    assert sc.enabled(tpl["screens"]) is True


def test_kare_does_not_see_live_actions_while_off(monkeypatch):
    """Changed 2026-11-16 (phase 4, said to the owner): was «no tool mentions the screen core» — phase 4 gives Kåre the
    actions on purpose. What must hold (kaare-d7): switched off, Kåre's camera tool has no live/live_stop/minutes."""
    monkeypatch.setattr(sc, "enabled", lambda settings=None: False)
    fn = d._kamera_tool("nb")["function"]
    props = fn["parameters"]["properties"]
    assert "live" not in props["action"]["enum"] and "live_stop" not in props["action"]["enum"]
    assert "minutes" not in props and "live" not in fn["description"].split("action='")[-1][:4]
    monkeypatch.setattr(sc, "enabled", lambda settings=None: True)
    on = d._kamera_tool("nb")["function"]["parameters"]["properties"]
    assert {"live", "live_stop"} <= set(on["action"]["enum"]) and "minutes" in on
