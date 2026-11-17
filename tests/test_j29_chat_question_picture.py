"""Run-queue J29 (2026-11-13): «Var det deg?» in chat shows the event's picture — the user must see it to answer.

- the chat question carries the event's stored snapshot (the pipe's store, `frigate_store.SNAPSHOT_DIR`) as a chat image
  through the existing chat image path (`/api/image/<id>`, the user's own image folder) — no model call, no description;
- access exactly as J24's `may_see_snapshot` (the person the question is about; never an opted-out person's event);
- no picture stored → the question as today plus «(ingen bilde lagret)»;
- Kåre's note on the next turn holds the question text only, never the image link.
Tmp files only (reuses the J11 fixture)."""

import pytest

from adapters import llm_adapter
from kaare_core.domain import frigate_store as domain_store
from kaare_core.memory import chat_questions as cq
from tests import test_p149_j11_chat_questions as j11

NOW = j11.NOW
env = j11.env  # the J11 fixture (tmp users/frigate/weights DBs)
JPEG = b"\xff\xd8fake\xff\xd9"


@pytest.fixture
def saved(monkeypatch):
    out = []

    def save(data, user_id, folder, ext="png"):
        out.append((data, user_id, folder, ext))
        return f"img{len(out)}"

    monkeypatch.setattr(cq, "save_image", save, raising=False)

    async def no_model(*a, **k):
        raise AssertionError("no model call for the chat question's picture")

    monkeypatch.setattr(llm_adapter, "ask_llm", no_model)
    monkeypatch.setattr(llm_adapter, "ask_llm_role", no_model)
    return out


def _snapshots(*eids):
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for e in eids:
        (domain_store.SNAPSHOT_DIR / f"{e}.jpg").write_bytes(JPEG)


def test_the_question_shows_the_events_picture(env, saved):
    _snapshots("e0", "e1", "e2")
    line = cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    assert "var det deg" in line
    assert line.rstrip().endswith("/api/image/img1")
    assert saved == [(JPEG, "user_a", "frigate", "jpg")]  # owner rule 2026-11-16: camera → frigate folder
    assert "/api/image/" not in cq.pending_note("user_a", "nb")  # Kåre's note: the question only


def test_no_picture_stored_says_so(env, saved):
    line = cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    assert "var det deg" in line and "(ingen bilde lagret)" in line and "/api/image/" not in line
    assert saved == []


def test_no_access_no_picture(env, saved, monkeypatch):
    _snapshots("e0", "e1", "e2")
    monkeypatch.setattr(cq.questions, "may_see_snapshot", lambda user, role, eid: False)
    line = cq.offer("user_a", "gui", "hei", [], "nb", now=NOW)
    assert "/api/image/" not in line and saved == []
