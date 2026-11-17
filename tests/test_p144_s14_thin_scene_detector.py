"""P144-s14 (main session / 2026-10-28): «scene too short / placeholder» is a Model Enforcer detector
(`frigate_thin_scene` in the P142 `frigate` profile), not an ad-hoc check in conclude.py.

- fires only on the scene call (`meta["kind"] == "scene"`), when the `scene` field is under 3 words or a placeholder;
- counted in enforcement.log (shadow by the profile), listed in the Model Enforcer catalog (GUI off/shadow/on);
- its result drives conclude's code text («ingen person eller dyr …, from the vision model's fields»);
- in mode «on» the scene gets one correction round (like the pipe's L3).
The live enforcement.log is never touched (conftest points it at tmp_path).
"""

import asyncio
import json

import pytest

from kaare_core import model_enforcement as me
from kaare_core.frigate_user import conclude, frames, route
from kaare_core.frigate_user import store as fstore

SCENE = {"persons": 0, "people": [], "animals": [], "vehicles": "unclear", "at_door": "unclear"}


def _check(text, kind="scene"):
    enf = me.ModelEnforcer.for_profile("frigate", "nb", rid="t")
    enf.check(text_out=text, used_tools=[], meta={"kind": kind} if kind else {})
    return enf.hits


@pytest.mark.parametrize("scene_text, fires", [
    ("natt", True),
    ("night", True),
    ("...", True),
    ("unclear", True),
    ("en nattlig innkjørsel med lysstriper", False),
])
def test_detector_judges_the_scene_field(scene_text, fires):
    assert ("frigate_thin_scene" in _check(json.dumps({**SCENE, "scene": scene_text}))) is fires


def test_detector_only_on_the_scene_call():
    assert "frigate_thin_scene" not in _check(json.dumps({**SCENE, "scene": "natt"}), kind=None)


def test_detector_is_in_the_frigate_profile_and_the_catalog():
    row = next(r for r in me.detector_catalog() if r["name"] == "frigate_thin_scene")
    assert row["profiles"] == [{"profile": "frigate", "default_mode": me.MODE_SHADOW}]


def test_shadow_hit_is_counted_in_the_enforcement_log(monkeypatch):
    logged = []
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: logged.append(a))
    _check(json.dumps({**SCENE, "scene": "natt"}))
    assert ("t", "frigate_thin_scene", "shadow", "natt") in logged


# ── conclude uses the detector result ───────────────────────────────────────────


def _scene_env(monkeypatch, tmp_path, answers):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate.db")
    fstore.reset_for_tests()
    monkeypatch.setattr(conclude, "_camera_cards", dict)
    monkeypatch.setattr(fstore, "save_episode_frames", lambda *a, **k: 0)

    async def one_frame(picked):
        return [frames.Frame(ts=1.0, camera="east", jpeg=b"\xff\xd8x")]

    calls = []

    async def llm(role, prompt, images=None, rid=""):
        if (":look" in rid):  # s16: the gate's closed look comes first; it saw an animal → description runs
            return {"text": json.dumps({"person": False, "animal": "rådyr", "vehicle": False, "nothing": False})}
        calls.append(prompt)
        return {"text": json.dumps({**SCENE, "scene": answers[min(len(calls), len(answers)) - 1]})}

    monkeypatch.setattr(frames, "fetch_frames", one_frame)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(route, "get_local_tz", conclude.get_local_tz)
    ep = fstore.Episode(episode_id=1, start_ts=1.0, end_ts=15.0, cameras=["east"], labels=["deer"], person_refs=[],
                        event_count=1, status="closed", conclusion=None, concluded_ts=None)
    ev = fstore.FrigateUserEvent(event_id="d1", camera="east", label="deer", start_ts=1.0, end_ts=11.0,
                                 path=[[0.2, 0.5, 1.0], [0.6, 0.5, 5.0]])
    return calls, ep, ev


def test_thin_scene_is_marked_from_the_detector(monkeypatch, tmp_path):
    calls, ep, ev = _scene_env(monkeypatch, tmp_path, ["natt"])
    scene, _ = asyncio.run(conclude._scene_step(ep, [ev], "nb"))
    assert scene["scene_thin"] is True and len(calls) == 1  # shadow: measured, no correction round
    fstore.reset_for_tests()


def test_detector_off_means_no_code_text(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"frigate:frigate_thin_scene": me.MODE_OFF})
    _calls, ep, ev = _scene_env(monkeypatch, tmp_path, ["natt"])
    scene, _ = asyncio.run(conclude._scene_step(ep, [ev], "nb"))
    assert not scene.get("scene_thin")
    fstore.reset_for_tests()


def test_mode_on_gives_one_correction_round(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"frigate:frigate_thin_scene": me.MODE_ON})
    calls, ep, ev = _scene_env(monkeypatch, tmp_path, ["natt", "en nattlig innkjørsel med lysstriper på linsen"])
    scene, _ = asyncio.run(conclude._scene_step(ep, [ev], "nb"))
    assert len(calls) == 2 and me._t_i18n("frigate_user_correction_thin_scene", "nb") in calls[1]
    assert scene["scene"].startswith("en nattlig") and not scene.get("scene_thin")
    fstore.reset_for_tests()


def test_code_text_is_marked_as_from_the_vision_models_closed_answer():
    # s16: the false-detection line is built from the closed look (B), not from the description — thin or not
    look = {"status": "done", "person": False, "animal": None, "vehicle": True, "nothing": False}
    assert conclude._seen_text(look, "nb") == "ingen person eller dyr, bare kjøretøy (fra bildemodellens lukkede svar)"
    assert conclude._seen_text({**look, "vehicle": False}, "nb") == \
        "ingen person eller dyr (fra bildemodellens lukkede svar)"


@pytest.fixture(autouse=True)
def _one_call_scene(monkeypatch):
    """P144-s25: these tests cover the one-call scene, which is now the fallback when the frame-by-frame run fails
    (tests/test_p144_s25_frames_step.py covers the frame run). Their fake models do not answer frame calls."""
    monkeypatch.setattr(conclude, "FRAMES_ON", False)
