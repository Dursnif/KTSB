"""N40 point 1 — closing an episode is pure code and never waits for the 27B.

Observed 2026-11-16 (episode 455): `conclude.run_once` closed quiet episodes and concluded them in one loop; while a
conclusion waited for the 27B (lent out), no new episode was closed, so the GUI showed «pågår» for ~13 min. Now the
close step runs on its own loop. Red first: the 27B «hangs» in a conclusion, a new episode goes quiet meanwhile — it must
still be closed. And no conclusion runs twice at once (the `_analysing` lock). Tmp files only (s06b fixture)."""

import asyncio
import time

from kaare_core.frigate_user import conclude, counts, pipe, stm
from kaare_core.frigate_user import store as fstore
from tests.test_p144_s06b_episode_conclusion import _ev, env  # noqa: F401  (env is a fixture)


def _status(event_id):
    ep_id = fstore.event_by_id(event_id).episode_id
    return fstore.episode(ep_id).status


def test_a_hanging_conclusion_does_not_stop_closing(env, monkeypatch):  # noqa: F811  (fixture imported above)
    gate = asyncio.Event()
    started = []

    async def hanging(ep, now):  # the 27B never answers
        started.append(ep.episode_id)
        await gate.wait()
        return False

    monkeypatch.setattr(conclude, "_conclude_episode", hanging)
    monkeypatch.setattr(conclude, "_STARTUP_DELAY_S", 0)
    monkeypatch.setattr(conclude, "_poll_s", lambda: 0.05)
    monkeypatch.setattr(counts, "rebuild", lambda *a, **k: None)
    monkeypatch.setattr(stm, "rebuild", lambda *a, **k: None)

    async def no_backfill():
        return None

    async def no_activity(now=None):
        return []

    monkeypatch.setattr(conclude, "_backfill_loop", no_backfill)
    monkeypatch.setattr(pipe, "unstored_activity", no_activity)
    old = time.time() - 3600
    assert fstore.record_event(_ev("a1", start=old))  # quiet long ago: closes at once, its conclusion will hang

    async def scenario():
        loop = asyncio.create_task(conclude.start_conclude_loop())
        for _ in range(1000):  # up to 20 s on a loaded machine; ends as soon as it happens
            await asyncio.sleep(0.02)
            if started:
                break
        assert started, "the conclusion never started"
        # while the 27B hangs, a new situation happens on another camera and goes quiet
        assert fstore.record_event(_ev("b1", camera="garasje", start=time.time() - 1800))
        for _ in range(1000):
            await asyncio.sleep(0.02)
            if _status("b1") != "open":
                break
        closed = _status("b1")
        gate.set()
        loop.cancel()
        for t in list(conclude._background):
            t.cancel()
        return closed

    assert asyncio.run(scenario()) == "closed"


def test_no_conclusion_runs_twice_at_once(env, monkeypatch):  # noqa: F811
    running, peak = [0], [0]

    async def slow(ep, now):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.05)
        running[0] -= 1
        return True

    monkeypatch.setattr(conclude, "_conclude_episode", slow)
    old = time.time() - 3600
    assert fstore.record_event(_ev("a1", start=old))
    assert fstore.record_event(_ev("b1", camera="garasje", start=old + 600))

    async def scenario():
        await asyncio.gather(conclude.run_once(), conclude.run_once(), conclude.conclude_round())

    asyncio.run(scenario())
    assert peak[0] == 1
