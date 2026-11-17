"""J18 (from P144-s31): places KTSB learns itself — where persons appear/disappear mid-frame, splits and hand-overs
joined, a spot seen often → one admin question «Hva er her?», the answer names it for code facts to the 27B.

Shapes from real data (run-queue J18 log): s31's 8-day count (nedside vest cell beside the car/wall, the doorbell's door);
ep 245 (the same person's track split on nedside vest around a hand-over to nedside øst); 2026-11-06 ~20:57 (two household
members outside on two sides of the house at nearly the same time — never «the same person»). Placeholders; tmp DB."""
import asyncio

import pytest

from kaare_core.frigate_user import conclude
from kaare_core.frigate_user import places_learned as pl
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu

T0 = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    yield
    fstore.reset_for_tests()


def _p(eid, cam, t, first, last, dur=8.0, kind="unknown"):
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label="person", start_ts=t, end_ts=t + dur, person_kind=kind,
                                   path=[[first[0], first[1], t], [last[0], last[1], t + dur - 1]])


CORNER = (0.25, 0.45)  # cell 1,2 — beside the car/wall (s31's most common mid-frame start on nedside vest)
EDGE_IN = (0.8, 0.99)  # from the bottom edge


def _history(n_days=4, per_day=3):
    return [_p(f"h{d}-{i}", "vest", T0 - (d + 1) * DAY + i * 600, CORNER, EDGE_IN) for d in range(n_days)
            for i in range(per_day)]


def test_a_spot_seen_on_several_days_becomes_one_question():
    qs = pl.questions(_history(), {})
    assert [(q["camera"], q["cell"], q["starts"], q["days"]) for q in qs] == [("vest", "1,2", 12, 4)]
    assert qs[0]["box"] == [0.2, 0.4, 0.2, 0.2]


def test_too_few_days_or_edge_points_ask_nothing():
    assert pl.questions(_history(n_days=2, per_day=6), {}) == []  # 12 starts, but on 2 days
    edge = [_p(f"e{i}", "vest", T0 - i * DAY / 2, EDGE_IN, (0.99, 0.5)) for i in range(12)]
    assert pl.questions(edge, {}) == []


def test_answered_spots_are_not_asked_again_and_at_most_max_open():
    hist = []
    for k, c in enumerate([(0.1, 0.1), (0.3, 0.3), (0.5, 0.5), (0.7, 0.7), (0.9, 0.3)]):
        hist += [_p(f"x{k}-{d}-{i}", "vest", T0 - (d + 1) * DAY + i, (c[0] * 0.9 + 0.08, c[1] * 0.9 + 0.08), EDGE_IN)
                 for d in range(3) for i in range(3)]
    assert len(pl.questions(hist, {})) == pl.MAX_OPEN
    answered = {("vest", q["cell"]): {"status": "skip"} for q in pl.questions(hist, {})}
    assert all(("vest", q["cell"]) not in answered for q in pl.questions(hist, answered))


def test_a_track_split_is_one_person_not_a_place():
    """ep 245's shape: the track ends mid-frame and a new one starts 3 s later at the same spot."""
    a = _p("a", "vest", T0, EDGE_IN, (0.35, 0.35), dur=13)
    b = _p("b", "vest", T0 + 16, (0.36, 0.34), EDGE_IN)
    j = pl.joins([a, b])
    assert j["splits"] == [("a", "b")]
    assert pl.counts([a, b]) == {}


def test_two_people_on_two_sides_at_once_are_never_joined():
    """2026-11-06 ~20:57: one household member at the bins on one side, another on the other side — a «hand-over» by time
    alone, but that cell pair was never seen before → no fact."""
    a = _p("s", "nedside", T0, EDGE_IN, (0.5, 0.5), dur=10)
    b = _p("w", "oppside", T0 + 12, (0.4, 0.6), EDGE_IN)
    lines = pl.fact_lines([a, b], [], {}, {}, "nb", conclude._t)
    assert lines == []


def test_a_known_hand_over_is_a_fact():
    past = []
    for k in range(3):
        t = T0 - (k + 1) * DAY
        past += [_p(f"pa{k}", "vest", t, EDGE_IN, (0.35, 0.35), dur=10), _p(f"pb{k}", "ost", t + 14, (0.6, 0.6), EDGE_IN)]
    now = [_p("a", "vest", T0, EDGE_IN, (0.36, 0.36), dur=10), _p("b", "ost", T0 + 13, (0.61, 0.6), EDGE_IN)]
    lines = pl.fact_lines(now, past, {}, {}, "nb", conclude._t)
    assert len(lines) == 1 and "3 ganger før" in lines[0]


def test_a_named_spot_gives_came_from_and_went_towards():
    answered = {("vest", "1,2"): {"status": "named", "name": "garasjehjørnet"},
                ("vest", "4,0"): {"status": "not_a_place", "name": "x"}}  # a name on a «not a place» row is never used
    ev = _p("p", "vest", T0, CORNER, CORNER)
    lines = pl.fact_lines([ev], [], answered, {}, "nb", conclude._t)
    assert len(lines) == 2 and "kom fra garasjehjørnet" in lines[0] and "gikk mot garasjehjørnet" in lines[1]
    assert "Person" not in " ".join(lines)  # never a name of a person
    assert pl.fact_lines([_p("q", "vest", T0, (0.85, 0.1), EDGE_IN)], [], answered, {}, "nb", conclude._t) == []


def test_boxes_are_used_when_there_is_no_path():
    ev = fstore.FrigateUserEvent(event_id="b", camera="vest", label="person", start_ts=T0, end_ts=T0 + 5,
                                 boxes=[[T0, 0.2, 0.3, 0.1, 0.15, 0.4, 0, 0], [T0 + 4, 0.7, 0.8, 0.1, 0.19, 0.4, 0, 0]])
    first, last = pl._points(ev)
    assert first[:2] == (pytest.approx(0.25), pytest.approx(0.45)) and last[2] == T0 + 4


def test_counts_hold_no_names():
    hist = _history()
    for e in hist:
        e.person_ref, e.person_kind = "Person A", "household"
    c = pl.counts(hist)
    assert "Person A" not in repr(c)


# ── the store: append-only answers ──────────────────────────────────────────────────────────────────────────────

def test_answers_are_kept_newest_counts():
    assert fstore.add_spot_answer("vest", "1,2", "skip", None, "admin")
    assert fstore.add_spot_answer("vest", "1,2", "named", "garasjehjørnet", "admin")
    assert fstore.spot_answers()[("vest", "1,2")]["name"] == "garasjehjørnet"
    rows = fstore._with_conn(lambda c: c.execute("SELECT COUNT(*) FROM spot_answers").fetchone()[0])
    assert rows == 2  # nothing overwritten


def test_bad_answers_are_refused():
    assert not fstore.add_spot_answer("vest", "1,2", "named", "  ", "admin")
    assert not fstore.add_spot_answer("vest", "12", "skip", None, "admin")
    assert not fstore.add_spot_answer("vest", "1,2", "delete", None, "admin")


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_texts_in_three_languages(lang):
    for key in ("spot_from", "spot_to", "spot_handover"):
        assert conclude._TEXT[key][lang]


# ── wiring: the admin endpoints and the 27B's facts ─────────────────────────────────────────────────────────────

def _seed(events):
    for e in events:
        assert fstore.record_event(e)


def test_admin_endpoints_list_and_answer(monkeypatch):
    _seed(_history())
    monkeypatch.setattr(rfu.time, "time", lambda: T0)
    qs = rfu.api_place_questions(_u={"sub": "boss"})["questions"]
    assert [q["id"] for q in qs] == ["vest|1,2"]
    assert rfu.api_place_answer(rfu.PlaceAnswer(id="vest|1,2", answer="named", name="garasjehjørnet"),
                                u={"sub": "boss"})["ok"]
    assert rfu.api_place_questions(_u={"sub": "boss"})["questions"] == []
    assert fstore.spot_answers()[("vest", "1,2")]["name"] == "garasjehjørnet"


def test_the_27b_gets_the_place_facts(monkeypatch):
    fstore.add_spot_answer("vest", "1,2", "named", "garasjehjørnet", "boss")
    ev = _p("p", "vest", T0, CORNER, EDGE_IN)
    ep = fstore.Episode(1, T0, T0 + 10, ["vest"], ["person"], [], 1, "closed", None, None,
                        scene={"status": "done", "by": "frames"})
    seen = {}

    class Stop(Exception):
        pass

    def capture(*a, facts=None, **kw):
        seen["facts"] = facts
        raise Stop

    async def yes(*a, **kw):
        return True

    monkeypatch.setattr(conclude, "label_gate", lambda *a, **kw: None)
    monkeypatch.setattr(conclude, "doubt_gate", lambda *a, **kw: None)
    monkeypatch.setattr(conclude, "_doubt_look", yes)
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "_camera_cards", dict)
    monkeypatch.setattr(conclude, "camera_fact_lines", lambda *a: [])
    monkeypatch.setattr(conclude, "_untracked_names", set)
    monkeypatch.setattr(conclude, "build_episode_prompt", capture)
    with pytest.raises(Stop):
        asyncio.run(conclude._conclude_from_scene(ep, [ev], "nb", None, T0 + 60))
    assert any("kom fra garasjehjørnet" in f for f in seen["facts"])


def test_the_question_image_is_never_an_opted_out_persons_event():
    """Rule 10: the admin card shows the newest event's snapshot — never one of an opted-out («untracked») person."""
    hist = _history()
    late = _p("optout", "vest", T0 - 60, CORNER, EDGE_IN, kind="untracked")
    q = pl.questions([*hist, late], {})[0]
    assert "event_id" not in q and q["starts"] == 13  # J18b: no event image at all; still counted (anonymously)
