"""N40 point 2 — an episode that «henger» (stuck) past a GUI limit says so honestly and gets «Analyser på nytt».

- the limit is a setting: `conclude.stuck_after_min`, default 30, 5–1440 (rule 25);
- `conclude.stuck_info(ep, now)` says only what code knows: not closed (open, quiet past the limit), queued (no try
  yet), waiting for the image model (attempts, next try), failed (attempts), or running (analysis started HH:MM);
  a fresh episode, or a done one, is not stuck;
- the episode list carries it; the «Analyser» endpoint and `reanalyse` accept a stuck open episode (it is closed first),
  never a fresh open one.
Tmp files only (s06b fixture). Placeholders only."""

import asyncio
import time

import pytest
from fastapi import HTTPException

from kaare_core.frigate_user import conclude, frames
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from tests.test_p144_s06b_episode_conclusion import (  # noqa: F401  (env is a fixture)
    _ev,
    _settings,
    env,
)

MIN = 60


@pytest.fixture
def limits(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    monkeypatch.setattr(conclude, "_settings", lambda: _settings(stuck_after_min=30))
    return env


def _episode(event_id, start, camera="inngang"):
    assert fstore.record_event(_ev(event_id, camera=camera, start=start))
    return fstore.episode(fstore.event_by_id(event_id).episode_id)


def test_setting_has_a_default_and_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    assert fsettings.get_settings()["conclude"]["stuck_after_min"] == 30
    assert fsettings.LIMITS["conclude.stuck_after_min"] == (5, 1440)
    with pytest.raises(ValueError):
        fsettings.update({"conclude": {"stuck_after_min": 2}})


def test_fresh_and_done_episodes_are_not_stuck(limits):
    now = time.time()
    ep = _episode("f1", now - 5 * MIN)
    assert conclude.stuck_info(ep, now) is None
    old = _episode("d1", now - 120 * MIN, camera="garasje")
    fstore.close_episodes(now, 120)
    fstore.conclude_episode(old.episode_id, {"status": "done", "summary": "x"})
    assert conclude.stuck_info(fstore.episode(old.episode_id), now) is None


def test_open_past_the_limit_is_not_closed(limits):
    now = time.time()
    ep = _episode("o1", now - 45 * MIN)
    info = conclude.stuck_info(ep, now)
    assert info["kind"] == "not_closed" and info["minutes"] >= 44


def test_closed_without_a_conclusion_is_queued_waiting_failed_or_running(limits):
    now = time.time()
    ep = _episode("c1", now - 60 * MIN)
    fstore.close_episodes(now, 120)
    ep = fstore.episode(ep.episode_id)
    assert conclude.stuck_info(ep, now)["kind"] == "queued"
    fstore.conclude_episode(ep.episode_id, {"status": conclude.WAITING, "attempts": 2, "next_try_ts": now + 300})
    info = conclude.stuck_info(fstore.episode(ep.episode_id), now)
    assert info["kind"] == "waiting" and info["attempts"] == 2 and info["next_try_ts"] == now + 300
    fstore.conclude_episode(ep.episode_id, {"status": "failed", "attempts": 3})
    assert conclude.stuck_info(fstore.episode(ep.episode_id), now)["kind"] == "failed"
    conclude._current.update(episode_id=ep.episode_id, started_ts=now - 20 * MIN)
    try:
        info = conclude.stuck_info(fstore.episode(ep.episode_id), now)
        assert info["kind"] == "running" and info["started_ts"] == now - 20 * MIN
    finally:
        conclude._current.clear()


def test_episode_list_carries_stuck(limits):
    now = time.time()
    _episode("o1", now - 45 * MIN)
    items = rfu._list_view(10, None)["episodes"]
    assert items[0]["stuck"]["kind"] == "not_closed"


def test_analyse_accepts_a_stuck_open_episode_and_closes_it(limits, monkeypatch):
    now = time.time()
    stuck = _episode("o1", now - 45 * MIN)
    fresh = _episode("o2", now - 1 * MIN, camera="garasje")
    monkeypatch.setattr(frames, "kept", lambda ep: [])

    async def none_fetched(ep, events, sem):
        return [], 0, 0

    monkeypatch.setattr(frames, "ensure", none_fetched)
    res = asyncio.run(conclude.reanalyse(stuck.episode_id))
    assert res == {"ok": False, "reason": "no_frames"}  # it got past «episode_open»
    assert fstore.episode(stuck.episode_id).status == "closed"
    assert asyncio.run(conclude.reanalyse(fresh.episode_id))["reason"] == "episode_open"
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(rfu.api_analyse_episode(fresh.episode_id, u={"sub": "boss"}))
    assert e.value.detail == "episode_open"
