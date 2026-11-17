"""J22 parts 2 + 3 (the user's chat 2026-11-07 ~11:46): Kåre said «Lagt inn: det var Userb» although only «nei» was stored —
the tool had answered just «Svaret er lagret.». And «så lenge brukere ikke har reservert seg for tracking, så skal
admin kunne verifisere alle brukere» («det var min kone Userb»).
  - the tool's reply says exactly what was stored: the answer, the event, which name the event now has (or that none was);
  - admin may answer «it was <member>» (`about`): stored on the event (that name, «bekreftet av <admin>») and in weights.db
    by=admin in the member's subject, weaker than the member's own (verbatim=False → VAGUE_WEIGHT); never for a member with
    tracking off (refused, nothing stored); refused when the member has answered themselves; the member's own later
    answer overrides; a non-admin still answers only for themselves.
Same tmp fixture as the J2 tests (users.db, frigate.db, weights.db at tmp_path). Placeholders."""
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core import weights
from kaare_core.frigate_user import questions
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.tools import executor_camera
from kaare_core.weights import store as wstore
from tests import test_p149_j2_questions as j2

env = j2.env
NOW, H = j2.NOW, j2.H


# ── part 2: the reply says exactly what was stored ──────────────────────────────────────────────────────────────

def test_a_no_answer_says_that_no_name_was_stored(env):
    j2._store(j2._ev("e1", NOW - H))
    out = questions.verify("user_a", event_id="e1", episode_id=None, verdict="no", lang="nb")
    assert "nei, ikke meg" in out and "ingen navn er lagret" in out
    ev = fstore.event_by_id("e1")
    assert ev.person_ref is None and ev.check["human"]["verdict"] == "no"


def test_a_yes_answer_says_which_name_the_event_now_has(env):
    j2._store(j2._ev("e1", NOW - H))
    out = questions.verify("user_a", event_id="e1", episode_id=None, verdict="yes", lang="nb")
    assert "står nå på user_a" in out


def test_the_chat_answer_reports_exactly_too(env, monkeypatch):
    """2026-11-07: answer_question replied only «Svaret er lagret.»"""
    j2._store(j2._ev("e1", NOW - H))
    card = chat_questions.add_card("user_a", "camera", "e1", source="gui")  # N39: answers bind by question card
    res = chat_questions.answer("user_a", card["qid"], "no")
    out = questions.stored_text("user_a", "e1", "no", res, "nb")
    assert "nei, ikke meg" in out and "ingen navn er lagret" in out


# ── part 3: admin may verify any member with tracking on ───────────────────────────────────────────────────────

def test_admin_it_was_member_names_the_event_and_says_so(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    res = questions.answer_about("boss", "user_a", "e1", channel="chat")
    assert res["ok"] is True
    ev = fstore.event_by_id("e1")
    assert ev.person_ref == "user_a" and ev.check["human"] == {**ev.check["human"], "by": "boss", "about": "user_a",
                                                               "verdict": "yes"}
    out = questions.stored_text("boss", "e1", "yes", res, "nb", about="user_a")
    assert "det var user_a" in out and "bekreftet av boss" in out


def test_admins_answer_is_weaker_than_the_members_own(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    questions.answer_about("boss", "user_a", "e1", channel="chat")
    weights.flush()
    rows = wstore.get_store().rows(("event", "e1", "user_a"))["answers"]
    assert [(r["by_user"], r["verbatim"]) for r in rows] == [("boss", 0)]


def test_never_for_a_member_with_tracking_off(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    res = questions.answer_about("boss", "user_c", "e1", channel="chat")
    assert res == {"ok": False, "error": "tracking_off"}
    assert fstore.event_by_id("e1").person_ref is None and "human" not in fstore.event_by_id("e1").check
    weights.flush()
    assert wstore.get_store().rows(("event", "e1", "user_c"))["answers"] == []


def test_only_admin_may_answer_about_someone_else(env):
    j2._store(j2._ev("e1", NOW - H))
    assert questions.answer_about("user_b", "user_a", "e1", channel="chat") == {"ok": False, "error": "not_admin"}


def test_the_members_own_answer_wins(env):
    j2._store(j2._ev("e1", NOW - H))
    assert questions.answer("user_a", "e1", "no", channel="gui")["ok"]
    assert questions.answer_about("boss", "user_a", "e1", channel="chat") == {"ok": False, "error": "own_answer"}
    j2._store(j2._ev("e2", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    assert questions.answer_about("boss", "user_a", "e2", channel="chat")["ok"]
    assert questions.answer("user_a", "e2", "no", channel="chat")["ok"]  # the member corrects admin's answer
    assert fstore.event_by_id("e2").person_ref is None and fstore.event_by_id("e2").check["human"]["by"] == "user_a"


def test_the_camera_tool_passes_about_for_admin(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    out = asyncio.run(executor_camera._kamera({"action": "verify", "event_id": "e1", "answer": "yes",
                                               "about": "user_a", "_user_id": "boss"}, "nb"))
    assert "det var user_a" in out and "bekreftet av boss" in out


# ── the card: admin's list of open person events and the members it may name ─────────────────────────────────

def test_admin_gets_open_person_events_and_only_trackable_members(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}),
              j2._ev("e2", NOW - 2 * H, claim="confirmed"))
    assert [q["event_id"] for q in questions.admin_candidates("boss", now=NOW)] == ["e1"]
    assert questions.admin_candidates("user_a", now=NOW) == []
    names = {m["username"] for m in questions.nameable_members()}
    assert "user_c" not in names and {"user_a", "user_b"} <= names


def test_the_card_endpoints_are_admin_only(env):


    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    app = FastAPI()
    app.include_router(rfu.router)
    c = TestClient(app)
    hdr = lambda u, r: {"Authorization": f"Bearer {auth.create_token(u, r)}"}  # noqa: E731
    assert c.get("/api/frigate_user/questions/who").status_code == 401
    assert c.get("/api/frigate_user/questions/who", headers=hdr("user_a", "adult")).status_code == 403
    d = c.get("/api/frigate_user/questions/who", headers=hdr("boss", "admin")).json()
    assert [q["event_id"] for q in d["questions"]] == ["e1"] and "user_c" not in {m["username"] for m in d["members"]}
    body = {"event_id": "e1", "member": "user_c"}
    assert c.post("/api/frigate_user/questions/who/answer", json=body, headers=hdr("boss", "admin")).status_code == 400
    body["member"] = "user_a"
    assert c.post("/api/frigate_user/questions/who/answer", json=body, headers=hdr("user_a", "adult")).status_code == 403
    r = c.post("/api/frigate_user/questions/who/answer", json=body, headers=hdr("boss", "admin"))
    assert r.status_code == 200 and fstore.event_by_id("e1").person_ref == "user_a"
