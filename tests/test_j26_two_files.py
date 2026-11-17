"""J26 — two files per person: KTSB's own file (guesses) vs the user profile (only what the person said or confirmed).

- a guess goes to `ktsb_file.enc` (encrypted with the person's key), labelled with source, rid, time, status «guess»; the
  weight store gets the subject and a pointer, never the value; with no key at hand → a SealedBox vault entry instead;
  an existing file that cannot be read is never overwritten;
- the night meeting's extract writes a guess, never the profile (the T32 case: «185» as job title, «Tove» as name);
- vault entries: the person's own (source «user») → the profile; anything else (meeting, old entries without a source)
  → a guess in KTSB's file;
- `move_meeting_fields`: an approved list of meeting-written profile fields → guesses with the date written, the profile
  field cleared, a change_log line without the value; fields not on the list untouched; never run by the tests on the house;
- `user_knowledge` is encrypted: written only as .enc; an old plaintext .md is encrypted and kept as *.plain.bak (never
  deleted); no key → nothing read, nothing written; no code outside the module reads user_knowledge.md any more.
No live state: every path points at tmp_path; keys are test keys. Placeholders only."""

import asyncio
import re
from pathlib import Path

import pytest
import yaml

from kaare_core import weights
from kaare_core.agents.miss_kare import evaluator
from kaare_core.crypto import generate_keypair, seal
from kaare_core.users import ktsb_file, profile_manager, user_knowledge
from kaare_core.weights import store as wstore

ROOT = Path(__file__).resolve().parent.parent
PUB, PRIV = generate_keypair()
_OTHER_PUB, OTHER = generate_keypair()


@pytest.fixture
def home(tmp_path, monkeypatch):
    users = tmp_path / "users"
    for mod in (ktsb_file, user_knowledge, profile_manager):
        monkeypatch.setattr(mod, "USERS_DIR", users)
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    key = {"k": PRIV}
    monkeypatch.setattr("kaare_core.session_keys.resolve_key_sync", lambda uid, allow_system_key=False: key["k"])
    monkeypatch.setattr(profile_manager, "_get_session_key", lambda uid, allow_system_key=False: key["k"])
    monkeypatch.setattr(profile_manager, "get_public_key_b64",
                        lambda uid: __import__("base64").b64encode(PUB).decode())
    yield users, key
    weights.flush()
    wstore.reset_for_tests()


# ── KTSB's own file ──

def test_guess_is_encrypted_labelled_and_only_a_pointer_in_weights(home):
    users, _key = home
    assert ktsb_file.add_guess("user_x", "work_and_education", "current_job_title", "185", source="meeting",
                               rid="rid-refl-1") == "stored"
    raw = (users / "user_x" / "ktsb_file.enc").read_text()
    assert "185" not in raw and "current_job_title" not in raw  # encrypted
    g = ktsb_file.guesses("user_x")
    assert len(g) == 1 and g[0]["value"] == "185" and g[0]["source"] == "meeting" and g[0]["rid"] == "rid-refl-1"
    assert g[0]["status"] == "guess" and g[0]["ts"]
    rows = weights.get_store().rows(("person_fact", "work_and_education.current_job_title", "user_x"))
    assert rows["evidence"][0]["source_class"] == "self" and rows["evidence"][0]["pointer"] == f"ktsb_file:{g[0]['id']}"
    assert "185" not in repr(rows)


def test_no_key_means_a_vault_entry_and_an_unreadable_file_is_never_overwritten(home):
    users, key = home
    key["k"] = None
    assert ktsb_file.add_guess("user_x", "identity", "preferred_name", "Tove", source="meeting") == "vaulted"
    assert list((users / "user_x").glob("vault_*.bin")) and not (users / "user_x" / "ktsb_file.enc").exists()
    key["k"] = PRIV
    ktsb_file.add_guess("user_x", "identity", "preferred_name", "A", source="meeting")
    before = (users / "user_x" / "ktsb_file.enc").read_text()
    key["k"] = OTHER  # a key that cannot open it
    assert ktsb_file.store_entry("user_x", ktsb_file._entry("s", "f", "v", "meeting", "", None, "")) is False
    assert (users / "user_x" / "ktsb_file.enc").read_text() == before


# ── the meeting never writes the profile ──

def test_meeting_extract_writes_a_guess_not_the_profile(home, monkeypatch):
    async def fake_extract(text, hint, lang):
        return {"section": "work_and_education", "field": "current_job_title", "value": "185"}

    monkeypatch.setattr(evaluator, "_extract_fact_json", fake_extract)
    monkeypatch.setattr(evaluator, "get_lang", lambda uid: "nb")
    monkeypatch.setattr(evaluator, "vault_or_update_nested_field",
                        lambda *a, **k: pytest.fail("the meeting must not write the profile"))
    asyncio.run(evaluator.extract_facts_from_meeting("user_x", "Kåre: 185 er jobben hans.", rid="rid-refl-2"))
    g = ktsb_file.guesses("user_x")
    assert [(x["field"], x["value"], x["source"], x["rid"]) for x in g] == [("current_job_title", "185", "meeting",
                                                                            "rid-refl-2")]


def _vault(users, payload):
    d = users / "user_x"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"vault_{len(list(d.glob('vault_*.bin'))):04d}.bin").write_text(seal(__import__("json").dumps(payload), PUB))


def test_vault_only_the_persons_own_words_reach_the_profile(home):
    users, _key = home
    _vault(users, {"type": "profile_field_nested", "section": "identity", "field": "preferred_name", "value": "Tove"})
    _vault(users, {"type": "profile_field_nested", "section": "food_drink_health", "field": "favorite_meal",
                   "value": "taco", "source": "user"})
    assert profile_manager.process_vault_files("user_x", PRIV) == 2
    prof = profile_manager.load_profile("user_x")
    assert (prof.get("identity") or {}).get("preferred_name") in (None, "")
    assert prof["food_drink_health"]["favorite_meal"] == "taco"
    assert [(g["field"], g["value"]) for g in ktsb_file.guesses("user_x")] == [("preferred_name", "Tove")]


def test_live_reply_vault_is_marked_as_the_users_own(home, monkeypatch):
    seen = []
    monkeypatch.setattr(profile_manager, "_get_session_key", lambda uid, allow_system_key=False: None)
    monkeypatch.setattr(profile_manager, "write_vault_entry", lambda uid, payload: seen.append(payload) or True)
    profile_manager.vault_or_update_nested_field("user_x", "identity", "preferred_name", "Userb")
    assert seen[0]["source"] == "user"


# ── moving the wrong values (built and tested, run only on the user's approved list) ──

def test_move_meeting_fields(home):
    _users, _key = home
    p = profile_manager.load_profile("user_x")
    p["work_and_education"] = {**(p.get("work_and_education") or {}), "current_job_title": "185"}
    p["identity"] = {**(p.get("identity") or {}), "preferred_name": "Tove", "nickname": "keep me"}
    profile_manager.save_profile("user_x", p)
    moved = ktsb_file.move_meeting_fields("user_x", [("work_and_education", "current_job_title", "2026-11-08"),
                                                     ("identity", "preferred_name", "2026-11-12"),
                                                     ("identity", "not_there", "2026-11-01")])
    assert moved == ["work_and_education.current_job_title", "identity.preferred_name"]
    prof = profile_manager.load_profile("user_x")
    assert prof["work_and_education"]["current_job_title"] is None and prof["identity"]["preferred_name"] is None
    assert prof["identity"]["nickname"] == "keep me"
    assert all("185" not in str(c) and "Tove" not in str(c) for c in prof["change_log"])
    g = {(x["field"], x["value"], x["ts"]) for x in ktsb_file.guesses("user_x")}
    assert g == {("current_job_title", "185", "2026-11-08"), ("preferred_name", "Tove", "2026-11-12")}


# ── user_knowledge encrypted ──

def test_user_knowledge_encrypted_old_plaintext_kept_as_backup(home):
    users, key = home
    d = users / "user_x"
    d.mkdir(parents=True)
    (d / "user_knowledge.md").write_text("- frykt og OCD\n", encoding="utf-8")
    assert user_knowledge.read("user_x", allow_system_key=True) == "- frykt og OCD"
    assert not (d / "user_knowledge.md").exists()
    assert (d / "user_knowledge.md.plain.bak").read_text() == "- frykt og OCD\n"  # kept, never deleted
    assert "OCD" not in (d / "user_knowledge.enc").read_text()
    assert user_knowledge.write("user_x", "- ny\n", allow_system_key=True)
    assert user_knowledge.read("user_x") == "- ny"
    key["k"] = None
    assert user_knowledge.read("user_x") == "" and user_knowledge.write("user_x", "x") is False


def test_no_key_never_reads_the_old_plaintext(home):
    users, key = home
    key["k"] = None
    d = users / "user_y"
    d.mkdir(parents=True)
    (d / "user_knowledge.md").write_text("- hemmelig\n", encoding="utf-8")
    assert user_knowledge.read("user_y", allow_system_key=True) == ""
    assert (d / "user_knowledge.md").exists()  # untouched until a key is at hand


def test_nobody_reads_user_knowledge_md_outside_the_module():
    files = [*ROOT.glob("kaare_*.py"), *(ROOT / "kaare_core").rglob("*.py"), *(ROOT / "adapters").rglob("*.py")]
    hits = [str(f.relative_to(ROOT)) for f in files if f.name != "user_knowledge.py"
            and re.search(r"user_knowledge\.md[\"']", f.read_text(encoding="utf-8", errors="replace"))]
    assert hits == []


def test_profile_template_has_no_ktsb_guess_section():
    tpl = yaml.safe_load((ROOT / "configs_default" / "profile_template.yaml").read_text()) \
        if (ROOT / "configs_default" / "profile_template.yaml").exists() else {}
    assert "ktsb_guesses" not in (tpl or {})
