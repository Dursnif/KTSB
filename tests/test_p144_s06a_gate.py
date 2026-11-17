"""P144-s06a: the store's gate by claim status (2026-10-26, «storage gate by claim status»).

`unverified` (a check ran, no contradiction, could not confirm) keeps Frigate's claimed name, marked unverified;
`rejected` and `none` drop it; a person who opted out of tracking is dropped whatever the status. The gaps the
store folds with come from the GUI setting (configs/frigate_user.yaml), with the old constants as fallback.
Never touches live state — users.db, frigate.db and the config point at tmp_path."""

import pytest

from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    fstore.reset_for_tests()
    ustore.init_db()
    for name in ("persona", "personb"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    ustore.update_user("personb", frigate_faces=["Person B"])
    ustore.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _ev(event_id="e1", *, ref="persona", kind="household", claim="unverified", start=T0, **kw):
    fields = {
        "event_id": event_id, "camera": "inngang", "label": "person", "start_ts": start,
        "end_ts": start + 10, "person_ref": ref, "person_kind": kind, "claim_status": claim,
        "claim_score": 0.91,
    }
    fields.update(kw)
    return fstore.FrigateUserEvent(**fields)


def _one(event_id="e1"):
    return next(e for e in fstore.events_between(0, T0 * 2) if e.event_id == event_id)


def test_unverified_keeps_name_marked_unverified(db):
    assert fstore.record_event(_ev(claim="unverified", description="Person A at the door"))
    e = _one()
    assert e.person_ref == "persona"
    assert e.person_kind == "household"
    assert e.claim_status == "unverified"
    assert "storage_gate" not in e.check


def test_unverified_guest_face_name_kept(db):
    assert fstore.record_event(_ev(ref="Person C", kind="safe_guest", claim="unverified"))
    assert _one().person_ref == "Person C"


@pytest.mark.parametrize("claim", ["rejected", "none"])
def test_rejected_and_none_drop_name(db, claim):
    assert fstore.record_event(_ev(claim=claim, analysis="persona walks in"))
    e = _one()
    assert e.person_ref is None
    assert e.person_kind == "unknown"
    assert e.check["storage_gate"] == f"claim_{claim}"
    assert "persona" not in (e.analysis or "").lower()


@pytest.mark.parametrize("claim", ["unverified", "confirmed"])
def test_opt_out_drops_name_whatever_the_status(db, claim):
    assert fstore.record_event(_ev(ref="personb", claim=claim))
    e = _one()
    assert e.person_ref is None
    assert e.person_kind == "untracked"


def test_store_gaps_come_from_settings(db):
    (db / "frigate_user.yaml").write_text("episode_gap_s: 30\nvisit_gap_s: 60\n")
    assert fstore.record_event(_ev("a", claim="confirmed", start=T0))
    # 100 s later: inside the old 120 s constant, outside the configured 30 s → new episode
    assert fstore.record_event(_ev("b", claim="confirmed", start=T0 + 100))
    assert _one("a").episode_id != _one("b").episode_id
    fstore.close_visits(now=T0 + 10_000)
    visits = fstore.visits_between(0, T0 * 2, person_ref="persona")
    assert len(visits) == 2  # 90 s apart > the configured 60 s visit gap


def test_store_gap_falls_back_without_config(db):
    assert fstore.record_event(_ev("a", claim="confirmed", start=T0))
    assert fstore.record_event(_ev("b", claim="confirmed", start=T0 + 100))
    assert _one("a").episode_id == _one("b").episode_id  # 120 s default
