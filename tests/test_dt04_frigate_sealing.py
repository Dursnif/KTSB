"""DT04 R0.6a — the camera memory is encrypted at rest (N19 point 3).

Proof the spec asks for (rule 28): a planted name is not readable in the raw bytes of frigate.db (or its WAL) nor in a
stored picture, while every lookup still works — by name (`=`/`IN`), visits, day rollups, episodes, the digest gate,
the «Was it you?» picture and the episode frames. Plaintext written before encryption is sealed once by schema v11,
and stays readable until then. A wrong key reads as empty, never raises.

Runs on tmp_path with the conftest test key — never the house's frigate.db or system_master.key.
"""

import asyncio
import sqlite3
import threading
import time

import pytest

from kaare_core import crypto
from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import digest_gate, pseudonyms, sealing
from kaare_core.frigate_user import pipe as fpipe
from kaare_core.frigate_user import store as fstore
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = 1_800_000_000.0
NAME = "Plantetnavn"  # planted household face name
PLATE = "ZZ98765"
JPEG = b"\xff\xd8\xff\xe0" + b"JFIF-planted-picture" * 50


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "state" / "frigate" / "frigate.db")
    monkeypatch.setattr(domain_store, "SNAPSHOT_DIR", tmp_path / "state" / "frigate_snapshots")
    fstore.reset_for_tests()
    ustore.init_db()
    ustore.create_user(username="user_x", display_name="User X", role="adult", pin="482915")
    ustore.update_user("user_x", frigate_faces=[NAME])
    frigate_consent.invalidate()
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _ev(event_id="e1", start=T0, **kw):
    fields = {"event_id": event_id, "camera": "inngang", "label": "person", "start_ts": start, "end_ts": start + 10,
              "person_ref": NAME, "person_kind": "household", "claim_status": "confirmed", "claim_score": 0.9,
              "description": f"{NAME} står ved døra", "analysis": f"Det er {NAME}",
              "check": {"claimed": NAME, "gate": {"verdict": "pass"}}}
    fields.update(kw)
    return fstore.FrigateUserEvent(**fields)


def _raw_db_bytes(tmp_path) -> bytes:
    d = tmp_path / "state" / "frigate"
    return b"".join(p.read_bytes() for p in d.iterdir() if p.is_file())


# ── the raw file holds no name ─────────────────────────────────────────────────

def test_a_planted_name_is_not_in_the_raw_db_bytes(db):
    assert fstore.record_event(_ev())
    fstore.close_visits(now=T0 + 7200)
    raw = _raw_db_bytes(db)
    assert NAME.encode() not in raw
    assert "står ved døra".encode() not in raw


def test_a_plate_is_not_in_the_raw_db_bytes(db):
    assert fstore.record_event(_ev(plate_claim=PLATE, plate_known=True))
    assert PLATE.encode() not in _raw_db_bytes(db)


# ── everything still reads ─────────────────────────────────────────────────────

def test_the_event_reads_back_in_plaintext(db):
    fstore.record_event(_ev())
    e = next(x for x in fstore.events_between(0, T0 * 2) if x.event_id == "e1")
    assert e.person_ref == NAME
    assert NAME in e.description
    assert e.check["claimed"] == NAME


def test_lookups_by_name_still_work(db):
    fstore.record_event(_ev("e1"))
    fstore.record_event(_ev("e2", start=T0 + 30))
    assert [e.event_id for e in fstore.named_events_after([NAME], 0)] == ["e1", "e2"]
    assert {e.event_id for e in fstore.events_between(0, T0 * 2, person_ref=NAME)} == {"e1", "e2"}
    fstore.close_visits(now=T0 + 7200)
    v = fstore.last_visit(NAME)
    assert v is not None and v.person_ref == NAME and v.event_count == 2
    assert NAME in fstore.known_refs()


def test_plate_lookups_still_work(db):
    fstore.record_event(_ev(plate_claim=PLATE, plate_known=True))
    assert fstore.known_plates() == {PLATE}
    assert [e.event_id for e in fstore.plate_events([PLATE])] == ["e1"]


def test_the_digest_gate_reads_the_sealed_check(db):
    fstore.record_event(_ev())
    assert digest_gate.verdicts(["e1"], db_path=fstore.DB_PATH) == {"e1": "pass"}


def test_the_same_name_seals_the_same_and_free_text_does_not():
    assert sealing.seal_ref(NAME) == sealing.seal_ref(NAME)
    assert sealing.seal(NAME) != sealing.seal(NAME)
    assert sealing.unseal(sealing.seal_ref(NAME)) == NAME
    assert sealing.seal(None) is None and sealing.seal("") == ""


# ── pictures ───────────────────────────────────────────────────────────────────

def test_event_snapshot_is_sealed_on_disk_and_served_in_plaintext(db):
    assert fpipe._store_event_snapshot("e1", JPEG)
    path = domain_store.SNAPSHOT_DIR / "e1.jpg"
    assert b"planted-picture" not in path.read_bytes()
    assert sealing.read_picture(path) == JPEG
    assert asyncio.run(fpipe._load_snapshot("e1"))  # the pipe's own cache read decrypts (then scales)


def test_episode_frames_are_sealed_on_disk(db, monkeypatch):
    monkeypatch.setattr(fstore.settings, "episode_frames_caps", lambda: (500.0, 600.0))
    assert fstore.save_episode_frames(7, [JPEG]) == 1
    f = fstore.episode_frames_dir() / "7" / "0.jpg"
    assert b"planted-picture" not in f.read_bytes()
    assert fstore.read_episode_frame(7, 0) == JPEG


def test_a_plaintext_picture_from_before_still_reads(db):
    domain_store.SNAPSHOT_DIR.mkdir(parents=True)
    (domain_store.SNAPSHOT_DIR / "old.jpg").write_bytes(JPEG)
    assert sealing.read_picture(domain_store.SNAPSHOT_DIR / "old.jpg") == JPEG


# ── one-time sealing of what was written before (schema v11) ───────────────────

def test_v11_seals_plaintext_rows_and_pictures_written_before(db, monkeypatch):
    # write as the old code did: sealing off
    for name in ("seal", "seal_ref", "seal_bytes"):
        monkeypatch.setattr(sealing, name, lambda x: x)
    fstore.record_event(_ev(plate_claim=PLATE, plate_known=True))
    fstore.close_visits(now=T0 + 7200)
    fpipe._store_event_snapshot("e1", JPEG)
    conn = sqlite3.connect(fstore.DB_PATH)
    conn.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    assert NAME.encode() in _raw_db_bytes(db)
    monkeypatch.undo()  # sealing back on; repoint the paths again (undo reset them too)
    monkeypatch.setattr(ustore, "DB_PATH", db / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", db / "state" / "frigate" / "frigate.db")
    monkeypatch.setattr(domain_store, "SNAPSHOT_DIR", db / "state" / "frigate_snapshots")
    monkeypatch.setattr(sealing, "_master", lambda: bytes(range(32)))
    sealing.reset_keys()
    fstore.reset_for_tests()
    assert fstore.schema_version() == 11
    assert NAME.encode() not in _raw_db_bytes(db)  # incl. free pages and the WAL — the migration vacuums
    assert PLATE.encode() not in _raw_db_bytes(db)
    assert b"planted-picture" not in (domain_store.SNAPSHOT_DIR / "e1.jpg").read_bytes()
    assert fstore.last_visit(NAME).person_ref == NAME
    assert fstore.known_plates() == {PLATE}


def test_a_wrong_key_reads_as_empty_and_never_raises(db, monkeypatch):
    fstore.record_event(_ev())
    monkeypatch.setattr(sealing, "_master", lambda: b"\x01" * 32)
    sealing.reset_keys()
    e = next(x for x in fstore.events_between(0, T0 * 2) if x.event_id == "e1")
    assert e.person_ref is None and e.description is None


# ── the system key is created once, even by two processes at the same moment ───

def test_a_second_creator_keeps_the_key_that_is_already_there(tmp_path, monkeypatch):
    """Two processes starting together on a fresh install: the second must use the first one's key, never replace it
    (a replaced key = camera memory sealed with a key that is gone)."""
    path = tmp_path / "system_master.key"
    monkeypatch.setattr(crypto, "_SYSTEM_MASTER_KEY_PATH", path)
    monkeypatch.setattr(crypto, "_system_master_key_cache", None)
    real_replace, real_link = crypto.os.replace, crypto.os.link

    def other_process_wins():  # between our exists() check and our rename/link
        if not path.exists():
            path.write_bytes(b"\x05" * 32)

    def racing_replace(src, dst):
        other_process_wins()
        return real_replace(src, dst)

    def racing_link(src, dst):
        other_process_wins()
        return real_link(src, dst)

    monkeypatch.setattr(crypto.os, "replace", racing_replace)
    monkeypatch.setattr(crypto.os, "link", racing_link)
    assert crypto.ensure_system_master_key() == b"\x05" * 32
    assert path.read_bytes() == b"\x05" * 32


def test_without_hard_links_the_key_is_still_created(tmp_path, monkeypatch):
    path = tmp_path / "system_master.key"
    monkeypatch.setattr(crypto, "_SYSTEM_MASTER_KEY_PATH", path)
    monkeypatch.setattr(crypto, "_system_master_key_cache", None)

    def no_links(src, dst):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(crypto.os, "link", no_links)
    key = crypto.ensure_system_master_key()
    assert len(key) == 32 and path.read_bytes() == key
    assert [p.name for p in tmp_path.iterdir()] == ["system_master.key"]  # no tmp left behind


# ── two processes opening an old DB at the same moment migrate it once (observed in the house 2026-11-14) ──

def test_two_processes_migrate_once(db, monkeypatch):
    """kaare-jing and kaare.service started together; both read version 10 and both ran v11. Each process has its own
    in-process lock, so the guard must work across processes (a file lock) and re-read the version under it."""
    fstore.record_event(_ev())
    conn = sqlite3.connect(fstore.DB_PATH)
    conn.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    runs = []
    real = fstore._MIGRATIONS[11]

    def slow_v11(c):
        runs.append(1)
        time.sleep(0.5)  # the other «process» reads the version meanwhile
        real(c)

    monkeypatch.setitem(fstore._MIGRATIONS, 11, slow_v11)

    def one_process():
        c = fstore._connect()
        try:
            fstore._migrate(c)
        finally:
            c.close()

    threads = [threading.Thread(target=one_process) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(runs) == 1
    assert fstore.schema_version() == 11


# ── R0.6c: the pseudonym key is sealed too (with it, a guessed name confirms who person-… is) ──

def test_the_pseudonym_key_is_not_readable_in_the_raw_db(db):
    pid = pseudonyms.pid(NAME)
    key_hex = sealing.unseal(fstore.meta_get(pseudonyms.KEY_NAME))
    assert key_hex and key_hex.encode() not in _raw_db_bytes(db)
    assert pseudonyms.pid(NAME) == pid  # stable


def test_an_existing_plaintext_pseudonym_key_keeps_every_id_and_is_sealed(db):
    plain = "ab" * 32
    fstore.meta_set_once(pseudonyms.KEY_NAME, plain)  # as stored before R0.6c
    before = pseudonyms.pid(NAME, key=bytes.fromhex(plain))
    assert pseudonyms.pid(NAME) == before
    assert sealing.is_sealed(fstore.meta_get(pseudonyms.KEY_NAME))
    assert plain.encode() not in _raw_db_bytes(db)
