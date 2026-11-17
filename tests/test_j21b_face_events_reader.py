"""J21b reader 2 — the camera tool's action='events' (and the old alias les_kamerahendelser) from frigate.db.

- module 0 on: named person events of the window, one line per person per visit (gap = visit_gap_s), oldest first, with
  span, display name, certainty word, the camera's display name, count and the surest event's id;
- names only for a logged-in household user (else the «names only for household» text) — stricter than the old file;
- nobody who opted out (untracked, or hidden since), no unnamed events, nothing outside the window; a name filter;
- module 0 off: it says so (P144 phase 6: Argus' old face_events.txt and its fallback are gone).
Never touches live state: users.db, frigate.db, weights.db, yaml point at tmp_path. Placeholders only."""

import asyncio

import pytest

from kaare_core.frigate_user import query
from kaare_core.tools import executor_camera
from kaare_core.tools.i18n import t
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    NOW,
    H,
    _ev,
    _sensors,
    _store,
    env,
)

NAMES_ONLY = t("fq_names_only_household", "nb")


@pytest.fixture
def db(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    monkeypatch.setattr(query.time, "time", lambda: NOW)
    return env


def _named(eid, start, ref="user_a", camera="cam_door", score=0.8):
    e = _ev(eid, start, ref=ref, claim="confirmed", camera=camera,
            sensors=_sensors({"user_a": "Person A", "user_b": "Person B"}.get(ref, ref), [("det_x", ref)], "agree"))
    e.claim_score = score
    return e


def _sessions(hours=24, name="", names=True):
    return query.person_sessions(hours, name, names=names, lang="nb", now=NOW)


def test_one_line_per_person_per_visit(db):
    _store(_named("a1", NOW - 5 * H), _named("a2", NOW - 5 * H + 600, camera="cam_yard", score=0.95),
           _named("a3", NOW - H), _named("b1", NOW - 3 * H, ref="user_b"))
    lines = _sessions()
    assert len(lines) == 3
    assert lines[0].startswith("[") and "USER_A" in lines[0] and "Inngang, Gårdsplass" in lines[0]
    assert "hendelser: 2" in lines[0] and "event_id: a2" in lines[0]  # the surest of the visit
    assert "USER_B" in lines[1] and "USER_A" in lines[2]  # oldest first


def test_names_only_for_household_users(db):
    _store(_named("a1", NOW - H))
    assert _sessions(names=False) == NAMES_ONLY


def test_window_unnamed_and_opted_out_left_out(db):
    _store(_named("old", NOW - 30 * H), _ev("anon", NOW - H, ref=None, kind="unknown"),
           _ev("u1", NOW - H, ref=None, kind="untracked"), _named("b1", NOW - 2 * H, ref="user_b"))
    ustore.set_frigate_tracking("user_b", False)  # opted out after storage → hidden at read time
    frigate_consent.invalidate()
    assert _sessions() == []


def test_name_filter(db):
    _store(_named("a1", NOW - H), _named("b1", NOW - H, ref="user_b"))
    lines = _sessions(name="USER_B")
    assert len(lines) == 1 and "USER_B" in lines[0]


# ── the tool helper: frigate.db when module 0 is on; off → it says so ──

def _tool(args, uid="user_a"):
    return asyncio.run(executor_camera._face_events(args, uid, "nb"))


def test_tool_reads_frigate_db_when_on(db, monkeypatch, tmp_path):
    _store(_named("a1", NOW - H))
    out = _tool({"hours_back": 24})
    assert "USER_A" in out and "event_id: a1" in out
    assert _tool({"hours_back": 24}, uid="nobody") == NAMES_ONLY


def test_tool_says_so_when_module_0_is_off(db, monkeypatch):
    monkeypatch.setattr(query, "available", lambda: False)
    _store(_named("a1", NOW - H))
    assert _tool({"hours_back": 24}) == t("fq_unavailable", "nb")
