"""P149 D15 + J26 pending move.

D15 (main session 2026-11-12): someone turning off camera tracking does NOT remove them from another household member's
own profile. If a user says his wife is called «Userb», that is his own fact: the night meeting that reads his profile sees
it. Consent masking and query_scrub apply only to the camera memory and to what leaves the house.

J26 pending move: the approved list of meeting-written profile fields is moved by the night sequence (the kaare user, the
system key — never a shell as another user, never the live weights.db from a shell). A field the person has rejected
(«det har jeg aldri sagt») is kept as a guess with status «rejected», never shown as a fact, and the weight store gets the
person's own «no». The list file is renamed when done, never deleted.
No live state: every path points at tmp_path; test keys. Placeholders only."""

import json
import re
from pathlib import Path

import pytest

import kaare_reflection
from kaare_core import weights
from kaare_core.crypto import generate_keypair
from kaare_core.users import frigate_consent, ktsb_file, profile_manager
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore

ROOT = Path(__file__).resolve().parent.parent
PUB, PRIV = generate_keypair()


@pytest.fixture
def home(tmp_path, monkeypatch):
    users = tmp_path / "users"
    for mod in (ktsb_file, profile_manager):
        monkeypatch.setattr(mod, "USERS_DIR", users)
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    ustore.init_db()
    monkeypatch.setattr("kaare_core.session_keys.resolve_key_sync", lambda uid, allow_system_key=False: PRIV)
    monkeypatch.setattr(profile_manager, "_get_session_key", lambda uid, allow_system_key=False: PRIV)
    yield users
    weights.flush()
    wstore.reset_for_tests()
    frigate_consent.invalidate()


def test_own_fact_about_an_opted_out_member_stays_in_the_meeting_context(home, monkeypatch):
    ustore.create_user(username="user_a", display_name="USER_A", role="adult", pin="482915")
    ustore.create_user(username="userb_x", display_name="Userb", role="adult", pin="482915")
    ustore.set_frigate_tracking("userb_x", False)  # opted out of the CAMERA
    frigate_consent.invalidate()
    p = profile_manager.load_profile("user_a")
    p["relationships_and_family"] = {**(p.get("relationships_and_family") or {}), "partner_name": "Userb"}
    profile_manager.save_profile("user_a", p)
    for name in ("get_recent_observations",):
        monkeypatch.setattr(kaare_reflection, name, lambda *a, **k: "")
    monkeypatch.setattr(kaare_reflection, "_get_recent_interactions", lambda *a, **k: "")
    monkeypatch.setattr(kaare_reflection, "_get_stm_daily_summaries", lambda *a, **k: "")
    profile_text, *_ = kaare_reflection._get_user_context("user_a")
    assert "Userb" in profile_text


def test_scrubbing_is_not_wired_into_profile_or_meeting_reading():
    for rel in ("kaare_core/users/profile_manager.py", "kaare_core/users/ktsb_file.py", "kaare_core/users/user_knowledge.py",
                "adapters/llm_adapter.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"query_scrub|hidden_names|_scrub_text", src), rel
    refl = (ROOT / "kaare_reflection.py").read_text(encoding="utf-8")
    assert [m.start() for m in re.finditer(r"query_scrub\.outgoing_query", refl)] and "hidden_names" not in refl


def _pending(users, uid, rows):
    d = users / uid
    d.mkdir(parents=True, exist_ok=True)
    (d / ktsb_file.PENDING_MOVE).write_text(json.dumps(rows), encoding="utf-8")
    return d


def test_pending_move_runs_once_keeps_the_list_and_marks_rejected(home):
    users = home
    p = profile_manager.load_profile("user_x")
    p["work_and_education"] = {**(p.get("work_and_education") or {}), "current_job_title": "185"}
    p["psychology_and_personality"] = {**(p.get("psychology_and_personality") or {}), "biggest_fear": "en tidslinje"}
    profile_manager.save_profile("user_x", p)
    d = _pending(users, "user_x", [
        {"section": "work_and_education", "field": "current_job_title", "written": "2026-11-08"},
        {"section": "psychology_and_personality", "field": "biggest_fear", "written": "2026-10-30",
         "rejected_by": "user_x"}])
    assert ktsb_file.run_pending_moves("user_x") == ["work_and_education.current_job_title",
                                                     "psychology_and_personality.biggest_fear"]
    assert not (d / ktsb_file.PENDING_MOVE).exists() and list(d.glob("ktsb_move_pending.done-*.json"))  # kept
    g = {x["field"]: x for x in ktsb_file.guesses("user_x")}
    assert g["current_job_title"]["status"] == "guess"
    assert g["biggest_fear"]["status"] == "rejected" and g["biggest_fear"]["value"] == "en tidslinje"
    w = weights.get_store().rows(("person_fact", "psychology_and_personality.biggest_fear", "user_x"))
    assert [(a["by_user"], a["verdict"]) for a in w["answers"]] == [("user_x", "no")]
    assert ktsb_file.run_pending_moves("user_x") == []  # once only
    prof = profile_manager.load_profile("user_x")
    assert prof["psychology_and_personality"]["biggest_fear"] is None


def test_meeting_never_shows_a_rejected_guess(home, monkeypatch):
    monkeypatch.setattr(kaare_reflection.user_knowledge, "read", lambda *a, **k: "")
    ktsb_file.store_entry("user_x", {**ktsb_file._entry("s", "f", "AVVIST-VERDI", "meeting", "", None, ""),
                                     "status": "rejected"})
    ktsb_file.add_guess("user_x", "s", "g", "GJETNING", source="meeting")
    text = kaare_reflection._load_user_knowledge("user_x")
    assert "GJETNING" in text and "AVVIST-VERDI" not in text


def test_meeting_runs_pending_moves_first():
    src = (ROOT / "kaare_reflection.py").read_text(encoding="utf-8")
    assert "ktsb_file.run_pending_moves(" in src
