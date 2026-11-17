"""I64 preparation (owner decision 2026-11-16): one seam every volume choice goes through —
`kaare_core.voice.volume.target_volume(node_id, node_cfg, requested, purpose)` — so automatic volume (per-node mode,
floor/ceiling, room sensors) can later live in one place without touching the call sites.

Today it changes nothing: every purpose returns `requested`. The owner's rule (I64 option A): Kåre may
always follow the user's own wish — a volume the user asked for (`user_request`) always goes through unchanged, also once I64's system
policy exists; only system choices (`announce` without a wish, `duck`) may ever be adjusted by it.
Call sites: `ha_control set_volume` (user_request), bridge `/speak` (announce), `_new_duck` (duck). The bridge is not
imported in tests (it loads speech models), so its call sites are checked in its source."""

import ast
import asyncio
from pathlib import Path

import pytest

from kaare_core.tools import executor_ha as eh
from kaare_core.voice import volume as vol

BRIDGE = Path(__file__).resolve().parents[1] / "services/voice/voice_bridge.py"
NODE = {"type": "ha_media_player", "entity_id": "media_player.example"}


@pytest.mark.parametrize("purpose", [vol.PURPOSE_USER_REQUEST, vol.PURPOSE_ANNOUNCE, vol.PURPOSE_DUCK])
@pytest.mark.parametrize("level", [0.0, 0.05, 0.33, 0.5, 1.0])
def test_today_every_purpose_returns_the_requested_level(purpose, level):
    assert vol.target_volume("n", NODE, level, purpose) == level


def test_nothing_requested_stays_nothing():
    assert vol.target_volume("n", NODE, None, vol.PURPOSE_ANNOUNCE) is None


def test_unknown_purpose_is_refused():
    with pytest.raises(ValueError):
        vol.target_volume("n", NODE, 0.3, "loud_please")


def test_a_future_system_policy_never_overrides_the_users_wish(monkeypatch):
    monkeypatch.setattr(vol, "_system_policy", lambda node_id, node_cfg, requested, purpose: 0.1)
    assert vol.target_volume("n", NODE, 0.4, vol.PURPOSE_USER_REQUEST) == 0.4
    assert vol.target_volume("n", NODE, 0.4, vol.PURPOSE_ANNOUNCE) == 0.1
    assert vol.target_volume("n", NODE, 0.4, vol.PURPOSE_DUCK) == 0.1


def test_ha_control_set_volume_goes_through_the_seam_as_a_user_request(monkeypatch):
    seen = []

    def spy(node_id, node_cfg, requested, purpose):
        seen.append((requested, purpose))
        return requested

    captured = {}

    async def fake_location(loc, action, params, lang):
        captured.update(params)
        return "ok"

    monkeypatch.setattr(vol, "target_volume", spy)
    monkeypatch.setattr(eh, "_control_location", fake_location)
    asyncio.run(eh._control_entity(None, "set_volume", lang="nb", location={"room": "x"}, volume_pct=35))
    assert seen == [(0.35, vol.PURPOSE_USER_REQUEST)]
    assert captured["volume_pct"] == 35


def _calls_seam_with(fn: ast.AST, purpose_attr: str) -> bool:
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "target_volume"
                and any(isinstance(a, ast.Attribute) and a.attr == purpose_attr for a in [*node.args,
                        *(k.value for k in node.keywords)])):
            return True
    return False


def _bridge_fn(name: str) -> ast.AST:
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name)


def test_bridge_speak_volume_goes_through_the_seam_as_announce():
    assert _calls_seam_with(_bridge_fn("_speak_background"), "PURPOSE_ANNOUNCE")


def test_bridge_duck_level_goes_through_the_seam_as_duck():
    assert _calls_seam_with(_bridge_fn("_new_duck"), "PURPOSE_DUCK")
