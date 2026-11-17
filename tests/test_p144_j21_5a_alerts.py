"""J21 step 5a (P144 phase 5): the away-mode alert moves into module 7 unchanged.

The expected calls below were produced by the OLD `frigate_responder._maybe_notify_away` before the move (run-queue J21
log: 406 recorded end events × score/away/night/baseline/role variants = 29 256 cases, 12 204 alerts, 0 differences between
old and new). Module 7 must give exactly these. The single sender is module 7's own starter (phase 6c,
tests/test_p144_6c_alert_starter.py); the old responder is gone (phase 6, List B). No real notify, no real users, no log file written."""
import asyncio

import pytest

from adapters import frigate_adapter
from kaare_core.frigate_user import alerts

USERS = [{"username": "a", "role": "admin"}, {"username": "b", "role": "adult"}, {"username": "c", "role": "child"}]
LANGS = {"a": "nb", "b": "en", "c": "de"}


@pytest.fixture
def world(monkeypatch):
    calls, logged = [], []

    async def notify(uid, msg, urgency=None, source_key=None):
        calls.append((uid, msg, str(urgency), source_key))

    monkeypatch.setattr(alerts, "notify_user", notify)
    monkeypatch.setattr(alerts, "list_users", lambda: USERS)
    monkeypatch.setattr(alerts, "get_lang", lambda uid: LANGS[uid])
    monkeypatch.setattr(alerts, "_cfg", lambda: {"cameras": {"cam_door": {"role": "front_door"}}, "away_mode": {}})
    monkeypatch.setattr(alerts, "write_notify_log", lambda e: logged.append({k: v for k, v in e.items() if k != "ts"}))
    state = {"away": True, "baseline": (0.5, 0.0)}
    monkeypatch.setattr(frigate_adapter, "is_away", lambda: state["away"])
    monkeypatch.setattr(frigate_adapter, "_get_deviation_score", lambda cam, label: state["baseline"])
    monkeypatch.setattr(frigate_adapter, "_is_nighttime", lambda a, b: True)
    return calls, logged, state


def _run(camera, label, score):
    asyncio.run(alerts.maybe_notify_away(camera, label, score))


def test_away_and_high_alerts_every_admin_and_adult_in_their_language(world):
    calls, logged, _ = world
    _run("cam_door", "person", 0.9)
    assert [(c[0], c[2], c[3]) for c in calls] == [("a", "high", "frigate:cam_door:person"),
                                                   ("b", "high", "frigate:cam_door:person")]
    assert "cam_door" in calls[0][1] and calls[0][1] != calls[1][1]  # nb and en texts
    assert [(e["user_id"], e["urgency"], e["channel"]) for e in logged] == [("a", "high", "sound_node"),
                                                                           ("b", "high", "sound_node")]


def test_a_known_normal_pattern_is_medium(world):
    calls, logged, state = world
    state["baseline"] = (0.1, 0.8)  # deviation < 0.3 at baseline confidence ≥ 0.5 → medium
    # R146 (2026-11-12, option B): a person stays HIGH while away; the graduation applies to other labels
    _run("cam_door", "person", 0.9)
    assert {c[2] for c in calls} == {"high"} and {e["urgency"] for e in logged} == {"high"}
    calls.clear()
    logged.clear()
    _run("cam_door", "car", 0.9)
    assert {c[2] for c in calls} == {"medium"} and {e["urgency"] for e in logged} == {"medium"}


def test_home_or_low_score_by_day_sends_nothing(world, monkeypatch):
    calls, _, state = world
    monkeypatch.setattr(frigate_adapter, "_is_nighttime", lambda a, b: False)  # by night a weak perimeter hit is high
    _run("cam_door", "person", 0.3)
    state["away"] = False
    _run("cam_door", "person", 0.9)
    assert calls == []


def test_no_label_uses_the_unknown_text(world):
    calls, _, _ = world
    _run("cam_door", "", 0.9)
    assert [c[3] for c in calls] == ["frigate:cam_door:unknown", "frigate:cam_door:unknown"]


def test_a_failure_never_raises(world, monkeypatch):
    monkeypatch.setattr(alerts, "list_users", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    _run("cam_door", "person", 0.9)  # logged, not raised


def test_a_weak_hit_at_the_perimeter_by_night_alerts(world):
    """The old path's rule (unchanged): away + perimeter + night + confidence < 0.5 → high."""
    calls, _, _ = world
    _run("cam_door", "person", 0.3)
    assert [c[0] for c in calls] == ["a", "b"]
