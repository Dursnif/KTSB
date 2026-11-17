"""N40 point 3 (November list 10) — one short-lived, in-memory cache of decoded events for the question pages.

BUG-log 2026-11-16: six question endpoints each loaded and unsealed 7–14 days of events at once (5–7 s). Now
`store.events_between` serves recent windows from one shared decode. kaare-d7's three rules, each tested:
1. privacy (R0.6a): RAM only, per process, seconds — dropped after the TTL even when nobody asks again; never on disk;
2. filtering (tracking off, role, may_see_snapshot) happens at read, after the cache — a tracking-off user sees nothing
   through a cache another user warmed;
3. invalidation is exact: a new event, or a human answer written on an event (N39), shows at once — not after the TTL.
Plus: one load for a whole page; a caller changing a returned event never changes what the next caller gets.
Tmp files only (the J2 fixture). Placeholders only."""

import time

from kaare_core.frigate_user import questions
from kaare_core.frigate_user import store as fstore
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    NOW,
    H,
    _ev,
    _store,
    env,
)

DAY = 86400


def _count_loads(monkeypatch):
    n = {"rows": 0}
    real = fstore._row_to_event

    def counting(r):
        n["rows"] += 1
        return real(r)

    monkeypatch.setattr(fstore, "_row_to_event", counting)
    return n


def test_a_whole_page_decodes_the_rows_once(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    _store(*[_ev(f"e{i}", NOW - i * H) for i in range(10)])
    n = _count_loads(monkeypatch)
    for days in (7, 14, 7, 9, 14, 7):  # the six endpoints of the questions tab, each with its own window
        assert len(fstore.events_between(NOW - days * DAY, NOW)) == 10
    assert n["rows"] == 10  # decoded once, served six times


def test_a_new_event_shows_at_once(env):  # noqa: F811
    _store(_ev("e1", NOW - H))
    assert [e.event_id for e in fstore.events_between(NOW - 7 * DAY, NOW)] == ["e1"]
    _store(_ev("e2", NOW - 2 * H))
    assert {e.event_id for e in fstore.events_between(NOW - 7 * DAY, NOW)} == {"e1", "e2"}


def test_an_answer_on_an_event_shows_at_once(env):  # noqa: F811
    _store(_ev("e1", NOW - H))
    assert not (fstore.events_between(NOW - 7 * DAY, NOW)[0].check or {}).get("human")
    assert questions.answer("user_a", "e1", "yes")["ok"]
    assert fstore.events_between(NOW - 7 * DAY, NOW)[0].check["human"]["verdict"] == "yes"


def test_tracking_off_user_sees_nothing_through_a_warm_cache(env):  # noqa: F811
    _store(_ev("e1", NOW - H, sensors={"relation": "disagree", "frigate": "Person C", "frigate_face": "Person C",
                                         "detectors": [{"detector": "det_x", "ref": "user_c", "face": "user_c"}]}))
    questions.candidates("user_a", now=NOW)  # another user warms the cache
    assert questions.candidates("user_c", now=NOW) == []  # opted out: never asked, cache or not


def test_a_caller_changing_an_event_does_not_change_the_cache(env):  # noqa: F811
    _store(_ev("e1", NOW - H))
    first = fstore.events_between(NOW - 7 * DAY, NOW)[0]
    first.camera = "changed"
    assert fstore.events_between(NOW - 7 * DAY, NOW)[0].camera == "cam_door"


def test_decoded_rows_are_dropped_after_the_ttl(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fstore, "EVENTS_CACHE_S", 0.2)
    _store(_ev("e1", NOW - H))
    fstore.events_between(NOW - 7 * DAY, NOW)
    assert fstore._events_cache_size() == 1
    time.sleep(0.6)
    assert fstore._events_cache_size() == 0  # nobody asked again — still gone (R0.6a: seconds, RAM only)
