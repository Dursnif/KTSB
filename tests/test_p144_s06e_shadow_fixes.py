"""P144-s06e — fixes from the first shadow day (2026-10-27):

1. a vehicle-only episode gets no model call at all (no 9B scene, no 27B) — a code summary line instead (`by: code`);
2. «unusual» on a baseline under 14 days is suppressed by code (the model's view kept as `unusual_suppressed`), except
   for urgent classes / person while away / not welcome / fire;
3. `raise_at_dev_meeting` is `{topic, text}`; a topic register in frigate.db (store v3) raises a topic once until it
   is resolved, and counts every sighting;
4. the household's known camera facts (knowledge.db world, read through its accessor) are in the conclusion prompt,
   capped by P133's `core_facts_max_chars`, never with an opted-out person's name.
Never touches live state (tmp DBs, fake LLM, world facts faked, `_log_llm_call_bg` patched).
"""

import asyncio
import json
import sqlite3
import time
from zoneinfo import ZoneInfo

import pytest

from adapters import frigate_adapter, llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.frigate_user import conclude, route
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

UTC = ZoneInfo("UTC")
T0 = 1_792_998_000.0  # 2026-10-25 07:00:00 UTC (a Sunday)

CAMS = {"road": {"display_name": "Nedside vest", "role": "driveway"},
        "door": {"display_name": "Inngang", "role": "front_door"}}


def _p(*pts):
    return [[[x, y], ts] for x, y, ts in pts]


def _ev(event_id, camera, start, *, label="car", path=None, role="driveway", kind="unknown", claim="none",
        person_ref=None, **kw):
    end = max([start + 10] + [p[1] + 1 for p in path or []])
    return fstore.FrigateUserEvent(
        event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=end, path_data=path,
        camera_role=role, person_kind=kind, claim_status=claim, person_ref=person_ref, **kw)


def _drive(eid, cam, start, label="car"):
    return _ev(eid, cam, start, label=label, path=_p((0.1, 0.5, start), (0.6, 0.5, start + 4)))


def _settings(**kw):
    c = {"idle_gate_s": 30, "max_attempts": 3, "poll_s": 30, "urgent_classes": ["not_welcome"],
         "urgent_labels": ["fire", "smoke"], "urgent_when_away_person": True}
    c.update(kw)
    return {"enabled": True, "episode_gap_s": 120, "conclude": c}


def _answer(**over):
    a = {"summary": "Jeg så noe.", "unusual": {"value": False, "reason": "vanlig"}, "notify_suggestion": None,
         "raise_at_dev_meeting": None, "confidence": {"level": "medium", "reasons": ["to kameraer"]}}
    a.update(over)
    return a


class Llm:
    def __init__(self):
        self.calls = []
        self.answer = _answer()

    async def __call__(self, role, prompt, images=None, rid=""):
        self.calls.append({"role": role, "prompt": prompt})
        if role == "frigate":
            return {"text": json.dumps({"persons": 1, "people": [], "vehicles": "", "at_door": "",
                                        "scene": "A person walks."})}
        return {"text": json.dumps(self.answer)}


WORLD = [
    {"id": 202, "section": "Cameras", "content": "Nedside vest viser tilhengerplassen: tilhengeren er vår egen.",
     "importance": 0.5},
    {"id": 203, "section": "Cameras", "content": "Ringeklokka ser mot sør.", "importance": 0.6},
    {"id": 300, "section": "Audio", "content": "Høyttaleren på kjøkkenet heter Nest.", "importance": 0.9},
    {"id": 301, "section": "Notes", "content": "Personb sin bil står ved road om natta.", "importance": 0.4},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    ustore.create_user(username="persona", display_name="Person A", role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    frigate_consent.invalidate()
    llm = Llm()

    async def no_recording(camera, ts, height=480):
        raise RuntimeError("no recording in tests")

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", no_recording)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_settings", lambda: _settings())
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(conclude, "_world_rows", lambda: [dict(r) for r in WORLD], raising=False)
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 28, "buckets": {}})
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(conclude, "get_local_tz", lambda: UTC)
    yield llm
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _only_episode():
    (ep,) = fstore.episodes_between(0, T0 * 2)
    return ep


def _person_episode(start=T0, kind="unknown"):
    assert fstore.record_event(_ev(f"p{start}", "door", start, label="person", role="front_door", kind=kind,
                                   path=_p((0.5, 0.2, start), (0.5, 0.6, start + 4))))


# ── 1. vehicle-only episodes: code, not models ──────────────────────────────────


def test_vehicle_only_episode_gets_no_model_call_and_a_code_summary(env):
    assert fstore.record_event(_drive("c1", "road", T0))
    assert fstore.record_event(_drive("c2", "road", T0 + 20))
    assert fstore.record_event(_drive("c3", "door", T0 + 40, label="truck"))
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    assert env.calls == []  # neither the 9B scene nor the 27B
    ep = _only_episode()
    c = ep.conclusion
    assert c["status"] == "done" and c["by"] == "code"
    assert c["summary"].startswith("Jeg så")
    assert "07:00" in c["summary"] and "bil" in c["summary"] and "lastebil" in c["summary"]
    assert c["unusual"]["value"] is False
    assert c["notify_suggestion"] is None and c["raise_at_dev_meeting"] is None
    assert ep.scene == {"status": "skipped", "reason": "vehicles_only"}
    assert ep.route  # the code route is still stored


def test_vehicle_only_episode_does_not_wait_for_the_idle_gate(env, monkeypatch):
    monkeypatch.setattr(app_state, "_last_user_prompt_time", time.time())  # a user is chatting right now
    assert fstore.record_event(_drive("c1", "road", T0))
    assert asyncio.run(conclude.run_once(now=T0 + 1000)) == 1
    assert _only_episode().conclusion["by"] == "code"


def test_episode_with_a_person_still_goes_to_the_models(env):
    assert fstore.record_event(_drive("c1", "road", T0))
    _person_episode(T0 + 10)
    asyncio.run(conclude.run_once(now=T0 + 1000))
    assert [c["role"] for c in env.calls] == ["frigate_conclude"]  # scene failed (no recording) → 27B
    assert _only_episode().conclusion["by"] == "model"


def test_vehicle_summary_in_english(monkeypatch):
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    ep = fstore.Episode(episode_id=1, start_ts=T0, end_ts=T0 + 60, cameras=["road"], labels=["car"],
                        person_refs=[], event_count=2, status="closed", conclusion=None, concluded_ts=None)
    evs = [_drive("a", "road", T0), _ev("b", "road", T0 + 30)]
    evs[1].drives = False  # a parked car: the store sets drives False when it never really moved
    text = route.vehicle_summary(ep, evs, "en", cameras=CAMS)
    assert text.startswith("I saw only vehicles")
    assert "07:00–07:01" in text and "Nedside vest" in text
    assert "1 × car moving" in text and "1 × car standing still" in text
    assert "No people or animals" in text


# ── 2. «unusual» needs a real baseline ──────────────────────────────────────────


def test_thin_baseline_suppresses_unusual_and_keeps_the_models_view(env, monkeypatch):
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 2, "buckets": {}})
    env.answer = _answer(unusual={"value": True, "reason": "aldri sett denne timen"})
    _person_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    c = _only_episode().conclusion
    assert c["unusual"]["value"] is False
    assert "14" in c["unusual"]["reason"] and "2" in c["unusual"]["reason"]
    assert c["unusual_suppressed"] == {"value": True, "reason": "aldri sett denne timen", "baseline_days": 2}


def test_full_baseline_keeps_unusual(env):
    env.answer = _answer(unusual={"value": True, "reason": "person kl. 07 på søndag er sjelden"})
    _person_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    c = _only_episode().conclusion
    assert c["unusual"]["value"] is True and "unusual_suppressed" not in c


def test_a_new_camera_counts_its_own_days(env, monkeypatch):
    # 28 days overall, but the door camera was added 3 days ago
    monkeypatch.setattr(normalcy, "_load_baseline",
                        lambda: {"days_of_data": 28, "camera_days": {"road": 28, "door": 3}, "buckets": {}})
    env.answer = _answer(unusual={"value": True, "reason": "aldri sett"})
    _person_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    c = _only_episode().conclusion
    assert c["unusual"]["value"] is False and c["unusual_suppressed"]["baseline_days"] == 3


@pytest.mark.parametrize("case", ["not_welcome", "away_person"])
def test_urgent_cases_are_not_gated(env, monkeypatch, case):
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 2, "buckets": {}})
    env.answer = _answer(unusual={"value": True, "reason": "ikke velkommen / borte"})
    if case == "away_person":
        monkeypatch.setattr(conclude, "_is_away", lambda: True)
        _person_episode()
    else:
        _person_episode(kind="not_welcome")
    asyncio.run(conclude.run_once(now=T0 + 1000))
    c = _only_episode().conclusion
    assert c["unusual"]["value"] is True and "unusual_suppressed" not in c


def test_fire_is_not_gated(env, monkeypatch):
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 2, "buckets": {}})
    env.answer = _answer(unusual={"value": True, "reason": "røyk"})
    assert fstore.record_event(_ev("f1", "door", T0, label="smoke", role="front_door",
                                   path=_p((0.5, 0.2, T0), (0.5, 0.6, T0 + 4))))
    asyncio.run(conclude.run_once(now=T0 + 1000))
    assert _only_episode().conclusion["unusual"]["value"] is True


# ── 3. dev-meeting topics: raised once, counted always ──────────────────────────


def test_same_topic_is_raised_once_and_counted_twice(env):
    env.answer = _answer(raise_at_dev_meeting={"topic": "trailer_false_car", "text": "Tilhengeren blir «bil»."})
    _person_episode(T0)
    asyncio.run(conclude.run_once(now=T0 + 1000))
    _person_episode(T0 + 2000)
    asyncio.run(conclude.run_once(now=T0 + 3000))
    first, second = fstore.episodes_between(0, T0 * 2)
    assert first.conclusion["raise_at_dev_meeting"]["raised"] is True
    assert second.conclusion["raise_at_dev_meeting"]["raised"] is False
    assert second.conclusion["raise_at_dev_meeting"]["count"] == 2
    (topic,) = fstore.dev_topics()
    assert topic["topic"] == "trailer_false_car" and topic["count"] == 2 and topic["status"] == "open"
    assert topic["first_ts"] < topic["last_ts"]
    assert topic["theme"] == "house"  # P118 B8 theme, set by code when the topic is first written
    # the second conclusion saw the open topic, so the model can reuse the key
    assert "trailer_false_car" in env.calls[-1]["prompt"]


def test_resolved_topic_is_raised_again(env):
    env.answer = _answer(raise_at_dev_meeting={"topic": "trailer_false_car", "text": "Tilhengeren blir «bil»."})
    _person_episode(T0)
    asyncio.run(conclude.run_once(now=T0 + 1000))
    assert fstore.resolve_dev_topic("trailer_false_car")
    _person_episode(T0 + 2000)
    asyncio.run(conclude.run_once(now=T0 + 3000))
    second = fstore.episodes_between(0, T0 * 2)[1]
    assert second.conclusion["raise_at_dev_meeting"]["raised"] is True
    (topic,) = fstore.dev_topics()
    assert topic["status"] == "open" and topic["count"] == 2


def test_parse_dev_topic_shapes():
    ok, _ = conclude.parse_conclusion(json.dumps(_answer(
        raise_at_dev_meeting={"topic": " Trailer False-Car! ", "text": "x"})))
    assert ok["raise_at_dev_meeting"] == {"topic": "trailer_false_car", "text": "x"}
    ok, _ = conclude.parse_conclusion(json.dumps(_answer(raise_at_dev_meeting="Kamera nedside vest tar feil")))
    assert ok["raise_at_dev_meeting"] == {"topic": "", "text": "Kamera nedside vest tar feil"}
    ok, _ = conclude.parse_conclusion(json.dumps(_answer(raise_at_dev_meeting={"topic": "x", "text": ""})))
    assert ok["raise_at_dev_meeting"] is None
    assert conclude.parse_conclusion(json.dumps(_answer(raise_at_dev_meeting=5)))[1] == "raise_at_dev_meeting_invalid"


def test_topic_without_key_gets_a_code_key_from_its_theme(env):
    env.answer = _answer(raise_at_dev_meeting="Kameraet tar feil på bil om natta")
    _person_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    (topic,) = fstore.dev_topics()
    assert topic["topic"] == "untagged_house"


def test_migration_v3_from_v2(tmp_path, monkeypatch):
    path = tmp_path / "frigate" / "frigate.db"
    monkeypatch.setattr(fstore, "DB_PATH", path)
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(fstore._MIGRATIONS[1])
    conn.executescript(fstore._MIGRATIONS[2])
    conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO episodes (start_ts, end_ts, status, conclusion) VALUES (?, ?, 'closed', ?)",
                 (T0, T0 + 10, json.dumps({"status": "done", "raise_at_dev_meeting": "old string"})))
    conn.commit()
    conn.close()
    fstore.reset_for_tests()
    assert fstore.schema_version() == fstore.SCHEMA_VERSION  # v2 → v3 and on (s12 added v4)
    assert fstore.episode(1).conclusion["raise_at_dev_meeting"] == "old string"  # old rows untouched
    assert fstore.dev_topics() == []
    fstore.reset_for_tests()


# ── 4. known camera facts in the conclusion prompt ──────────────────────────────


def test_conclusion_prompt_has_the_households_camera_facts(env):
    _person_episode()
    assert fstore.record_event(_drive("c1", "road", T0 + 10))
    asyncio.run(conclude.run_once(now=T0 + 1000))
    prompt = env.calls[-1]["prompt"]
    assert "tilhengeren er vår egen" in prompt  # names an episode camera («Nedside vest»)
    assert "Ringeklokka ser mot sør" in prompt  # camera category
    assert "Høyttaleren på kjøkkenet" not in prompt  # not about cameras
    assert prompt.index("tilhengeren er vår egen") < prompt.index("Ringeklokka ser mot sør")  # episode cameras first


def test_camera_facts_are_capped_by_p133(env, monkeypatch):
    monkeypatch.setattr(conclude, "get_memory_prompt_config", lambda: {"core_facts_max_chars": 70}, raising=False)
    _person_episode()
    assert fstore.record_event(_drive("c1", "road", T0 + 10))
    asyncio.run(conclude.run_once(now=T0 + 1000))
    prompt = env.calls[-1]["prompt"]
    assert "tilhengeren er vår egen" in prompt and "Ringeklokka ser mot sør" not in prompt


def test_camera_facts_never_carry_an_opted_out_name(env, monkeypatch):
    ustore.create_user(username="personb", display_name="Personb", role="adult", pin="719364")
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    _person_episode()
    assert fstore.record_event(_drive("c1", "road", T0 + 10))
    asyncio.run(conclude.run_once(now=T0 + 1000))
    prompt = env.calls[-1]["prompt"]
    assert "står ved road om natta" in prompt  # the fact mentions an episode camera …
    assert "Personb" not in prompt  # … but the opted-out name is scrubbed
