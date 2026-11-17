"""P151 phase 1: screen core — stream choice, sessions, pairing, settings (pure, no network)."""

import json

import pytest

from kaare_core.voice import screen as sc
from kaare_core.voice.screen import ScreenHub, StreamInfo, choose_stream

# Camera list exactly as T34 measured in this kind of house (names are placeholders).
FRIGATE = {"cameras": {
    "door": {"ffmpeg": {"inputs": [{"path": "rtsp://127.0.0.1:8554/door_1", "roles": ["record"]}]}},
    "yard": {"ffmpeg": {"inputs": [{"path": "rtsp://127.0.0.1:8554/yard_1", "roles": ["record"]}]}},
    "north": {"ffmpeg": {"inputs": [{"path": "rtsp://127.0.0.1:8554/north", "roles": ["record"]}]}},
}}
GO2RTC = ["door_1", "door_2", "yard_1", "yard_2", "yard_10x", "north", "northern_1"]


def test_camera_streams_main_and_sub_siblings():
    assert sc.camera_streams(FRIGATE, "yard", GO2RTC) == ["yard_1", "yard_2"]
    assert sc.camera_streams(FRIGATE, "north", GO2RTC) == ["north"]  # «northern_1» is another camera
    assert sc.camera_streams(FRIGATE, "missing", GO2RTC) == []


def test_choose_direct_largest_h264_that_fits():
    s = [StreamInfo("yard_1", "h264", 2560, 1440), StreamInfo("yard_2", "h264", 640, 360)]
    c = choose_stream(s, 1280, 720)
    assert (c.stream, c.mode, c.width) == ("yard_2", "mse", 640)


def test_choose_reencode_when_only_large_or_hevc():
    hevc = choose_stream([StreamInfo("north", "hevc", 3840, 2160)], 1280, 720)
    assert (hevc.mode, hevc.width, hevc.height) == ("fmp4", 1280, 720)
    big = choose_stream([StreamInfo("y1", "h264", 2304, 1296), StreamInfo("y0", "h264", 3840, 2160)], 1920, 1080)
    assert (big.stream, big.mode, big.width) == ("y1", "fmp4", 1920)  # smallest that still covers the screen


def test_choose_forced_reencode_keeps_size_of_fitting_stream():
    c = choose_stream([StreamInfo("door_2", "h264", 1280, 960)], 1280, 960, force_reencode=True)
    assert (c.mode, c.width, c.reason) == ("fmp4", 1280, "stutter")


def test_choose_nothing_usable():
    assert choose_stream([StreamInfo("x", "h264", 0, 0)], 1280, 720) is None


def test_settings_defaults_and_override():
    assert sc.live_minutes(None, {}) == 2.0
    assert sc.live_minutes(None, {"live_minutes": 5}) == 5.0
    assert sc.live_minutes(10, {"live_minutes": 5}) == 10.0  # Kåre's single-action override wins (D4)
    assert sc.live_minutes(0, {"live_minutes": "junk"}) == 2.0
    assert sc.live_minutes(99999, {}) == sc.MAX_LIVE_MINUTES
    assert sc.enabled({}) is True
    assert sc.enabled({"enabled": False}) is False
    assert sc.enabled({"enabled": "off"}) is False


def test_max_size_from_node():
    assert sc.max_size({}) == (1280, 720)
    assert sc.max_size({"screen_max": "1920x1080"}) == (1920, 1080)
    assert sc.max_size({"screen_max": "800x600"}) == (800, 600)
    assert sc.max_size({"screen_max": "huge"}) == (1280, 720)


def test_session_key_sees_content_others_see_nothing(tmp_path):
    hub = ScreenHub(tmp_path)
    s = hub.start("tv", "camera", minutes=2, camera="yard")
    assert hub.state_for("tv", s.token)["content"]["camera"] == "yard"
    assert hub.state_for("tv", "guess") == {"paired": False}
    assert hub.state_for("other", s.token) == {"paired": False}  # a key is bound to its node


def test_new_session_replaces_old_and_bumps_rev(tmp_path):
    hub = ScreenHub(tmp_path)
    a = hub.start("tv", "camera", minutes=2, camera="yard")
    b = hub.start("tv", "card", title="Recipe")
    assert hub.session(a.token) is None and b.rev == a.rev + 1
    assert hub.current("tv").token == b.token


def test_expiry(tmp_path, monkeypatch):
    hub = ScreenHub(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(sc, "_now", lambda: now[0])
    s = hub.start("tv", "camera", minutes=2)
    card = hub.start("tv2", "card")  # cards have no timer
    now[0] += 119
    assert hub.session(s.token) is not None and hub.expired() == []
    now[0] += 2
    assert hub.session(s.token) is None
    assert [x.token for x in hub.expired()] == [s.token]
    assert hub.current("tv") is None and hub.current("tv2").token == card.token


def test_pairing_flow(tmp_path):
    hub = ScreenHub(tmp_path)
    first = hub.state_for("samsung", "", device_key="dev-1")
    code = first["pair_code"]
    assert first["paired"] is False and len(code) == 4
    assert hub.state_for("samsung", "", device_key="dev-1")["pair_code"] == code  # stable while it waits
    assert hub.confirm_code("samsung", "0000" if code != "0000" else "1111") is False
    assert hub.confirm_code("samsung", code) is True
    assert hub.state_for("samsung", "dev-1")["paired"] is True
    assert hub.state_for("samsung", "", device_key="dev-2") == {"paired": False}  # node taken: no new code
    # survives a restart, file is private
    again = ScreenHub(tmp_path)
    assert again.is_paired("samsung")
    assert json.loads((tmp_path / "paired.json").read_text()) == {"samsung": "dev-1"}
    assert oct((tmp_path / "paired.json").stat().st_mode & 0o777) == "0o600"
    assert again.unpair("samsung") and not ScreenHub(tmp_path).is_paired("samsung")


def test_pair_code_expires(tmp_path, monkeypatch):
    hub = ScreenHub(tmp_path)
    now = [0.0]
    monkeypatch.setattr(sc, "_now", lambda: now[0])
    code = hub.state_for("s", "", device_key="d")["pair_code"]
    now[0] += sc.PAIR_CODE_TTL_S + 1
    assert hub.confirm_code("s", code) is False


def test_paired_device_sees_current_content(tmp_path):
    hub = ScreenHub(tmp_path)
    code = hub.state_for("s", "", device_key="d")["pair_code"]
    hub.confirm_code("s", code)
    assert hub.state_for("s", "d")["content"] is None
    hub.start("s", "camera", minutes=2, camera="door")
    assert hub.state_for("s", "d")["content"]["camera"] == "door"
    assert hub.seen_recently("s")


def test_playing_only_when_frames_move_for_this_rev(tmp_path):
    hub = ScreenHub(tmp_path)
    s = hub.start("tv", "camera", minutes=2)
    hub.report(s.token, {"t": 0.0, "rev": s.rev})
    assert not hub.current("tv").playing_since
    hub.report(s.token, {"t": 2.0, "rev": s.rev - 1})  # an old page still running
    assert not hub.current("tv").playing_since
    hub.report(s.token, {"t": 2.0, "rev": s.rev})
    assert hub.current("tv").playing_since


def test_card_counts_as_showing_when_loaded(tmp_path):
    hub = ScreenHub(tmp_path)
    s = hub.start("tv", "card")
    hub.report(s.token, {"event": "loaded", "rev": s.rev})
    assert hub.current("tv").public()["playing"] is True


def test_switch_to_reencode_once(tmp_path):
    hub = ScreenHub(tmp_path)
    s = hub.start("tv", "camera", minutes=2)
    ch = sc.StreamChoice("door_2", "fmp4", 1280, 960, 1280, "stutter")
    assert hub.switch_to_reencode(s.token, ch) is True
    assert hub.current("tv").choice.mode == "fmp4" and hub.current("tv").rev == s.rev
    assert hub.switch_to_reencode(s.token, ch) is False


@pytest.mark.parametrize("prev,cur,want", [
    ({"frames": 100, "dropped": 0}, {"frames": 200, "dropped": 25}, True),  # the doorbell in T34 (~25 %)
    ({"frames": 100, "dropped": 0}, {"frames": 200, "dropped": 1}, False),
    ({}, {"frames": 10, "dropped": 9}, False),  # too few frames to judge
    ({"t": 5.0}, {"t": 5.0, "event": "stalled"}, True),
    ({"t": 5.0}, {"t": 8.0}, False),
])
def test_stutters(prev, cur, want):
    assert sc.stutters(prev, cur) is want


# ── phase 3: screens without Cast (browser) ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("paired,samsung,power,front,seen,want", [
    (False, True, "on", True, True, ([], "not_paired")),           # never shows anything before pairing (D9)
    (True, True, None, False, False, (["power_on", "browser"], "")),  # Samsung off the network → wake, then browser
    (True, True, "standby", False, False, (["power_on", "browser"], "")),
    (True, True, "on", False, False, (["browser"], "")),            # on, something else in front
    (True, True, "on", True, True, ([], "")),                        # our page already in front
    (True, False, None, False, True, ([], "")),                      # a paired tablet with the page open
    (True, False, None, False, False, ([], "page_not_open")),        # KTSB cannot bring it up itself
])
def test_browser_open_steps(paired, samsung, power, front, seen, want):
    assert sc.browser_open_steps(paired, samsung, power, front, seen) == want


@pytest.mark.parametrize("was_off,front,want", [
    (True, False, "power_off"), (True, True, "power_off"), (False, False, "exit"), (False, True, ""),
])
def test_browser_restore_step(was_off, front, want):
    assert sc.browser_restore_step(was_off, front) == want


def test_a_session_whose_page_went_away_ends(tmp_path, monkeypatch):
    """BUG 2026-11-16: a recipe card stayed «active» for an hour after a camera snapshot took the TV over (the page
    stopped polling). A session that was showing and whose page has been silent for STALE_S ends — without a restore
    (something else owns the screen now)."""
    hub = ScreenHub(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(sc, "_now", lambda: now[0])
    card = hub.start("tv", "card")
    hub.state_for("tv", card.token)  # the page polls …
    hub.report(card.token, {"event": "loaded", "rev": card.rev})  # … and shows it
    now[0] += sc.STALE_S - 1
    assert hub.expired() == []
    now[0] += 2
    gone = hub.expired()
    assert [s.token for s in gone] == [card.token] and gone[0].stale and hub.current("tv") is None


def test_a_session_not_yet_showing_is_not_stale(tmp_path, monkeypatch):
    hub = ScreenHub(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(sc, "_now", lambda: now[0])
    hub.start("tv", "card")
    now[0] += sc.STALE_S * 3
    assert hub.expired() == []  # never shown: the open/confirm step handles that, not the stale rule
