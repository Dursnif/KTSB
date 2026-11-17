"""P144-s11 Part 1 — the episode view's API (/api/frigate_user/episodes*), admin only, fail closed.

- list: newest first, `before` paginates, local time from the GUI timezone, cameras in order of activity with display
  names, persons only as the storage gate stored them (with claim status), `by`, summary, unusual, urgent;
- a name of someone who has since said «Frigate may not track me» is not shown (read-time check, fail closed);
- detail: events + check summary, route, scene, conclusion, frames list;
- frame route: Frigate's recording snapshot at the frame's time (patched), 404 when the recording is gone / no frame.
Never touches live state: frigate.db / users.db / frigate_user.yaml point at tmp_path; Frigate is patched.
"""

from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from adapters import frigate_adapter
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0  # 2027-01-15 08:00:00 UTC
WALK = [[[0.2, 0.5], T0], [[0.6, 0.5], T0 + 6]]
CAMS = {"cam_door": {"display_name": "Inngang"}, "cam_yard": {"display_name": "Gårdsplass"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(rfu, "get_local_tz", lambda: ZoneInfo("Europe/Oslo"), raising=False)
    monkeypatch.setattr(rfu, "_camera_cards", lambda: CAMS, raising=False)
    fstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    ustore.create_user(username="mum", display_name="Mum", role="adult", pin="482915")
    calls = []

    async def fake_snapshot(camera, ts, height=480):
        calls.append((camera, ts, height))
        if camera == "cam_gone":
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", "http://f"),
                                        response=httpx.Response(404))
        return b"JPEG:" + camera.encode()

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snapshot)
    app = FastAPI()
    app.include_router(rfu.router)
    c = TestClient(app)
    c.calls = calls
    yield c
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _hdr(user="boss", role="admin"):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


def _ev(event_id, *, camera="cam_door", label="person", start=T0, ref=None, kind="unknown", claim="none", check=None):
    return fstore.FrigateUserEvent(
        event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 10, person_ref=ref,
        person_kind=kind, claim_status=claim, check=check or {}, path_data=[[p, t - T0 + start] for p, t in WALK],
        camera_role="front_door")


def _episode(start, *events):
    for e in events:
        assert fstore.record_event(e)
    ev = next(e for e in fstore.events_between(0, T0 * 3) if e.event_id == events[0].event_id)
    return ev.episode_id


def _three_episodes():
    a = _episode(T0, _ev("a1", start=T0))
    b = _episode(T0 + 1000, _ev("b1", camera="cam_yard", start=T0 + 1000),
                 _ev("b2", start=T0 + 1004, ref="Userb", kind="household", claim="unverified",
                     check={"decision": "unverified", "l0": {"layer": "l0", "verdict": "pass", "strong": False,
                                                             "reason": "ok", "contradictions": []}}))
    c = _episode(T0 + 2000, _ev("c1", start=T0 + 2000, label="car"))
    fstore.set_episode_scene(b, {"status": "done", "scene": "To personer ved døra", "people": [], "at_door": "ja",
                                 "vehicles": "", "persons": 2,
                                 "frames": [{"camera": "cam_yard", "ts": T0 + 1001}, {"camera": "cam_door",
                                                                                      "ts": T0 + 1005}]},
                             "Gårdsplass → Inngang")
    fstore.conclude_episode(b, {"status": "done", "by": "model", "summary": "Userb kom hjem.",
                                "unusual": {"value": False, "reason": "vanlig"},
                                "unusual_suppressed": {"value": True, "reason": "aldri sett", "baseline_days": 2},
                                "notify_suggestion": {"who": "admin", "why": "x"},
                                "raise_at_dev_meeting": {"topic": "t", "text": "tekst", "count": 3, "raised": False},
                                "confidence": {"level": "medium", "reasons": ["to kameraer"]},
                                "urgent": [{"event_id": "b2", "reason": "not_welcome", "status": "done"}]})
    fstore.conclude_episode(c, {"status": "done", "by": "code", "summary": "En bil kjørte forbi.",
                                "unusual": {"value": False, "reason": ""}, "confidence": {"level": "medium"}})
    return a, b, c


# ── access ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/api/frigate_user/episodes", "/api/frigate_user/episodes/1",
                                  "/api/frigate_user/episodes/1/frames/0"])
@pytest.mark.parametrize("who", [None, ("mum", "adult"), ("kid", "child")])
def test_non_admin_is_refused(client, path, who):
    r = client.get(path, headers=_hdr(*who) if who else {})
    assert r.status_code in (401, 403)


# ── list ────────────────────────────────────────────────────────────────────────


def test_list_newest_first_and_paginates(client):
    a, b, c = _three_episodes()
    r = client.get("/api/frigate_user/episodes?limit=2", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["episodes"]] == [c, b]
    assert body["enabled"] is True
    assert body["has_more"] is True
    before = body["episodes"][-1]["start_ts"]
    r2 = client.get(f"/api/frigate_user/episodes?limit=2&before={before}", headers=_hdr()).json()
    assert [e["id"] for e in r2["episodes"]] == [a]
    assert r2["has_more"] is False


def test_list_item_shape(client):
    _a, b, c = _three_episodes()
    items = {e["id"]: e for e in client.get("/api/frigate_user/episodes", headers=_hdr()).json()["episodes"]}
    eb = items[b]
    assert eb["date"] == "2027-01-15" and eb["start"] == "09:16:40"  # local time (Europe/Oslo, UTC+1)
    assert eb["duration_s"] >= 6
    assert [x["camera"] for x in eb["cameras"]] == ["cam_yard", "cam_door"]  # order of activity, not alphabet
    assert [x["name"] for x in eb["cameras"]] == ["Gårdsplass", "Inngang"]
    assert eb["persons"] == [{"name": "Userb", "claim_status": "unverified", "kind": "household"}]
    assert eb["unnamed_persons"] == 1
    assert eb["by"] == "model" and eb["summary"] == "Userb kom hjem."
    assert eb["unusual"] == {"value": False, "reason": "vanlig"}
    assert eb["unusual_suppressed"]["value"] is True
    assert eb["urgent"] is True
    assert items[c]["by"] == "code" and items[c]["urgent"] is False


def test_open_episode_has_no_conclusion(client):
    a, _b, _c = _three_episodes()
    ea = next(e for e in client.get("/api/frigate_user/episodes", headers=_hdr()).json()["episodes"] if e["id"] == a)
    assert ea["by"] is None and ea["summary"] == "" and ea["status"] == "open"


def test_name_rejected_by_the_gate_never_appears(client):
    _episode(T0, _ev("r1", ref="Userb", kind="household", claim="rejected"))
    body = client.get("/api/frigate_user/episodes", headers=_hdr()).text
    assert "Userb" not in body


def test_opted_out_person_is_hidden_at_read_time(client):
    ustore.create_user(username="userb", display_name="Userb", role="adult", pin="482915")
    ep = _episode(T0, _ev("n1", ref="userb", kind="household", claim="confirmed"))
    fstore.conclude_episode(ep, {"status": "done", "by": "model", "summary": "Userb kom hjem.",
                                 "unusual": {"value": False, "reason": ""}, "confidence": {"level": "low"}})
    shown = client.get("/api/frigate_user/episodes", headers=_hdr()).json()["episodes"][0]
    assert shown["persons"] == [{"name": "Userb", "claim_status": "confirmed", "kind": "household"}]  # display name
    ustore.set_frigate_tracking("userb", False)  # she says no after the fact
    frigate_consent.invalidate()
    for path in ("/api/frigate_user/episodes", f"/api/frigate_user/episodes/{ep}"):
        text = client.get(path, headers=_hdr()).text
        assert "Userb" not in text and "userb" not in text


def test_bad_limit_is_refused(client):
    assert client.get("/api/frigate_user/episodes?limit=0", headers=_hdr()).status_code == 422


# ── detail ──────────────────────────────────────────────────────────────────────


def test_detail_shape(client):
    _a, b, _c = _three_episodes()
    r = client.get(f"/api/frigate_user/episodes/{b}", headers=_hdr())
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == b and d["route"] == "Gårdsplass → Inngang"
    assert d["scene"]["scene"] == "To personer ved døra"
    assert d["conclusion"]["notify_suggestion"] == {"who": "admin", "why": "x"}
    assert d["conclusion"]["raise_at_dev_meeting"]["count"] == 3
    assert [f["index"] for f in d["frames"]] == [0, 1]
    assert d["frames"][0]["camera_name"] == "Gårdsplass" and d["frames"][0]["clock"] == "09:16:41"
    ev = {e["event_id"]: e for e in d["events"]}
    assert ev["b2"]["person"] == "Userb" and ev["b2"]["claim_status"] == "unverified"
    assert ev["b2"]["check"]["decision"] == "unverified"
    assert ev["b2"]["check"]["layers"][0]["layer"] == "l0"
    assert ev["b1"]["camera_name"] == "Gårdsplass"


def test_detail_missing_is_404(client):
    assert client.get("/api/frigate_user/episodes/999", headers=_hdr()).status_code == 404


# ── frames ──────────────────────────────────────────────────────────────────────


def test_frame_falls_back_to_frigate_recording(client):
    _a, b, _c = _three_episodes()
    r = client.get(f"/api/frigate_user/episodes/{b}/frames/1", headers=_hdr())
    assert r.status_code == 200 and r.content == b"JPEG:cam_door"
    assert r.headers["content-type"] == "image/jpeg"
    assert client.calls == [("cam_door", T0 + 1005, 480)]


def test_frame_out_of_range_or_no_scene_is_404(client):
    a, b, _c = _three_episodes()
    assert client.get(f"/api/frigate_user/episodes/{b}/frames/2", headers=_hdr()).status_code == 404
    assert client.get(f"/api/frigate_user/episodes/{a}/frames/0", headers=_hdr()).status_code == 404
    assert client.calls == []


def test_frame_recording_gone_is_404(client):
    ep = _episode(T0, _ev("g1", camera="cam_gone"))
    fstore.set_episode_scene(ep, {"status": "done", "scene": "x", "frames": [{"camera": "cam_gone", "ts": T0}]}, "")
    r = client.get(f"/api/frigate_user/episodes/{ep}/frames/0", headers=_hdr())
    assert r.status_code == 404 and r.json()["detail"] == "recording_gone"
