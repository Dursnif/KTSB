"""P144 R138 / run-queue J3b — Jang's digest leaves out Frigate lines for objects the stationary gate judged unchanged.
Argus' store is not touched (it still indexes every line); only the digest lines are held and filtered.
frigate.db is a tmp file; nothing reaches the house."""

import json

import pytest

import kaare_argus as argus
from kaare_core.frigate_user import digest_gate as dg
from kaare_core.frigate_user import store as fstore


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    yield tmp_path / "frigate" / "frigate.db"
    fstore.reset_for_tests()


def _event(eid, verdict=None, label="waste_bin"):
    check = {"decision": "none"}
    if verdict:
        check["gate"] = {"verdict": verdict, "key": "cam|waste_bin|0,4", "thing_id": None}
    assert fstore.record_event(fstore.FrigateUserEvent(event_id=eid, camera="cam", label=label, start_ts=1000.0,
                                                       end_ts=1010.0, check=check))


def _ev(eid, label="waste_bin"):
    return {"event_id": eid, "label": label, "camera": "cam"}


def test_unchanged_lines_are_left_out(db):
    _event("e1", "unchanged")
    h = dg.DigestHold(db)
    assert h.add(_ev("e1"), "start line", now=0) == [] and h.add(_ev("e1"), "end line", now=1) == []
    assert h.release(now=2) == [] and h.skipped == 2 and h.held() == 0


def test_changed_and_unknown_are_released(db):
    _event("e1", "changed")
    _event("e2", "unknown")
    h = dg.DigestHold(db)
    h.add(_ev("e1"), "a", now=0)
    h.add(_ev("e2"), "b", now=0)
    assert sorted(h.release(now=1)) == ["a", "b"]


def test_no_verdict_is_held_then_released(db):
    _event("e1")  # stored, never judged (not ticked)
    h = dg.DigestHold(db)
    h.add(_ev("e1"), "a", now=0)
    h.add(_ev("e9"), "z", now=0)  # not even stored yet
    assert h.release(now=dg.HOLD_S - 1) == [] and h.held() == 2
    assert sorted(h.release(now=dg.HOLD_S + 1)) == ["a", "z"]


def test_long_event_is_never_held_past_the_cap(db):
    h = dg.DigestHold(db)
    for t in range(0, int(dg.HOLD_MAX_S) + 60, 60):  # a line every minute: the per-line hold never runs out
        h.add(_ev("e1"), f"l{t}", now=t)
        out = h.release(now=t)
        if out:
            break
    assert out and t >= dg.HOLD_MAX_S


def test_persons_and_animals_pass_at_once(db):
    h = dg.DigestHold(db)
    assert h.add(_ev("p1", "person"), "p", now=0) == ["p"]
    assert h.add(_ev("d1", "dog"), "d", now=0) == ["d"]
    assert h.add({"label": "car"}, "no id", now=0) == ["no id"]


def test_missing_db_releases_after_hold(tmp_path):
    h = dg.DigestHold(tmp_path / "none.db")
    h.add(_ev("e1"), "a", now=0)
    assert h.release(now=dg.HOLD_S + 1) == ["a"]


def test_db_is_opened_read_only(db):
    _event("e1", "unchanged")
    before = db.read_bytes()
    assert dg.verdicts(["e1"], db) == {"e1": "unchanged"}
    assert db.read_bytes() == before


# ── wired into Argus: the digest only; the store still gets the line ──

def test_argus_digest_line_goes_through_the_hold():
    with open(argus.__file__) as f:
        src = f.read()
    assert "digest_add(\n                        digest_hold, digest_buffer" in src and "digest_flush(digest_hold, digest_buffer)" in src
    assert src.count("batch.append(doc)") == 1  # every line is still indexed


def test_argus_digest_entry_helper(db):
    _event("e1", "unchanged")
    hold = dg.DigestHold(db)
    buf = []
    line = json.dumps({"event_id": "e1", "label": "waste_bin"})
    argus.digest_add(hold, buf, "frigate-mqtt", json.loads(line), "x", now=0)
    argus.digest_add(hold, buf, "kaare", {}, "y", now=0)
    assert buf == ["y"]
    argus.digest_flush(hold, buf, now=5)
    assert buf == ["y"] and hold.skipped == 1
