"""P144-s14 item 1 — the label claim gate: Frigate's label is a claim, like a face.

After the 9B scene step, code compares Frigate's person/animal labels in the episode with what the 9B saw. The scene
lists no person and no animal → code concludes «likely false detection» (`by: code`, not unusual, no alert,
`false_detection` stored for the review list) and the 27B is never asked. Urgent cases always go to the 27B.
Decided from the `people` / `animals` lists — never from the `persons` count, never from Frigate's score.
Never touches live state (tmp DBs, fake LLM, weather faked).
"""

import asyncio
import json
from zoneinfo import ZoneInfo

import pytest

from adapters import llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.frigate_user import conclude, route
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

UTC = ZoneInfo("UTC")
T0 = 1_793_143_388.0  # 2026-10-28 00:23:08 Europe/Oslo (episode 47's Frigate event)
OSLO = ZoneInfo("Europe/Oslo")

CAMS = {"east": {"display_name": "utekamera nedside øst", "role": "driveway"},
        "road": {"display_name": "veikamera", "role": "road_facing"},
        "door": {"display_name": "Inngang", "role": "front_door"}}

FALSE_DEER_SCENE = {  # the 9B on episode 47 (the user's screenshot): light streaks, a car with lights, no animal
    "persons": 0, "people": [], "animals": [], "vehicles": "en bil med lys",
    "at_door": "", "scene": "Lysstriper over bildet fra billykter, vanndråper på linsen, en bil med lys.",
}
# s16: the gate reads the closed blind look (form B), not the description — the fixture gives both
LOOK_NOTHING_BUT_CAR = {"status": "done", "person": False, "animal": None, "vehicle": True, "nothing": False}
LOOK_DEER = {"status": "done", "person": False, "animal": "rådyr", "vehicle": False, "nothing": False}
LOOK_PERSON = {"status": "done", "person": True, "animal": None, "vehicle": False, "nothing": False}
REAL_DEER_SCENE = {
    "persons": 0, "people": [], "animals": [{"kind": "rådyr", "count": 1}], "vehicles": "",
    "at_door": "", "scene": "Et rådyr går langs veien i IR-lys ved autovernet.",
}


def _p(*pts):
    return [[[x, y], ts] for x, y, ts in pts]


def _ev(event_id, camera, start, *, label="deer", role="driveway", kind="unknown", **kw):
    path = _p((0.2, 0.5, start), (0.6, 0.5, start + 4))
    return fstore.FrigateUserEvent(event_id=event_id, camera=camera, label=label, start_ts=start,
                                   end_ts=start + 10, path_data=path, camera_role=role, person_kind=kind, **kw)


def _answer():
    return {"summary": "Jeg så et rådyr.", "unusual": {"value": False, "reason": "vanlig"},
            "notify_suggestion": None, "raise_at_dev_meeting": None,
            "confidence": {"level": "medium", "reasons": ["ett kamera"]}}


class Llm:
    def __init__(self):
        self.calls = []

    async def __call__(self, role, prompt, images=None, rid=""):
        self.calls.append({"role": role, "prompt": prompt})
        return {"text": json.dumps(_answer())}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    llm = Llm()
    state = {"scene": dict(FALSE_DEER_SCENE), "look": dict(LOOK_NOTHING_BUT_CAR)}

    async def fake_scene(ep, events, lang, kept=None):  # s24: `kept` = frames already on disk (a retry)
        return {"status": "done", **state["scene"], "look": state["look"], "frames": [], "claims": [],
                "role": "frigate", "lang": lang}, "rute fra koden"

    async def no_weather(ep):
        return {"status": "ok", "source": "met.no", "precip_mm_h": 1.2, "wind": 4.0, "wind_unit": "m/s",
                "cloud_pct": 100.0}

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude, "_scene_step", fake_scene)
    monkeypatch.setattr(conclude, "_episode_weather", no_weather)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(conclude, "_world_rows", list, raising=False)
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 28, "buckets": {}})
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(conclude, "get_local_tz", lambda: UTC)
    llm.state = state
    yield llm
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _only_episode():
    (ep,) = fstore.episodes_between(0, T0 * 2)
    return ep


def _run():
    return asyncio.run(conclude.run_once(now=T0 + 1000))


# ── the scene schema gets an `animals` list ─────────────────────────────────────


def test_scene_schema_and_parser_have_animals():
    assert '"animals"' in conclude._SCHEMA_SCENE
    parsed, why = conclude.parse_scene(json.dumps({
        "persons": 0, "people": [], "animals": [{"kind": "rådyr", "count": 2}, "katt", {"kind": ""}],
        "vehicles": "", "at_door": "", "scene": "To rådyr og en katt."}))
    assert why == ""
    assert parsed["animals"] == [{"kind": "rådyr", "count": 2}, {"kind": "katt", "count": 1}]


def test_scene_without_animals_key_parses_as_empty():
    parsed, _ = conclude.parse_scene(json.dumps({"persons": 0, "people": [], "scene": "Tomt."}))
    assert parsed["animals"] == []


# ── the gate ────────────────────────────────────────────────────────────────────


def test_false_deer_is_concluded_by_code_without_the_27b(env):
    assert fstore.record_event(_ev("d1", "east", T0))
    assert _run() == 1
    assert [c["role"] for c in env.calls] == []  # the 27B never asked
    c = _only_episode().conclusion
    assert c["status"] == "done" and c["by"] == "code"
    assert c["summary"].startswith("Trolig feildeteksjon")
    assert "«hjort»" in c["summary"] and "bare kjøretøy" in c["summary"]  # s16: the line is built from the look
    assert c["unusual"]["value"] is False and c["notify_suggestion"] is None
    fd = c["false_detection"]
    assert fd["frigate_labels"] == ["deer"]
    assert fd["seen"].startswith("ingen person eller dyr, bare kjøretøy")
    assert fd["weather"]["precip_mm_h"] == 1.2


def test_false_detection_line_carries_weather_and_part_of_day(env, monkeypatch):
    monkeypatch.setattr(route, "get_local_tz", lambda: OSLO)  # 00:23 in the household's clock = night
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    s = _only_episode().conclusion["summary"]
    assert "(natt, vær (met.no): nedbør 1,2 mm/t" in s


def test_real_deer_goes_to_the_27b(env):
    env.state["scene"] = dict(REAL_DEER_SCENE)
    env.state["look"] = dict(LOOK_DEER)
    assert fstore.record_event(_ev("d1", "road", T0, role="road_facing"))
    _run()
    assert [c["role"] for c in env.calls] == ["frigate_conclude"]
    c = _only_episode().conclusion
    assert c["by"] == "model" and "false_detection" not in c


def test_a_person_seen_in_the_look_is_not_gated(env):
    env.state["look"] = dict(LOOK_PERSON)
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert [c["role"] for c in env.calls] == ["frigate_conclude"]


def test_the_description_no_longer_gates(env):
    # s16: the description (C) is «what happened», not the gate — its count/lists are not read, the look decides
    env.state["scene"] = {**FALSE_DEER_SCENE, "persons": 2, "animals": [{"kind": "hund", "count": 1}]}
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert env.calls == [] and _only_episode().conclusion["by"] == "code"


def test_frigate_person_with_an_empty_scene_is_a_false_detection(env):
    env.state["scene"] = {**FALSE_DEER_SCENE, "scene": "En busk som beveger seg i vinden."}
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door"))
    _run()
    c = _only_episode().conclusion
    assert c["by"] == "code" and "«person»" in c["summary"] and env.calls == []


def test_a_failed_scene_never_gates(env, monkeypatch):
    async def failed(ep, events, lang, kept=None):
        return {"status": "failed", "reason": "no_frames"}, "rute"

    monkeypatch.setattr(conclude, "_scene_step", failed)
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert [c["role"] for c in env.calls] == ["frigate_conclude"]


@pytest.mark.parametrize("case", ["not_welcome", "away_person", "fire", "urgent_result"])
def test_urgent_cases_always_go_to_the_27b(env, monkeypatch, case):
    if case == "not_welcome":
        assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door", kind="not_welcome"))
    elif case == "away_person":
        monkeypatch.setattr(conclude, "_is_away", lambda: True)
        assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door"))
    elif case == "fire":
        assert fstore.record_event(_ev("f1", "door", T0, label="smoke", role="front_door"))
    else:
        assert fstore.record_event(_ev("d1", "east", T0))
        ep = _only_episode()
        assert fstore.conclude_episode(ep.episode_id, {"urgent": [{"event_id": "d1", "status": "done"}]})
    _run()
    assert [c["role"] for c in env.calls] == ["frigate_conclude"]
    assert "false_detection" not in _only_episode().conclusion


def test_score_is_never_used(env):
    # a high Frigate score does not save a deer the 9B did not see
    assert fstore.record_event(_ev("d1", "east", T0, claim_score=0.99))
    _run()
    assert _only_episode().conclusion["by"] == "code"


def test_false_detection_summary_in_english_and_german():
    fd = {"frigate_labels": ["deer", "person"], "seen": "light streaks", "weather": None}
    en = conclude.false_detection_summary(fd, "night", "en")
    de = conclude.false_detection_summary(fd, "Nacht", "de")
    assert en.startswith("Likely false detection") and "«deer»" in en and "light streaks" in en
    assert de.startswith("Wahrscheinlich Fehlerkennung") and "«Hirsch»" in de


# ── the 9B is blind to Frigate's animal label (acceptance set 2026-10-28: with «deer: 1» in the route the 9B
# listed a deer on the false ep47 3/3; blind 1/3 no animal) ─────────────────────────────────────────────────


def test_scene_route_hides_animal_labels_but_the_stored_route_keeps_them(monkeypatch):
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    ep = fstore.Episode(episode_id=1, start_ts=T0, end_ts=T0 + 14, cameras=["east"], labels=["deer"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    evs = [_ev("d1", "east", T0)]
    evs[0].path = [[0.2, 0.5, T0], [0.6, 0.5, T0 + 4]]
    blind = route.route_text(ep, evs, "nb", cameras=CAMS, blind=True)
    full = route.route_text(ep, evs, "nb", cameras=CAMS)
    assert "deer" not in blind and "hjort" not in blind
    assert "deer: 1" in full


def test_scene_route_hides_person_and_vehicle_labels_too(monkeypatch):
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    ep = fstore.Episode(episode_id=1, start_ts=T0, end_ts=T0 + 14, cameras=["door"], labels=["car", "person"],
                        person_refs=[], event_count=2, status="closed", conclusion=None, concluded_ts=None)
    evs = [_ev("p1", "door", T0, label="person"), _ev("c1", "door", T0 + 2, label="car")]
    for e in evs:
        e.path = [[0.2, 0.5, T0], [0.6, 0.5, T0 + 4]]
    blind = route.route_text(ep, evs, "nb", cameras=CAMS, blind=True)
    assert "person" not in blind and "car" not in blind  # s16 (s15 measured): every label hidden from the 9B
    assert "person: 1" in route.route_text(ep, evs, "nb", cameras=CAMS)  # the stored route keeps them


def test_the_real_scene_call_is_blind_and_the_stored_route_is_not(tmp_path, monkeypatch):
    from kaare_core.frigate_user import frames  # noqa: PLC0415 — only this test needs it

    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate.db")
    fstore.reset_for_tests()
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(fstore, "save_episode_frames", lambda *a, **k: 0)

    async def one_frame(picked):
        return [frames.Frame(ts=T0, camera="east", jpeg=b"\xff\xd8x")]

    seen = {}

    async def llm(role, prompt, images=None, rid=""):
        seen["prompt"] = prompt
        return {"text": json.dumps({**FALSE_DEER_SCENE})}

    monkeypatch.setattr(frames, "fetch_frames", one_frame)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude, "_enforce_shadow", lambda *a, **k: ([], None))
    ep = fstore.Episode(episode_id=1, start_ts=T0, end_ts=T0 + 14, cameras=["east"], labels=["deer"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    ev = _ev("d1", "east", T0)
    ev.path = [[0.2, 0.5, T0], [0.6, 0.5, T0 + 4]]
    scene, stored_route = asyncio.run(conclude._scene_step(ep, [ev], "nb"))
    assert "deer" not in seen["prompt"] and "hjort" not in seen["prompt"]
    assert "deer: 1" in stored_route
    assert scene["status"] == "done" and scene["animals"] == []
    fstore.reset_for_tests()


@pytest.mark.parametrize("vehicle, expect", [
    (False, "bildet viste ingen person eller dyr (fra bildemodellens lukkede svar)"),
    (True, "bildet viste ingen person eller dyr, bare kjøretøy (fra bildemodellens lukkede svar)"),
])
def test_the_false_detection_line_comes_from_the_look(env, vehicle, expect):
    # s16: built by code from the closed look's fields, whatever the description said (thin or not)
    env.state["scene"] = {**FALSE_DEER_SCENE, "scene": "natt", "scene_thin": True}
    env.state["look"] = {**LOOK_NOTHING_BUT_CAR, "vehicle": vehicle}
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert expect in _only_episode().conclusion["summary"]


def test_a_person_seen_by_the_single_image_check_blocks_the_gate(env):
    # episode 23 (2026-10-27): the 9 scene frames missed a far-away person, the per-event check (L3) saw one
    env.state["scene"] = {**FALSE_DEER_SCENE, "scene": "En innkjørsel med en trailer og en bil langs veien."}
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door",
                                   attributes={"persons": 1}, description="En person i mørk jakke.",
                                   check={"l3_blind": True}))  # s16: only a blind L3 count blocks
    _run()
    assert [c["role"] for c in env.calls] == ["frigate_conclude"]
    assert "false_detection" not in _only_episode().conclusion
