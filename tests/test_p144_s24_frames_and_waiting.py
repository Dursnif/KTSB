"""P144-s24 items 2 + 3 — frames always kept and listed; «venter på analyse» when the image model cannot be reached.

Found 2026-10-31 (ep 105/106): with the 9B instance down, look + scene failed («empty»), the frames were on disk but the
scene JSON had no `frames` list (GUI: «no images»), and the 27B concluded the episode for good from labels + route
(«Jeg så et hjortedyr …» — nothing was seen).

- item 2: the frame set is decided by code before any model call; the scene JSON lists it (camera, ts, file) whatever
  happens to look/scene, and the GUI endpoint returns it.
- item 3: unreachable ≠ a bad answer. Unreachable → scene + conclusion `waiting` (no 27B), retried by the loop on the
  kept frames (no new Frigate fetch), at most one waiting episode per round, a backoff between tries. A bad answer keeps
  today's handling (concluded on the route) — but a conclusion without any image check says so (code fact).
Never touches live state (tmp DBs, fake Frigate, fake LLM).
"""

import asyncio
import json

import pytest

from adapters import frigate_adapter, llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.frigate_user import conclude, route
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p144_s06d_scene import CAMS, GOOD, SCENE, T0, UTC, _leaving

LOOK_PERSON = {"person": True, "animal": "none", "vehicle": False, "nothing": False}
DOWN = {"ok": False, "error": "All connection attempts failed", "text": "", "tool_calls": None, "unreachable": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    state = {"calls": [], "snaps": [], "down": False, "look": json.dumps(LOOK_PERSON), "scene": json.dumps(SCENE)}

    async def fake_llm(role, prompt, images=None, rid=""):
        state["calls"].append({"role": role, "rid": rid, "prompt": prompt, "images": images})
        if role == "frigate" and state["down"]:
            return dict(DOWN)
        if (":look" in rid):
            return {"text": state["look"]}
        return {"text": state["scene"] if role == "frigate" else json.dumps(GOOD) + " [LANG:en]"}

    async def fake_snap(camera, ts, height=480):
        state["snaps"].append((camera, ts))
        return f"{camera}-{ts:.0f}".encode()

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", fake_llm)
    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snap)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(rfu, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": True, "episode_gap_s": 120, "conclude": {
        "idle_gate_s": 30, "max_attempts": 3, "poll_s": 30, "urgent_classes": [], "urgent_labels": [],
        "urgent_when_away_person": False}})
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "en")
    monkeypatch.setattr(conclude, "_episode_weather", _no_weather)
    monkeypatch.setattr(normalcy, "_load_baseline", dict)
    monkeypatch.setattr(conclude, "_world_rows", list)
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    yield state
    frigate_consent.invalidate()
    fstore.reset_for_tests()


async def _no_weather(ep):
    return None


def _roles(state):
    return [("look" if (":look" in c["rid"]) else c["role"]) for c in state["calls"]]


def _ep():
    return fstore.episodes_between(0, T0 * 2)[0]


# ── item 2: frames always kept and listed ─────────────────────────────────────


@pytest.mark.parametrize("look", ["not json at all", ""])
def test_look_fails_frames_still_listed_and_on_disk(env, look):
    env["look"] = env["scene"] = look  # a bad / empty answer on both calls (the model was there)
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    ep = _ep()
    assert ep.scene["status"] == "failed" and ep.scene["look"]["status"] == "failed"
    listed = ep.scene["frames"]
    assert 3 <= len(listed) == len(env["snaps"])
    assert [(f["camera"], f["ts"]) for f in listed] == env["snaps"]
    assert all(f["file"] == i for i, f in enumerate(listed))
    assert fstore.stored_frame_indexes(ep.episode_id) == list(range(len(listed)))
    detail = rfu._detail_view(ep.episode_id)
    assert len(detail["frames"]) == len(listed) and all(f["stored"] for f in detail["frames"])


def test_model_down_frames_still_listed(env):
    env["down"] = True
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    ep = _ep()
    assert len(ep.scene["frames"]) == len(env["snaps"]) >= 3
    assert len(rfu._detail_view(ep.episode_id)["frames"]) == len(env["snaps"])


def test_frame_list_reads_the_file_index(env):
    # a re-analysis on a partly trimmed set lists only the files still there: position ≠ file number
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    ep = _ep()
    fstore.save_episode_frames(ep.episode_id, [b"a", b"b", b"c", b"d"])
    scene = {**ep.scene, "frames": [{"camera": "door", "ts": T0, "file": 3}]}
    fstore.set_episode_scene(ep.episode_id, scene, ep.route)
    (f,) = rfu._frame_list(fstore.episode(ep.episode_id), CAMS)
    assert f["index"] == 0 and f["file"] == 3 and f["stored"] is True


# ── item 3: «venter på analyse» ───────────────────────────────────────────────


def test_unreachable_model_keeps_the_episode_waiting(env):
    env["down"] = True
    _leaving()
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 0
    assert "frigate_conclude" not in _roles(env)  # no 27B conclusion built on nothing
    ep = _ep()
    assert ep.scene["status"] == "waiting" and ep.scene["reason"] == "model_unreachable"
    c = ep.conclusion
    assert c["status"] == "waiting" and c["reason"] == "model_unreachable" and not c.get("summary")
    assert rfu._episode_item(ep, fstore.episode_events(ep.episode_id), CAMS, set())["conclusion_status"] == "waiting"


def test_waiting_episode_is_retried_on_the_kept_frames_when_the_model_answers(env):
    env["down"] = True
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    fetched = len(env["snaps"])
    env["down"] = False
    env["calls"].clear()
    assert asyncio.run(conclude.run_once(now=T0 + 5000)) == 1
    assert _roles(env) == ["look", "frigate", "frigate_conclude"]
    assert len(env["snaps"]) == fetched  # no new Frigate fetch: the kept frames
    assert env["calls"][0]["images"]  # the stored frames went to the look
    ep = _ep()
    assert ep.scene["status"] == "done" and ep.conclusion["status"] == "done"
    assert ep.conclusion.get("images_checked") is not False
    assert len(ep.scene["frames"]) == fetched


def test_waiting_retry_has_a_backoff(env):
    env["down"] = True
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    env["calls"].clear()
    asyncio.run(conclude.run_once(now=T0 + 1010))  # 10 s later: too soon
    assert env["calls"] == []


def test_only_one_waiting_episode_is_tried_per_round(env):
    env["down"] = True
    _leaving()
    for i, start in enumerate((T0 + 3000, T0 + 6000)):  # two more episodes, far apart
        fstore.record_event(fstore.FrigateUserEvent(
            event_id=f"x{i}", camera="door", label="person", start_ts=start, end_ts=start + 8,
            path_data=[[[0.2, 0.5], start], [[0.6, 0.5], start + 6]], camera_role="front_door"))
    asyncio.run(conclude.run_once(now=T0 + 9000))
    eps = fstore.episodes_between(0, T0 * 2)
    assert len(eps) == 3
    env["calls"].clear()
    asyncio.run(conclude.run_once(now=T0 + 99_000))  # all three due again, the model still down
    assert len([c for c in env["calls"] if (":look" in c["rid"])]) == 1


def test_bad_answer_keeps_todays_handling_and_says_not_checked_with_images(env):
    env["look"] = env["scene"] = "garbage"
    _leaving()
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    assert _roles(env) == ["look", "frigate", "frigate_conclude"]
    prompt = env["calls"][-1]["prompt"]
    assert conclude._t("no_image_check", "en") in prompt
    c = _ep().conclusion
    assert c["status"] == "done" and c["images_checked"] is False
    assert c["summary"].endswith(conclude._t("not_checked_suffix", "en"))


def test_no_frames_at_all_says_not_checked_with_images(env, monkeypatch):
    async def none(camera, ts, height=480):
        raise RuntimeError("no recording")

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", none)
    _leaving()
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    c = _ep().conclusion
    assert c["images_checked"] is False and c["summary"].endswith(conclude._t("not_checked_suffix", "en"))


def test_a_look_that_failed_but_a_done_scene_counts_as_checked_with_images(env):
    env["look"] = "garbage"  # the description still saw the frames
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    c = _ep().conclusion
    assert c["status"] == "done" and c.get("images_checked") is not False
    assert not c["summary"].endswith(conclude._t("not_checked_suffix", "en"))


def test_unreachable_27b_does_not_use_up_attempts(env, monkeypatch):
    async def llm(role, prompt, images=None, rid=""):
        if role == "frigate_conclude":
            return {**DOWN}
        if (":look" in rid):
            return {"text": json.dumps(LOOK_PERSON)}
        return {"text": json.dumps(SCENE)}

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    _leaving()
    for i in range(5):
        asyncio.run(conclude.run_once(now=T0 + 1000 + i * 4000))
    c = _ep().conclusion
    assert c["status"] == "failed" and c["reason"] == "model_unreachable" and c["attempts"] == 0


@pytest.fixture(autouse=True)
def _one_call_scene(monkeypatch):
    """P144-s25: these tests cover the one-call scene, which is now the fallback when the frame-by-frame run fails
    (tests/test_p144_s25_frames_step.py covers the frame run). Their fake models do not answer frame calls."""
    monkeypatch.setattr(conclude, "FRAMES_ON", False)
