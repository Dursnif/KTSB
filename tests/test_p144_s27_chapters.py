"""P144-s27 items 1–2 — long episodes as chapters (code only) and a frame budget that follows what happens.

Measured on Halloween (ep 115), the walk tests (ep 102), ep 108 and short ones (s27 log). The rule: no being anywhere for a
while starts a chapter; a camera outside the chapter's group starts one; a long stretch without any scene change is its
own calm chapter. Short episodes keep today's grid.
"""

from itertools import pairwise

from kaare_core.frigate_user import chapters as ch
from kaare_core.frigate_user import episode_state as es

T0 = 1_793_300_000.0


def _track(camera, start, end, label="person", box=(0.4, 0.4, 0.1, 0.3)):
    marks = [(T0 + start, "visible", list(box)), (T0 + end, "gone", list(box))]
    return es.Track(f"{camera}-{start}", camera, label, T0 + start, T0 + end, marks)


def _bounds(tracks, dur):
    return [round(ts - T0) for ts, _sig in ch.boundaries(tracks, T0, T0 + dur)]


# ── boundaries from Frigate's tracks ──────────────────────────────────────────


def test_two_visits_with_an_empty_stretch_are_three_chapters():
    """Ep 102: visit 1, nobody for 1:43, visit 2."""
    tracks = [_track("vest", 0, 62), _track("east", 2, 9), _track("vest", 165, 215), _track("east", 170, 180)]
    assert _bounds(tracks, 221) == [62, 165]
    parts = ch.split(tracks, T0, T0 + 221, [])
    assert [p.kind for p in parts] == ["active", "empty", "active"]


def test_a_new_place_starts_a_chapter():
    """Ep 115: up the drive → at the door → back down the drive."""
    tracks = [_track("drive_a", 0, 16), _track("drive_b", 3, 14), _track("door_a", 17, 240), _track("door_b", 18, 256),
              _track("drive_a", 230, 262), _track("drive_b", 231, 265)]
    assert _bounds(tracks, 266) == [17, 230]


def test_a_camera_joining_at_the_start_of_a_chapter_is_the_same_place():
    """Ep 108: doorbell and porch camera see her within a second."""
    tracks = [_track("door_a", 0, 106), _track("door_b", 1, 83)]
    assert _bounds(tracks, 106) == []


def test_a_flicker_on_another_camera_is_not_a_new_place():
    tracks = [_track("door_a", 0, 200), _track("drive_a", 100, 102)]
    assert _bounds(tracks, 200) == []


def test_a_new_camera_soon_after_a_boundary_joins_the_chapter():
    """Ep 115 +241: a third drive camera 11 s after the walk down started."""
    tracks = [_track("door_a", 0, 240), _track("drive_a", 100, 150), _track("drive_b", 108, 150)]
    assert _bounds(tracks, 240) == [100]


def test_very_close_boundaries_become_one():
    """Ep 115 +360/+363: a few seconds of track are no chapter of their own."""
    tracks = [_track("door", 0, 290), _track("door", 360, 363), _track("door", 391, 394)]
    assert _bounds(tracks, 397) == [290, 360, 391] or _bounds(tracks, 397) == [290, 360]


def test_vehicles_alone_make_no_chapters_when_beings_are_there():
    tracks = [_track("door", 0, 100), _track("road", 40, 60, label="car")]
    assert _bounds(tracks, 100) == []


def test_only_vehicles_use_the_vehicles():
    tracks = [_track("road", 0, 20, label="car"), _track("road", 100, 120, label="car")]
    assert _bounds(tracks, 120) == [20, 100]


def test_no_tracks_no_boundaries():
    assert _bounds([], 100) == []


# ── calm inside a chapter ─────────────────────────────────────────────────────


def test_a_long_stretch_without_scene_change_is_a_calm_chapter():
    """Ep 108: out → sits for 88 s without any change → back in."""
    tracks = [_track("door_a", 0, 106), _track("door_b", 1, 83)]
    keys = {"door_a": [(T0 + 10, 516), (T0 + 40, 30), (T0 + 70, 28)], "door_b": [(T0 + 10, 189), (T0 + 98, 103)]}
    parts = ch.split(tracks, T0, T0 + 106, keys)
    assert [p.kind for p in parts] == ["active", "calm", "active"]
    assert [round(p.start - T0) for p in parts] == [0, 10, 98]


def test_movement_all_along_is_one_chapter():
    tracks = [_track("door_a", 0, 100)]
    keys = {"door_a": [(T0 + i, 100) for i in range(5, 100, 10)]}
    assert [p.kind for p in ch.split(tracks, T0, T0 + 100, keys)] == ["active"]


# ── which episodes get chapters ───────────────────────────────────────────────


def test_short_episodes_keep_the_grid():
    assert ch.wants_chapters(T0, T0 + 59, [_track("a", 0, 59)]) is False
    assert ch.wants_chapters(T0, T0 + 61, [_track("a", 0, 61)]) is True
    assert ch.wants_chapters(T0, T0 + 500, []) is False


# ── the scan's keyframes ──────────────────────────────────────────────────────


def _flat(seq):
    """A stand-in for frames: changed() below decides; keyframes takes the compare function."""
    return [(T0 + i * 4, v) for i, v in enumerate(seq)]


def _diff(a, b):
    return 100 if a != b else 0


def test_keyframes_need_the_change_to_persist():
    """A single different frame (headlights, someone passing) is no keyframe."""
    assert ch.keyframes(_flat("aaaXaaaa"), compare=_diff) == []


def test_keyframes_follow_a_lasting_change():
    out = ch.keyframes(_flat("aaabbbbbcccc"), compare=_diff)
    assert [round(ts - T0) for ts, _n in out] == [16, 36]


def test_keyframes_nothing_happens_nothing_chosen():
    assert ch.keyframes(_flat("aaaaaaaa"), compare=_diff) == []


def test_scan_times_are_capped():
    times = ch.scan_times(T0, T0 + 3000)
    assert len(times) <= ch.SCAN_MAX_PER_CAMERA
    assert ch.scan_times(T0, T0 + 40)[1] - T0 == ch.SCAN_STEP_S


# ── the budget ────────────────────────────────────────────────────────────────


def _parts(tracks, dur, keys=None):
    return ch.split(tracks, T0, T0 + dur, keys or {})


def test_budget_empty_chapter_gets_one_frame_and_the_person_gone_frame_is_kept():
    tracks = [_track("vest", 0, 60), _track("vest", 165, 215)]
    picks = ch.budget(_parts(tracks, 221), {}, tracks, T0, T0 + 221)
    times = [round(t - T0) for t, _c in picks]
    empty = [t for t in times if 60 < t < 165]
    assert len(empty) == 1
    assert any(t > 215 for t in times), "the frame where the person is gone (s25: ep 108 #9)"


def test_budget_calm_chapter_gets_few_frames():
    tracks = [_track("door_a", 0, 200)]
    keys = {"door_a": [(T0 + 10, 300), (T0 + 180, 300)]}
    parts = _parts(tracks, 200, keys)
    picks = ch.budget(parts, keys, tracks, T0, T0 + 200)
    calm = next(p for p in parts if p.kind == "calm")
    assert sum(1 for t, _c in picks if calm.start <= t < calm.end) <= ch.CALM_FRAMES


def test_budget_follows_activity_and_keeps_to_the_cap():
    tracks = [_track("door_a", 0, 400), _track("door_b", 1, 400)]
    keys = {"door_a": [(T0 + i, 100 + i) for i in range(4, 400, 8)], "door_b": [(T0 + i + 3, 50) for i in range(4, 400, 8)]}
    picks = ch.budget(_parts(tracks, 400, keys), keys, tracks, T0, T0 + 400)
    assert ch.N_MIN < len(picks) <= ch.CAP_EPISODE
    assert picks == sorted(picks)


def test_budget_shows_every_camera_of_a_chapter():
    tracks = [_track("a", 0, 100), _track("b", 3, 100), _track("c", 5, 100)]
    picks = ch.budget(_parts(tracks, 100), {}, tracks, T0, T0 + 100)
    assert {c for _t, c in picks} >= {"a", "b", "c"}


def test_budget_a_short_long_episode_never_costs_more_than_today():
    tracks = [_track("a", 0, 90), _track("b", 1, 90)]
    keys = {"a": [(T0 + i, 100) for i in range(4, 90, 4)], "b": [(T0 + i + 1, 100) for i in range(4, 90, 4)]}
    assert len(ch.budget(_parts(tracks, 90, keys), keys, tracks, T0, T0 + 90)) <= ch.N_MIN


def test_budget_frames_are_spaced():
    tracks = [_track("a", 0, 200)]
    keys = {"a": [(T0 + i * 0.5, 100) for i in range(1, 400)]}
    times = [t for t, _c in ch.budget(_parts(tracks, 200, keys), keys, tracks, T0, T0 + 200)]
    assert all(b - a >= ch.MIN_SPACING_S for a, b in pairwise(times))


def test_view_of_a_chapter_is_plain_data():
    tracks = [_track("door_a", 0, 62), _track("door_a", 165, 215)]
    parts = _parts(tracks, 221)
    view = ch.to_view(parts)
    assert [v["kind"] for v in view] == ["active", "empty", "active"]
    assert view[1]["start"] == parts[1].start and "signal" in view[1]
    assert ch.chapter_of(view, parts[2].start + 1) == 2


def test_a_long_start_without_any_being_is_an_empty_chapter():
    """Ep 25: cars on the road for 107 s, then someone opens the door. Those 107 s show a road, not the situation."""
    tracks = [_track("road", 0, 50, label="car"), _track("door", 107, 400)]
    parts = _parts(tracks, 400)
    assert [p.kind for p in parts] == ["empty", "active"]
    assert round(parts[1].start - T0) == 107
    picks = ch.budget(parts, {}, tracks, T0, T0 + 400)
    assert sum(1 for t, _c in picks if t < T0 + 107) <= ch.EMPTY_FRAMES


def test_a_track_ending_inside_calm_breaks_it():
    """J5 / R63 (ep 108): she gets up and goes back in, but the scan has no keyframe big enough — the track that ends
    inside the calm stretch breaks it, so «back in» gets an active chapter of its own (and its frames)."""
    tracks = [_track("door_a", 0, 106), _track("door_b", 1, 83)]
    keys = {"door_a": [(T0 + 10, 516), (T0 + 40, 30), (T0 + 70, 28)], "door_b": [(T0 + 10, 189)]}
    parts = ch.split(tracks, T0, T0 + 106, keys)
    assert [p.kind for p in parts] == ["active", "calm", "active"]
    assert round(parts[2].start - T0) == 83 - round(ch.TRACK_END_LEAD_S)


def test_a_track_ending_at_the_episode_end_does_not_split():
    tracks = [_track("door_a", 0, 100)]
    keys = {"door_a": [(T0 + 10, 516)]}
    assert [p.kind for p in ch.split(tracks, T0, T0 + 100, keys)] == ["active", "calm"]
