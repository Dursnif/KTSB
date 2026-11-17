"""P144-s06d Part 2 — the per-episode scene step: ~9 frames picked by code, a code route (K), ONE call to the `frigate`
role with the frames as separate numbered images; the 27B conclusion gets route + scene.

Frames rule (s09 `frames.py`): candidates = path points + start/end of each driving event; first point per camera,
then nearest to evenly spaced times (> 2 s apart). Route (s09 `k_text.py`): camera order with clock times, movement in
the image, direction from camera roles, time of day from the clock — never a model, never a face claim.
Never touches live state (tmp DBs, fake Frigate, fake LLM, `_log_llm_call_bg` patched).
"""

import asyncio
import json
import time
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from adapters import frigate_adapter, llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.frigate_user import conclude, frames, route
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

UTC = ZoneInfo("UTC")
T0 = 1_792_998_000.0  # 2026-10-25 07:00:00 UTC (a Sunday)
T_NOW = time.time()


def _p(*pts):
    return [[[x, y], ts] for x, y, ts in pts]


def _ev(event_id, camera, start, *, label="person", end=None, path=None, role=None, person_ref=None,
        kind="unknown", claim="none", **kw):
    if end is None:  # an event ends after its last path point
        end = max([start + 10] + [p[1] + 1 for p in path or []])
    return fstore.FrigateUserEvent(
        event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=end,
        path_data=path, camera_role=role, person_ref=person_ref, person_kind=kind, claim_status=claim, **kw)


# a person comes out of the front door, walks down the driveway and out at the gate (s09 ep1/ep99 shape)
LEAVING = [
    ("e1", "door", T0, _p((0.5, 0.2, T0), (0.5, 0.6, T0 + 4)), "front_door"),
    ("e2", "drive", T0 + 12, _p((0.2, 0.3, T0 + 12), (0.5, 0.5, T0 + 20), (0.8, 0.7, T0 + 28)), "driveway"),
    ("e3", "gate", T0 + 40, _p((0.3, 0.3, T0 + 40), (0.7, 0.6, T0 + 48)), "road_facing"),
]
CAMS = {"door": {"display_name": "Inngang", "role": "front_door"},
        "drive": {"display_name": "Innkjørsel", "role": "driveway"},
        "gate": {"display_name": "Port", "role": "road_facing"}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    ustore.create_user(username="persona", display_name="Person A", role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    frigate_consent.invalidate()
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _leaving(person_ref=None, claim="none", kind="unknown"):
    for eid, cam, start, path, role in LEAVING:
        assert fstore.record_event(_ev(eid, cam, start, path=path, role=role, person_ref=person_ref,
                                       claim=claim, kind=kind))
    (ep,) = fstore.episodes_between(0, T0 * 2)
    return ep, fstore.episode_events(ep.episode_id)


# ── store additions ─────────────────────────────────────────────────────────────


def test_episode_events_by_id_include_a_late_driving_event(db):
    # a car that arrived 42 h ago and drives off now belongs to the episode although its start_ts is long before it
    leave = T0 + 42 * 3600
    fstore.record_event(_ev("car", "drive", T0, label="car", end=leave + 8, role="driveway",
                            path=_p((0.75, 0.3, T0), (0.4, 0.3, T0 + 6), (0.4, 0.3, leave), (0.9, 0.4, leave + 6))))
    fstore.record_event(_ev("p", "door", leave + 2, path=_p((0.5, 0.2, leave + 2), (0.5, 0.6, leave + 5)),
                            role="front_door"))
    ep_id = next(e for e in fstore.events_between(0, T0 * 2) if e.event_id == "p").episode_id
    assert {e.event_id for e in fstore.episode_events(ep_id)} == {"car", "p"}


def test_store_keeps_a_trimmed_path(db):
    fstore.record_event(_ev("e2", "drive", T0 + 12, path=LEAVING[1][3], role="driveway"))
    e = fstore.episode_events(fstore.episodes_between(0, T0 * 2)[0].episode_id)[0]
    assert e.path == [[0.2, 0.3, T0 + 12], [0.5, 0.5, T0 + 20], [0.8, 0.7, T0 + 28]]


# ── frames (code rule) ──────────────────────────────────────────────────────────


def test_frames_first_per_camera_then_spread(db):
    _ep, events = _leaving()
    picked = frames.pick(frames.candidates(events))
    cams = [c for _, c in picked]
    assert {"door", "drive", "gate"} <= set(cams)
    assert len(picked) <= frames.N_FRAMES
    ts = [t for t, _ in picked]
    assert ts == sorted(ts)
    assert all(b - a > frames.MIN_SPACING_S for a, b in pairwise(ts))
    assert ts[0] <= T0 + 1 and ts[-1] >= T0 + 47  # spans the whole episode


def test_frames_skip_non_driving_events(db):
    _leaving()
    fstore.record_event(_ev("parked", "drive", T0 + 15, label="car", end=T0 + 3000, role="driveway",
                            path=_p((0.5, 0.3, T0 + 15), (0.51, 0.3, T0 + 2900))))
    ep = fstore.episodes_between(0, T0 * 2)[0]
    picked = frames.pick(frames.candidates(fstore.episode_events(ep.episode_id)))
    assert all(t < T0 + 100 for t, _ in picked)  # nothing from the parked car's 50 minutes


def test_fetch_frames_uses_recording_snapshots_and_drops_missing(monkeypatch):
    asked = []

    async def fake(camera, ts, height=480):
        asked.append((camera, ts, height))
        if camera == "gone":
            raise RuntimeError("404")
        return b"jpeg-" + camera.encode()

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake)
    got = asyncio.run(frames.fetch_frames([(T0, "door"), (T0 + 5, "gone"), (T0 + 9, "drive")]))
    assert [(f.camera, f.jpeg) for f in got] == [("door", b"jpeg-door"), ("drive", b"jpeg-drive")]
    assert {h for _, _, h in asked} == {frames.FRAME_HEIGHT}


def test_recording_snapshot_url(monkeypatch):
    seen = {}

    class R:
        content = b"x"

        def raise_for_status(self):
            pass

    class C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            seen["url"] = url
            return R()

    monkeypatch.setattr(frigate_adapter, "_frigate_url", lambda: "http://frigate:5000")
    monkeypatch.setattr(frigate_adapter.httpx, "AsyncClient", C)
    assert asyncio.run(frigate_adapter.fetch_recording_snapshot("door", 1792998000.5)) == b"x"
    assert seen["url"] == "http://frigate:5000/api/door/recordings/1792998000.500/snapshot.jpg?height=480"


# ── route (code) ────────────────────────────────────────────────────────────────


def test_route_order_clock_direction_and_part_of_day(db):
    ep, events = _leaving()
    text = route.route_text(ep, events, "en", cameras=CAMS)
    assert text.index("Inngang") < text.index("Innkjørsel") < text.index("Port")
    assert "07:00:00" in text and "07:00:12" in text and "07:00:40" in text
    assert "morning" in text and "dusk" not in text and "dawn" not in text
    assert "leaving" in text  # front door → driveway → road side
    assert "front door" in text  # the camera's meaning comes from its role


def test_route_never_carries_face_claims(db):
    ep, events = _leaving(person_ref="persona", claim="unverified", kind="household")
    text = route.route_text(ep, events, "en", cameras=CAMS)
    assert "persona" not in text and "Person A" not in text


def test_route_door_only_says_it_cannot_tell(db):
    fstore.record_event(_ev("d", "door", T0, path=_p((0.5, 0.2, T0), (0.5, 0.6, T0 + 4)), role="front_door"))
    ep = fstore.episodes_between(0, T0 * 2)[0]
    text = route.route_text(ep, fstore.episode_events(ep.episode_id), "en", cameras=CAMS)
    assert "cannot tell" in text


def test_route_in_norwegian(db):
    ep, events = _leaving()
    text = route.route_text(ep, events, "nb", cameras=CAMS)
    assert "morgen" in text and "07:00:00" in text


# ── the scene step + the conclusion ─────────────────────────────────────────────

SCENE = {"persons": 1, "people": [{"age": "adult", "gender": "woman", "clothing": "pink jacket",
                                   "carried": "black shoulder bag"}],
         "vehicles": "", "at_door": "comes out of the front door", "scene": "A woman walks down the driveway."}
GOOD = {"summary": "Jeg så en person gå ut.", "unusual": {"value": False, "reason": "vanlig"},
        "notify_suggestion": None, "raise_at_dev_meeting": None,
        "confidence": {"level": "medium", "reasons": ["tre kameraer"]}}


@pytest.fixture
def loop(db, monkeypatch):
    calls, looks = [], []

    async def fake_llm(role, prompt, images=None, rid=""):
        if (":look" in rid):  # s16: the gate's closed look (a person) — counted apart from these calls
            looks.append(prompt)
            return {"text": json.dumps({"person": True, "animal": "none", "vehicle": False, "nothing": False})}
        calls.append({"role": role, "prompt": prompt, "images": images})
        return {"text": json.dumps(SCENE if role == "frigate" else GOOD) + " [LANG:nb]"}

    async def fake_snap(camera, ts, height=480):
        return f"{camera}-{ts:.0f}".encode()

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", fake_llm)
    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", fake_snap)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": True, "episode_gap_s": 120, "conclude": {
        "idle_gate_s": 30, "max_attempts": 3, "poll_s": 30, "urgent_classes": [], "urgent_labels": [],
        "urgent_when_away_person": False}})
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "en")
    monkeypatch.setattr(normalcy, "_load_baseline", dict)
    monkeypatch.setattr(conclude, "_world_rows", list)  # P144-s06e: never read the live knowledge.db
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    return calls


def test_scene_call_then_conclusion(loop):
    _leaving(person_ref="persona", claim="unverified", kind="household")
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    assert [c["role"] for c in loop] == ["frigate", "frigate_conclude"]
    scene_call, concl_call = loop
    n = len(scene_call["images"])
    assert 3 <= n <= frames.N_FRAMES
    assert all(isinstance(i, str) for i in scene_call["images"])  # base64, one per image
    for i in range(1, n + 1):
        assert f"image {i} =" in scene_call["prompt"]
    assert "07:00:00" in scene_call["prompt"]  # clock per image
    assert "leaving" in scene_call["prompt"]  # K's route in the same prompt (Part 0)
    assert "persona" not in scene_call["prompt"] and "Person A" not in scene_call["prompt"]  # no claim
    ep = fstore.episodes_between(0, T0 * 2)[0]
    assert ep.scene["status"] == "done" and ep.scene["scene"] == SCENE["scene"]
    assert ep.scene["claims"] == [{"person_ref": "persona", "person_kind": "household",
                                   "claim_status": "unverified"}] * 3
    assert "leaving" in ep.route
    # the 27B gets route + scene
    assert "leaving" in concl_call["prompt"] and SCENE["scene"] in concl_call["prompt"]
    assert "black shoulder bag" in concl_call["prompt"]


def test_scene_without_frames_still_concludes_with_route(loop, monkeypatch):
    async def none(camera, ts, height=480):
        raise RuntimeError("no recording")

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", none)
    _leaving()
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    assert [c["role"] for c in loop] == ["frigate_conclude"]
    ep = fstore.episodes_between(0, T0 * 2)[0]
    assert ep.scene == {"status": "failed", "reason": "no_frames"}
    assert "leaving" in loop[0]["prompt"]


def test_scene_is_not_repeated_on_a_conclusion_retry(loop, monkeypatch):
    replies = iter(["not json", json.dumps(GOOD)])

    async def fake_llm(role, prompt, images=None, rid=""):
        if (":look" in rid):  # s16: the closed look, once per episode like the scene
            return {"text": json.dumps({"person": True, "animal": "none", "vehicle": False, "nothing": False})}
        loop.append({"role": role})
        return {"text": json.dumps(SCENE) if role == "frigate" else next(replies)}

    monkeypatch.setattr(conclude, "ask_llm_role", fake_llm)
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    asyncio.run(conclude.run_once(now=T0 + 1100))
    assert [c["role"] for c in loop] == ["frigate", "frigate_conclude", "frigate_conclude"]


def test_parse_scene():
    ok, why = conclude.parse_scene("```json\n" + json.dumps(SCENE) + "\n``` [LANG:en]")
    assert why == "" and ok["persons"] == 1 and ok["people"][0]["carried"] == "black shoulder bag"
    assert conclude.parse_scene("I see a person")[0] is None
    assert conclude.parse_scene(json.dumps({"persons": 1}))[1] == "scene_missing"


# ── found on real episodes (2026-10-27): event counts read as people; road_facing ≠ «at the road» ────────────


def test_route_says_events_not_people(db):
    for i in range(3):  # one person crossing three cameras = three Frigate events, never at the same time
        fstore.record_event(_ev(f"p{i}", ("door", "drive", "drive")[i], T0 + 10 * i,
                                path=_p((0.2, 0.5, T0 + 10 * i), (0.6, 0.5, T0 + 10 * i + 4)),
                                role=("front_door", "driveway", "driveway")[i]))
    ep = fstore.episodes_between(0, T0 * 2)[0]
    text = route.route_text(ep, fstore.episode_events(ep.episode_id), "en", cameras=CAMS)
    assert "person ×3" not in text
    assert "3 Frigate events" in text and "at most 1 person at the same time" in text


def test_road_facing_camera_last_is_not_called_leaving(db):
    # this house: the road_facing camera hangs on the house wall — a woman walking up from the road ends there
    cams = {"gate": {"display_name": "Port", "role": "driveway"}, "wall": {"display_name": "Vegg", "role": "road_facing"}}
    fstore.record_event(_ev("a", "gate", T0, path=_p((0.2, 0.5, T0), (0.6, 0.5, T0 + 4)), role="driveway"))
    fstore.record_event(_ev("b", "wall", T0 + 8, path=_p((0.2, 0.5, T0 + 8), (0.9, 0.5, T0 + 12)), role="road_facing"))
    ep = fstore.episodes_between(0, T0 * 2)[0]
    text = route.route_text(ep, fstore.episode_events(ep.episode_id), "en", cameras=cams)
    assert "leaving" not in text and "arriving" not in text
    assert "cannot tell" in text


def test_route_arriving_ends_at_front_door(db):
    fstore.record_event(_ev("a", "drive", T0, path=_p((0.2, 0.5, T0), (0.6, 0.5, T0 + 4)), role="driveway"))
    fstore.record_event(_ev("b", "door", T0 + 8, path=_p((0.5, 0.2, T0 + 8), (0.5, 0.6, T0 + 12)), role="front_door"))
    ep = fstore.episodes_between(0, T0 * 2)[0]
    assert "arriving" in route.route_text(ep, fstore.episode_events(ep.episode_id), "en", cameras=CAMS)


def test_parse_scene_vehicles_list_becomes_text():
    raw = dict(SCENE, vehicles=[{"type": "grey SUV", "location": "on the road"}, "trailer with a green tarp"])
    ok, _ = conclude.parse_scene(json.dumps(raw))
    assert ok["vehicles"] == "grey SUV (on the road); trailer with a green tarp"


def test_parse_scene_rejects_the_schema_placeholder():
    # seen live (ep97): the 9B copied the schema — "scene": "..."
    ok, why = conclude.parse_scene(json.dumps({"persons": 1, "people": [], "vehicles": "...", "at_door": "...",
                                               "scene": "..."}))
    assert ok is None and why == "scene_missing"
    ok, _ = conclude.parse_scene(json.dumps(dict(SCENE, vehicles="...", at_door="...")))
    assert ok["vehicles"] == "" and ok["at_door"] == ""


def test_conclusion_prompt_says_code_wins_over_the_scene(loop):
    _leaving()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    prompt = loop[-1]["prompt"]
    assert "where it disagrees with the route and time above, the code is right" in prompt


@pytest.fixture(autouse=True)
def _one_call_scene(monkeypatch):
    """P144-s25: these tests cover the one-call scene, which is now the fallback when the frame-by-frame run fails
    (tests/test_p144_s25_frames_step.py covers the frame run). Their fake models do not answer frame calls."""
    monkeypatch.setattr(conclude, "FRAMES_ON", False)
