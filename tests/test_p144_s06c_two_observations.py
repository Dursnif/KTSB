"""P144-s06c — one strong contradiction never rejects a Frigate claim alone.

- one strong contradiction → `unverified`, with `doubt` (reason, layer, values) in the stored `check`; the name stays;
- `rejected` only when two observations (two events) of the same claimed person in the same episode agree on a
  strong contradiction, or another layer adds a strong contradiction;
- two observations in different episodes → still `unverified`.
Evidence: s06a log finding 2 — the 27B said «full» and «no» beard for the same person on two ~28k px images.
Nothing here reaches Frigate, an LLM, live users.db / frigate.db / configs or live logs (fixture from s06a's tests).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p144_s06c_two_observations.py -v
"""

import json

from kaare_core.frigate_user import checks, pipe
from kaare_core.frigate_user import store as fstore
from tests import test_p144_s06a_pipe as s06a
from tests.test_p144_s06a_pipe import T0, _event, _process, _stored

env = s06a.env  # the s06a pipe fixture: tmp users.db / frigate.db / configs, fake Frigate + LLM


def V(layer, verdict, strong=False, contradictions=None):
    return checks.Vote(layer=layer, verdict=verdict, strong=strong, reason="t",
                       contradictions=contradictions or [])


_BEARD = {"layer": "l3", "field": "beard", "seen": "full", "profile": "no"}
_GLASSES = {"layer": "l3", "field": "glasses", "seen": "yes", "profile": "no"}
_ELSEWHERE = {"layer": "l1", "field": "seen_elsewhere", "seen": "garasje", "profile": None}


# ── checks (pure) ───────────────────────────────────────────────────────────────


def test_l3_strong_vote_carries_structured_contradiction():
    vote = checks.l3_compare({"persons": 1, "beard": "full", "glasses": "no", "age_band": "adult"},
                             {"beard": "no", "glasses": "no", "age_band": "adult"})
    assert vote.verdict == "no" and vote.strong
    assert vote.contradictions == [{"layer": "l3", "field": "beard", "seen": "full", "profile": "no"}]


def test_l1_strong_vote_carries_structured_contradiction():
    votes = checks.l1_context(camera="inngang", start_ts=T0, end_ts=T0 + 10,
                              seen=[checks.Seen("garasje", T0, T0 + 5, True)],
                              household_member=True, household_away=False)
    assert votes[0].strong and votes[0].contradictions == [_ELSEWHERE]


def test_one_strong_contradiction_is_unverified_with_doubt():
    votes = [V("l0", "pass"), V("l3", "no", True, [_BEARD])]
    assert checks.decide(has_claim=True, votes=votes, check_ran=True) == "unverified"
    doubt = checks.doubt_of(votes)
    assert doubt["strong"] == [_BEARD]


def test_l1_alone_is_unverified():
    assert checks.decide(has_claim=True, votes=[V("l1", "no", True, [_ELSEWHERE])], check_ran=False) == "unverified"


def test_two_fields_of_one_image_do_not_reject():
    votes = [V("l3", "no", True, [_BEARD, _GLASSES])]
    assert checks.decide(has_claim=True, votes=votes, check_ran=True) == "unverified"


def test_two_layers_in_one_event_reject():
    votes = [V("l1", "no", True, [_ELSEWHERE]), V("l3", "no", True, [_BEARD])]
    assert checks.decide(has_claim=True, votes=votes, check_ran=True) == "rejected"


def test_agreeing_observation_in_episode_rejects():
    votes = [V("l3", "no", True, [_BEARD])]
    earlier = [{**_BEARD, "event_id": "e0"}]
    assert checks.decide(has_claim=True, votes=votes, check_ran=True, episode_doubts=earlier) == "rejected"
    assert checks.matching_doubts(checks.doubt_of(votes)["strong"], earlier) == ["e0"]


def test_disagreeing_observation_in_episode_does_not_reject():
    votes = [V("l3", "no", True, [_BEARD])]
    other_field = [{**_GLASSES, "event_id": "e0"}]
    other_value = [{**_BEARD, "seen": "circle_beard", "event_id": "e0"}]
    assert checks.decide(has_claim=True, votes=votes, check_ran=True, episode_doubts=other_field) == "unverified"
    assert checks.decide(has_claim=True, votes=votes, check_ran=True, episode_doubts=other_value) == "unverified"


def test_another_layer_in_episode_rejects():
    votes = [V("l3", "no", True, [_BEARD])]
    assert checks.decide(has_claim=True, votes=votes, check_ran=True,
                         episode_doubts=[{**_ELSEWHERE, "event_id": "e0"}]) == "rejected"


# ── pipe + store ────────────────────────────────────────────────────────────────


def _at(env, monkeypatch, start):
    async def fake_get_event(event_id):
        return {"id": event_id, "start_time": start, "end_time": start + 10, "data": {"attributes": env["face"]}}
    monkeypatch.setattr(pipe.frigate_adapter, "get_event", fake_get_event)


def test_one_strong_contradiction_keeps_name_with_visible_doubt(env):
    env["llm_reply"] = {"beard": "full"}  # profile Person A: beard no
    _process(_event(), "Person A", 0.95)
    e = _stored()
    assert e.claim_status == "unverified"
    assert e.person_ref == "persona"  # the store's gate keeps the name for unverified
    doubt = e.check["doubt"]
    assert doubt["strong"] == [{"layer": "l3", "field": "beard", "seen": "full", "profile": "no"}]
    assert doubt["reason"]


def test_two_observations_same_episode_reject_both(env, monkeypatch):
    env["llm_reply"] = {"beard": "full"}
    _at(env, monkeypatch, T0)
    _process(_event("e1"), "Person A", 0.95)
    _at(env, monkeypatch, T0 + 30)
    _process(_event("e2", camera="garasje"), "Person A", 0.95)
    e1, e2 = _stored("e1"), _stored("e2")
    assert e1.episode_id == e2.episode_id
    assert e2.claim_status == "rejected" and e2.person_ref is None
    assert e2.check["doubt"]["confirmed_by"] == ["e1"]
    assert e1.claim_status == "rejected" and e1.person_ref is None
    assert "Person A" not in json.dumps(e1.check) and "persona" not in json.dumps(e2.check)


def test_two_observations_different_episodes_stay_unverified(env, monkeypatch):
    env["llm_reply"] = {"beard": "full"}
    _at(env, monkeypatch, T0)
    _process(_event("e1"), "Person A", 0.95)
    fstore.close_episodes(now=T0 + 5000)
    _at(env, monkeypatch, T0 + 10_000)
    _process(_event("e2"), "Person A", 0.95)
    e1, e2 = _stored("e1"), _stored("e2")
    assert e1.episode_id != e2.episode_id
    assert e1.claim_status == e2.claim_status == "unverified"
    assert e1.person_ref == e2.person_ref == "persona"


def test_urgent_handler_sees_final_decision(env, monkeypatch):
    env["llm_reply"] = {"beard": "full"}
    _at(env, monkeypatch, T0)
    _process(_event("e1"), "Person A", 0.95)
    _at(env, monkeypatch, T0 + 30)
    _process(_event("e2"), "Person A", 0.95)
    last, _img = env["urgent"][-1]
    assert last.event_id == "e2" and last.claim_status == "rejected" and last.person_ref is None
