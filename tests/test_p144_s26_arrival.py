"""P144-s26 items 2, 3 and 5 — one situation = one episode.

- **Attach at arrival:** a Frigate end event is stored at once as a skeleton (time, camera, label, path, drives) and put
  in its episode *before* any model call; L3 fills in the same row later. The skeleton never carries a name (the store's
  gate runs on it like on every write); a failing L3 never takes the event out of its episode; the s16 RAM counts only
  hear about the finished row.
- **Never conclude half an episode:** the conclusion waits while any of the episode's events is still waiting for its
  analysis.
- **Burst test (red before s26):** ~50 person events over 5 minutes with a slow fake L3 → one episode, no overlap, one
  conclusion after the queue drained.
Never touches live state (tmp DBs, fake Frigate, fake LLM).
"""

import asyncio
import io
import json

import pytest
from PIL import Image

from kaare_core import app_state
from kaare_core.frigate_user import conclude, faces, pipe
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0
BIG_FACE = [{"label": "face", "box": [0.4, 0.2, 0.2, 0.3], "score": 0.8}]


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (960, 720), (180, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


L3_REPLY = {"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult", "headwear": "no",
            "description": "a person in a blue jacket", "analysis": "someone at the front door"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n"
        "  Person A: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
        "  Person B: {class: household}\n")
    fstore.reset_for_tests()
    ustore.init_db()
    for name in ("persona", "personb"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    ustore.update_user("personb", frigate_faces=["Person B"])
    ustore.set_frigate_tracking("personb", False)  # opted out: never a name, not even for a moment
    frigate_consent.invalidate()
    getattr(pipe, "_arrivals", {}).clear()

    state = {"times": {}, "gate": None, "l3_started": [], "l3_fail": False, "inflight": [], "concluded": []}

    async def fake_get_event(event_id):
        start, end = state["times"].get(event_id, (T0, T0 + 10))
        return {"id": event_id, "start_time": start, "end_time": end, "data": {"attributes": BIG_FACE}}

    async def fake_snapshot(event_id):
        return _jpeg()

    async def fake_llm(role, prompt, images=None, rid=""):
        state["l3_started"].append(rid)
        if state["gate"] is not None:
            await state["gate"].acquire()  # a slow 9B: the test lets one answer out at a time
        if state["l3_fail"]:
            raise RuntimeError("9B gone")
        return {"ok": True, "text": json.dumps(L3_REPLY), "meta": {}}

    async def fake_in_progress():
        return list(state["inflight"])

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", fake_get_event)
    monkeypatch.setattr(pipe.frigate_adapter, "fetch_in_progress_events", fake_in_progress, raising=False)
    monkeypatch.setattr(pipe, "_load_snapshot", fake_snapshot)
    monkeypatch.setattr(pipe, "ask_llm_role", fake_llm)
    monkeypatch.setattr(pipe.household_state, "is_away", lambda: False)
    monkeypatch.setattr(pipe, "_place_of", lambda camera: None)
    monkeypatch.setattr(pipe, "_urgent_handler", None)
    yield state
    getattr(pipe, "_arrivals", {}).clear()
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _event(event_id="e1", camera="inngang", duration=10.0):
    return {"event_id": event_id, "camera": camera, "label": "person", "score": 0.9, "top_score": 0.9,
            "sub_label": None, "sub_label_score": None, "duration": duration, "has_snapshot": True}


def _row(event_id="e1"):
    return fstore.event_by_id(event_id)


async def _until(pred, steps=400):
    for _ in range(steps):
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False


# ── item 2: the skeleton ─────────────────────────────────────────────────────


def test_event_is_in_its_episode_before_the_l3_call_returns(env):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        task = asyncio.create_task(pipe.process(_event(), "Person A", 0.95))
        assert await _until(lambda: env["l3_started"]), "L3 never started"
        sk = _row()
        assert sk is not None, "nothing stored while L3 runs — the event waits for the 9B (the Halloween bug)"
        assert sk.episode_id is not None
        assert sk.check.get("pending") == "analysis"
        assert sk.person_ref is None and sk.claim_status == "none"  # the skeleton never carries a name
        created, episode_id = sk.created_ts, sk.episode_id
        env["gate"].release()
        await task
        done = _row()
        assert "pending" not in done.check
        assert done.person_ref == "persona" and done.claim_status in ("unverified", "confirmed")
        assert done.episode_id == episode_id and done.created_ts == created  # same row, same episode, arrival time
        assert done.attributes.get("persons") == 1
    asyncio.run(main())


def test_opted_out_claim_never_lands_in_the_skeleton(env):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        task = asyncio.create_task(pipe.process(_event(), "Person B", 0.95))
        assert await _until(lambda: env["l3_started"])
        sk = _row()
        blob = json.dumps([sk.person_ref, sk.check, sk.attributes, sk.description, sk.analysis])
        assert "Person B" not in blob and "personb" not in blob
        env["gate"].release()
        await task
        done = _row()
        blob = json.dumps([done.person_ref, done.check, done.attributes, done.description, done.analysis])
        assert "Person B" not in blob and "personb" not in blob
        assert done.person_kind == "untracked"
    asyncio.run(main())


def test_failing_l3_keeps_the_event_in_its_episode(env):
    env["l3_fail"] = True
    asyncio.run(pipe.on_end_event(_event(), None, None))
    ev = _row()
    assert ev is not None and ev.episode_id is not None
    assert "pending" not in ev.check
    assert ev.check.get("analysis") == "failed"


def test_counts_hear_only_the_finished_row(env, monkeypatch):
    heard = []
    monkeypatch.setattr(fstore, "_stored_listeners", [lambda ev, ep: heard.append((ev.person_ref, ev.check.get("pending")))])
    asyncio.run(pipe.process(_event(), "Person A", 0.95))
    assert heard == [("persona", None)]


def test_event_without_model_work_is_stored_once_and_final(env):
    asyncio.run(pipe.process({**_event(), "label": "car"}, None, None))
    ev = _row()
    assert ev is not None and "pending" not in ev.check
    assert env["l3_started"] == []


def test_pending_skeleton_is_finished_after_a_restart(env, monkeypatch):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        task = asyncio.create_task(pipe.process(_event(), None, None))
        assert await _until(lambda: env["l3_started"])
        task.cancel()  # the API stopped in the middle of the analysis
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert _row().check.get("pending") == "analysis"
        env["gate"] = None
        n = await pipe.recover_pending(now=T0 + 60)
        assert n == 1
        assert "pending" not in _row().check
    asyncio.run(main())


# ── item 3: no conclusion on half an episode ────────────────────────────────


def _settings_for_conclude(monkeypatch, env):
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": True, "episode_gap_s": 120, "conclude": {
        "idle_gate_s": 0, "max_attempts": 3, "poll_s": 30, "urgent_classes": [], "urgent_labels": [],
        "urgent_when_away_person": False}})
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)

    async def fake_conclude(ep, now=None):
        events = await asyncio.to_thread(fstore.episode_events, ep.episode_id)
        env["concluded"].append((ep.episode_id, now, sum(1 for e in events if e.check.get("pending"))))
        await asyncio.to_thread(fstore.conclude_episode, ep.episode_id, {"status": "done", "summary": "x"})
        return True

    monkeypatch.setattr(conclude, "_conclude_episode", fake_conclude)
    monkeypatch.setattr(conclude, "_merge_split_episodes", _no_merge, raising=False)


async def _no_merge(now):
    return 0


def test_conclusion_waits_while_an_event_of_the_episode_is_pending(env, monkeypatch):
    _settings_for_conclude(monkeypatch, env)

    async def main():
        env["gate"] = asyncio.Semaphore(0)
        task = asyncio.create_task(pipe.process(_event(), None, None))
        assert await _until(lambda: env["l3_started"])
        await conclude.run_once(now=T0 + 400)  # long quiet by the clock: closed, but its event is not analysed
        assert env["concluded"] == []
        env["gate"].release()
        await task
        await conclude.run_once(now=T0 + 430)
        assert len(env["concluded"]) == 1
    asyncio.run(main())


# ── item 5: the burst ───────────────────────────────────────────────────────


def test_burst_of_50_person_events_with_a_slow_l3_is_one_episode(env, monkeypatch):
    """~50 person events over 5 minutes, two cameras, each 8 s, ending 6 s apart. Every L3 «takes 10 s» (virtual
    clock: one answer per 10 s tick), so the analysis queue runs ~8 minutes behind — Halloween's shape. The conclude
    loop runs every tick on the virtual clock. Before s26: the first events are stored, the episode closes on the
    clock, every later batch opens a new episode → many overlapping episodes, many conclusions."""
    _settings_for_conclude(monkeypatch, env)
    n, spacing, l3_s = 50, 6.0, 10.0
    ends = {}
    for i in range(n):
        start = T0 + i * spacing
        env["times"][f"b{i:02d}"] = (start, start + 8)
        ends[f"b{i:02d}"] = start + 8

    async def main():
        env["gate"] = asyncio.Semaphore(0)
        tasks = []
        clock = T0
        arrived = 0
        released = 0
        while True:
            clock += 2.0
            while arrived < n and ends[f"b{arrived:02d}"] <= clock:  # Frigate's end events, in time order
                ev = _event(f"b{arrived:02d}", camera="inngang" if arrived % 2 else "oppkjorsel", duration=8)
                tasks.append(asyncio.create_task(pipe.on_end_event(ev, None, None)))
                arrived += 1
            if int(clock - T0) % int(l3_s) == 0 and released < n:
                env["gate"].release()  # one L3 answer per 10 s
                released += 1
            await asyncio.sleep(0.01)
            await conclude.run_once(now=clock)
            if arrived == n and all(t.done() for t in tasks) and env["concluded"]:
                break
            assert clock < T0 + 3600, "the burst never drained"
        await asyncio.gather(*tasks)
        for _ in range(5):
            clock += 30
            await conclude.run_once(now=clock)
        return clock

    asyncio.run(main())
    eps = fstore.episodes_between(T0 - 3600, T0 + 7200)
    assert len(eps) == 1, f"one visit became {len(eps)} episodes"
    assert eps[0].event_count == n
    assert len(env["concluded"]) == 1
    assert env["concluded"][0][2] == 0, "concluded while events were still waiting for their analysis"
    assert all("pending" not in e.check for e in fstore.episode_events(eps[0].episode_id))
