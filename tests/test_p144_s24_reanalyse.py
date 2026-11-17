"""P144-s24 item 4 — «Analyser» / «Analyser på nytt» per episode (admin), on the kept frames.

- `POST /api/frigate_user/episodes/{id}/analyse`: admin only (401/403 fail closed), one run at a time (409), rate
  limited (429), switch off / open episode / no kept frames refused; `GET /api/frigate_user/episode_analysis` = job.
- the run: look → scene → conclusion on the frames on disk (no Frigate), L3 untouched, the previous conclusion + scene
  kept as `history` (provenance), urgent results kept, the Frigate STM line replaced (no parallel truth), the day
  reopened in the `frigate` LTM marker when it was already compressed (`frigate_ltm.reopen_episode`).
- unreachable model → nothing overwritten, the job says so.
Never touches live state (tmp DBs, fake Frigate, fake LLM).
"""

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from adapters import frigate_adapter, llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.frigate_user import conclude, route, stm
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import frigate_ltm
from kaare_core.memory.short_term import STMRegistry
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p144_s06d_scene import CAMS, GOOD, SCENE, T0, UTC, _leaving

LOOK = {"person": True, "animal": "none", "vehicle": False, "nothing": False}
NEW = {**GOOD, "summary": "Jeg så en kvinne gå ut med en veske."}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")  # never the house's settings file
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    fstore.reset_for_tests()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    ustore.create_user(username="mum", display_name="Mum", role="adult", pin="482915")
    frigate_consent.invalidate()
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(frigate_ltm, "get_local_tz", lambda: UTC)
    state = {"calls": [], "snaps": [], "down": False, "answer": GOOD}

    async def fake_llm(role, prompt, images=None, rid=""):
        state["calls"].append({"role": role, "rid": rid, "images": images})
        if role == "frigate" and state["down"]:
            return {"ok": False, "text": "", "unreachable": True}
        if (":look" in rid):
            return {"text": json.dumps(LOOK)}
        return {"text": json.dumps(SCENE if role == "frigate" else state["answer"])}

    async def fake_snap(camera, ts, height=480):
        state["snaps"].append((camera, ts))
        return f"{camera}-{ts:.0f}".encode()

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", fake_llm)
    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snap)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(rfu, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    state["enabled"] = True
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": state["enabled"], "episode_gap_s": 120, "conclude": {
        "idle_gate_s": 30, "max_attempts": 3, "poll_s": 30, "urgent_classes": [], "urgent_labels": [],
        "urgent_when_away_person": False}})
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(stm, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(normalcy, "_load_baseline", dict)
    monkeypatch.setattr(conclude, "_world_rows", list)
    monkeypatch.setattr(conclude, "_episode_weather", _none)
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    monkeypatch.setattr(app_state, "STM_REGISTRY", STMRegistry(), raising=False)
    monkeypatch.setattr(rfu, "_JOB", {"state": "idle"})
    monkeypatch.setattr(rfu, "_STARTS", [])
    app = FastAPI()
    app.include_router(rfu.router)
    with TestClient(app) as client:  # one event loop for the whole test: the job's background task keeps running
        state["client"] = client
        yield state
    frigate_consent.invalidate()
    fstore.reset_for_tests()


async def _none(ep):
    return None


def _hdr(user="boss", role="admin"):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


def _concluded_episode(env):
    _leaving()
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    ep = fstore.episodes_between(0, T0 * 2)[0]
    env["calls"].clear()
    env["snaps"].clear()
    env["answer"] = NEW
    return ep.episode_id


def _wait_job(c):
    for _ in range(200):
        job = c.get("/api/frigate_user/episode_analysis", headers=_hdr()).json()
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job never finished")


# ── the lock (fail closed) ────────────────────────────────────────────────────


def test_analyse_is_admin_only(env):
    ep_id = _concluded_episode(env)
    c = env["client"]
    assert c.post(f"/api/frigate_user/episodes/{ep_id}/analyse").status_code == 401
    assert c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr("mum", "adult")).status_code == 403
    assert c.get("/api/frigate_user/episode_analysis", headers=_hdr("mum", "adult")).status_code == 403
    assert env["calls"] == []


def test_refused_cases(env):
    c = env["client"]
    assert c.post("/api/frigate_user/episodes/999/analyse", headers=_hdr()).status_code == 404
    ep_id = _concluded_episode(env)
    env["enabled"] = False
    r = c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr())
    assert r.status_code == 409 and r.json()["detail"] == "switch_off"
    assert env["calls"] == []


def test_one_run_at_a_time_and_rate_limited(env, monkeypatch):
    ep_id = _concluded_episode(env)
    c = env["client"]
    rfu._JOB.update(state="running", episode_id=ep_id)
    r = c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr())
    assert r.status_code == 409 and r.json()["detail"] == "analysis_running"
    rfu._JOB.update(state="idle")
    now = time.time()
    monkeypatch.setattr(rfu, "_rate_max", lambda: 3)
    rfu._STARTS.extend([now] * 3)
    assert c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr()).status_code == 429


# ── the run ───────────────────────────────────────────────────────────────────


def test_reanalysis_runs_on_kept_frames_and_keeps_history(env):
    ep_id = _concluded_episode(env)
    before = fstore.episode(ep_id)
    old_summary = before.conclusion["summary"]
    c = env["client"]
    r = c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr())
    assert r.status_code == 202 and r.json()["state"] == "running"
    job = _wait_job(c)
    assert job["state"] == "done" and job["episode_id"] == ep_id and job["result"]["ok"] is True
    roles = [("look" if (":look" in x["rid"]) else x["role"]) for x in env["calls"]]
    assert roles == ["look", "frigate", "frigate_conclude"]
    assert env["snaps"] == []  # no Frigate: the kept frames
    ep = fstore.episode(ep_id)
    assert ep.conclusion["status"] == "done" and ep.conclusion["summary"] == NEW["summary"]
    (h,) = ep.conclusion["history"]
    assert h["conclusion"]["summary"] == old_summary and "history" not in h["conclusion"]
    assert h["scene"]["status"] == "done" and "frames" not in h["scene"] and h["replaced_ts"] > 0
    assert len(ep.scene["frames"]) == len(before.scene["frames"])
    # the Frigate STM holds the new line, not the old one (no parallel truth)
    lines = [t.text for t in app_state.STM_REGISTRY.get("frigate")._dialog]
    assert any(NEW["summary"] in ln for ln in lines) and not any(old_summary in ln for ln in lines)
    detail = c.get(f"/api/frigate_user/episodes/{ep_id}", headers=_hdr()).json()
    assert detail["history_count"] == 1


def test_second_reanalysis_stacks_history(env):
    ep_id = _concluded_episode(env)
    for _ in range(2):
        assert asyncio.run(conclude.reanalyse(ep_id))["ok"] is True
    assert len(fstore.episode(ep_id).conclusion["history"]) == 2


def test_reanalysis_with_the_model_down_overwrites_nothing(env):
    ep_id = _concluded_episode(env)
    before = fstore.episode(ep_id)
    env["down"] = True
    res = asyncio.run(conclude.reanalyse(ep_id))
    assert res == {"ok": False, "reason": "model_unreachable"}
    after = fstore.episode(ep_id)
    assert after.conclusion == before.conclusion and after.scene == before.scene


def test_no_kept_frames_and_none_fetchable(env, monkeypatch):
    ep_id = _concluded_episode(env)
    for p in (fstore.episode_frames_dir() / str(ep_id)).glob("*.jpg"):
        p.unlink()

    async def gone(camera, ts, height=480):
        raise RuntimeError("404")

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", gone)
    assert asyncio.run(conclude.reanalyse(ep_id)) == {"ok": False, "reason": "no_frames"}
    assert env["calls"] == []


def test_missing_files_are_fetched_first_while_frigate_still_has_them(env):
    ep_id = _concluded_episode(env)
    n = len(fstore.episode(ep_id).scene["frames"])
    for p in (fstore.episode_frames_dir() / str(ep_id)).glob("*.jpg"):
        p.unlink()
    assert asyncio.run(conclude.reanalyse(ep_id))["ok"] is True
    assert len(env["snaps"]) == n and fstore.stored_frame_indexes(ep_id) == list(range(n))


def test_urgent_results_survive_a_reanalysis(env):
    ep_id = _concluded_episode(env)
    item = {"event_id": "e1", "status": "done", "summary": "haster", "ts": T0}
    conclude._merge(ep_id, None, item)
    asyncio.run(conclude.reanalyse(ep_id))
    assert fstore.episode(ep_id).conclusion["urgent"] == [item]


# ── LTM: the day is compressed again ─────────────────────────────────────────


def test_reopen_removes_the_episode_from_a_days_done_list(env):
    ep_id = _concluded_episode(env)
    day = frigate_ltm._day_of(fstore.episode(ep_id).start_ts)
    fstore.set_ltm_marker({"settled_through": None, "done": {day: [ep_id, 4242]}})
    assert frigate_ltm.reopen_episode(ep_id) == "reopened"
    assert fstore.ltm_marker()["done"][day] == [4242]


def test_reopen_unsettles_a_settled_day(env):
    ep_id = _concluded_episode(env)
    ep = fstore.episode(ep_id)
    day = frigate_ltm._day_of(ep.start_ts)
    later = frigate_ltm._day_of(ep.start_ts + 2 * 86400)
    fstore.set_ltm_marker({"settled_through": later, "done": {}})
    assert frigate_ltm.reopen_episode(ep_id) == "reopened"
    m = fstore.ltm_marker()
    assert m["settled_through"] is None or m["settled_through"] < day  # first day of memory → scan from the start
    assert ep_id not in m["done"].get(day, [])
    # the night step now finds exactly this episode for that day — nothing else is compressed twice
    batches, _settled = frigate_ltm.day_batches(day, m, T0 + 30 * 86400, "nb", set())
    assert [b.episode_ids for b in batches] == [[ep_id]]


def test_reopen_of_a_day_not_in_ltm_changes_nothing(env):
    ep_id = _concluded_episode(env)
    fstore.set_ltm_marker({"settled_through": None, "done": {}})
    assert frigate_ltm.reopen_episode(ep_id) == "not_in_ltm"
    assert fstore.ltm_marker() == {"settled_through": None, "done": {}}


def test_the_endpoint_reopens_the_ltm_day(env):
    ep_id = _concluded_episode(env)
    day = frigate_ltm._day_of(fstore.episode(ep_id).start_ts)
    fstore.set_ltm_marker({"settled_through": None, "done": {day: [ep_id]}})
    c = env["client"]
    assert c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr()).status_code == 202
    job = _wait_job(c)
    assert job["result"]["ltm"] == "reopened" and fstore.ltm_marker()["done"][day] == []


@pytest.fixture(autouse=True)
def _one_call_scene(monkeypatch):
    """P144-s25: these tests cover the one-call scene, which is now the fallback when the frame-by-frame run fails
    (tests/test_p144_s25_frames_step.py covers the frame run). Their fake models do not answer frame calls."""
    monkeypatch.setattr(conclude, "FRAMES_ON", False)
