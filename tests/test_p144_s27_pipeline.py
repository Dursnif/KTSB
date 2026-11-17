"""P144-s27 item 4 — chapters, the budget and the change regions in the analysis. A long episode gets its frames from the
plan, the 27B gets chapter headers from code and the positions of what looks different after; the look stays small; short
episodes are untouched; a Frigate that is down never costs the analysis. Fakes only (tmp DBs, fake LLM, fake Frigate)."""

import asyncio
import io
import json
import random
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from kaare_core import model_enforcement as me
from kaare_core.frigate_user import change_detect, chapters, conclude, frames, route
from kaare_core.frigate_user import store as fstore

UTC = ZoneInfo("UTC")
T0 = 1_792_998_000.0
CAMS = {"door": {"display_name": "Ringeklokke", "role": "front_door"}}
FULL = {"persons": [{"age": "adult", "gender": "woman", "clothing": "red", "place": "door", "posture": "standing",
                     "carried": ""}], "doors": []}
SAME = {"known": [{"id": "P1", "in_image": "yes"}], "new_persons": [], "doors": [], "correction": ""}
LOOK = {"person": True, "animal": "none", "vehicle": False, "nothing": False}


def _jpeg(v=90):
    b = io.BytesIO()
    Image.new("RGB", (854, 480), (v, v, v)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fstore, "episode_frames_dir", lambda: tmp_path / "frames")
    fstore.reset_for_tests()
    state = {"prompts": [], "fetches": 0, "down": False, "regions": [], "scan_cells": 0, "new_person": False}

    async def llm(role, prompt, images=None, rid=""):
        state["prompts"].append((rid, prompt, len(images or [])))
        if (":look" in rid):
            return {"text": json.dumps(LOOK)}
        if (":change" in rid):
            return {"text": json.dumps({"different": "a cloth"})}
        if (":conclude" in rid):
            return {"text": json.dumps({"summary": "ok", "unusual": {"value": False, "reason": ""},
                                        "confidence": {"level": "low", "reasons": []}, "notify_suggestion": None,
                                        "raise_at_dev_meeting": None})}
        answer = FULL if not any(":frames" in p[0] for p in state["prompts"][:-1]) else SAME
        return {"text": json.dumps(answer)}

    async def fetch(camera, ts, height=480):
        state["fetches"] += 1
        if state["down"]:
            raise OSError("frigate down")
        return _jpeg()

    async def timeline(event_id):
        return []

    async def configs():
        return {}

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(frames.frigate_adapter, "fetch_recording_snapshot", fetch)
    monkeypatch.setattr(frames.frigate_adapter, "fetch_camera_configs", configs)
    monkeypatch.setattr(conclude.frigate_adapter, "fetch_timeline", timeline)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_window_animal_labels", lambda ep: set())
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)
    monkeypatch.setattr(me, "get_enforcer_modes", dict)

    def detect(before, after, near, masks=None, skip=None):
        return {"regions": list(state["regions"]), "guard": None}

    monkeypatch.setattr(change_detect, "detect", detect)
    yield state
    fstore.reset_for_tests()


def _episode(dur):
    ev = fstore.FrigateUserEvent(event_id="e1", camera="door", label="person", start_ts=T0, end_ts=T0 + dur,
                                 path_data=[[[0.5, 0.5], T0], [[0.5, 0.6], T0 + dur]], camera_role="front_door")
    assert fstore.record_event(ev)
    (ep,) = fstore.episodes_between(0, T0 * 2)
    fstore.close_episodes(T0 + dur + 3600, 60)
    ep = fstore.episode(ep.episode_id)
    return ep, fstore.episode_events(ep.episode_id)


def _scene(ep, events, **kw):
    scene, _route = asyncio.run(conclude._scene_step(ep, events, "nb", **kw))
    return scene


def test_long_episode_gets_chapters_in_the_scene(env):
    ep, events = _episode(200)
    scene = _scene(ep, events)
    assert scene["status"] == "done"
    assert scene["chapters"] and scene["chapters"][0]["kind"] in ("active", "calm", "empty")
    assert scene["frames"], "the plan's frames are listed"
    assert sorted(f["ts"] for f in scene["frames"]) == [f["ts"] for f in scene["frames"]]


def test_short_episode_keeps_the_grid_and_has_no_chapters(env):
    ep, events = _episode(30)
    scene = _scene(ep, events)
    assert scene["status"] == "done" and not scene.get("chapters")
    assert len(scene["frames"]) <= frames.N_FRAMES


def test_the_look_never_gets_more_than_the_old_frame_count(env, monkeypatch):
    monkeypatch.setattr(chapters, "budget", lambda parts, keys, tracks, t0, t1: [(T0 + i * 5, "door") for i in range(20)])
    ep, events = _episode(200)
    _scene(ep, events)
    (look,) = [p for p in env["prompts"] if (":look" in p[0])]
    assert look[2] == frames.N_FRAMES


def test_frigate_down_during_the_scan_falls_back_without_error(env):
    env["down"] = True
    ep, events = _episode(200)
    scene = _scene(ep, events)
    assert scene["status"] == "failed" and scene["reason"] == "no_frames"


def test_chapter_headers_reach_the_27b_from_code(env):
    ep, events = _episode(200)
    ep.scene = _scene(ep, events)
    ep.route = "r"
    prompt = conclude.build_episode_prompt(ep, events, "nb", away=False, cameras=CAMS, hidden=set())
    assert "Kapittel 1" in prompt


def test_change_region_reaches_the_27b_as_position_only(env):
    env["regions"] = [{"cells": 6, "box": (0.1, 0.1, 0.2, 0.2)}]
    ep, events = _episode(200)
    ep.scene = _scene(ep, events)
    assert ep.scene["changes"]["regions"][0]["camera"] == "door"
    assert ep.scene["changes"]["regions"][0]["where"] == "pos_top"
    prompt = conclude.build_episode_prompt(ep, events, "nb", away=False, cameras=CAMS, hidden=set())
    assert "øverst" in prompt and "annerledes" in prompt


def test_no_region_no_section_in_the_prompt(env):
    ep, events = _episode(200)
    ep.scene = _scene(ep, events)
    prompt = conclude.build_episode_prompt(ep, events, "nb", away=False, cameras=CAMS, hidden=set())
    assert "annerledes etter episoden" not in prompt


def test_the_image_model_never_sees_a_claim_about_the_region(env):
    env["regions"] = [{"cells": 6, "box": (0.1, 0.1, 0.2, 0.2)}]
    ep, events = _episode(200)
    _scene(ep, events)
    for rid, prompt, _n in env["prompts"]:
        if (":frames" in rid or ":look" in rid):
            assert "annerledes" not in prompt and "different after" not in prompt


def test_crops_are_not_stored_on_the_scene(env):
    env["regions"] = [{"cells": 6, "box": (0.1, 0.1, 0.2, 0.2)}]
    ep, events = _episode(200)
    scene = _scene(ep, events)
    json.dumps(scene)  # plain data: no bytes, no crops, no reference frames
    assert "crops" not in scene["changes"] and "refs" not in scene["changes"]


def test_replan_on_kept_frames_reuses_them(env):
    ep, events = _episode(200)
    kept = [(0, frames.Frame(ts=T0 + 0.5, camera="door", jpeg=_jpeg()))]
    fstore.save_episode_frames(ep.episode_id, [_jpeg()], [0])
    scene = _scene(ep, events, kept=kept, replan=True)
    assert scene["chapters"]
    assert any(f["file"] == 0 for f in scene["frames"])


def test_retry_on_kept_frames_keeps_them_and_the_chapters(env):
    ep, events = _episode(200)
    ep.scene = {"status": "waiting", "chapters": [{"start": T0, "end": T0 + 200, "kind": "active", "signal": "start",
                                                   "cameras": ["door"]}]}
    kept = [(0, frames.Frame(ts=T0 + 5, camera="door", jpeg=_jpeg()))]
    before = env["fetches"]
    scene = _scene(ep, events, kept=kept)
    assert [f["file"] for f in scene["frames"]] == [0]
    assert scene["chapters"][0]["kind"] == "active"
    assert env["fetches"] - before <= 4, "only the change detector's frames, no scan"


# ── what the GUI gets ─────────────────────────────────────────────────────────


def _story_scene():
    rows = [{"frame": 1, "camera": "door", "ts": T0 + 2, "time": "07:00:02", "kind": "full", "present": ["P1"], "changes": [],
             "persons": {"P1": {"place": "door", "posture": "standing", "carried": ""}}},
            {"frame": 2, "camera": "door", "ts": T0 + 100, "time": "07:01:40", "kind": "change", "present": [], "changes": [],
             "persons": {}}]
    return {"status": "done", "by": "frames", "frames_state": {"persons": {"P1": {}}, "cams": {}, "rows": rows, "corrections": []},
            "frames": [{"camera": "door", "ts": T0 + 2, "file": 0}, {"camera": "door", "ts": T0 + 100, "file": 1}],
            "chapters": [{"start": T0, "end": T0 + 50, "kind": "active", "signal": "start", "cameras": ["door"]},
                         {"start": T0 + 50, "end": T0 + 106, "kind": "empty", "signal": "gap_start", "cameras": []}],
            "changes": {"regions": [{"camera": "door", "box": [0.1, 0.1, 0.2, 0.2], "cells": 6, "where": "pos_top"}],
                        "guards": {"door": "light"}}}


def test_story_has_chapters_with_their_lines_and_frames(env):
    story = conclude.episode_story(_story_scene(), CAMS, "nb")
    a, b = story["chapters"]
    assert a["n"] == 1 and a["kind"] == "active" and a["cameras"] == ["inngangskamera «Ringeklokke»"]
    assert a["frames"] == [0] and b["frames"] == [1]
    assert len(a["lines"]) == 1 and "Ringeklokke" in a["lines"][0] and "står" in a["lines"][0]
    assert a["start"].count(":") == 2


def test_story_change_lines_are_position_only_in_the_household_language(env):
    story = conclude.episode_story(_story_scene(), CAMS, "de")
    # J15: change items also carry «fact» (empty for a plain position) — was the three keys only
    assert story["changes"] == [{"camera": "Haustürkamera «Ringeklokke»", "where": "oben", "words": "", "fact": ""}]


def test_story_without_chapters_is_a_flat_timeline(env):
    scene = _story_scene()
    scene.pop("chapters")
    story = conclude.episode_story(scene, CAMS, "nb")
    assert story["chapters"] == [] and len(story["lines"]) == 2


def test_story_of_an_old_scene_is_empty_and_does_not_fail(env):
    # J15: the story also has «after_frames» (the stored after frame per changed camera) — was three keys
    empty = {"chapters": [], "lines": [], "changes": [], "after_frames": []}
    assert conclude.episode_story({"status": "done", "scene": "x"}, CAMS, "nb") == empty
    assert conclude.episode_story(None, CAMS, "nb") == empty


# ── the image model's words on a large change region ──────────────────────────


def test_a_large_region_gets_the_image_models_words_for_the_27b(env, monkeypatch):
    """Measured: tarp, window, trailer read right on big regions; small ones invent (a frog, a horse, a man)."""
    env["regions"] = [{"cells": change_detect.WORDS_MIN_CELLS, "box": (0.1, 0.1, 0.5, 0.5)}]
    monkeypatch.setattr(change_detect, "crop", lambda j, b: j)
    ep, events = _episode(200)
    ep.scene = _scene(ep, events)
    assert ep.scene["changes"]["regions"][0]["words"] == "a cloth"
    (call,) = [p for p in env["prompts"] if (":change" in p[0])]
    assert call[2] == 2, "before + after crop"
    prompt = conclude.build_episode_prompt(ep, events, "nb", away=False, cameras=CAMS, hidden=set())
    assert "a cloth" in prompt


def test_a_small_region_stays_a_position(env):
    env["regions"] = [{"cells": change_detect.WORDS_MIN_CELLS - 1, "box": (0.1, 0.1, 0.2, 0.2)}]
    ep, events = _episode(200)
    scene = _scene(ep, events)
    assert not [p for p in env["prompts"] if (":change" in p[0])]
    assert "words" not in scene["changes"]["regions"][0]


def test_the_change_prompt_names_nothing(env, monkeypatch):
    env["regions"] = [{"cells": 100, "box": (0.1, 0.1, 0.5, 0.5)}]
    monkeypatch.setattr(change_detect, "crop", lambda j, b: j)
    ep, events = _episode(200)
    _scene(ep, events)
    (call,) = [p for p in env["prompts"] if (":change" in p[0])]
    low = call[1].lower()
    assert "person" not in low and "door" not in low and "jacket" not in low and "camera name" not in low


def test_unreachable_model_on_a_region_costs_only_the_words(env, monkeypatch):
    env["regions"] = [{"cells": 100, "box": (0.1, 0.1, 0.5, 0.5)}]
    monkeypatch.setattr(change_detect, "crop", lambda j, b: j)
    real = conclude.ask_llm_role

    async def llm(role, prompt, images=None, rid=""):
        if (":change" in rid):
            return {"text": "", "unreachable": True}
        return await real(role, prompt, images=images, rid=rid)

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    ep, events = _episode(200)
    scene = _scene(ep, events)
    assert scene["status"] == "done"
    assert "words" not in scene["changes"]["regions"][0]


# ── Frigate's silence stands (the «lost someone» idea measured false: 5 real of 22, live 0 of 9) ──────────────


def test_a_changed_scene_is_no_proof_of_a_person(env, monkeypatch):
    """Ep 239 16:07:38: a car had moved, the 9B copied P1 forward, code wrote «står» — 2 minutes of a person who had gone in.
    A frame that differs from the empty scene (door open, car moved) does not overrule Frigate's silence."""
    def noisy(seed):
        rnd = random.Random(seed)
        im = Image.effect_noise((854, 480), 60).convert("RGB")
        im.putpixel((rnd.randint(0, 800), 1), (1, 2, 3))
        b = io.BytesIO()
        im.save(b, "JPEG")
        return b.getvalue()

    jpegs = iter(noisy(i) for i in range(1000))

    async def fetch(camera, ts, height=480):
        return next(jpegs)

    monkeypatch.setattr(frames.frigate_adapter, "fetch_recording_snapshot", fetch)

    async def timeline(event_id):
        return [{"timestamp": T0, "class_type": "visible", "data": {"box": [0.4, 0.4, 0.2, 0.4]}},
                {"timestamp": T0 + 20, "class_type": "gone", "data": {"box": [0.4, 0.4, 0.2, 0.4]}}]

    monkeypatch.setattr(conclude.frigate_adapter, "fetch_timeline", timeline)
    ep, events = _episode(30)
    kept = [(0, frames.Frame(ts=T0 + 5, camera="door", jpeg=next(jpegs))),
            (1, frames.Frame(ts=T0 + 28, camera="door", jpeg=next(jpegs)))]
    scene = _scene(ep, events, kept=kept)
    last = sorted(scene["frames_state"]["rows"], key=lambda r: r["ts"])[-1]
    assert last["present"] == [], "no track + a changed scene: still nobody"


def test_recordings_gone_keeps_the_kept_frames_on_a_replan(env, monkeypatch):
    """«Analyser» on an old episode: Frigate's recordings are gone after ~7 days. The 9 kept frames must not shrink to the one
    or two that happen to lie near a planned time."""
    ep, events = _episode(200)
    kept = [(i, frames.Frame(ts=T0 + 0.5 + i * 20, camera="door", jpeg=_jpeg())) for i in range(9)]
    fstore.save_episode_frames(ep.episode_id, [_jpeg() for _ in kept], [i for i, _f in kept])
    env["down"] = True
    scene = _scene(ep, events, kept=kept, replan=True)
    assert [f["file"] for f in scene["frames"]] == list(range(9))
    assert scene["chapters"], "chapters from Frigate's tracks still stand"
