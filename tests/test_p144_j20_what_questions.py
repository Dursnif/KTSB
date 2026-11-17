"""J20 (2026-11-06): one card «Hvem / Hva / Hvor?». «Hva er dette?» asks admin about still, non-living things KTSB
sees often (J3 gate keys) and still things Frigate misreads as an animal (the swan-shaped flower pot as «bird»); an answer
«ikke en <label>» / «feil deteksjon» becomes a narrow confirmed-wrong rule (camera + label + cell; P144 v2 #28) in
weights.db. Code holds that detection back only at that cell, only when it did not move and no other camera saw the same
label within 30 s — in shadow (logged on the event) until the GUI switch «Hold tilbake bekreftet feil» is on.
Tmp DBs; Frigate is a fake; no real HTTP."""
import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.config_migrations import fill_missing_keys
from kaare_core.frigate_user import pipe
from kaare_core.frigate_user import places_learned as pl
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.frigate_user import thing_names as tn
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore
from tests import test_p144_s06a_pipe as s06a

TZ = ZoneInfo("Europe/Oslo")
NOW = datetime(2027, 1, 15, 20, 0, tzinfo=TZ).timestamp()
DAY = 86400.0
BIN_KEY = "vest|waste_bin|1,2"
BIRD_BOX = [0.42, 0.61, 0.05, 0.07]  # centre 0.445, 0.645 → cell 4,6


@pytest.fixture(autouse=True)
def tmp_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    yield
    fstore.reset_for_tests()
    wstore.reset_for_tests()


def _bin(eid, t, verdict="unchanged", key=BIN_KEY, cam="vest"):
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label="waste_bin", start_ts=t, end_ts=t + 5, drives=False,
                                   check={"gate": {"verdict": verdict, "key": key, "thing_id": None}})


def _bird(eid, t, box=BIRD_BOX, stationary=True, cam="trapp"):
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label="bird", start_ts=t, end_ts=t + 8, stationary=stationary,
                                   drives=not stationary, boxes=[[t, *box, 0.7, 40, int(stationary)],
                                                                 [t + 8, *box, 0.7, 80, int(stationary)]])


def _days(make, n_days=3, per_day=2):
    return [make(f"e{d}-{i}", NOW - (d + 1) * DAY + i * 900) for d in range(n_days) for i in range(per_day)]


# ── the questions ───────────────────────────────────────────────────────────────────────────────────────────────

def test_a_still_thing_seen_on_three_days_is_asked_about():
    qs = tn.questions(_days(_bin), {}, NOW)
    assert [(q["id"], q["label"], q["days"]) for q in qs] == [(BIN_KEY, "waste_bin", 3)]
    assert qs[0]["kind"] == "what" and qs[0]["misread"] is False


def test_too_few_days_or_a_changed_thing_is_not_asked():
    assert tn.questions(_days(_bin, n_days=2, per_day=4), {}, NOW) == []
    changed = [_bin(f"c{d}", NOW - (d + 1) * DAY, verdict="changed") for d in range(4)]
    assert tn.questions(changed, {}, NOW) == []


def test_a_still_animal_at_the_same_spot_is_a_misread_question():
    """The swan pot: Frigate's «bird» that never moves, same cell, three days."""
    qs = tn.questions(_days(_bird), {}, NOW)
    assert [(q["id"], q["misread"]) for q in qs] == [("trapp|bird|4,6", True)]


def test_a_moving_animal_is_never_a_question():
    assert tn.questions(_days(lambda e, t: _bird(e, t, stationary=False)), {}, NOW) == []


def test_persons_are_never_a_what_question():
    evs = [fstore.FrigateUserEvent(event_id=f"p{d}", camera="vest", label="person", start_ts=NOW - d * DAY,
                                   stationary=True, check={"gate": {"key": "vest|person|1,1"}}) for d in range(1, 5)]
    assert tn.questions(evs, {}, NOW) == []


def test_answered_keys_are_not_asked_again():
    tn.answer("boss", BIN_KEY, "named", name="blå dunk")
    assert tn.questions(_days(_bin), tn.answers(), NOW) == []


# ── answers and the confirmed-wrong rule ─────────────────────────────────────────────────────────────────────────

def test_answers_are_append_only_newest_counts():
    assert tn.answer("boss", "trapp|bird|4,6", "skip")["ok"]  # N39: dict result
    assert tn.answer("boss", "trapp|bird|4,6", "not_label", name="svanepotte")["ok"]  # N39: dict result
    a = tn.answers()["trapp|bird|4,6"]
    assert (a["status"], a["name"]) == ("not_label", "svanepotte")
    assert fstore._with_conn(lambda c: c.execute("SELECT COUNT(*) FROM thing_answers").fetchone()[0]) == 2


def test_bad_answers_are_refused():
    assert tn.answer("boss", "trapp|bird|4,6", "named", name="  ")["ok"] is False  # N39: dict result
    assert tn.answer("boss", "not a key", "skip")["ok"] is False  # N39: dict result
    assert tn.answer("boss", "trapp|bird|4,6", "delete")["ok"] is False  # N39: dict result
    assert tn.answer("", "trapp|bird|4,6", "skip")["ok"] is False  # N39: dict result


@pytest.mark.parametrize("status", ["not_label", "wrong"])
def test_not_a_label_and_wrong_detection_make_a_narrow_rule(status):
    tn.answer("boss", "trapp|bird|4,6", status)
    assert tn.rule_for("trapp", "bird", BIRD_BOX) == "trapp|bird|4,6"
    assert tn.rule_for("trapp", "bird", [0.62, 0.61, 0.05, 0.07]) is None  # the next cell: not covered
    assert tn.rule_for("trapp", "cat", BIRD_BOX) is None  # another label
    assert tn.rule_for("hage", "bird", BIRD_BOX) is None  # another camera


def test_a_name_alone_makes_no_rule():
    tn.answer("boss", "trapp|bird|4,6", "named", name="svanepotte")
    assert tn.rule_for("trapp", "bird", BIRD_BOX) is None


def test_hold_back_only_when_still_and_unsupported():
    tn.answer("boss", "trapp|bird|4,6", "not_label")
    still = [[[0.445, 0.68], NOW], [[0.446, 0.68], NOW + 5]]
    moved = [[[0.445, 0.68], NOW], [[0.7, 0.68], NOW + 5]]
    assert tn.hold("trapp", "bird", BIRD_BOX, still, NOW, others=[])["hold"] is True
    assert tn.hold("trapp", "bird", BIRD_BOX, moved, NOW, others=[])["hold"] is False
    seen_elsewhere = [fstore.FrigateUserEvent(event_id="o", camera="hage", label="bird", start_ts=NOW + 10)]
    assert tn.hold("trapp", "bird", BIRD_BOX, still, NOW, others=seen_elsewhere)["hold"] is False
    assert tn.hold("trapp", "bird", [0.62, 0.61, 0.05, 0.07], still, NOW, others=[]) is None  # no rule there


def test_history_newest_first():
    tn.answer("boss", BIN_KEY, "named", name="blå dunk")
    tn.answer("boss", "trapp|bird|4,6", "not_label", name="svanepotte")
    assert [(h["id"], h["status"]) for h in tn.history(tn.answers())] == [("trapp|bird|4,6", "not_label"),
                                                                           (BIN_KEY, "named")]


# ── the switch (rule 25) ────────────────────────────────────────────────────────────────────────────────────────

def test_the_switch_defaults_to_shadow_and_validates():
    assert fsettings.questions_wrong_rules() is False
    fsettings.update({"questions": {"wrong_rules": True}})
    assert fsettings.questions_wrong_rules() is True
    with pytest.raises(ValueError):
        fsettings.update({"questions": {"wrong_rules": "yes"}})


def test_the_shipped_default_has_the_switch_off():
    tpl = yaml.safe_load(Path("/kaare/configs_default/frigate_user.yaml").read_text(encoding="utf-8"))
    assert tpl["questions"]["wrong_rules"] is False


# ── the image moment: a still animal is part of the place ──────────────────────────────────────────────────────

def test_a_still_animal_does_not_block_the_image_moment_but_a_person_does():
    noon = datetime(2027, 1, 15, 12, 0, tzinfo=TZ).timestamp()
    pot = _bird("pot", noon - 3600, stationary=True)
    pot.end_ts = noon + 3600
    assert pl.empty_moment([pot], "trapp", NOW, TZ) == noon
    person = fstore.FrigateUserEvent(event_id="p", camera="trapp", label="person", start_ts=noon - 10, end_ts=noon + 10)
    assert pl.empty_moment([pot, person], "trapp", NOW, TZ) != noon


# ── wiring: the pipe and the API ────────────────────────────────────────────────────────────────────────────────

def test_pipe_records_the_rule_in_shadow_and_holds_back_when_on(monkeypatch):
    tn.answer("boss", "trapp|bird|4,6", "not_label")
    data = {"box": BIRD_BOX, "path_data": [[[0.445, 0.68], NOW], [[0.446, 0.68], NOW + 5]]}
    rule, sel, why, wr = asyncio.run(pipe._wrong_rule_check("e1", "trapp", "bird", data, NOW, "normal", True, "selected"))
    assert (sel, wr["mode"], wr["hold"]) == (True, "shadow", True)
    fsettings.update({"questions": {"wrong_rules": True}})
    rule, sel, why, wr = asyncio.run(pipe._wrong_rule_check("e1", "trapp", "bird", data, NOW, "normal", True, "selected"))
    assert (rule, sel, why, wr["mode"]) == ("none", False, "confirmed_wrong", "on")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(rfu, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(rfu, "_camera_cards", dict, raising=False)
    monkeypatch.setattr(rfu.time, "time", lambda: NOW)
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    ustore.create_user(username="mum", display_name="Mum", role="adult", pin="482915")
    app = FastAPI()
    app.include_router(rfu.router)
    yield TestClient(app)
    frigate_consent.invalidate()


def _hdr(user, role):
    return {"Authorization": f"Bearer {auth.create_token(user, role)}"}


@pytest.mark.parametrize("path", ["/api/frigate_user/questions/what", "/api/frigate_user/questions/what/answered"])
@pytest.mark.parametrize("who", [None, ("mum", "adult")])
def test_what_endpoints_are_admin_only(client, path, who):
    r = client.get(path, headers=_hdr(*who) if who else {})
    assert r.status_code == (401 if who is None else 403)


def test_what_answer_is_admin_only(client):
    body = {"id": "trapp|bird|4,6", "answer": "skip"}
    assert client.post("/api/frigate_user/questions/what/answer", json=body).status_code == 401
    assert client.post("/api/frigate_user/questions/what/answer", json=body, headers=_hdr("mum", "adult")).status_code == 403


def test_what_flow_through_the_api(client):
    for e in _days(_bin):
        fstore.record_event(e)
    qs = client.get("/api/frigate_user/questions/what", headers=_hdr("boss", "admin")).json()["questions"]
    assert [q["id"] for q in qs] == [BIN_KEY]
    r = client.post("/api/frigate_user/questions/what/answer", headers=_hdr("boss", "admin"),
                    json={"id": BIN_KEY, "answer": "named", "name": "blå dunk"})
    assert r.status_code == 200
    assert client.get("/api/frigate_user/questions/what", headers=_hdr("boss", "admin")).json()["questions"] == []
    hist = client.get("/api/frigate_user/questions/what/answered", headers=_hdr("boss", "admin")).json()["answered"]
    assert [(h["id"], h["name"]) for h in hist] == [(BIN_KEY, "blå dunk")]


def test_migration_adds_the_switch_to_an_existing_install():
    tpl = yaml.safe_load(Path("/kaare/configs_default/frigate_user.yaml").read_text(encoding="utf-8"))
    old = {"questions": {"chat": False}}
    fill_missing_keys(old, tpl)
    assert old["questions"] == {"chat": False, "wrong_rules": False}  # the user's own value kept, the new key added


env = s06a.env


def test_arrival_runs_the_rule_check_and_writes_it_on_the_event(env, monkeypatch):
    seen = []

    async def fake(event_id, camera, label, data, start_ts, rule, sel, why):
        seen.append(event_id)
        return rule, sel, why, {"key": "inngang|person|1,1", "hold": True, "why": "confirmed_wrong", "mode": "shadow"}

    monkeypatch.setattr(pipe, "_wrong_rule_check", fake)
    s06a._process(s06a._event("w1"), "Person A", 0.95)
    assert seen == ["w1"]
    assert s06a._stored("w1").check["wrong_rule"]["mode"] == "shadow"
