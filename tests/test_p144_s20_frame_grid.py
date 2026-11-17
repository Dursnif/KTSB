"""P144-s20 item 3 — even frame grid, clipped to the episode window (Bug «frame choice leaves gaps …», s18).

Measured (s20 log, m3): on 96 real episodes today's path-point candidates left 57 episodes under 9 frames, frames up to
72 s after the episode on 7 (non-driving cameras), ep 87 a 41 s hole and ep 89 (a person standing still after 4 s) 2
frames. Even candidates over each event + one frame per person/animal event + the window clip: 44 under 9, 0 outside,
ep 87 hole 20 s, ep 89 6 frames, +67 Frigate fetches over 96 episodes; 9B (8 episodes × 2 runs): person found 16/16 both
ways, scene text 146 vs 87 chars, look/scene time +0.2/+0.1 s.
  - a person/animal is shown over its whole event (standing still is still there), a vehicle over its active window
    (a parked car's hours are not activity);
  - candidates every ~MIN_SPACING_S over each event, plus Frigate's path points;
  - a non-driving event (another camera's object that moved) is clipped to the episode window;
  - pick: first point per camera, then one point inside each person/animal event not yet shown (leaving room for 3
    spread frames), then the even time targets.
"""

from itertools import pairwise

from kaare_core.frigate_user import frames, store

T0 = 1_793_300_000.0


def _ev(eid, camera, start, end, label="person", drives=True, active=None, path=None):
    a0, a1 = active or (None, None)
    return store.FrigateUserEvent(event_id=eid, camera=camera, label=label, start_ts=start, end_ts=end, drives=drives,
                                  active_start=a0, active_end=a1, path=[[0.5, 0.5, t] for t in (path or [])])


def test_a_person_standing_still_gets_frames():  # ep 89: active 0–4 s, stood 7 s more
    ev = _ev("p", "deck", T0, T0 + 11.3, active=(T0, T0 + 4.0), path=[T0 + 0.1, T0 + 2.8])
    picked = frames.pick(frames.candidates([ev], (T0, T0 + 4.0)), events=[ev])
    assert len(picked) >= 4
    assert max(t for t, _ in picked) > T0 + 6


def test_clustered_path_leaves_no_big_hole():  # ep 87: path points bunched at the start and the end
    ev = _ev("p", "porch", T0, T0 + 65, path=[T0 + x for x in (0.2, 0.9, 1.6, 2.4, 3.0, 60.5, 62.0, 63.4, 64.5)])
    picked = frames.pick(frames.candidates([ev], (T0, T0 + 65)), events=[ev])
    ts = [T0] + [t for t, _ in picked] + [T0 + 65]
    assert max(b - a for a, b in pairwise(ts)) <= 20
    assert all(b - a > frames.MIN_SPACING_S for a, b in pairwise([t for t, _ in picked]))


def test_no_frame_after_the_episode_from_a_non_driving_camera():  # ep 20/24/57/68/75/92
    door = _ev("p", "door", T0, T0 + 30)
    road = _ev("c", "road", T0 + 20, T0 + 100, label="car", drives=False, active=(T0 + 20, T0 + 100),
               path=[T0 + 25, T0 + 90])
    picked = frames.pick(frames.candidates([door, road], (T0, T0 + 30)), events=[door, road])
    assert all(t <= T0 + 30 for t, _ in picked)
    assert {"door", "road"} <= {c for _, c in picked}


def test_every_person_event_gets_a_frame_when_there_is_room():
    long = _ev("long", "drive", T0, T0 + 120)
    short = [_ev(f"s{i}", f"cam{i % 2}", T0 + 30 + 20 * i, T0 + 31 + 20 * i) for i in range(4)]
    evs = [long, *short]
    picked = frames.pick(frames.candidates(evs, (T0, T0 + 120)), events=evs)
    for e in evs:
        assert any(e.start_ts - 0.5 <= t <= e.end_ts + 0.5 and c == e.camera for t, c in picked), e.event_id
    assert len(picked) <= frames.N_FRAMES


def test_no_frame_before_the_episode_starts():  # ep 84/90: a person sat still on the porch minutes before
    sitting = _ev("p", "door", T0 - 170, T0 + 7, active=(T0, T0 + 7))
    picked = frames.pick(frames.candidates([sitting], (T0, T0 + 5)), events=[sitting])
    assert picked and all(t >= T0 for t, _ in picked)


def test_a_parked_vehicle_is_still_its_active_window_only():
    car = _ev("car", "drive", T0, T0 + 3000, label="car", active=(T0, T0 + 8), path=[T0 + 2, T0 + 6])
    picked = frames.pick(frames.candidates([car], (T0, T0 + 3000)), events=[car])
    assert all(t <= T0 + 8 for t, _ in picked)


def test_without_a_window_nothing_is_clipped():  # callers that pass no window keep working
    ev = _ev("p", "deck", T0, T0 + 11.3, active=(T0, T0 + 4.0))
    assert max(t for t, _ in frames.candidates([ev])) > T0 + 6
