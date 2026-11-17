"""P144-s19: the second face sensor's vote inside the shadow pipe — agree, disagree, Double Take only, Frigate only,
unknown name, opted-out names on both sides, a late vote, Double Take off/offline, duplicate attempts, and the 9B
staying claim-free. Fakes only: no broker, no Frigate, no LLM, no live users.db / frigate.db / configs."""

import asyncio
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from kaare_core.frigate_user import face_votes, faces, pipe, query
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0
BIG_FACE = [{"label": "face", "box": [0.4, 0.2, 0.2, 0.3], "score": 0.8}]


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (960, 720), (180, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: true}\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n"
        "  Person A: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
        "  Person B: {class: household, attributes: {beard: full, age_band: adult}}\n"
        "  Person D: {class: household, attributes: {beard: 'no', age_band: adult}}\n"
    )
    fstore.reset_for_tests()
    ustore.init_db()
    for name in ("persona", "personb", "persond"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    ustore.update_user("personb", frigate_faces=["Person B"])
    ustore.update_user("persond", frigate_faces=["Person D"])
    frigate_consent.invalidate()
    face_votes.reset_for_tests()

    state = {"llm_calls": [], "llm_reply": {}}

    async def fake_get_event(event_id):
        return {"id": event_id, "start_time": T0, "end_time": T0 + 10, "data": {"attributes": BIG_FACE}}

    async def fake_snapshot(event_id):
        return _jpeg()

    async def fake_llm(role, prompt, images=None, rid=""):
        state["llm_calls"].append({"role": role, "prompt": prompt})
        reply = {"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult", "headwear": "no",
                 "description": "a person in a blue jacket", "analysis": "someone at the door"}
        reply.update(state["llm_reply"])
        return {"ok": True, "text": json.dumps(reply), "meta": {}}

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", fake_get_event)
    monkeypatch.setattr(pipe, "_load_snapshot", fake_snapshot)
    monkeypatch.setattr(pipe, "ask_llm_role", fake_llm)
    monkeypatch.setattr(pipe.household_state, "is_away", lambda: False)
    monkeypatch.setattr(pipe, "_place_of", lambda camera: None)
    monkeypatch.setattr(pipe, "_urgent_handler", None)
    yield state
    face_votes.reset_for_tests()
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _event(event_id="e1"):
    return {"event_id": event_id, "camera": "inngang", "label": "person", "score": 0.9, "top_score": 0.9,
            "sub_label": None, "sub_label_score": None, "duration": 10.0, "has_snapshot": True}


def _vote(event_id, name, confidence=95.0, kind="mqtt"):
    payload = json.dumps({"id": event_id, "camera": "inngang",
                          "match": {"name": name, "confidence": confidence, "match": True, "type": kind,
                                    "base64": "AAAA"}}).encode()
    return face_votes.on_message(f"double-take/matches/{name}", payload)


def _run(coro):
    async def main():
        out = await coro
        await asyncio.sleep(0)
        return out
    return asyncio.run(main())


def _process(name=None, score=0.95, event_id="e1"):
    return _run(pipe.process(_event(event_id), name, score))


def _stored(event_id="e1"):
    return fstore.event_by_id(event_id)


def _word(e):
    return query.certainty(e.check, e.claim_status)


def test_agree_is_confirmed_very_likely(env):
    # J14 (ep 266): two «uncertain» sensors agreeing no longer confirm, and machines alone say at most «likely» —
    # was: both on Auto → confirmed → «very_likely». Now Frigate is set «likely» so the confirm path is still tested.
    Path(fsettings.CONFIG_PATH).write_text(
        "enabled: true\nface_votes: {enabled: true}\nface_sensors: {frigate: {mode: admin, trust: likely}}\n")
    _vote("e1", "person a")
    _process("Person A")
    e = _stored()
    assert e.claim_status == "confirmed" and e.person_ref == "persona"
    assert e.check["sensors"]["relation"] == "agree"
    assert e.check["l2"]["names"][0]["ref"] == "persona"
    assert _word(e) == "likely"


def test_agree_of_auto_sensors_stays_unverified(env):
    _vote("e1", "person a")
    _process("Person A")
    e = _stored()
    assert e.claim_status == "unverified" and e.check["sensors"]["relation"] == "agree"


def test_disagree_with_lean_stores_the_leaning_candidate_at_likely(env):
    _vote("e1", "person a")  # image: no beard; Person B's profile: full beard → B contradicted, lean to A
    _process("Person B")
    e = _stored()
    assert e.check["sensors"]["relation"] == "disagree" and e.check["sensors"]["lean"] == "persona"
    assert e.person_ref == "persona" and e.claim_status == "unverified"
    assert _word(e) == "likely"
    assert e.check["review"] is True
    assert e.check["sensors"]["frigate"] == "personb"  # the disagreement is kept for the 4b review list


def test_disagree_without_lean_is_uncertain(env):
    _vote("e1", "person d")  # A and D have the same profile → no lean
    _process("Person A")
    e = _stored()
    assert e.person_ref == "persona" and _word(e) == "uncertain"
    assert e.check["sensors"]["double_take"] == "persond"


def test_double_take_only_runs_the_check_and_is_likely(env):
    _vote("e1", "person a")
    _process(None, None)
    e = _stored()
    assert e.person_ref == "persona" and e.person_kind == "household"
    assert e.claim_status == "unverified" and e.check["sensors"]["relation"] == "double_take_only"
    assert e.claim_score is None  # Double Take's confidence is not Frigate's score
    assert _word(e) == "likely"


def test_frigate_only_is_as_before(env):
    _process("Person A")
    e = _stored()
    assert e.claim_status == "unverified" and "sensors" not in e.check and "l2" not in e.check
    assert _word(e) == "likely"


def test_unknown_name_is_never_a_user(env):
    _vote("e1", "zed")
    _process("Person A")
    e = _stored()
    assert e.person_ref == "persona" and e.check["sensors"]["relation"] == "disagree"
    assert _word(e) == "uncertain"
    assert "zed" not in json.dumps(e.check)


def test_unknown_name_alone_stores_no_name(env):
    _vote("e1", "zed")
    _process(None, None)
    e = _stored()
    assert e.person_ref is None and e.claim_status == "none"


def test_opted_out_frigate_name_with_agreeing_vote_is_untracked(env):
    ustore.set_frigate_tracking("persona", False)
    frigate_consent.invalidate()
    _vote("e1", "person a")
    _process("Person A")
    e = _stored()
    assert e.person_ref is None and e.person_kind == "untracked"
    text = json.dumps(e.check)
    assert "persona" not in text and "Person A" not in text


def test_opted_out_double_take_name_is_not_stored(env):
    ustore.set_frigate_tracking("persond", False)
    frigate_consent.invalidate()
    _vote("e1", "person d")
    _process("Person A")
    e = _stored()
    assert e.person_ref == "persona"
    text = json.dumps(e.check)
    assert "persond" not in text and "Person D" not in text
    assert e.check["sensors"]["double_take_kind"] == "untracked"


def test_opted_out_double_take_only_is_untracked(env):
    ustore.set_frigate_tracking("persona", False)
    frigate_consent.invalidate()
    _vote("e1", "person a")
    _process(None, None)
    e = _stored()
    assert e.person_ref is None and e.person_kind == "untracked"
    assert "persona" not in json.dumps(e.check)


def test_late_vote_updates_the_stored_check_without_a_model_call(env):
    # J14: Frigate set «likely» so a late agreeing vote can still confirm (was: both on Auto); word capped at «likely»
    Path(fsettings.CONFIG_PATH).write_text(
        "enabled: true\nface_votes: {enabled: true}\nface_sensors: {frigate: {mode: admin, trust: likely}}\n")
    _process("Person A")
    assert _stored().claim_status == "unverified"
    calls = len(env["llm_calls"])

    async def main():
        pipe.start_face_votes()
        _vote("e1", "person a")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if _stored().claim_status == "confirmed":
                break
    asyncio.run(main())
    e = _stored()
    assert e.claim_status == "confirmed" and e.check["l2"]["late"] is True
    assert len(env["llm_calls"]) == calls  # no new LLM call — code only
    assert _word(e) == "likely"


def test_late_disagreement_after_double_take_only_keeps_it_honest(env):
    _process("Person A")

    async def main():
        pipe.start_face_votes()
        _vote("e1", "person d")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if "sensors" in _stored().check:
                break
    asyncio.run(main())
    assert _word(_stored()) == "uncertain"


def test_off_or_offline_changes_nothing(env, tmp_path):
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nface_votes: {enabled: false}\n")
    _vote("e1", "person a")
    _process("Person A")
    e = _stored()
    assert "sensors" not in e.check and e.claim_status == "unverified"


def test_duplicate_attempts_are_one_vote(env):
    # J14: Frigate set «likely» so «confirmed» still shows the attempts counted as one vote (was: both on Auto)
    Path(fsettings.CONFIG_PATH).write_text(
        "enabled: true\nface_votes: {enabled: true}\nface_sensors: {frigate: {mode: admin, trust: likely}}\n")
    for kind in ("mqtt", "latest", "snapshot", "latest"):
        _vote("e1", "person a", kind=kind)
    _process("Person A")
    e = _stored()
    assert len(e.check["l2"]["names"]) == 1 and e.claim_status == "confirmed"


def test_the_9b_never_sees_either_sensor(env):
    _vote("e1", "person d")
    _process("Person A")
    _vote("e2", "person a")
    _process(None, None, event_id="e2")
    for call in env["llm_calls"]:
        for needle in ("Person A", "Person D", "persona", "persond", "person a", "person d", "Double Take"):
            assert needle not in call["prompt"]
