"""P144-s11 Part 2 — episode frames stored on AI-pc through the store module.

- the scene step saves the exact frames the 9B saw to `<state>/frigate_snapshots/episodes/<id>/<i>.jpg` (the folder
  follows `store.DB_PATH`, so a test that repoints the DB never writes the live folder);
- cap `episode_frames_max_mb` (0 = do not store): over it, the oldest episodes are trimmed to one representative frame
  (the middle one), which is kept long-term under its own cap `episode_frames_keep_mb` (over it, the oldest go);
- the frame route serves a stored frame first, without asking Frigate; the detail says which frames are stored;
- usage route for the storage-limits bars; settings keys validated.
Never touches live state: frigate.db / users.db / frigate_user.yaml / the frames folder point at tmp_path.
"""

import asyncio
from pathlib import Path

import conftest
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from adapters import frigate_adapter
from kaare_core.config_migrations import fill_missing_keys
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests import test_p144_s06d_scene as s06d
from tests.test_p144_s06d_scene import (
    loop,  # noqa: F401 — s06d's fake LLM + Frigate, on this module's tmp `db`
)

T0 = 1_800_000_000.0
KB = 1024


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    fstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _caps(tmp_path, max_mb, keep_mb):
    (tmp_path / "frigate_user.yaml").write_text(
        f"enabled: true\nepisode_frames_max_mb: {max_mb}\nepisode_frames_keep_mb: {keep_mb}\n")


def _files(tmp_path, ep):
    d = tmp_path / "frigate_snapshots" / "episodes" / str(ep)
    return sorted(int(p.stem) for p in d.glob("*.jpg")) if d.exists() else []


# ── the folder ──────────────────────────────────────────────────────────────────


def test_frames_folder_follows_the_db(db):
    assert fstore.episode_frames_dir() == db / "frigate_snapshots" / "episodes"


def test_live_frames_folder(monkeypatch):
    # P144-s17: conftest points frigate.db at tmp in every test — check the rule on the shipped path (pure path math)
    monkeypatch.setattr(fstore, "DB_PATH", conftest.LIVE_FRIGATE_DB)
    assert fstore.DB_PATH.parent.parent / "frigate_snapshots" / "episodes" == fstore.episode_frames_dir()
    assert str(fstore.episode_frames_dir()) == "/kaare/state/frigate_snapshots/episodes"


# ── save + read ─────────────────────────────────────────────────────────────────


def test_save_and_read(db):
    assert fstore.save_episode_frames(7, [b"a", b"bb", b"ccc"]) == 3
    assert _files(db, 7) == [0, 1, 2]
    assert fstore.read_episode_frame(7, 1) == b"bb"
    assert fstore.read_episode_frame(7, 5) is None
    assert fstore.read_episode_frame(8, 0) is None
    assert fstore.stored_frame_indexes(7) == [0, 1, 2]


def test_cap_zero_stores_nothing(db):
    _caps(db, 0, 100)
    assert fstore.save_episode_frames(7, [b"a"]) == 0
    assert _files(db, 7) == []


def test_rotation_keeps_the_middle_frame_of_old_episodes(db):
    _caps(db, 1, 100)  # 1 MB for full frame sets
    frame = b"x" * (200 * KB)
    for ep in (1, 2, 3):
        fstore.save_episode_frames(ep, [frame] * 3)  # 600 KB per episode
    # 3 × 600 KB > 1 MB: the oldest are trimmed (non-representative frames count against the cap) until it fits
    assert _files(db, 1) == [1]  # middle of 0..2
    assert _files(db, 3) == [0, 1, 2]  # newest stays whole
    usage = fstore.episode_frames_usage()
    assert usage["full_mb"] <= 1.0


def test_representative_frames_have_their_own_cap(db):
    _caps(db, 1, 1)
    rep = b"y" * (300 * KB)
    for ep in range(1, 6):
        fstore.save_episode_frames(ep, [rep] * 3)
    usage = fstore.episode_frames_usage()
    assert usage["full_mb"] <= 1.0 and usage["keep_mb"] <= 1.0
    assert _files(db, 1) == []  # oldest representative gone when the keep cap is full
    assert _files(db, 5) == [0, 1, 2]


def test_single_frame_episode_is_its_own_representative(db):
    _caps(db, 1, 100)
    fstore.save_episode_frames(1, [b"z" * (900 * KB)])
    fstore.save_episode_frames(2, [b"z" * (900 * KB)] * 2)
    assert _files(db, 1) == [0]


# ── the scene step saves the frames ─────────────────────────────────────────────


def test_scene_step_saves_the_frames_the_model_saw(db, loop):  # noqa: F811
    s06d._leaving()
    assert asyncio.run(s06d.conclude.run_once(now=s06d.T0 + 1000)) == 1
    ep = fstore.episodes_between(0, s06d.T0 * 2)[0]
    n = len(ep.scene["frames"])
    assert n >= 3 and _files(db, ep.episode_id) == list(range(n))
    first = ep.scene["frames"][0]
    assert fstore.read_episode_frame(ep.episode_id, 0) == f"{first['camera']}-{first['ts']:.0f}".encode()


# ── API ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(rfu, "_camera_cards", dict)
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    calls = []

    async def fake_snapshot(camera, ts, height=480):
        calls.append(camera)
        return b"FRIGATE"

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snapshot)
    app = FastAPI()
    app.include_router(rfu.router)
    c = TestClient(app)
    c.calls = calls
    return c


def _hdr():
    return {"Authorization": f"Bearer {auth.create_token('boss', 'admin')}"}


def _episode_with_frames():
    ev = fstore.FrigateUserEvent(event_id="a", camera="cam", label="person", start_ts=T0, end_ts=T0 + 10,
                                 path_data=[[[0.2, 0.5], T0], [[0.6, 0.5], T0 + 6]], camera_role="front_door")
    assert fstore.record_event(ev)
    ep = fstore.episodes_between(0, T0 * 2)[0].episode_id
    fstore.set_episode_scene(ep, {"status": "done", "scene": "x",
                                  "frames": [{"camera": "cam", "ts": T0 + i} for i in range(3)]}, "")
    return ep


def test_frame_route_serves_stored_frame_first(client):
    ep = _episode_with_frames()
    fstore.save_episode_frames(ep, [b"S0", b"S1", b"S2"])
    r = client.get(f"/api/frigate_user/episodes/{ep}/frames/1", headers=_hdr())
    assert r.status_code == 200 and r.content == b"S1" and client.calls == []


def test_frame_route_falls_back_when_trimmed(client, db):
    ep = _episode_with_frames()
    fstore.save_episode_frames(ep, [b"S0", b"S1", b"S2"])
    (db / "frigate_snapshots" / "episodes" / str(ep) / "0.jpg").unlink()
    r = client.get(f"/api/frigate_user/episodes/{ep}/frames/0", headers=_hdr())
    assert r.content == b"FRIGATE" and client.calls == ["cam"]


def test_detail_says_which_frames_are_stored(client):
    ep = _episode_with_frames()
    fstore.save_episode_frames(ep, [b"S0", b"S1", b"S2"])
    (fstore.episode_frames_dir() / str(ep) / "2.jpg").unlink()
    d = client.get(f"/api/frigate_user/episodes/{ep}", headers=_hdr()).json()
    assert [f["stored"] for f in d["frames"]] == [True, True, False]


def test_usage_route(client):
    fstore.save_episode_frames(1, [b"x" * KB] * 3)
    r = client.get("/api/frigate_user/episode_frames/usage", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"full_mb", "keep_mb", "episodes", "max_mb", "keep_cap_mb"}
    assert body["episodes"] == 1
    assert client.get("/api/frigate_user/episode_frames/usage").status_code in (401, 403)


# ── settings ────────────────────────────────────────────────────────────────────


def test_settings_defaults_and_limits(db):
    s = fsettings.get_settings()
    assert s["episode_frames_max_mb"] == 500 and s["episode_frames_keep_mb"] == 500
    assert fsettings.update({"episode_frames_max_mb": 0})["episode_frames_max_mb"] == 0
    with pytest.raises(ValueError):
        fsettings.update({"episode_frames_max_mb": -1})
    with pytest.raises(ValueError):
        fsettings.update({"episode_frames_keep_mb": 10**7})


def test_template_has_the_frame_caps_and_migration_adds_them():
    tpl = yaml.safe_load(Path("/kaare/configs_default/frigate_user.yaml").read_text(encoding="utf-8"))
    assert tpl["episode_frames_max_mb"] == 500 and tpl["episode_frames_keep_mb"] == 500
    assert tpl["enabled"] is True  # DT04 R0.6b: ships on (heavy modules off); was False
    old = {"enabled": True, "episode_gap_s": 120}
    added = fill_missing_keys(old, tpl)
    assert "episode_frames_max_mb" in added and old["episode_frames_keep_mb"] == 500
