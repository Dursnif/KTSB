"""P144 / run-queue J15 — ep 266: the bins were moved, the pixels changed on two cameras, but the 27B only got a position.

(1) after frames: candidates end +5/+15/+30/+60/+120 s; the first two without an active track on that camera are used
    (detect() already requires the change in both); (2) a changed region over where a non-living stationary thing stood
    (J3 key, before the episode) — or where one stands now — becomes a code fact for the 27B with the label, «sett på N
    kameraer», «står nå et annet sted»; the 9B never gets a label; (3) the after frame is kept for the GUI, never in the
    frames a model re-reads. Data shaped on ep 266 (frigate.db copy); placeholders, synthetic boxes."""

from kaare_core.frigate_user import conclude, frames, thing_changes
from kaare_core.frigate_user import store as fstore
from kaare_core.frigate_user.episode_state import Track

T1 = 1_800_000_000.0


def _track(camera, start, end):
    return Track(f"{camera}-{start}", camera, "person", T1 + start, T1 + end)


def test_after_times_skip_moments_with_an_active_track():
    tracks = [_track("cam_e", -60, 20)]  # someone still in view until end + 20 s
    assert frames.quiet_after_times(T1, "cam_e", tracks) == [T1 + 30, T1 + 60]
    assert frames.quiet_after_times(T1, "cam_w", tracks) == [T1 + 5, T1 + 15]
    assert frames.quiet_after_times(T1, "cam_e", [_track("cam_e", -60, 500)]) == []  # never quiet → no after frames


def _gate_ev(eid, cam, key, ts, label="waste_bin"):
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label=label, start_ts=ts,
                                   check={"gate": {"verdict": "unchanged", "key": key, "thing_id": None}})


EP266_REGIONS = [{"camera": "cam_e", "box": [0.07, 0.275, 0.155, 0.425], "cells": 14, "where": "pos_left"},
                 {"camera": "cam_w", "box": [0.296, 0.1, 0.338, 0.2], "cells": 6, "where": "pos_top"}]
BEFORE = [_gate_ev("b1", "cam_e", "cam_e|waste_bin|0,4", T1 - 3600)]
AFTER = [_gate_ev("a1", "cam_w", "cam_w|waste_bin|3,1", T1 + 1700), _gate_ev("a2", "cam_e", "cam_e|car|8,8", T1 + 60,
                                                                            label="car")]


def test_ep266_regions_become_thing_facts():
    regions = [dict(r) for r in EP266_REGIONS]
    thing_changes.annotate(regions, BEFORE, AFTER)
    e, w = regions
    assert e["thing_before"] == "waste_bin" and e["thing_cameras"] == 2 and e.get("thing_elsewhere") == "cam_w"
    assert w["thing_after"] == "waste_bin" and w["thing_cameras"] == 2
    assert "thing_before" not in w


def test_unrelated_region_stays_a_position():
    regions = [{"camera": "cam_e", "box": [0.7, 0.7, 0.8, 0.8], "cells": 5, "where": "pos_right"}]
    thing_changes.annotate(regions, BEFORE, AFTER)
    assert not any(k.startswith("thing") for k in regions[0])


def test_the_27b_line_names_the_thing_and_the_cameras():
    regions = [dict(r) for r in EP266_REGIONS]
    thing_changes.annotate(regions, BEFORE, AFTER)
    line = conclude._change_line(regions[0], {}, "nb")
    assert "søppeldunk" in line and "sto" in line and "2 kameraer" in line and "et annet sted" in line
    assert "står nå" in conclude._change_line(regions[1], {}, "nb")


def test_after_frames_are_never_in_the_frames_a_model_rereads(monkeypatch, tmp_path):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ep = fstore.Episode(episode_id=266, start_ts=T1 - 80, end_ts=T1, cameras=["cam_e"], labels=["person"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None,
                        scene={"frames": [], "changes": {"after_frames": [{"camera": "cam_e", "ts": T1 + 30, "file": 100}]}})
    assert frames.kept(ep) == []
    fstore.reset_for_tests()
