"""P144-s26 items 3 and 4 — never close an episode while its activity is still on its way; merge the ones that split.

- `store.close_episodes(hold=…)`: activity not stored yet (an end event being written, a track Frigate still has open)
  keeps a quiet episode open — the attach rule's own test — at most `HOLD_MAX_S` past its end.
- `pipe.unstored_activity()`: a moving open track holds, a parked car / an unticked label / a camera with episodes off
  holds nothing, Frigate unreachable = no hold (the old behaviour).
- `store.split_groups` + `store.merge_episodes`: chains of closed episodes within the gap become one; every old
  conclusion is kept as history, urgent results stay, absorbed rows are kept (status `merged`) and hidden from readers.
- `frames.repick_merged`: kept frames reused, the rest fetched, new file numbers (old files stay).
- `conclude._merge_split_episodes`: merges, re-picks frames, the survivor waits for its analysis; counts, STM and the
  LTM day follow.
Never touches live state (tmp DBs, fake Frigate).
"""

import asyncio

import pytest

from kaare_core.frigate_user import conclude, counts, frames, pipe, stm
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore

T0 = 1_800_000_000.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    fstore.reset_for_tests()
    getattr(pipe, "_arrivals", {}).clear()
    yield tmp_path
    fstore.reset_for_tests()


def _put(event_id, start, end, camera="inngang", label="person", **kw):
    assert fstore.record_event(fstore.FrigateUserEvent(event_id=event_id, camera=camera, label=label, start_ts=start,
                                                       end_ts=end, **kw))
    return fstore.event_by_id(event_id)


# ── item 3: the close rule ───────────────────────────────────────────────────


def test_hold_inside_the_gap_keeps_the_episode_open(db):
    ep_id = _put("a", T0, T0 + 10).episode_id
    closed = fstore.close_episodes(now=T0 + 200, gap_s=120, hold=[(T0 + 90, T0 + 195)])  # a track still moving
    assert closed == []
    assert fstore.episode(ep_id).status == "open"
    late = _put("b", T0 + 90, T0 + 195)  # its end event comes: it joins, no new episode
    assert late.episode_id == ep_id


def test_hold_far_away_does_not_keep_it(db):
    ep_id = _put("a", T0, T0 + 10).episode_id
    fstore.close_episodes(now=T0 + 200, gap_s=120, hold=[(T0 + 500, T0 + 600)])
    assert fstore.episode(ep_id).status == "closed"


def test_hold_never_longer_than_hold_max(db):
    ep_id = _put("a", T0, T0 + 10).episode_id
    now = T0 + 10 + 120 + fstore.HOLD_MAX_S + 1
    fstore.close_episodes(now=now, gap_s=120, hold=[(T0 + 5, now)])  # someone standing at the door for ages
    assert fstore.episode(ep_id).status == "closed"


def test_without_hold_the_clock_closes_as_before(db):
    ep_id = _put("a", T0, T0 + 10).episode_id
    fstore.close_episodes(now=T0 + 200, gap_s=120)
    assert fstore.episode(ep_id).status == "closed"


def _track(camera="inngang", label="person", start=T0, path=None):
    return {"id": "t1", "camera": camera, "label": label, "start_time": start, "end_time": None,
            "data": {"top_score": 0.9, "path_data": path}}


def test_open_tracks_become_holds(db, monkeypatch):
    moving = [[[0.1, 0.5], T0 + 100], [[0.4, 0.5], T0 + 150], [[0.7, 0.5], T0 + 190]]
    parked = [[[0.5, 0.5], T0 + 10], [[0.501, 0.5], T0 + 150]]
    tracks = [_track(path=moving), _track(label="car", path=parked)]

    async def fake():
        return tracks

    monkeypatch.setattr(pipe.frigate_adapter, "fetch_in_progress_events", fake)
    monkeypatch.setattr(pipe, "_drive", lambda camera, label, score, kind, plate: ("normal", True, "selected"))
    holds = asyncio.run(pipe.unstored_activity(now=T0 + 200))
    assert holds == [(T0 + 100, T0 + 190)]  # the walking person's burst; the parked car holds nothing


def test_unticked_label_holds_nothing(db, monkeypatch):
    async def fake():
        return [_track(label="dog")]

    monkeypatch.setattr(pipe.frigate_adapter, "fetch_in_progress_events", fake)
    monkeypatch.setattr(pipe, "_drive", lambda camera, label, score, kind, plate: ("none", False, "label_not_selected"))
    assert asyncio.run(pipe.unstored_activity(now=T0 + 200)) == []


def test_frigate_down_means_no_hold(db, monkeypatch):
    async def down():
        raise OSError("no route")

    monkeypatch.setattr(pipe.frigate_adapter, "fetch_in_progress_events", down)
    assert asyncio.run(pipe.unstored_activity(now=T0 + 200)) == []


def test_an_arrival_being_written_holds_every_open_episode(db, monkeypatch):
    async def none():
        return []

    monkeypatch.setattr(pipe.frigate_adapter, "fetch_in_progress_events", none)
    pipe._arrivals["x"] = __import__("time").time()
    try:
        holds = asyncio.run(pipe.unstored_activity(now=T0 + 200))
    finally:
        pipe._arrivals.clear()
    ep_id = _put("a", T0, T0 + 10).episode_id
    fstore.close_episodes(now=T0 + 200, gap_s=120, hold=holds)
    assert fstore.episode(ep_id).status == "open"


# ── item 4: merging ─────────────────────────────────────────────────────────


def _split_pair():
    """Two closed episodes 60 s apart (inside the 120 s gap) — the second one opened after the first closed."""
    a = _put("a1", T0, T0 + 30).episode_id
    fstore.close_episodes(now=T0 + 200, gap_s=120)
    b = _put("b1", T0 + 90, T0 + 120).episode_id
    fstore.close_episodes(now=T0 + 400, gap_s=120)
    assert a != b
    fstore.conclude_episode(a, {"status": "done", "summary": "Jeg så gjest A ved døra.",
                                "urgent": [{"event_id": "a1", "status": "done"}]})
    fstore.set_episode_scene(a, {"status": "done", "frames": [{"camera": "inngang", "ts": T0 + 5, "file": 0}]}, "r")
    fstore.conclude_episode(b, {"status": "done", "summary": "Jeg så noen gå ned til veien.",
                                "history": [{"replaced_ts": 1, "conclusion": {"summary": "eldre"}, "scene": {}}]})
    return a, b


def test_split_groups_finds_the_chain_and_skips_open_and_pending(db):
    a, b = _split_pair()
    _put("far", T0 + 2000, T0 + 2010)  # an open episode far away
    assert fstore.split_groups(T0 - 10, T0 + 3000, gap_s=120) == [[a, b]]
    _put("p1", T0 + 100, T0 + 110, check={"pending": "analysis"})
    fstore.close_episodes(now=T0 + 3000, gap_s=120)
    busy = fstore.event_by_id("p1").episode_id
    assert all(busy not in g for g in fstore.split_groups(T0 - 10, T0 + 1000, gap_s=120))


def test_merge_moves_events_keeps_history_and_hides_the_absorbed(db):
    a, b = _split_pair()
    out = fstore.merge_episodes([b, a], scene={"status": "waiting", "reason": "merged"}, now=T0 + 500)
    assert out["survivor"] == a and out["absorbed"] == [b]
    assert out["frames"][a] == [{"camera": "inngang", "ts": T0 + 5, "file": 0}]
    ep = fstore.episode(a)
    assert ep.event_count == 2 and ep.start_ts == T0 and ep.end_ts == T0 + 120
    hist = ep.conclusion["history"]
    summaries = [h["conclusion"].get("summary") for h in hist]
    assert "Jeg så gjest A ved døra." in summaries and "Jeg så noen gå ned til veien." in summaries
    assert "eldre" in summaries  # the absorbed episode's own earlier history is kept too
    assert {h.get("merged_from") for h in hist if "merged_from" in h} == {a, b}
    assert ep.conclusion["urgent"] == [{"event_id": "a1", "status": "done"}]
    assert ep.conclusion.get("status") is None and ep.scene == {"status": "waiting", "reason": "merged"}
    absorbed = fstore.episode(b)
    assert absorbed.status == fstore.MERGED and absorbed.conclusion["merged_into"] == a
    assert absorbed.conclusion["summary"] == "Jeg så noen gå ned til veien."  # kept, never deleted
    assert [e.episode_id for e in fstore.episodes_between(T0 - 10, T0 + 1000)] == [a]
    assert [e.episode_id for e, _ in fstore.episodes_page()] == [a]
    assert [e.episode_id for e, _ in fstore.episodes_window(T0 - 10, T0 + 1000)] == [a]
    assert fstore.split_groups(T0 - 10, T0 + 1000, gap_s=120) == []


def test_merge_refuses_an_open_episode(db):
    a = _put("a1", T0, T0 + 30).episode_id
    fstore.close_episodes(now=T0 + 200, gap_s=120)
    b = _put("b1", T0 + 300, T0 + 310).episode_id
    assert fstore.merge_episodes([a, b]) is None
    assert fstore.event_by_id("b1").episode_id == b


def test_repick_reuses_kept_frames_and_fetches_the_rest(db, monkeypatch):
    a, b = _split_pair()
    fstore.save_episode_frames(a, [b"old-a0"], [0])
    fetched = []

    async def fake_one(ts, camera):
        fetched.append((round(ts, 1), camera))
        return frames.Frame(ts=ts, camera=camera, jpeg=f"new-{ts:.0f}".encode())

    monkeypatch.setattr(frames, "_one", fake_one)
    fstore.merge_episodes([a, b], scene={"status": "waiting", "reason": "merged"})
    ep = fstore.episode(a)
    events = fstore.episode_events(a)
    points = frames.pick(frames.candidates(events, (ep.start_ts, ep.end_ts)), events=events)
    near = min(points, key=lambda p: abs(p[0] - (T0 + 5)))
    parts = {a: [{"camera": "inngang", "ts": near[0] + 0.5, "file": 0}], b: []}
    listing, reused, n_fetched, missing = asyncio.run(frames.repick_merged(ep, events, parts))
    assert reused == 1 and n_fetched == len(points) - 1 and missing == 0
    assert all(f["file"] >= frames.MERGED_FILE_BLOCK for f in listing)
    assert [f["ts"] for f in listing] == sorted(f["ts"] for f in listing)
    reused_entry = next(f for f in listing if f.get("from_episode") == a)
    assert fstore.read_episode_frame(a, reused_entry["file"]) == b"old-a0"
    assert fstore.read_episode_frame(a, 0) == b"old-a0"  # the survivor's own earlier file stays


def test_conclude_merge_step_merges_and_the_survivor_waits_for_analysis(db, monkeypatch):
    a, _b = _split_pair()
    calls = {"counts": 0, "stm": 0, "ltm": []}
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": True, "episode_gap_s": 120, "conclude": {}})
    monkeypatch.setattr(counts, "rebuild", lambda *a, **k: calls.__setitem__("counts", calls["counts"] + 1))
    monkeypatch.setattr(stm, "refresh", lambda *a, **k: calls.__setitem__("stm", calls["stm"] + 1))
    monkeypatch.setattr(conclude, "_reopen_ltm", lambda i: calls["ltm"].append(i) or "not_in_ltm")
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")

    async def fake_one(ts, camera):
        return frames.Frame(ts=ts, camera=camera, jpeg=b"x")

    monkeypatch.setattr(frames, "_one", fake_one)
    assert asyncio.run(conclude._merge_split_episodes(T0 + 600)) == 1
    ep = fstore.episode(a)
    assert ep.scene["status"] == conclude.WAITING and ep.scene["reason"] == "merged" and ep.scene["frames"]
    assert calls == {"counts": 1, "stm": 1, "ltm": [a]}
    assert [e.episode_id for e in conclude._pending(T0 + 600, 3)] == [a]  # the loop concludes it next, once
    assert asyncio.run(conclude._merge_split_episodes(T0 + 700)) == 0  # nothing left to merge
