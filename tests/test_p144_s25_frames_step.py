"""P144-s25 — the scene step runs frame by frame with the memory in code (conclude._frames_step).

An ep 108-shaped episode on one camera: a woman comes out (best first frame, full description), sits, sits on with
Frigate's track stationary (code skips that frame — no call), and is gone in the last frame (Frigate's timeline «gone»
before it) while the 9B copies her forward → code sets her absent and the P142 `frigate_frames` profile logs
`frigate_state_carried` (shadow). Every frame prompt leads with Kåre's honesty block. The 27B gets the code-written
timeline instead of the one-call scene. Fallback to the one-call scene when the first frame is unreadable; «waiting»
when the image model is unreachable. Fakes only (tmp DBs, fake LLM, fake Frigate, enforcement log patched).
"""

import asyncio
import json
from zoneinfo import ZoneInfo

import pytest

from kaare_core import model_enforcement as me
from kaare_core.frigate_user import conclude, frames, route
from kaare_core.frigate_user import store as fstore
from kaare_core.tools.i18n import t as _t_i18n

UTC = ZoneInfo("UTC")
T0 = 1_792_998_000.0  # 2026-10-25 07:00:00 UTC
CAMS = {"door": {"display_name": "Ringeklokke", "role": "front_door"}}
FULL = {"persons": [{"age": "adult", "gender": "woman", "clothing": "rødbrun hettegenser", "place": "ved døra",
                     "posture": "standing", "carried": ""}], "doors": [{"door": "inngangsdør", "state": "open"}],
        "things": []}
SIT = {"known": [{"id": "P1", "in_image": "yes", "posture": "sitting", "place": "i stolen ved veggen"}],
       "new_persons": [], "doors": [], "things": [], "correction": ""}
STILL_THERE = {"known": [{"id": "P1", "in_image": "yes"}], "new_persons": [], "doors": [], "things": [],
               "correction": ""}
LOOK = {"person": True, "animal": "none", "vehicle": False, "nothing": False}
SCENE = {"persons": 1, "people": [], "animals": [], "vehicles": "", "at_door": "", "scene": "En kvinne på trappa."}
TIMELINE = [{"timestamp": T0, "class_type": "visible", "data": {"box": [0.5, 0.3, 0.5, 0.7]}},
            {"timestamp": T0 + 10, "class_type": "stationary", "data": {"box": [0.47, 0.55, 0.15, 0.33]}},
            {"timestamp": T0 + 70, "class_type": "active", "data": {"box": [0.41, 0.44, 0.3, 0.56]}},
            {"timestamp": T0 + 98, "class_type": "gone", "data": {"box": [0.61, 0.13, 0.39, 0.87]}}]
FRAME_TS = (T0 + 2, T0 + 14, T0 + 40, T0 + 100)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    state = {"prompts": [], "answers": {}, "unreachable": False, "logged": [], "timeline": TIMELINE}

    async def llm(role, prompt, images=None, rid=""):
        state["prompts"].append((rid, prompt, len(images or [])))
        if state["unreachable"]:
            return {"text": "", "unreachable": True}
        if (":look" in rid):
            return {"text": json.dumps(LOOK)}
        if (":scene" in rid):
            return {"text": json.dumps(SCENE)}
        frame = int(rid.rsplit(":", 1)[1])
        answer = state["answers"].get(frame)
        return {"text": answer if isinstance(answer, str) else json.dumps(answer)}

    async def timeline(event_id):
        return state.get("timeline_by", {}).get(event_id, state["timeline"])

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude.frigate_adapter, "fetch_timeline", timeline)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_window_animal_labels", lambda ep: set())
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: state["logged"].append(a))
    monkeypatch.setattr(me, "get_enforcer_modes", dict)
    state["answers"].update({1: FULL, 2: SIT, 4: STILL_THERE})
    yield state
    fstore.reset_for_tests()


def _episode():
    ev = fstore.FrigateUserEvent(event_id="e1", camera="door", label="person", start_ts=T0, end_ts=T0 + 106,
                                 path_data=[[[0.5, 0.5], T0], [[0.5, 0.6], T0 + 100]], camera_role="front_door")
    assert fstore.record_event(ev)
    (ep,) = fstore.episodes_between(0, T0 * 2)
    return ep, fstore.episode_events(ep.episode_id)


def _kept():
    return [(i, frames.Frame(ts=ts, camera="door", jpeg=b"\xff\xd8jpeg")) for i, ts in enumerate(FRAME_TS)]


def _scene(env):
    ep, events = _episode()
    scene, _route = asyncio.run(conclude._scene_step(ep, events, "nb", kept=_kept()))
    return ep, events, scene


def _frame_calls(env):
    return [p for p in env["prompts"] if (":frames" in p[0])]


def test_the_scene_is_the_frame_by_frame_run(env):
    _ep, _events, scene = _scene(env)
    assert scene["status"] == "done" and scene["by"] == "frames"
    assert scene["persons"] == 1 and scene["people"][0]["clothing"] == "rødbrun hettegenser"
    assert not [p for p in env["prompts"] if (":scene" in p[0])]  # no one-call scene


def test_best_first_frame_full_then_changes_with_the_state_skip_without_movement(env):
    _scene(env)
    calls = _frame_calls(env)
    assert [c[0] for c in calls] == ["rid-frig-ep1:frames:1", "rid-frig-ep1:frames:2", "rid-frig-ep1:frames:4"]  # 3 skipped
    assert all(c[2] == 1 for c in calls)  # one image per call
    # P144-s25 language task: the frame prompts are English (episode_state.MODEL_LANG) — was «fast skjema» / «kjent fra før»
    assert "fixed schema" in calls[0][1] and "known already" not in calls[0][1]
    assert "known already" in calls[1][1] and "P1" in calls[1][1] and "rødbrun hettegenser" in calls[1][1]
    assert "sitting" in calls[2][1]  # the state the 9B is shown carries frame 2's change


def test_the_honesty_block_is_not_in_the_frame_prompts(env):
    # s25 measured (variant d): in front of the frame prompt it made this 9B write more English, more new persons and
    # more things; it closes the correction messages instead (test below)
    _scene(env)
    honesty = _t_i18n("frigate_honesty", "nb")
    calls = _frame_calls(env)
    assert len(calls) == 3 and not any(honesty in c[1] for c in calls)
    assert "løgn" in honesty and "identitetsbrudd" in honesty


def test_code_sets_a_copied_forward_person_absent_and_the_profile_logs_it(env):
    _ep, _events, scene = _scene(env)
    last = scene["frames_state"]["rows"][-1]
    assert last["frame"] == 4 and last["present"] == [] and last["override"] == "frigate_no_track"
    assert "ingen person i bildet" in scene["timeline"][-1]
    assert scene["frames_run"]["hits"].get("frigate_state_carried") == 1
    assert any(a[1] == "frigate_state_carried" and a[2] == "shadow" for a in env["logged"])


def test_the_timeline_reads_in_time_order_and_skips_nothing_it_ran(env):
    _ep, _events, scene = _scene(env)
    assert len(scene["timeline"]) == 3
    assert "står" in scene["timeline"][0] and "sitter" in scene["timeline"][1]
    assert scene["timeline"][0].startswith("07:00:02 ") and "Ringeklokke" in scene["timeline"][0]


def test_the_27b_gets_the_timeline_not_the_l3_text(env):
    ep, events, scene = _scene(env)
    ep.scene = scene
    events[0].description, events[0].analysis = "Kvinne i rød lue.", "Hun åpner døra."
    prompt = conclude.build_episode_prompt(ep, events, "nb", away=False, cameras=CAMS)
    assert "Bilde for bilde" in prompt and scene["timeline"][1] in prompt
    assert "Står det ikke der, skriv det ikke" in prompt
    assert "rød lue" not in prompt and "åpner døra" not in prompt


def test_an_unreadable_first_frame_falls_back_to_the_one_call_scene(env):
    env["answers"][1] = "Jeg ser en kvinne."
    _ep, _events, scene = _scene(env)
    assert scene["status"] == "done" and scene.get("by") != "frames"
    assert scene["scene"] == "En kvinne på trappa." and scene["frames_failed"].startswith("first_frame")


def test_a_later_unreadable_frame_keeps_the_state(env):
    env["answers"][2] = "{oops"
    _ep, _events, scene = _scene(env)
    assert scene["by"] == "frames" and scene["frames_run"]["unreadable"] == 1
    assert any(r["kind"] == "unreadable" for r in scene["frames_state"]["rows"])


def test_unreachable_image_model_waits(env):
    env["unreachable"] = True
    _ep, _events, scene = _scene(env)
    assert scene["status"] == conclude.WAITING


def test_without_a_timeline_presence_uses_the_event_end_minus_the_linger(env):
    env["timeline"] = []  # Frigate's timeline not reachable: end 106 − 6 = 100 → frame 4 at 100 s still covered
    _ep, _events, scene = _scene(env)
    assert scene["by"] == "frames"
    assert scene["frames_state"]["rows"][-1]["present"] == ["P1"]  # honest: without the timeline code cannot tell


def test_cross_episode_memory_reaches_the_27b(env):
    ep_prev, _events, scene = _scene(env)
    fstore.set_episode_scene(ep_prev.episode_id, scene, "")
    nxt = fstore.FrigateUserEvent(event_id="e2", camera="door", label="person", start_ts=T0 + 1800,
                                  end_ts=T0 + 1810, path_data=[[[0.5, 0.5], T0 + 1800]], camera_role="front_door")
    assert fstore.record_event(nxt)
    ep2 = next(e for e in fstore.episodes_between(0, T0 * 2) if e.episode_id != ep_prev.episode_id)
    tracks = asyncio.run(conclude._episode_tracks(fstore.episode_events(ep2.episode_id)))
    lines = conclude._before_lines(ep2, tracks, CAMS, "nb")
    assert any("forrige episode endte kl. 07:01:40" in x and "ingen i bildet til slutt" in x for x in lines)


def test_the_frigate_frames_profile_is_in_the_gui_catalog_capped_at_shadow():
    rows = {r["name"]: r for r in me.detector_catalog()}
    for name in ("frigate_state_carried", "frigate_unsupported_change", "frigate_missed_change",
                 "frigate_wrong_language", "cut_or_invalid_output", "frigate_empty_output"):
        prof = {p["profile"]: p["default_mode"] for p in rows[name]["profiles"]}
        assert prof.get("frigate_frames") == me.MODE_SHADOW, name
    assert {p["profile"] for p in rows["frigate_thin_scene"]["profiles"]} == {"frigate"}  # other profiles unchanged


def test_detectors_report_only_code_facts_on_frame_calls():
    enf = me.ModelEnforcer.for_profile("frigate_frames", "nb", rid="t")
    assert enf.check(text_out="{}", used_tools=[], meta={"kind": "frame", "state_carried": ["P1"],
                                                          "wrong_language": ["en"]}) is None  # shadow: no correction
    assert set(enf.hits) == {"frigate_state_carried", "frigate_wrong_language"}
    enf2 = me.ModelEnforcer.for_profile("frigate_frames", "nb", rid="t")
    enf2.check(text_out="{}", used_tools=[], meta={"kind": "scene", "state_carried": ["P1"]})
    assert enf2.hits == []


def test_a_detector_switched_on_gets_one_correction_with_the_honesty_block(monkeypatch):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"frigate_frames:frigate_state_carried": me.MODE_ON})
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)
    enf = me.ModelEnforcer.for_profile("frigate_frames", "nb", rid="t")
    action = enf.check(text_out="{}", used_tools=[], meta={"kind": "frame", "state_carried": ["P1"]})
    assert action is not None and "P1" in action.correction_message
    assert _t_i18n("frigate_honesty", "nb") in action.correction_message


def test_a_real_change_on_a_frame_frigate_lost_keeps_the_person(env):
    # ep 102 #7: Frigate lost the man at the low camera, the 9B saw him take a bottle out — that is no copy
    env["answers"][4] = {"known": [{"id": "P1", "in_image": "yes", "carried": "en flaske", "posture": "standing"}],
                         "new_persons": [], "doors": [], "things": [], "correction": ""}
    _ep, _events, scene = _scene(env)
    last = scene["frames_state"]["rows"][-1]
    assert last["present"] == ["P1"] and "override" not in last
    assert scene["people"][0]["carried"] == "en flaske"


def test_no_new_person_where_frigate_tracks_nobody(env):
    # ep 106 shape: Frigate tracks an animal on the porch but no person (the person track ended before frame 1); the
    # first frame shows nobody, then the 9B «finds» a person on the empty porch
    deer = fstore.FrigateUserEvent(event_id="d1", camera="door", label="deer", start_ts=T0, end_ts=T0 + 106,
                                   path_data=[[[0.2, 0.5], T0], [[0.3, 0.5], T0 + 100]], camera_role="front_door")
    assert fstore.record_event(deer)
    env["timeline_by"] = {
        "e1": [{"timestamp": T0, "class_type": "visible", "data": {"box": [0.5, 0.3, 0.1, 0.2]}},
               {"timestamp": T0 + 1, "class_type": "gone", "data": {"box": [0.5, 0.3, 0.1, 0.2]}}],
        "d1": [{"timestamp": T0, "class_type": "visible", "data": {"box": [0.2, 0.5, 0.1, 0.1]}},
               {"timestamp": T0 + 50, "class_type": "active", "data": {"box": [0.3, 0.5, 0.1, 0.1]}},
               {"timestamp": T0 + 104, "class_type": "gone", "data": {"box": [0.3, 0.5, 0.1, 0.1]}}]}
    env["answers"].update({1: {"persons": [], "doors": [], "things": []},
                           2: {"known": [], "new_persons": [{"age": "adult", "gender": "man", "clothing": "mørk"}],
                               "doors": [], "things": [], "correction": ""}})
    _ep, _events, scene = _scene(env)
    assert len(_frame_calls(env)) >= 2  # the frames ran (the animal moved), they were not skipped
    assert scene["persons"] == 0 and all(r["present"] == [] for r in scene["frames_state"]["rows"])


def test_state_carried_is_logged_only_for_a_pure_copy(env):
    env["answers"][4] = {"known": [{"id": "P1", "in_image": "yes", "carried": "en flaske"}], "new_persons": [],
                         "doors": [], "things": [], "correction": ""}
    _ep, _events, scene = _scene(env)
    assert "frigate_state_carried" not in scene["frames_run"]["hits"]


def test_a_reworded_place_or_posture_on_an_empty_frame_is_still_a_copy(env):
    # s25 measured: on empty frames the 9B rewords the place («driveway» → «left edge of driveway») or flips the posture;
    # only something newly carried proved sight (ep 102 #7, the bottle) — the rest is set absent by code
    env["answers"][4] = {"known": [{"id": "P1", "in_image": "yes", "place": "helt ved veggen", "posture": "bent"}],
                         "new_persons": [], "doors": [], "things": [], "correction": ""}
    _ep, _events, scene = _scene(env)
    assert scene["frames_state"]["rows"][-1]["present"] == []
    assert scene["frames_run"]["hits"].get("frigate_state_carried") == 1
