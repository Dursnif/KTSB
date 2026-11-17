"""The Frigate user's fact store (P144-s04): the storage gate, visit folding, last_visit, schema
migration from version 0, and the `frigate` system account. Never touches live state — both
users.db and frigate.db point at tmp_path."""

import sqlite3

import pytest

from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent, profile_manager
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    for name in ("userb", "userone"):
        ustore.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    ustore.update_user("userb", frigate_faces=["Userb"])
    ustore.update_user("userone", frigate_faces=["Userone"])
    ustore.set_frigate_tracking("userb", False)
    frigate_consent.invalidate()
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _ev(event_id="e1", *, person_ref="userone", kind="household", claim="confirmed", start=T0, **kw):
    fields = {
        "event_id": event_id,
        "camera": "inngang",
        "label": "person",
        "start_ts": start,
        "end_ts": start + 10,
        "person_ref": person_ref,
        "person_kind": kind,
        "claim_status": claim,
        "claim_score": 0.91,
    }
    fields.update(kw)
    return fstore.FrigateUserEvent(**fields)


def _one(event_id="e1"):
    rows = fstore.events_between(0, T0 * 2)
    return next(e for e in rows if e.event_id == event_id)


# ── storage gate ────────────────────────────────────────────────────────────────


def test_confirmed_household_keeps_name(db):
    assert fstore.record_event(_ev())
    e = _one()
    assert e.person_ref == "userone"
    assert e.person_kind == "household"
    assert e.claim_status == "confirmed"


def test_rejected_claim_never_stores_name(db):
    assert fstore.record_event(
        _ev(claim="rejected", analysis="Userone står ved døra", check={"claimed": "Userone"})
    )
    e = _one()
    assert e.person_ref is None
    assert e.person_kind == "unknown"
    assert e.claim_status == "rejected"
    assert "Userone" not in (e.analysis or "")
    assert "Userone" not in str(e.check)


def test_unverified_claim_keeps_name(db):
    """Changed by s06a (2026-10-26, «storage gate by claim status»): a claim checked without contradiction
    but not confirmed keeps the name, marked unverified. Was: name dropped, kind unknown."""
    assert fstore.record_event(_ev(claim="unverified"))
    e = _one()
    assert e.person_ref == "userone"
    assert e.person_kind == "household"
    assert e.claim_status == "unverified"


def test_opted_out_person_is_untracked(db):
    assert fstore.record_event(
        _ev(person_ref="userb", description="Userb i rød jakke", analysis="userb går inn")
    )
    e = _one()
    assert e.person_ref is None
    assert e.person_kind == "untracked"
    assert "userb" not in (e.description or "").lower()
    assert "userb" not in (e.analysis or "").lower()


def test_opted_out_by_face_name_is_untracked(db):
    assert fstore.record_event(_ev(person_ref="Userb"))
    assert _one().person_ref is None


def test_consent_lookup_error_fails_closed(db, monkeypatch):
    def boom(_):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(ustore, "get_user", boom)
    assert fstore.record_event(_ev())
    e = _one()
    assert e.person_ref is None
    assert e.person_kind == "untracked"


def test_safe_guest_face_name_kept(db):
    assert fstore.record_event(_ev(person_ref="Onkel Per", kind="safe_guest"))
    assert _one().person_ref == "Onkel Per"


def test_record_event_upserts_same_event(db):
    assert fstore.record_event(_ev(end_ts=T0 + 5))
    assert fstore.record_event(_ev(end_ts=T0 + 50))
    rows = fstore.events_between(0, T0 * 2)
    assert len(rows) == 1
    assert rows[0].end_ts == T0 + 50


def test_invalid_kind_is_refused(db):
    assert fstore.record_event(_ev(kind="bogus")) is False
    assert fstore.events_between(0, T0 * 2) == []


def test_events_between_filters(db):
    fstore.record_event(_ev("a", start=T0))
    fstore.record_event(_ev("b", start=T0 + 100, camera="hage"))
    fstore.record_event(_ev("c", start=T0 + 200, person_ref="Onkel Per", kind="safe_guest"))
    assert [e.event_id for e in fstore.events_between(T0, T0 + 150)] == ["a", "b"]
    assert [e.event_id for e in fstore.events_between(0, T0 * 2, camera="hage")] == ["b"]
    assert [e.event_id for e in fstore.events_between(0, T0 * 2, person_ref="Onkel Per")] == ["c"]


# ── visits ──────────────────────────────────────────────────────────────────────


def test_events_within_gap_fold_into_one_visit(db):
    fstore.record_event(_ev("a", start=T0, camera="inngang"))
    fstore.record_event(_ev("b", start=T0 + 600, camera="hage"))
    fstore.close_visits(T0 + 700)
    visits = fstore.visits_between(0, T0 * 2, person_ref="userone")
    assert len(visits) == 1
    v = visits[0]
    assert v.event_count == 2
    assert v.first_ts == T0
    assert v.last_ts == T0 + 610
    assert sorted(v.cameras) == ["hage", "inngang"]


def test_events_beyond_gap_start_new_visit(db):
    fstore.record_event(_ev("a", start=T0))
    fstore.record_event(_ev("b", start=T0 + fstore.VISIT_GAP_S + 60))
    fstore.close_visits(T0 + 10_000)
    assert len(fstore.visits_between(0, T0 * 2, person_ref="userone")) == 2


def test_anonymous_events_make_no_visit(db):
    fstore.record_event(_ev(person_ref="userb"))  # opted out → no name
    fstore.close_visits(T0 + 100)
    assert fstore.visits_between(0, T0 * 2) == []


def test_close_visits_is_idempotent(db):
    fstore.record_event(_ev("a"))
    fstore.close_visits(T0 + 100)
    fstore.close_visits(T0 + 200)
    v = fstore.visits_between(0, T0 * 2)
    assert len(v) == 1 and v[0].event_count == 1


def test_last_visit(db):
    assert fstore.last_visit("userone") is None
    fstore.record_event(_ev("a", start=T0))
    fstore.record_event(_ev("b", start=T0 + 7200))
    fstore.close_visits(T0 + 8000)
    v = fstore.last_visit("userone")
    assert v is not None and v.first_ts == T0 + 7200
    assert fstore.last_visit("nobody") is None


# ── schema ──────────────────────────────────────────────────────────────────────


def test_db_and_folder_created_on_first_use(db):
    assert not fstore.DB_PATH.exists()
    fstore.events_between(0, 1)
    assert fstore.DB_PATH.exists()


def test_migration_from_version_0(db):
    """A file that exists but has no schema (version 0) is brought to the current version."""
    fstore.DB_PATH.parent.mkdir(parents=True)
    sqlite3.connect(fstore.DB_PATH).close()
    assert fstore.schema_version() == fstore.SCHEMA_VERSION
    conn = sqlite3.connect(fstore.DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"events", "visits", "hourly", "daily", "meta"} <= tables


def test_newer_schema_is_refused(db):
    fstore.DB_PATH.parent.mkdir(parents=True)
    conn = sqlite3.connect(fstore.DB_PATH)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '999')")
    conn.commit()
    conn.close()
    assert fstore.record_event(_ev()) is False


# ── system account ──────────────────────────────────────────────────────────────


def test_frigate_is_a_system_account(db):
    assert "frigate" in profile_manager.SYSTEM_ACCOUNTS
    assert "frigate" not in ustore.SYSTEM_IDENTITIES  # never listed as a person/identity
    assert ustore.get_user("frigate") is None  # no user row, no PIN, no keypair


@pytest.mark.parametrize("name", ["frigate", "kare"])
def test_service_account_names_are_reserved(db, name):
    with pytest.raises(ValueError):
        ustore.create_user(username=name, display_name=name, role="adult", pin="482915")


# ── episodes (amendment from the main session) ─────────────────────────────────

G = fstore.EPISODE_GAP_S


def _ep(event_id):
    return _one(event_id).episode_id


def test_episode_chains_across_cameras_within_gap(db):
    fstore.record_event(_ev("a", start=T0, camera="inngang"))
    fstore.record_event(_ev("b", start=T0 + 10 + G - 5, camera="hage"))
    assert _ep("a") is not None and _ep("a") == _ep("b")
    ep = fstore.episode(_ep("a"))
    assert sorted(ep.cameras) == ["hage", "inngang"]
    assert ep.status == "open"


def test_new_episode_after_gap_of_quiet(db):
    fstore.record_event(_ev("a", start=T0))
    fstore.record_event(_ev("b", start=T0 + 10 + G + 1))
    assert _ep("a") != _ep("b")


def test_return_to_first_camera_joins_episode(db):
    fstore.record_event(_ev("a", start=T0, camera="inngang"))
    fstore.record_event(_ev("b", start=T0 + 60, camera="hage"))
    fstore.record_event(_ev("c", start=T0 + 120, camera="inngang"))
    assert _ep("a") == _ep("b") == _ep("c")
    ep = fstore.episode(_ep("a"))
    assert ep.event_count == 3
    assert ep.start_ts == T0 and ep.end_ts == T0 + 130


def test_close_episodes_returns_newly_closed_once(db):
    fstore.record_event(_ev("a", start=T0))
    assert fstore.close_episodes(T0 + 10 + G - 1) == []  # still within G
    closed = fstore.close_episodes(T0 + 10 + G + 1)
    assert [e.episode_id for e in closed] == [_ep("a")]
    assert closed[0].status == "closed"
    assert fstore.close_episodes(T0 + 10_000) == []


def test_event_after_close_starts_new_episode(db):
    fstore.record_event(_ev("a", start=T0))
    fstore.close_episodes(T0 + 10 + G + 1)
    fstore.record_event(_ev("b", start=T0 + 30))  # late arrival: closed episodes never reopen
    assert _ep("a") != _ep("b")


def test_episode_person_refs_only_gate_allowed_names(db):
    fstore.record_event(_ev("a", start=T0, person_ref="userone"))
    fstore.record_event(_ev("b", start=T0 + 20, person_ref="userb"))  # opted out
    ep = fstore.episode(_ep("a"))
    assert ep.person_refs == ["userone"]
    assert sorted(ep.labels) == ["person"]


def test_upsert_keeps_episode(db):
    fstore.record_event(_ev("a", start=T0, end_ts=T0 + 5))
    first = _ep("a")
    fstore.record_event(_ev("a", start=T0, end_ts=T0 + 90))
    assert _ep("a") == first
    assert fstore.episode(first).end_ts == T0 + 90


def test_episodes_between_and_conclusion(db):
    fstore.record_event(_ev("a", start=T0))
    fstore.record_event(_ev("b", start=T0 + 5000))
    eps = fstore.episodes_between(T0 - 1, T0 + 100)
    assert [e.episode_id for e in eps] == [_ep("a")]
    assert fstore.conclude_episode(_ep("a"), {"summary": "posten kom"})
    ep = fstore.episode(_ep("a"))
    assert ep.conclusion == {"summary": "posten kom"} and ep.concluded_ts
    assert fstore.episode(999_999) is None
