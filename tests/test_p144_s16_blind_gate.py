"""P144-s16 Part A — the gate is one blind, closed, non-leading 9B question (s15 form B); code compares it with Frigate.

- The look call: the episode's frames + «what is in these images: person / animal (which) / vehicle / nothing?»,
  short JSON, no route, no Frigate label, no name (s15: 45/45 clear people, 34/35 far away, «person» on empty 2/40,
  false deer 0/5).
- Code: Frigate claimed a person/animal and B saw neither → «likely false detection» by code, no description call, no
  27B. Urgent cases never gated; a failed look never gates.
- L3 (item 4): a blind L3 that counted a person keeps the gate open; a primed L3 (label in its prompt, before s16) does
  not.
- Every Frigate label and name is out of what the 9B sees: the look, the description's route, the L3 prompt.
- P142: empty / invalid look answers are counted by the `frigate` profile like scene answers.
Never touches live state (tmp DBs, fake LLM, weather faked).
"""

import asyncio
import json
import re
from zoneinfo import ZoneInfo

import pytest

from adapters import llm_adapter
from kaare_core import app_state, normalcy
from kaare_core import model_enforcement as me
from kaare_core.frigate_user import conclude, frames, pipe, route
from kaare_core.frigate_user import store as fstore
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

UTC = ZoneInfo("UTC")
T0 = 1_793_143_388.0
CAMS = {"east": {"display_name": "utekamera nedside øst", "role": "driveway"},
        "door": {"display_name": "Inngang", "role": "front_door"}}

NOTHING = {"person": False, "animal": "none", "vehicle": False, "nothing": True}
NOTHING_BUT_CAR = {"person": False, "animal": "none", "vehicle": True, "nothing": False}
A_PERSON = {"person": True, "animal": "none", "vehicle": False, "nothing": False}
A_DEER = {"person": False, "animal": "rådyr", "vehicle": False, "nothing": False}
SCENE = {"persons": 1, "people": [{"age": "adult", "gender": "man", "clothing": "", "carried": ""}],
         "animals": [], "vehicles": "", "at_door": "", "scene": "En mann går opp innkjørselen mot døra."}
GOOD = {"summary": "Jeg så en person.", "unusual": {"value": False, "reason": "vanlig"},
        "notify_suggestion": None, "raise_at_dev_meeting": None,
        "confidence": {"level": "medium", "reasons": ["ett kamera"]}}


def _ev(event_id, camera, start, *, label="deer", role="driveway", kind="unknown", **kw):
    path = [[[0.2, 0.5], start], [[0.6, 0.5], start + 4]]
    return fstore.FrigateUserEvent(event_id=event_id, camera=camera, label=label, start_ts=start,
                                   end_ts=start + 10, path_data=path, camera_role=role, person_kind=kind, **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    state = {"look": json.dumps(NOTHING), "calls": []}

    async def llm(role, prompt, images=None, rid=""):
        kind = "look" if (":look" in rid) else "scene" if (":scene" in rid) else role
        state["calls"].append({"kind": kind, "role": role, "prompt": prompt, "images": images})
        if kind == "look":
            return {"text": state["look"]}
        return {"text": json.dumps(SCENE if kind == "scene" else GOOD)}

    async def two_frames(picked):
        return [frames.Frame(ts=T0, camera="east", jpeg=b"\xff\xd8a"),
                frames.Frame(ts=T0 + 3, camera="door", jpeg=b"\xff\xd8b")]

    async def no_weather(ep):
        return None

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(frames, "fetch_frames", two_frames)
    monkeypatch.setattr(fstore, "save_episode_frames", lambda *a, **k: 0)
    monkeypatch.setattr(conclude, "_episode_weather", no_weather)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(conclude, "_world_rows", list)
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 28, "buckets": {}})
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(conclude, "get_local_tz", lambda: UTC)
    yield state
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _run():
    return asyncio.run(conclude.run_once(now=T0 + 1000))


def _only_episode():
    (ep,) = fstore.episodes_between(0, T0 * 2)
    return ep


def _kinds(env):
    return [c["kind"] for c in env["calls"]]


# ── the look prompt is s15's form B ─────────────────────────────────────────────


def test_look_prompt_is_the_measured_closed_question():
    picked = [frames.Frame(ts=T0, camera="east", jpeg=b"x"), frames.Frame(ts=T0 + 3, camera="door", jpeg=b"y")]
    p = conclude.build_look_prompt(picked, "nb", CAMS)
    assert ("Hva er i bildene? Velg alt som passer: person, dyr (hvilken art), kjøretøy, ingenting. Svar bare ut fra "
            "det du faktisk ser.") in p
    assert '{"person": true|false, "animal": "art eller none", "vehicle": true|false, "nothing": true|false}' in p
    assert "bilde 1 =" in p and "bilde 2 =" in p
    assert "Rute" not in p and "Frigate" not in p  # no route text, no claim
    for lang in ("en", "de"):
        other = conclude.build_look_prompt(picked, lang, CAMS)
        assert '"person": true|false' in other and other != p


@pytest.mark.parametrize("text, expect", [
    (json.dumps(NOTHING), {"person": False, "animal": None, "vehicle": False, "nothing": True}),
    ("```json\n" + json.dumps(A_DEER) + "\n```", {"person": False, "animal": "rådyr", "vehicle": False,
                                                  "nothing": False}),
    ('{"person": "true", "animal": "ingen", "vehicle": "false", "nothing": false}',
     {"person": True, "animal": None, "vehicle": False, "nothing": False}),
])
def test_parse_look(text, expect):
    parsed, why = conclude.parse_look(text)
    assert why == "" and parsed == expect


@pytest.mark.parametrize("text, why", [
    ("", "empty"), ("I see a man", "no_json_object"), ('{"vehicle": true}', "look_fields_missing"),
])
def test_parse_look_refuses(text, why):
    assert conclude.parse_look(text) == (None, why)


# ── the gate reads B, code compares ────────────────────────────────────────────


def test_b_saw_nothing_closes_a_false_person_without_description_or_27b(env):
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door"))
    assert _run() == 1
    assert _kinds(env) == ["look"]  # no description call, no 27B
    c = _only_episode().conclusion
    assert c["by"] == "code" and c["summary"].startswith("Trolig feildeteksjon")
    assert "«person»" in c["summary"] and "ingen person eller dyr" in c["summary"]
    ep = _only_episode()
    assert ep.scene["look"]["status"] == "done" and ep.scene["look"]["person"] is False
    assert ep.scene["status"] == "skipped"


def test_b_saw_only_a_vehicle_says_so(env):
    env["look"] = json.dumps(NOTHING_BUT_CAR)
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert "bare kjøretøy" in _only_episode().conclusion["summary"]


@pytest.mark.parametrize("look", [A_PERSON, A_DEER])
def test_b_saw_a_being_goes_on_to_description_and_27b(env, look):
    env["look"] = json.dumps(look)
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert _kinds(env) == ["look", "scene", "frigate_conclude"]
    c = _only_episode().conclusion
    assert c["by"] == "model" and "false_detection" not in c


def test_the_description_no_longer_gates(env):
    # C listed no person/animal, but B saw a person: B decides (C is «what happened», not the gate)
    env["look"] = json.dumps(A_PERSON)
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door"))
    _run()
    assert _kinds(env)[-1] == "frigate_conclude"


def test_a_failed_look_never_gates(env):
    env["look"] = "Jeg ser en innkjørsel."
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    assert _kinds(env) == ["look", "scene", "frigate_conclude"]
    ep = _only_episode()
    assert ep.scene["look"]["status"] == "failed" and ep.conclusion["by"] == "model"


def test_the_27b_sees_the_blind_look(env):
    env["look"] = json.dumps(A_PERSON)
    assert fstore.record_event(_ev("d1", "east", T0))
    _run()
    concl = env["calls"][-1]["prompt"]
    assert "lukket spørsmål" in concl and "dyr: ingen" in concl


def test_urgent_case_is_never_gated(env, monkeypatch):
    monkeypatch.setattr(conclude, "_is_away", lambda: True)
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door"))
    _run()
    assert _kinds(env)[-1] == "frigate_conclude"
    assert "false_detection" not in _only_episode().conclusion


# ── item 4: L3 ──────────────────────────────────────────────────────────────────


def test_a_blind_l3_person_keeps_the_gate_open(env):
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door",
                                   attributes={"persons": 1}, check={"l3_blind": True}))
    _run()
    assert _kinds(env)[-1] == "frigate_conclude"
    assert "false_detection" not in _only_episode().conclusion


def test_a_primed_l3_person_does_not_block_the_gate(env):
    # stored before s16: Frigate's label was in the L3 prompt → its count is the yes-bias s15 measured
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door",
                                   attributes={"persons": 1}))
    _run()
    assert _only_episode().conclusion["by"] == "code"


# ── no Frigate label or name reaches the 9B ─────────────────────────────────────


def test_no_label_or_name_in_any_9b_prompt(env):
    env["look"] = json.dumps(A_PERSON)
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door", person_ref="persona",
                                   kind="household", claim_status="unverified"))
    assert fstore.record_event(_ev("d1", "east", T0 + 5))
    assert fstore.record_event(_ev("c1", "east", T0 + 8, label="car"))
    _run()
    nine_b = [c["prompt"] for c in env["calls"] if c["kind"] in ("look", "scene")]
    assert len(nine_b) == 2
    for p in nine_b:
        assert "persona" not in p and "deer" not in p and "hjort" not in p
        assert not re.search(r"\bcar\b", p)
    route_part = nine_b[1].split("\n\n")[1]  # the description call's route block
    assert not re.search(r"\bperson\b", route_part)


@pytest.mark.parametrize("lang, phrase", [
    ("nb", "ett objekt sett av flere kameraer"), ("en", "one object seen by several cameras"),
    ("de", "ein Objekt auf mehreren Kameras"),
])
def test_blind_route_template_says_object(lang, phrase, monkeypatch):
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    ep = fstore.Episode(episode_id=1, start_ts=T0, end_ts=T0 + 14, cameras=["door"], labels=["person"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    ev = _ev("p1", "door", T0, label="person")
    ev.path = [[0.2, 0.5, T0], [0.6, 0.5, T0 + 4]]
    blind = route.route_text(ep, [ev], lang, cameras=CAMS, blind=True)
    assert phrase in blind
    assert not re.search(r"\b[Pp]erson\b", blind)


def test_stored_route_for_an_animal_says_object_not_person(monkeypatch):
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    ep = fstore.Episode(episode_id=1, start_ts=T0, end_ts=T0 + 14, cameras=["east"], labels=["deer"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    ev = _ev("d1", "east", T0)
    ev.path = [[0.2, 0.5, T0], [0.6, 0.5, T0 + 4]]
    full = route.route_text(ep, [ev], "en", cameras=CAMS)
    assert "deer: 1" in full and "one object seen by several cameras" in full


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_l3_prompt_carries_no_label(lang):
    p = _t_i18n("frigate_user_l3_prompt", lang, camera="inngang")
    assert "{label}" not in p and ", person)" not in p and "(inngang)" in p


def test_pipe_l3_call_has_no_label(monkeypatch):
    seen = {}

    async def llm(role, prompt, images=None, rid=""):
        seen["prompt"] = prompt
        return {"text": json.dumps({"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult",
                                    "headwear": "no", "description": "x", "analysis": "y"}), "meta": {}}

    monkeypatch.setattr(pipe, "ask_llm_role", llm)
    monkeypatch.setattr(pipe, "get_lang", lambda *a, **k: "nb")
    parsed, err = asyncio.run(pipe._l3("inngang", "deer", "aW1n", "e1"))
    assert parsed and err == ""
    assert "deer" not in seen["prompt"] and "hjort" not in seen["prompt"]


# ── P142: the look is covered by the frigate profile ─────────────────────────────


@pytest.mark.parametrize("text, detector", [
    ("", "frigate_empty_output"), ("Jeg ser en innkjørsel.", "cut_or_invalid_output"),
    ('{"vehicle": true}', "cut_or_invalid_output"),
])
def test_bad_look_answers_are_counted_by_the_frigate_profile(text, detector, monkeypatch):
    logged = []
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: logged.append(a))
    parsed, why, hits, _ = conclude._judge_look({"text": text}, "nb", "frigate-look-1")
    assert parsed is None and why
    assert detector in hits
    assert any(a[1] == detector for a in logged)


@pytest.fixture(autouse=True)
def _one_call_scene(monkeypatch):
    """P144-s25: these tests cover the one-call scene, which is now the fallback when the frame-by-frame run fails
    (tests/test_p144_s25_frames_step.py covers the frame run). Their fake models do not answer frame calls."""
    monkeypatch.setattr(conclude, "FRAMES_ON", False)
