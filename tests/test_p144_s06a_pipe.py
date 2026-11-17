"""P144-s06a: the Frigate user's shadow pipe end to end with fakes — raw claim → L0/L1/L3 → code decision → store.
Nothing here reaches Frigate, an LLM, live users.db / frigate.db / configs or live logs."""

import asyncio
import io
import json

import pytest
from PIL import Image

from kaare_core.frigate_user import faces, pipe
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0
BIG_FACE = [{"label": "face", "box": [0.4, 0.2, 0.2, 0.3], "score": 0.8}]  # 192×216 px on 960×720
SMALL_FACE = [{"label": "face", "box": [0.4, 0.2, 0.05, 0.05], "score": 0.8}]


def _jpeg(color=(180, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (960, 720), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n"
        "  Person A: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
        "  Person B: {class: household}\n"
        "  Person C: {class: safe_guest, attributes: {beard: full, glasses: 'yes', age_band: adult}}\n"
    )
    fstore.reset_for_tests()
    ustore.init_db()
    for name in ("persona", "personb"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    ustore.update_user("personb", frigate_faces=["Person B"])
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()

    state = {"llm_calls": [], "llm_reply": {}, "face": BIG_FACE, "jpeg": _jpeg(), "away": False, "urgent": []}

    async def fake_get_event(event_id):
        return {"id": event_id, "start_time": T0, "end_time": T0 + 10, "data": {"attributes": state["face"]}}

    async def fake_snapshot(event_id):
        return state["jpeg"]

    async def fake_llm(role, prompt, images=None, rid=""):
        state["llm_calls"].append({"role": role, "prompt": prompt, "images": images})
        reply = {"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult", "headwear": "no",
                 "description": "a person in a blue jacket", "analysis": "someone at the front door"}
        reply.update(state["llm_reply"])
        return {"ok": True, "text": json.dumps(reply) + " [LANG:nb]", "meta": {}}

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", fake_get_event)
    monkeypatch.setattr(pipe, "_load_snapshot", fake_snapshot)
    monkeypatch.setattr(pipe, "ask_llm_role", fake_llm)
    monkeypatch.setattr(pipe.household_state, "is_away", lambda: state["away"])
    monkeypatch.setattr(pipe, "_place_of", lambda camera: "ute" if camera == "inngang" else None)

    async def fake_urgent(ev, img):
        state["urgent"].append((ev, img))

    monkeypatch.setattr(pipe, "_urgent_handler", fake_urgent)
    yield state
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _event(event_id="e1", camera="inngang", label="person"):
    return {"event_id": event_id, "camera": camera, "label": label, "score": 0.9, "top_score": 0.9,
            "sub_label": None, "sub_label_score": None, "duration": 10.0, "has_snapshot": True}


def _process(ev, name=None, score=None):
    async def main():
        result = await pipe.process(ev, name, score)
        await asyncio.sleep(0)
        return result
    return asyncio.run(main())


def _stored(event_id="e1"):
    return next(e for e in fstore.events_between(0, T0 * 2) if e.event_id == event_id)


def test_switch_off_stores_nothing_and_calls_nothing(env, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text("enabled: false\n")
    assert _process(_event(), "Person A", 0.95) is None
    assert env["llm_calls"] == []
    assert fstore.events_between(0, T0 * 2) == []


def test_small_face_is_unverified_without_llm_call(env):
    env["face"] = SMALL_FACE
    _process(_event(), "Person A", 0.95)
    assert env["llm_calls"] == []
    e = _stored()
    assert e.claim_status == "unverified"
    assert e.person_ref == "persona" and e.person_kind == "household"  # unverified keeps the name
    assert e.check["l0"]["verdict"] == "unsure"


def test_matching_attributes_unverified_keeps_name_with_place(env):
    _process(_event(), "Person A", 0.95)
    assert len(env["llm_calls"]) == 1
    call = env["llm_calls"][0]
    assert call["role"] == "frigate" and len(call["images"]) == 1
    assert "Person A" not in call["prompt"]  # the 9B never sees the claim
    assert "circle_beard" in call["prompt"]  # same beard value set as the face profile
    e = _stored()
    assert e.claim_status == "unverified" and e.person_ref == "persona"
    assert e.place == "ute"
    assert e.description == "a person in a blue jacket"
    assert "[LANG" not in (e.analysis or "")
    assert e.attributes["beard"] == "no"


def test_beard_on_no_beard_profile_is_doubt_not_rejection(env):
    # P144-s06c: one image's strong contradiction gives «unverified» + doubt (was «rejected», name dropped);
    # two agreeing observations in one episode reject — tests/test_p144_s06c_two_observations.py
    env["llm_reply"] = {"beard": "full"}
    _process(_event(), "Person A", 0.95)
    e = _stored()
    assert e.claim_status == "unverified" and e.person_ref == "persona"
    assert e.check["doubt"]["strong"][0]["field"] == "beard"


def test_opted_out_claim_is_checked_then_not_stored(env):
    """The bug: the strip at ingestion hid the claim from the check. Now the check sees it, storage drops it."""
    env["llm_reply"] = {"beard": "full"}
    decision = _process(_event(), "Person B", 0.95)
    assert decision["claim"] is True  # the check saw Frigate's claim
    e = _stored()
    assert e.person_ref is None and e.person_kind in ("untracked", "unknown")
    assert "Person B" not in json.dumps(e.check) and "personb" not in json.dumps(e.check)


def test_guest_face_name_is_person_ref(env):
    env["llm_reply"] = {"beard": "full", "glasses": "yes", "age_band": "adult"}
    _process(_event(), "Person C", 0.95)
    e = _stored()
    assert e.person_ref == "Person C" and e.person_kind == "safe_guest"
    assert e.claim_status == "unverified"  # one yes (L3) is not two independent ones


def test_no_claim_is_none_and_unknown(env):
    _process(_event(), None, None)
    e = _stored()
    assert e.claim_status == "none" and e.person_ref is None and e.person_kind == "unknown"
    assert e.description  # appearance of an unknown person is kept (7 days, s07)


def test_no_snapshot_no_check_is_none(env):
    async def no_snapshot(event_id):
        return None
    pipe._load_snapshot = no_snapshot  # restored by monkeypatch teardown of the fixture's patch
    _process(_event(), "Person A", 0.95)
    e = _stored()
    assert e.claim_status == "none" and e.person_ref is None


def test_car_is_stored_without_llm_call(env):
    _process(_event(label="car"), "AB12345", 0.9)
    assert env["llm_calls"] == []
    e = _stored()
    assert e.label == "car" and e.person_ref is None


def test_urgent_handler_gets_the_stored_event(env):
    _process(_event(), "Person A", 0.95)
    stored, img = env["urgent"][0]
    assert stored.event_id == "e1" and stored.episode_id is not None
    assert img  # the snapshot in RAM


def test_frigate_profile_logs_lang_tag_in_shadow(env, monkeypatch):
    hits = []
    monkeypatch.setattr(pipe.model_enforcement, "_log_enforcement",
                        lambda rid, detector, event, phrase="", outcome="": hits.append((detector, event)))
    _process(_event(), "Person A", 0.95)
    assert ("frigate_lang_tag", "shadow") in hits
    assert len(env["llm_calls"]) == 1  # shadow: no retry


def test_broken_json_is_logged_and_event_still_stored(env, monkeypatch):
    hits = []
    monkeypatch.setattr(pipe.model_enforcement, "_log_enforcement",
                        lambda rid, detector, event, phrase="", outcome="": hits.append(detector))

    async def broken(role, prompt, images=None, rid=""):
        return {"ok": True, "text": "I see a person", "meta": {}}

    monkeypatch.setattr(pipe, "ask_llm_role", broken)
    _process(_event(), "Person A", 0.95)
    assert "cut_or_invalid_output" in hits
    e = _stored()
    assert e.claim_status == "unverified" and e.check["l3"]["verdict"] == "unsure"


def test_raw_claim_never_logged(env, caplog):
    caplog.set_level("DEBUG")
    _process(_event(), "Person B", 0.95)
    assert "Person B" not in caplog.text
