"""Run-queue J29b (main session 2026-11-13): the camera tool's `ask` and `verify` in chat show the event's picture too —
whoever answers must see it. Same access as J24 (`may_see_snapshot`: the person the question is about; admin sees every
picture, never a name); never a picture for a question about an event with an opted-out person (the J2/J22 rule, an
extra guard here). No picture stored → «(ingen bilde lagret)». No model call. The /api/image line in a camera tool result
reaches the chat through router_generate's camera-tool image collection. Tmp files only (the J11 fixture)."""

import asyncio

import pytest

from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions as cq
from kaare_core.tools import executor_camera
from tests import test_p149_j11_chat_questions as j11

NOW = j11.NOW
env = j11.env
JPEG = b"\xff\xd8fake\xff\xd9"


@pytest.fixture
def saved(monkeypatch):
    out = []

    def save(data, user_id, folder, ext="png"):
        out.append((user_id, folder, ext))
        return f"img{len(out)}"

    monkeypatch.setattr(cq, "save_image", save)
    return out


def _snapshots(*eids):
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for e in eids:
        (domain_store.SNAPSHOT_DIR / f"{e}.jpg").write_bytes(JPEG)


def _tool(args, uid="user_a"):
    return asyncio.run(executor_camera._kamera({**args, "_user_id": uid}, "nb"))


def test_ask_shows_the_picture(env, saved):
    _snapshots("e0")
    out = _tool({"action": "ask", "event_id": "e0"})
    assert "/api/image/img1" in out and saved == [("user_a", "frigate", "jpg")]  # owner rule 2026-11-16: camera pictures → frigate folder


def test_verify_shows_the_picture_of_what_was_answered(env, saved):
    _snapshots("e0")
    out = _tool({"action": "verify", "event_id": "e0", "answer": "yes"})
    assert "/api/image/img1" in out


def test_no_picture_stored_says_so(env, saved):
    out = _tool({"action": "verify", "event_id": "e0", "answer": "yes"})
    assert "(ingen bilde lagret)" in out and saved == []


def test_a_refused_ask_shows_no_picture(env, saved):
    _snapshots("nope")
    out = _tool({"action": "ask", "event_id": "nope"})
    assert "/api/image/" not in out and saved == []


def test_never_a_picture_for_an_opted_out_persons_event_even_for_admin(env, saved):
    _snapshots("u1")
    fstore.record_event(fstore.FrigateUserEvent(event_id="u1", camera="cam", label="person", start_ts=NOW - 60,
                                                end_ts=NOW - 50, person_kind="untracked"))
    assert cq.picture_line("boss", "u1", "nb", role="admin") == "" and saved == []


def test_a_which_one_list_shows_one_picture_per_event(env, saved):
    _snapshots("e0", "e1")
    text = "Episoden har flere hendelser …:\n- event_id e0: cam 09:00\n- event_id e1: cam 08:00"
    out = executor_camera._question_pictures("user_a", text, "nb")
    assert out.count("/api/image/") == 2


def test_a_refusal_names_no_event_so_no_picture(env, saved):
    assert executor_camera._question_pictures("user_a", "Det er ikke et åpent spørsmål.", "nb") == ""
