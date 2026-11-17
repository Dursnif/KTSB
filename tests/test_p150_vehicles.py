"""P150 phase 6b (2026-11-14, two yes: «anonym id på ikke kjente … samme som ukjente mennesker i frigate.. en måte/en
vei» → «menneske og frigate er bygget.. du skal replikere»).

Vehicles get the same path unknown people already have: a recognised (verified) plate becomes a stable pseudonymous
`vehicle-<hex>` id — the HMAC of the normalised plate under frigate.db's pseudonym key, like `person-<hex>`; the admin
classifies it (household / safe guest / not welcome) and may give it a household name, like faces in «Ansiktsklassifisering»;
the privacy rule still drops the plate text after its days, the id stays. Until the blind LLM read (R153) exists no plate
counts as verified — no id from noise. The plate text never leaves the store (not in the API, not in the GUI).
Never the house: frigate.db and configs point at tmp_path.
"""

import asyncio
import json

import pytest
import yaml
from fastapi import HTTPException

from kaare_core.frigate_user import pseudonyms, vehicles
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu
from tests.test_p144_s04_frigate_store import T0, _ev, _one
from tests.test_p144_s04_frigate_store import db as _s04_db  # noqa: F401 — fixture

DAY = 86400.0


@pytest.fixture
def db(_s04_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(vehicles, "CONFIG_PATH", tmp_path / "frigate_vehicles.yaml")
    return _s04_db


def _car(eid, start, plate="AB 12345", vid=None, known=False):
    return _ev(eid, person_ref=None, kind="unknown", claim="none", start=start, camera="innkjorsel", label="car",
               plate_claim=plate, plate_known=known, vehicle_id=vid)


# ── the id ────────────────────────────────────────────────────────────────────

def test_plate_normalising_and_a_stable_vehicle_id(db):
    assert pseudonyms.canonical_plate(" ab-12 345 ") == "AB12345"
    assert pseudonyms.canonical_plate("OI 1O") == "0110"
    a = pseudonyms.vehicle_id("AB 12345")
    assert a.startswith("vehicle-") and len(a) == len("vehicle-") + 6
    assert pseudonyms.vehicle_id("ab-12345") == a
    assert pseudonyms.vehicle_id("AB 12346") != a
    assert "12345" not in a


def test_no_plate_is_verified_until_the_llm_read_exists():
    assert vehicles.verified_plate({"plate": "AB12345"}) is False


def test_an_unverified_plate_gets_no_id_a_verified_one_does(db, monkeypatch):
    assert vehicles.id_for("AB 12345", plate_known=False, event={}) is None
    monkeypatch.setattr(vehicles, "verified_plate", lambda event: True)
    assert vehicles.id_for("AB 12345", plate_known=False, event={}) == pseudonyms.vehicle_id("AB 12345")
    assert vehicles.id_for("Toyota Auris", plate_known=True, event={}) is None, "a known plate has its Frigate name"


# ── the store keeps the id; the privacy rule drops only the text ──────────────

def test_the_id_is_stored_and_survives_the_privacy_rule(db):
    vid = pseudonyms.vehicle_id("AB 12345")
    assert fstore.record_event(_car("c1", T0, vid=vid))
    fstore.expire_unknown_plates(days=7, now=T0 + 30 * DAY)
    e = _one("c1")
    assert e.plate_claim is None and e.vehicle_id == vid


def test_a_later_update_without_the_id_keeps_it(db):
    vid = pseudonyms.vehicle_id("AB 12345")
    fstore.record_event(_car("c1", T0, vid=vid))
    fstore.record_event(_car("c1", T0, vid=None))
    assert _one("c1").vehicle_id == vid


def test_vehicle_library_counts_days_and_reads(db):
    vid = pseudonyms.vehicle_id("AB 12345")
    for i, day in enumerate((0, 0, 3, 9)):
        fstore.record_event(_car(f"c{i}", T0 + day * DAY + i, vid=vid))
    fstore.record_event(_car("other", T0, vid=None))
    lib = fstore.vehicle_library()
    assert len(lib) == 1
    row = lib[0]
    assert row["vehicle_id"] == vid and row["reads"] == 4 and row["days_seen"] == 3
    assert row["newest_event_id"] == "c3"
    assert "AB" not in json.dumps(row) and "12345" not in json.dumps(row)


# ── classification + household name, like faces ───────────────────────────────

def test_classify_and_name_a_vehicle(db):
    vid = pseudonyms.vehicle_id("AB 12345")
    assert vehicles.classify(vid) == vehicles.UNCLASSIFIED
    vehicles.set_vehicle(vid, "safe_guest", "Bestefars bil")
    assert vehicles.classify(vid) == "safe_guest" and vehicles.name_of(vid) == "Bestefars bil"
    data = yaml.safe_load(vehicles.CONFIG_PATH.read_text(encoding="utf-8"))
    assert data["vehicles"][vid] == {"class": "safe_guest", "name": "Bestefars bil"}


@pytest.mark.parametrize(("vid", "cls", "name"), [
    ("person-abcdef", "household", ""),       # not a vehicle id
    ("vehicle-xyz", "household", ""),         # not hex
    ("vehicle-abcdef", "best_friend", ""),    # unknown class
    ("vehicle-abcdef", "household", "x" * 101),
])
def test_bad_input_is_refused(db, vid, cls, name):
    with pytest.raises(ValueError):
        vehicles.set_vehicle(vid, cls, name)


# ── the API (Integrations → Frigate → Kjøretøy) ───────────────────────────────

def test_api_lists_vehicles_without_plate_text_and_sets_one(db):
    vid = pseudonyms.vehicle_id("AB 12345")
    fstore.record_event(_car("c1", T0, vid=vid))
    fstore.record_event(_car("k1", T0, plate="Toyota Auris", known=True))
    got = asyncio.run(rfu.api_list_vehicles(_u=None))
    assert [v["vehicle_id"] for v in got["vehicles"]] == [vid]
    assert got["vehicles"][0]["class"] == vehicles.UNCLASSIFIED
    assert got["known"] == [{"name": "Toyota Auris", "reads": 1}]
    assert "AB 12345" not in json.dumps(got) and "12345" not in json.dumps(got)
    row = asyncio.run(rfu.api_put_vehicle(vid, rfu.VehicleUpdate(**{"class": "household", "name": "Varebilen"}),
                                          u={"sub": "admin"}))
    assert row["class"] == "household" and row["name"] == "Varebilen"


def test_api_refuses_a_vehicle_it_has_never_seen(db):
    with pytest.raises(HTTPException) as err:
        asyncio.run(rfu.api_put_vehicle("vehicle-abcdef", rfu.VehicleUpdate(**{"class": "household", "name": ""}),
                                        u={"sub": "admin"}))
    assert err.value.status_code == 404


# ── «Hvem / Hva / Hvor»: the vehicle question ─────────────────────────────────

def _seen_on_days(vid, days):
    for d in days:
        fstore.record_event(_car(f"{vid}-d{d}", T0 + d * DAY, vid=vid))


def test_a_vehicle_becomes_a_question_after_enough_days(db):
    vid = pseudonyms.vehicle_id("AB 12345")
    _seen_on_days(vid, [0, 1])
    assert vehicles.questions() == []
    _seen_on_days(vid, [5])
    assert [q["vehicle_id"] for q in vehicles.questions()] == [vid]


def test_an_answer_or_a_skip_closes_the_question_but_keeps_the_vehicle(db):
    a, b = pseudonyms.vehicle_id("AB 12345"), pseudonyms.vehicle_id("CD 67890")
    _seen_on_days(a, [0, 1, 2])
    _seen_on_days(b, [0, 1, 2])
    vehicles.set_vehicle(a, "safe_guest", "Bestefars bil")
    asyncio.run(rfu.api_vehicle_skip(rfu.VehicleSkip(vehicle_id=b), _u=None))
    assert vehicles.questions() == []
    assert {r["vehicle_id"] for r in vehicles.rows()} == {a, b}


def test_question_days_is_a_gui_setting(db):
    got = rfu.api_put_vehicle_settings(rfu.VehicleSettings(question_days=5), _u=None)
    assert got == {"question_days": 5} and vehicles.question_days() == 5
    with pytest.raises(HTTPException):
        rfu.api_put_vehicle_settings(rfu.VehicleSettings(question_days=0), _u=None)


def test_template_ships_the_vehicle_config():
    with open("/kaare/configs_default/frigate_vehicles.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data == {"question_days": 3, "vehicles": {}}
