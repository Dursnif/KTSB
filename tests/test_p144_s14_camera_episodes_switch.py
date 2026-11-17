"""P144-s14 item 7 — «Kan lage episoder» per camera card (2026-10-28: «det styres i GUI under
Kamera-innstillinger hvilket kamera som skal hva»). No hidden role list overrides the user's ticks any more.

- the camera card's `episodes` switch decides whether a camera's events may open/keep open an episode;
- a camera without the key gets the role default (road_facing → off, others → on);
- the per-label ticks + min_confidence decide the rest on every camera;
- config migration step 9 writes the switch from the old `episode_ignore_roles` (this house: [] → all on), so nothing
  changes on day one;
- `episode_ignore_roles` is retired (no setting, no GUI list).
Reuses s12's fixture (tmp DBs, fake camera config, fake LLM).
"""

import copy

import pytest
import test_p144_s06a_pipe as s06a
from test_p144_s06a_pipe import env  # noqa: F401 — the fixture cams builds on
from test_p144_s12_camera_settings import (  # noqa: F401 — cams is a fixture
    CAMERAS,
    _ev,
    _label,
    cams,
)

from kaare_core import config_migrations as cm
from kaare_core.frigate_user import pipe
from kaare_core.frigate_user import settings as fsettings


def _with(monkeypatch, **cards):
    c = copy.deepcopy(CAMERAS)
    for cam, extra in cards.items():
        c[cam] = {**c[cam], **extra}
    monkeypatch.setattr(pipe.frigate_store, "camera_config", lambda: {"cameras": c})


# ── the accessor ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("card, expect", [
    ({"role": "road_facing"}, False),
    ({"role": "driveway"}, True),
    ({}, True),
    ({"role": "road_facing", "episodes": True}, True),
    ({"role": "front_door", "episodes": False}, False),
    ({"role": "road_facing", "episodes": "yes"}, False),  # not a bool → the role default
])
def test_camera_makes_episodes(card, expect):
    assert fsettings.camera_makes_episodes(card) is expect


def test_ignore_roles_setting_is_retired():
    assert "episode_ignore_roles" not in fsettings.get_settings()
    with pytest.raises(ValueError):
        fsettings.update({"episode_ignore_roles": []})


# ── the pipe + store ────────────────────────────────────────────────────────────


def test_road_camera_switch_on_and_deer_ticked_drives(cams, monkeypatch):  # noqa: F811
    _with(monkeypatch, veg={"episodes": True, "labels": {**CAMERAS["veg"]["labels"], "deer": _label(True)}})
    s06a._process(_ev("d1", camera="veg", label="deer"))
    e = s06a._stored("d1")
    assert e.drives is True and e.episode_id is not None
    assert e.check["drive"] == {"selected": True, "why": "selected"}


def test_switch_off_gives_events_only(cams, monkeypatch):  # noqa: F811
    _with(monkeypatch, inngang={"episodes": False})
    s06a._process(_ev("p1", camera="inngang"))
    e = s06a._stored("p1")
    assert e.drives is False
    assert e.check["drive"] == {"selected": True, "why": "episodes_off"}


def test_new_camera_without_the_key_gets_the_role_default(cams):  # noqa: F811
    s06a._process(_ev("r1", camera="veg", label="license_plate"))  # road_facing, no key → off
    assert s06a._stored("r1").drives is False
    s06a._process(_ev("p1", camera="innkjorsel"))  # driveway, no key → on
    assert s06a._stored("p1").drives is True


def test_fire_drives_even_with_the_switch_off(cams, monkeypatch):  # noqa: F811
    _with(monkeypatch, inngang={"episodes": False})
    s06a._process(_ev("f1", camera="inngang", label="fire"))
    assert s06a._stored("f1").drives is True


# ── migration step 9 ────────────────────────────────────────────────────────────


def _migrate(tmp_path, ignore_line: str | None, cameras: dict) -> dict:
    (tmp_path / "frigate_user.yaml").write_text(f"enabled: true\n{ignore_line or ''}\n")
    cm._RUN_CONFIG_DIR = tmp_path
    data = {"cameras": copy.deepcopy(cameras)}
    notes = cm._s9_camera_episode_switch(data, {})
    return data, notes


def test_migration_keeps_this_houses_behaviour(tmp_path):
    data, _ = _migrate(tmp_path, "episode_ignore_roles: []", CAMERAS)
    assert {c: v["episodes"] for c, v in data["cameras"].items()} == {"inngang": True, "innkjorsel": True,
                                                                       "veg": True}


def test_migration_from_the_old_default(tmp_path):
    data, notes = _migrate(tmp_path, "episode_ignore_roles: [road_facing]", CAMERAS)
    assert data["cameras"]["veg"]["episodes"] is False and data["cameras"]["inngang"]["episodes"] is True
    assert notes


def test_migration_without_the_old_key_uses_the_role_default_and_never_overwrites(tmp_path):
    cams_ = copy.deepcopy(CAMERAS)
    cams_["inngang"]["episodes"] = False  # the user's own choice
    data, _ = _migrate(tmp_path, None, cams_)
    assert data["cameras"]["inngang"]["episodes"] is False
    assert data["cameras"]["veg"]["episodes"] is False and data["cameras"]["innkjorsel"]["episodes"] is True


def test_step_9_is_registered_for_frigate_cameras():
    step = next(s for s in cm.STEPS if s.number == 9)
    assert step.filename == "frigate_cameras.yaml" and cm.LATEST >= 9
