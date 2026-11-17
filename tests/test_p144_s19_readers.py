"""P144-s19: everything that turns stored claims into words picks up the second face sensor — Kåre's camera actions
(last_seen / episode), the 27B conclusion's «who» line, the GUI episode detail — with names only as consent allows.
tmp DBs/configs only; placeholders (Person A …)."""

from zoneinfo import ZoneInfo

import pytest

from kaare_core.frigate_user import conclude, faces, query
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

OSLO = ZoneInfo("Europe/Oslo")
NOW = 1_800_000_000.0
CAMS = {"cam_door": {"display_name": "Inngang", "role": "front_door"}}
L0_PASS = {"layer": "l0", "verdict": "pass", "strong": False, "reason": "face_px=20000", "contradictions": []}


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n  Person A: {class: household}\n  Person D: {class: household}\n")
    monkeypatch.setattr(query, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(fstore, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(query, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(router_frigate_user, "_camera_cards", lambda: CAMS)
    fstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    for u, face in (("membera", "Person A"), ("memberd", "Person D")):
        ustore.create_user(username=u, display_name=u.title(), role="adult", pin="482915")
        ustore.update_user(u, frigate_faces=[face])
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _sensors(relation, frigate="membera", second="memberd", lean=None):
    return {"provider": "double_take", "relation": relation, "frigate": frigate, "frigate_face": None,
            "double_take": second, "double_take_face": None, "double_take_kind": "household", "lean": lean}


def _ev(event_id, start, *, ref="membera", claim="unverified", sensors=None):
    check = {"decision": claim, "l0": L0_PASS}
    if sensors:
        check["sensors"] = sensors
    return fstore.FrigateUserEvent(event_id=event_id, camera="cam_door", label="person", start_ts=start,
                                   end_ts=start + 30, person_ref=ref, person_kind="household", claim_status=claim,
                                   claim_score=0.82, check=check, place="gang")


def _store(*events):
    for e in events:
        assert fstore.record_event(e)
    fstore.close_visits(now=NOW)


def test_last_seen_two_sensors_agree(mem):
    _store(_ev("a1", NOW - 3600, claim="confirmed", sensors=_sensors("agree", second="membera")))
    out = query.last_seen("Membera", names=True, lang="nb", now=NOW)
    # J14 / P149 D1: was «høyst sannsynlig»; machine «confirmed» now reads «sannsynlig»
    assert "sannsynlig" in out and "høyst sannsynlig" not in out and "ansiktssensorer er enige" in out
    assert "Double Take: Membera" in out


def test_last_seen_disagreement_names_both_sensors(mem):
    _store(_ev("a1", NOW - 3600, sensors=_sensors("disagree")))
    out = query.last_seen("Membera", names=True, lang="nb", now=NOW)
    assert "usikkert" in out and "uenige" in out
    assert "Frigate: Membera" in out and "Double Take: Memberd" in out


def test_other_sensor_name_hidden_after_opt_out(mem):
    _store(_ev("a1", NOW - 3600, sensors=_sensors("disagree")))
    ustore.set_frigate_tracking("memberd", False)
    frigate_consent.invalidate()
    out = query.last_seen("Membera", names=True, lang="nb", now=NOW)
    assert "Memberd" not in out and "memberd" not in out and "Person D" not in out


def test_episode_detail_shows_both_sensors(mem):
    _store(_ev("a1", NOW - 3600, sensors=_sensors("disagree")))
    ep_id = fstore.event_by_id("a1").episode_id
    out = query.episode(ep_id, names=True, lang="nb")
    assert "Frigate: Membera · Double Take: Memberd" in out and "usikkert" in out
    assert "Memberd" not in query.episode(ep_id, names=False, lang="nb")


def test_conclusion_who_line_names_the_disagreement(mem):
    ev = _ev("a1", NOW - 3600, sensors=_sensors("disagree"))
    who = conclude._who(ev, "nb")
    assert "membera" in who and "uenige" in who


def test_conclusion_who_line_two_sensors(mem):
    ev = _ev("a1", NOW - 3600, claim="confirmed", sensors=_sensors("agree", second="membera"))
    assert "enige" in conclude._who(ev, "nb")


def test_gui_detail_carries_both_sensors(mem):
    _store(_ev("a1", NOW - 3600, sensors=_sensors("disagree")))
    ep_id = fstore.event_by_id("a1").episode_id
    detail = router_frigate_user._detail_view(ep_id)
    s = detail["events"][0]["check"]["sensors"]
    assert s["relation"] == "disagree" and s["frigate"] == "Membera" and s["double_take"] == "Memberd"
    assert detail["events"][0]["certainty"] == "uncertain"


def test_gui_detail_hides_an_opted_out_second_sensor_name(mem):
    _store(_ev("a1", NOW - 3600, sensors=_sensors("disagree")))
    ustore.set_frigate_tracking("memberd", False)
    frigate_consent.invalidate()
    ep_id = fstore.event_by_id("a1").episode_id
    assert "Memberd" not in str(router_frigate_user._detail_view(ep_id))
    assert "memberd" not in str(router_frigate_user._detail_view(ep_id))
