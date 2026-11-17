"""P144-s28 item 0 — urgent events go first in the 9B queue (phase 2, one at a time).

A burst of normal person events is waiting behind the lock when an urgent one arrives: it must be analysed next, not
last. What counts as urgent is unchanged (conclude.is_urgent: a not-welcome face, a person while away, the urgent
labels). Same order among equals (first come, first served). Never touches live state (tmp DBs, fake Frigate/LLM).
"""

import asyncio
import io
import json

import pytest
from PIL import Image

from kaare_core.frigate_user import conclude, faces, pipe
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0
BIG_FACE = [{"label": "face", "box": [0.4, 0.2, 0.2, 0.3], "score": 0.8}]
L3_REPLY = {"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult", "headwear": "no",
            "description": "a person", "analysis": "someone at the door"}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (960, 720), (180, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text("faces:\n  Person C: {class: not_welcome}\n")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    state = {"order": [], "gate": None, "away": False}

    async def fake_get_event(event_id):
        return {"id": event_id, "start_time": T0, "end_time": T0 + 5, "data": {"attributes": BIG_FACE}}

    async def fake_snapshot(event_id):
        return _jpeg()

    async def fake_llm(role, prompt, images=None, rid=""):
        state["order"].append(rid.removeprefix("rid-frigate-shadow-"))
        await state["gate"].acquire()
        return {"ok": True, "text": json.dumps(L3_REPLY), "meta": {}}

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", fake_get_event)
    monkeypatch.setattr(pipe, "_load_snapshot", fake_snapshot)
    monkeypatch.setattr(pipe, "ask_llm_role", fake_llm)
    monkeypatch.setattr(pipe, "_place_of", lambda camera: None)
    monkeypatch.setattr(pipe, "_urgent_handler", None)
    monkeypatch.setattr(pipe, "_lock", pipe._Queue())  # a fresh queue per test (each test has its own event loop)
    monkeypatch.setattr(conclude, "_is_away", lambda: state["away"])
    monkeypatch.setattr(pipe.household_state, "is_away", lambda: state["away"])
    yield state
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _event(event_id: str) -> dict:
    return {"event_id": event_id, "camera": "inngang", "label": "person", "score": 0.9, "top_score": 0.9,
            "duration": 5.0, "has_snapshot": True}


async def _settle(n=30):
    for _ in range(n):
        await asyncio.sleep(0.005)


async def _arrive(*items) -> list[asyncio.Task]:
    """Start the events one after the other (phase 1 runs in a thread: in one go they could reach the queue in any order)."""
    tasks = []
    for ev, name in items:
        tasks.append(asyncio.create_task(pipe.process(ev, name, 0.95 if name else None)))
        await _settle()
    return tasks


async def _burst_then(env, last_event, last_name):
    env["gate"] = asyncio.Semaphore(0)
    tasks = await _arrive(*((_event(f"n{i}"), None) for i in range(4)))
    assert env["order"] == ["n0"], "one at a time: the first normal event is with the 9B, the rest queue"
    tasks.append(asyncio.create_task(pipe.process(last_event, last_name, 0.95)))
    await _settle()
    for _ in range(len(tasks)):
        env["gate"].release()
        await _settle()
    await asyncio.gather(*tasks)
    return env["order"]


def test_not_welcome_face_is_analysed_next_not_last(env):
    order = asyncio.run(_burst_then(env, _event("urgent"), "Person C"))
    assert order == ["n0", "urgent", "n1", "n2", "n3"]


def test_person_while_away_goes_before_events_queued_earlier(env):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        tasks = await _arrive(*((_event(f"n{i}"), None) for i in range(3)))
        env["away"] = True  # the house just left: the next person event is urgent, those already queued keep their place
        tasks.append(asyncio.create_task(pipe.process(_event("away"), None, None)))
        await _settle()
        for _ in tasks:
            env["gate"].release()
            await _settle()
        await asyncio.gather(*tasks)
    asyncio.run(main())
    assert env["order"] == ["n0", "away", "n1", "n2"]


def test_normal_events_keep_first_come_first_served(env):
    order = asyncio.run(_burst_then(env, _event("n4"), None))
    assert order == ["n0", "n1", "n2", "n3", "n4"]


def test_two_urgent_events_keep_their_own_order(env):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        tasks = await _arrive(*((_event(f"n{i}"), None) for i in range(2)))
        tasks.append(asyncio.create_task(pipe.process(_event("u1"), "Person C", 0.9)))
        await _settle()
        tasks.append(asyncio.create_task(pipe.process(_event("u2"), "Person C", 0.9)))
        await _settle()
        for _ in tasks:
            env["gate"].release()
            await _settle()
        await asyncio.gather(*tasks)
    asyncio.run(main())
    assert env["order"] == ["n0", "u1", "u2", "n1"]


def test_cancelled_waiter_does_not_block_the_queue(env):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        tasks = await _arrive(*((_event(f"n{i}"), None) for i in range(3)))
        tasks[1].cancel()
        await _settle()
        env["gate"].release()
        env["gate"].release()
        await asyncio.gather(tasks[0], tasks[2])
    asyncio.run(main())
    assert env["order"] == ["n0", "n2"]


def test_fire_never_waits_for_the_queue(env):
    async def main():
        env["gate"] = asyncio.Semaphore(0)
        tasks = await _arrive(*((_event(f"n{i}"), None) for i in range(2)))
        result = await asyncio.wait_for(pipe.process({**_event("fire1"), "label": "fire"}, None, None), timeout=2)
        assert result is not None and result["stored"] is True
        env["gate"].release()
        await _settle()
        env["gate"].release()
        await asyncio.gather(*tasks)
    asyncio.run(main())
