"""Which nodes a spoken announcement goes to (DT04-S05f).

Seen: target «tv-a» for node `tv_a` («TV A») was not found, the bridge silently fell back to the
local speaker, nothing played (no aplay/mpc in Docker), and Kåre said «it was played». The resolver is a plain module so
it can be tested — the bridge itself loads speech models and cannot be imported in pytest.
"""

import ast
import asyncio
from pathlib import Path

import pytest

from kaare_core.voice import speak_target as st

NODES = {
    "tv_a": {"description": "TV A", "room": "garage", "has_display": True},
    "kjokken_hoyttaler": {"description": "Kjøkkenhøyttaler", "room": "kjøkken"},
    "gammel_node": {"description": "Gammel", "enabled": False},
    "bedroom_kid": {"room": "bedroom kid"},
}


def _resolve(target, nodes=NODES, **kw):
    return st.resolve(target, nodes, **kw)


@pytest.mark.parametrize("target", ["tv-a", "tv_a", "TV A", "TV-A", "tv a"])
def test_hyphen_underscore_space_and_case_match_the_node(target):
    assert _resolve(target) == (["tv_a"], "")


def test_display_name_matches():
    assert _resolve("Kjøkkenhøyttaler") == (["kjokken_hoyttaler"], "")


def test_unknown_target_never_falls_back_to_local():
    targets, reason = _resolve("garasjen-som-ikke-finnes")
    assert targets == [] and reason == st.UNKNOWN


def test_disabled_node_is_said_not_local():
    assert _resolve("gammel_node") == ([], st.DISABLED)


def test_local_and_all_still_work():
    assert _resolve("local") == (["local"], "")
    assert _resolve("")[0] == ["local"]
    targets, _ = _resolve("all")
    assert targets[0] == "local" and "gammel_node" not in targets


def test_room_and_substring_still_work():
    assert _resolve("garage") == (["tv_a"], "")
    assert _resolve("kid") == (["bedroom_kid"], "")


def test_known_targets_lists_enabled_nodes_with_names():
    known = st.known_targets(NODES)
    assert "tv_a (TV A)" in known and not any("gammel" in k for k in known)


# ── receipt after the outcome: did playback start? ───────────────────────────────────────────────────────────────


def test_confirm_start_reports_per_target():
    async def ok():
        return True

    async def refused():
        return False

    async def boom():
        raise FileNotFoundError("aplay")

    async def still_playing():
        await asyncio.sleep(5)
        return True

    async def run():
        outcome, pending = await st.confirm_start(
            {"a": ok(), "b": refused(), "c": boom(), "d": still_playing()}, window=0.05, remote_window=0.05
        )
        for p in pending:
            p.cancel()
        return outcome

    out = asyncio.run(run())
    assert out == {"a": st.PLAYED, "b": st.FAILED, "c": st.FAILED, "d": st.PLAYING}


def test_summary_is_honest():
    assert st.summary({"a": st.PLAYED, "b": st.PLAYING}) == "ok"
    assert st.summary({"a": st.PLAYED, "b": st.FAILED}) == "partial"
    assert st.summary({"a": st.FAILED}) == "failed"
    assert st.summary({}) == "failed"


# ── the bridge itself (cannot be imported in tests — it loads speech models — so its source is checked) ──────────

BRIDGE = Path(__file__).resolve().parents[1] / "services/voice/voice_bridge.py"


def _bridge_fn(name):
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_bridge_never_falls_back_to_local_silently():
    src = BRIDGE.read_text(encoding="utf-8")
    assert "falling back to local" not in src
    assert "speak_target.resolve(" in ast.unparse(_bridge_fn("_resolve_targets"))


def test_bridge_speak_does_not_use_the_i18n_t_as_a_loop_variable():
    # `t` is the i18n function in the bridge; a loop that still compared `t == "local"` would never play locally.
    names = {n.id for n in ast.walk(_bridge_fn("_speak_background")) if isinstance(n, ast.Name)}
    assert "t" not in names


def test_bridge_speak_endpoint_waits_and_reports_unknown_targets():
    src = ast.unparse(_bridge_fn("speak_endpoint"))
    assert "status_code=404" in src and "known_targets" in src and "request.wait" in src


def test_bridge_mpc_and_aplay_missing_do_not_crash():
    assert "FileNotFoundError" in ast.unparse(_bridge_fn("_mpc"))
    assert "FileNotFoundError" in ast.unparse(_bridge_fn("play_wav"))


def test_slow_remote_failure_is_still_caught():
    # A Cast speech failed only after 9.4 s in the house log (2026-11-15) — a fixed 3 s window would have said «playing».
    async def slow_fail():
        await asyncio.sleep(0.1)
        return False

    async def local_long_clip():
        await asyncio.sleep(5)
        return True

    async def run():
        outcome, pending = await st.confirm_start(
            {"tv_b": slow_fail(), "local": local_long_clip()}, window=0.03, remote_window=0.5
        )
        for p in pending:
            p.cancel()
        return outcome

    assert asyncio.run(run()) == {"tv_b": st.FAILED, "local": st.PLAYING}
