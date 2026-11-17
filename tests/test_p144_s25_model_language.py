"""P144-s25 language task — the 9B's frame-by-frame calls run in English whatever the GUI language (a narrow, measured
exception to CLAUDE.md rule 8: model-to-model data only). Measured 2026-10-31 (s25 log): frames in English = same
content scores, language consistency 100 % vs 34 %, ~11 % faster. The L3 and the closed look (the gate) measured WORSE
in English (look: 5/27 unreadable prose answers, 10 vs 2 invented animals, 2× slower; L3 headwear 15/12 vs 22/5) — they
stay in the household language, and so does the untested one-call scene fallback. Code turns fixed values (posture,
door state) into the GUI language; the 27B still writes in the GUI language. Fakes only.
"""

import asyncio
import json

from kaare_core import model_enforcement as me
from kaare_core.frigate_user import conclude, episode_state, pipe
from kaare_core.tools.i18n import t as _t_i18n
from tests.test_p144_s25_frames_step import CAMS, _frame_calls, _scene, env  # noqa: F401

FULL_EN = {"persons": [{"age": "adult", "gender": "woman", "clothing": "maroon hoodie", "place": "by the door",
                        "posture": "standing", "carried": ""}], "doors": [{"door": "front door", "state": "open"}]}
SIT_EN = {"known": [{"id": "P1", "in_image": "yes", "posture": "sitting", "place": "in the chair"}],
          "new_persons": [], "doors": [], "correction": ""}


def test_the_model_language_is_english():
    assert episode_state.MODEL_LANG == "en"


def test_frame_prompts_are_english_with_a_norwegian_gui(env):  # noqa: F811 — the fixture is imported from test_p144_s25_frames_step
    _scene(env)  # the fixture runs the scene step with lang «nb»
    calls = _frame_calls(env)
    assert calls[0][1].startswith("This is one image from my camera")
    assert "Text values in English" in calls[0][1]
    assert "This is known already" in calls[1][1] and "Persons:" in calls[1][1]
    assert not any("Dette er" in c[1] or "norsk" in c[1] for c in calls)


def test_the_look_prompt_stays_in_the_household_language(env):  # noqa: F811 — the fixture is imported from test_p144_s25_frames_step
    _scene(env)
    look = next(p for p in env["prompts"] if (":look" in p[0]))
    assert "Hva er i bildene" in look[1]  # measured: English made the gate worse


def test_the_timeline_speaks_the_gui_language(env):  # noqa: F811 — the fixture is imported from test_p144_s25_frames_step
    env["answers"].update({1: FULL_EN, 2: SIT_EN})
    _ep, _events, scene = _scene(env)
    assert "står" in scene["timeline"][0] and "sitter" in scene["timeline"][1]  # code's words, nb
    assert "åpen" in scene["timeline"][0]  # the door state translated by code
    assert "by the door" in scene["timeline"][0]  # the 9B's own words stay as written


def test_the_language_detector_expects_english_on_frame_calls(env):  # noqa: F811 — the fixture is imported from test_p144_s25_frames_step
    nb = {"known": [{"id": "P1", "in_image": "yes", "posture": "sitting", "place": "i stolen ved veggen",
                     "carried": "en kopp med kaffe"}], "new_persons": [], "doors": [], "correction": ""}
    env["answers"].update({1: FULL_EN, 2: nb})
    _ep, _events, scene = _scene(env)
    assert scene["frames_run"]["hits"].get("frigate_wrong_language") == 1  # frame 2 only


def test_corrections_to_the_9b_are_english(monkeypatch):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"frigate_frames:frigate_state_carried": me.MODE_ON})
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)
    _hits, action = conclude._enforce_frames("{}", "nb", "t", {"kind": "frame", "state_carried": ["P1"]})
    assert action is not None and action.correction_message.startswith("The camera system sees no person")
    assert _t_i18n("frigate_honesty", "en") in action.correction_message


def test_l3_prompt_stays_in_the_household_language(monkeypatch):
    seen = []

    async def llm(role, prompt, images=None, rid=""):
        seen.append(prompt)
        return {"text": json.dumps({"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult",
                                    "headwear": "no", "description": "dark jacket", "analysis": "stands"})}

    monkeypatch.setattr(pipe, "ask_llm_role", llm)
    monkeypatch.setattr(pipe, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)
    asyncio.run(pipe._l3("vest", "person", "aGk=", "e1"))
    assert seen[0].startswith("Se på dette ene kamerabildet")  # measured: English L3 read headwear worse


def test_the_27b_is_told_to_write_in_the_gui_language_over_english_input(env):  # noqa: F811 — the fixture is imported from test_p144_s25_frames_step
    ep, events, scene = _scene(env)
    ep.scene = scene
    prompt = conclude.build_episode_prompt(ep, events, "nb", away=False, cameras=CAMS)
    assert "Bildemodellens egne ord står på engelsk" in prompt and "norsk (bokmål)" in prompt


def test_the_one_call_scene_fallback_stays_as_it_was(env):  # noqa: F811 — the fixture is imported from test_p144_s25_frames_step
    env["answers"][1] = "not json"
    _scene(env)
    scene_call = next(p for p in env["prompts"] if (":scene" in p[0]))
    assert "norsk (bokmål)" in scene_call[1]  # not measured in English → unchanged

