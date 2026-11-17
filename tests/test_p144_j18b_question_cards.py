"""J18b (Userone via the main session 2026-11-06): the three question cards always show, with a short history; a place's
image is a recording frame with NO active track on that camera (never a person, never an opted-out person), by day when
possible; no such frame → text only. History endpoints are read-only and keep today's access: places/things admin only,
«Var det deg?» history = the caller's own answers only. Tmp DBs; Frigate is a fake."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from adapters import frigate_adapter
from kaare_core import weights
from kaare_core.frigate_user import places_learned as pl
from kaare_core.frigate_user import questions, thing_links
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore

TZ = ZoneInfo("Europe/Oslo")
NOW = datetime(2027, 1, 15, 20, 0, tzinfo=TZ).timestamp()
NOON = datetime(2027, 1, 15, 12, 0, tzinfo=TZ).timestamp()
DAY = 86400.0


def _ev(eid, cam, t0, t1, label="person", kind="unknown", drives=True, stationary=None):
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label=label, start_ts=t0, end_ts=t1, person_kind=kind,
                                   drives=drives, stationary=stationary)


# ── the empty moment (red first) ────────────────────────────────────────────────────────────────────────────────

def test_the_image_moment_has_no_active_track_and_is_by_day():
    evs = [_ev("p", "vest", NOON - 10, NOON + 10),  # someone at noon today → not then
           _ev("car", "vest", NOON - 7200, NOON + 7200, label="car", drives=False, stationary=True)]  # a parked car is fine
    ts = pl.empty_moment(evs, "vest", NOW, TZ)
    assert ts is not None and all(not (e.start_ts - pl.EMPTY_PAD_S <= ts <= e.end_ts + pl.EMPTY_PAD_S)
                                  for e in evs if e.label == "person")
    assert 9 <= datetime.fromtimestamp(ts, TZ).hour <= 15


def test_an_opted_out_persons_track_also_blocks_the_moment():
    evs = [_ev(f"u{d}_{h}", "vest", NOW - d * DAY - 12 * 3600 + h * 3600 - 60, NOW - d * DAY - 12 * 3600 + h * 3600 + 60,
               kind="untracked") for d in range(8) for h in range(-4, 5)]
    ts = pl.empty_moment(evs, "vest", NOW, TZ)
    assert ts is None or all(not (e.start_ts - pl.EMPTY_PAD_S <= ts <= e.end_ts + pl.EMPTY_PAD_S) for e in evs)


def test_no_empty_moment_gives_none():
    busy = [_ev("b", "vest", NOW - 30 * DAY, NOW + DAY)]
    assert pl.empty_moment(busy, "vest", NOW, TZ) is None


# ── API: access, image, history ─────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(rfu, "get_local_tz", lambda: TZ, raising=False)
    monkeypatch.setattr(rfu, "_camera_cards", dict, raising=False)
    monkeypatch.setattr(rfu.time, "time", lambda: NOW)
    fstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    ustore.create_user(username="mum", display_name="Mum", role="adult", pin="482915")
    calls = []

    async def fake_snapshot(camera, ts, height=480):
        calls.append((camera, ts))
        return b"JPEG"

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snapshot)
    app = FastAPI()
    app.include_router(rfu.router)
    c = TestClient(app)
    c.calls = calls
    yield c
    frigate_consent.invalidate()
    fstore.reset_for_tests()
    wstore.reset_for_tests()


def _hdr(user, role):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


ADMIN_PATHS = ["/api/frigate_user/questions/places/answered", "/api/frigate_user/questions/things/answered",
               "/api/frigate_user/questions/places/image/vest/1,2"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
@pytest.mark.parametrize("who", [None, ("mum", "adult")])
def test_admin_history_and_image_are_refused_to_others(client, path, who):
    r = client.get(path, headers=_hdr(*who) if who else {})
    assert r.status_code == (401 if who is None else 403)


def test_my_history_needs_login(client):
    assert client.get("/api/frigate_user/questions/mine/history").status_code == 401


def test_place_image_is_a_recording_frame_with_no_track(client):
    for d in range(1, 5):  # a person every day at the spot, at noon
        t = NOON - d * DAY
        fstore.record_event(fstore.FrigateUserEvent(event_id=f"p{d}", camera="vest", label="person", start_ts=t,
                                                    end_ts=t + 20, path=[[0.25, 0.45, t], [0.9, 0.99, t + 19]]))
    r = client.get("/api/frigate_user/questions/places/image/vest/1,2", headers=_hdr("boss", "admin"))
    assert r.status_code == 200 and r.content == b"JPEG"
    cam, ts = client.calls[-1]
    assert cam == "vest"
    assert all(not (NOON - d * DAY - pl.EMPTY_PAD_S <= ts <= NOON - d * DAY + 20 + pl.EMPTY_PAD_S) for d in range(1, 5))


def test_place_question_carries_no_event_image(client):
    for d in range(1, 5):
        for i in range(3):
            t = NOON - d * DAY + i * 600
            fstore.record_event(fstore.FrigateUserEvent(event_id=f"q{d}{i}", camera="vest", label="person", start_ts=t,
                                                        end_ts=t + 20, path=[[0.25, 0.45, t], [0.9, 0.99, t + 19]]))
    qs = client.get("/api/frigate_user/questions/places", headers=_hdr("boss", "admin")).json()["questions"]
    assert qs and all("event_id" not in q for q in qs)


def test_places_history_lists_registered_spots(client):
    fstore.add_spot_answer("vest", "1,2", "named", "garasjehjørnet", "boss")
    fstore.add_spot_answer("vest", "4,4", "not_a_place", None, "boss")
    out = client.get("/api/frigate_user/questions/places/answered", headers=_hdr("boss", "admin")).json()["answered"]
    assert [(a["cell"], a["status"], a.get("name")) for a in out] == [("4,4", "not_a_place", None),
                                                                     ("1,2", "named", "garasjehjørnet")]


def test_things_history_lists_answered_pairs(client):
    pair = thing_links.pair_id("cam_e|waste_bin|0,4", "cam_w|waste_bin|3,1")
    thing_links.answer("boss", pair, "yes")
    out = client.get("/api/frigate_user/questions/things/answered", headers=_hdr("boss", "admin")).json()["answered"]
    assert [(a["id"], a["answer"], a["label"]) for a in out] == [(pair, "yes", "waste_bin")]


def test_my_history_is_my_own_answers_only(client, monkeypatch):
    fstore.record_event(fstore.FrigateUserEvent(event_id="e1", camera="vest", label="person", start_ts=NOW - 3600,
                                                end_ts=NOW - 3590))
    weights.answer(("event", "e1", "mum"), by_user="mum", verdict="yes", verbatim=True, context="frigate:e1",
                   pointer="frigate:e1")
    weights.answer(("event", "e1", "boss"), by_user="boss", verdict="no", verbatim=True, context="frigate:e1",
                   pointer="frigate:e1b")
    weights.flush()
    mine = client.get("/api/frigate_user/questions/mine/history", headers=_hdr("mum", "adult")).json()["answered"]
    assert [(a["event_id"], a["answer"]) for a in mine] == [("e1", "yes")]
    assert set(mine[0]) == {"event_id", "camera", "ts", "answer", "answered_ts"}  # no image, no name, no detail
    assert questions.history("nobody") == []
