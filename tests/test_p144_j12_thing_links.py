"""P144 R139 / run-queue J12 — code proposes that stationary things on two cameras are the same thing.

A pair needs: same label, both still on ≥ 3 days, and at least one change at the same time; each co-change day is world
evidence (the machine side never passes 0.5); the pair is ONE house question for admin only; «ja» links the keys to one
thing_id, «nei» keeps them apart; the place register is never written. Tmp files only; images are synthetic."""

import io

import pytest
from PIL import Image

from kaare_core import weights
from kaare_core.frigate_user import store as fstore
from kaare_core.frigate_user import thing_links as tl
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.weights import store as wstore

DAY = 86400.0
T0 = 1_800_000_000.0
A, B = "cam_e|waste_bin|0,4", "cam_w|waste_bin|3,1"


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    yield
    weights.flush()
    wstore.reset_for_tests()


def _ev(eid, key, ts, verdict):
    cam, label, _ = key.split("|")
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label=label, start_ts=ts,
                                   check={"gate": {"verdict": verdict, "key": key, "thing_id": None}})


def _week(co_change=True, label_b="waste_bin"):
    b = B.replace("waste_bin", label_b)
    evs = []
    for d in range(4):
        evs += [_ev(f"a{d}", A, T0 + d * DAY, "unchanged"), _ev(f"b{d}", b, T0 + d * DAY + 60, "unchanged")]
    evs.append(_ev("ac", A, T0 + 5 * DAY, "changed"))
    evs.append(_ev("bc", b, T0 + 5 * DAY + (600 if co_change else 3 * 3600), "changed"))
    return evs


def test_still_and_changing_together_is_a_pair(st):
    c = tl.candidates(_week())
    assert [x["pair"] for x in c] == [tl.pair_id(A, B)] and c[0]["still_days"] == (4, 4)


def test_no_pair_without_a_shared_change_or_with_other_labels(st):
    assert tl.candidates(_week(co_change=False)) == []
    assert tl.candidates(_week(label_b="car")) == []


def test_machine_evidence_never_passes_half(st):
    evs = _week()
    for d in range(30):  # a month of moving together
        evs += [_ev(f"ax{d}", A, T0 + (10 + d) * DAY, "changed"), _ev(f"bx{d}", B, T0 + (10 + d) * DAY + 30, "changed")]
    tl.record(tl.candidates(evs), {tl.pair_id(A, B): True})
    w = weights.get(("thing_pair", tl.pair_id(A, B)))
    assert w.belief <= 0.5 and w.validated is None and tl.thing_id(A) is None


def test_admin_only_and_yes_links(st):
    evs = _week()
    assert tl.questions_for_admin(evs, is_admin=False) == []
    q = tl.questions_for_admin(evs, is_admin=True)
    assert len(q) == 1 and q[0]["events"] == ["ac", "bc"] and q[0]["label"] == "waste_bin"
    assert tl.answer("boss", q[0]["id"], "yes")["ok"]
    assert tl.thing_id(A) == tl.thing_id(B) is not None
    assert tl.questions_for_admin(evs, is_admin=True) == []  # answered once


def test_no_keeps_apart_and_skip_hides(st):
    pid = tl.pair_id(A, B)
    tl.answer("boss", pid, "no")
    assert tl.thing_id(A) is None
    assert tl.answer("boss", "bad", "yes")["ok"] is False


def _jpeg(colour):
    im = Image.new("RGB", (100, 100), colour)
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def test_colour_alike_by_day_unknown_at_night(st):
    box = [0.1, 0.1, 0.5, 0.5]
    assert tl.colour_alike(_jpeg((30, 60, 200)), box, _jpeg((35, 62, 190)), box) is True
    assert tl.colour_alike(_jpeg((30, 60, 200)), box, _jpeg((200, 40, 40)), box) is False
    assert tl.colour_alike(_jpeg((90, 90, 90)), box, _jpeg((30, 60, 200)), box) is None  # grey = IR
    assert tl.colour_alike(None, box, _jpeg((1, 2, 3)), box) is None


# ── wired: admin's list + answer ──

def test_admin_question_list_has_the_pair(st, tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    for ev in _week():
        assert fstore.record_event(ev)
    monkeypatch.setattr(rfu.time, "time", lambda: T0 + 6 * DAY)
    out = rfu.api_thing_questions(_u={"sub": "boss"})["questions"]
    assert [q["id"] for q in out] == [tl.pair_id(A, B)]
    assert rfu.api_thing_answer(rfu.ThingAnswer(pair=tl.pair_id(A, B), answer="yes"), u={"sub": "boss"})["ok"]
    fstore.reset_for_tests()
