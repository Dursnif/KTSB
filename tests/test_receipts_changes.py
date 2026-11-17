"""Receipts for changes and removals (main session 2026-11-12: «hver lagring eller endring skal ha kvittering»): edit,
delete, done, clear, pin on the note tool; lesson edit/retire; profile observation edit/delete and curiosity; world
edit/delete/delete_var; self image add/edit/retire; timer cancel. A removal's receipt says what type was removed, where, by
whom and when — never the content (only the id / a content-free ref). Outside a tool call nothing changes.
No live state: every store is in tmp_path or faked. Placeholders only."""

import asyncio

import pytest

import kaare_core.memory.knowledge_store as ks
import kaare_core.memory.learning_store as ls
from kaare_core import receipts
from kaare_core.tools import executor, executor_personality, lister, timer_service

SECRET = "hemmelig innhold 4711"


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(lister, "_KARE_PATH", tmp_path / "kare_huske.json")
    monkeypatch.setattr(lister, "_SHOPPING_PATH", tmp_path / "handle.json")
    monkeypatch.setattr(lister, "_reminder_path", lambda uid: tmp_path / f"huske_{uid}.json")
    monkeypatch.setattr(lister, "_get_settings", lambda: {"kare_reminders": {}})
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    monkeypatch.setattr(executor_personality, "_is_allowed_self_contributor", lambda uid: True)


def _run(name, args):
    return asyncio.run(executor.execute_tool(name, {"_user_id": "user_x", **args}))


def _id_of(text):
    return receipts.parse(text)[0]["ref"].split(":", 1)[1]


def _receipt_line(out):
    return next(ln for ln in out.splitlines() if ln.startswith("⟦saved"))


def test_note_changes_and_removals(stores):
    kid = _id_of(_run("note", {"action": "write", "list_name": "kare", "text": SECRET}))
    for action, kind in (("pin", "note_change"), ("unpin", "note_change"), ("edit", "note_change")):
        out = _run("note", {"action": action, "list_name": "kare", "note_id": kid, "text": "ny tekst"})
        assert [r["kind"] for r in receipts.parse(out)] == [kind], (action, out)
    out = _run("note", {"action": "delete", "list_name": "kare", "note_id": kid})
    assert receipts.parse(out) == [{"kind": "note_removed", "ref": f"kare:{kid}"}]
    assert SECRET not in _receipt_line(out)
    sid = _id_of(_run("note", {"action": "write", "list_name": "shopping", "text": "melk"}))
    out = _run("note", {"action": "mark_bought", "list_name": "shopping", "note_id": sid})
    assert receipts.parse(out)[0]["kind"] == "note_change"
    rid = _id_of(_run("note", {"action": "write", "list_name": "remember", "text": SECRET}))
    out = _run("note", {"action": "delete", "list_name": "remember", "note_id": rid})
    assert receipts.parse(out)[0]["kind"] == "note_removed" and SECRET not in _receipt_line(out)
    assert receipts.parse(_run("note", {"action": "delete", "list_name": "kare", "note_id": "nope"})) == []


def test_lesson_world_and_self_image(stores):
    lid = _id_of(_run("note", {"action": "write", "list_name": "lessons", "text": "Bruk romnavn alltid."}))
    out = _run("note", {"action": "delete", "list_name": "lessons", "note_id": lid})
    assert receipts.parse(out) == [{"kind": "lesson_removed", "ref": f"lesson:{lid}"}]
    _run("world", {"action": "add", "category": "Notater", "text": SECRET})
    out = _run("world", {"action": "delete", "fragment": "4711"})
    assert receipts.parse(out)[0]["kind"] == "world_removed" and SECRET not in _receipt_line(out)
    out = _run("self_image", {"action": "update", "observation": "Jeg liker orden"})
    assert receipts.parse(out)[0]["kind"] == "self_image"


def test_profile_observation_delete_has_no_content(stores, monkeypatch):
    monkeypatch.setattr(executor_personality, "delete_observation_fragment", lambda uid, frag: (True, 1))
    out = _run("user_profile", {"action": "delete", "fragment": SECRET})
    assert receipts.parse(out)[0]["kind"] == "observation_removed" and SECRET not in _receipt_line(out)


def test_timer_cancel(stores, monkeypatch):
    monkeypatch.setattr(timer_service, "_persist_timers", lambda: None)
    monkeypatch.setattr(timer_service, "_timers", {})

    async def go():
        tid = _id_of(await executor.execute_tool("timer", {"_user_id": "user_x", "action": "set", "prompt": "ovn",
                                                           "in_seconds": 600}))
        return tid, await executor.execute_tool("timer", {"_user_id": "user_x", "action": "cancel", "timer_id": tid})

    tid, out = asyncio.run(go())
    assert receipts.parse(out) == [{"kind": "timer_removed", "ref": f"timer:{tid}"}]


def test_outside_a_tool_call_nothing_changes(stores):
    lister.kare_note_add("x y z noe", lang="nb")
    item = lister._read(lister._KARE_PATH)[0]
    assert receipts.parse(lister.kare_note_done(item["id"])) == []
