"""Receipts for the note tool (J22 part 4 follow-up, «kvitteringer for de andre verktøyene»; memory feedback_rid_and_receipts).

Every save through the note tool returns one receipt built by code from what was written — the stable marker
`⟦saved kind=… ref=…⟧` with the stored id — plus the repeat instruction, so Kåre repeats it instead of writing his own
version. Covered: shopping, the user's reminder list, Kåre's own notes (new and refreshed), lessons, the dev agenda and
observations. A writer called without `receipt_who` (night jobs, internal callers) answers exactly as before.
No live state: lists, learning.db and knowledge.db point at tmp_path. Placeholders only."""

import asyncio

import pytest

import kaare_core.memory.knowledge_store as ks
import kaare_core.memory.learning_store as ls
from kaare_core import receipts
from kaare_core.tools import agenda, executor, lister


@pytest.fixture
def lists(tmp_path, monkeypatch):
    monkeypatch.setattr(lister, "_KARE_PATH", tmp_path / "kare_huske.json")
    monkeypatch.setattr(lister, "_SHOPPING_PATH", tmp_path / "handle.json")
    monkeypatch.setattr(lister, "_reminder_path", lambda uid: tmp_path / f"huske_{uid}.json")
    monkeypatch.setattr(lister, "_get_settings", lambda: {"kare_reminders": {}})
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    return tmp_path


def _note(args):
    return asyncio.run(executor.execute_tool("note", {"_user_id": "user_x", **args}))


CASES = [
    ({"action": "write", "list_name": "shopping", "text": "melk"}, "note", "shopping:"),
    ({"action": "write", "list_name": "remember", "text": "ring tannlegen"}, "note", "remember:"),
    ({"action": "write", "list_name": "kare", "text": "sjekk sandkassen etter slippet"}, "note", "kare:"),
    ({"action": "write", "list_name": "lessons", "text": "Bruk alltid romnavn, ikke entity_id."}, "lesson", "lesson:"),
    ({"action": "write", "list_name": "agenda", "text": "Timeren gikk to ganger i natt"}, "agenda", "topic:"),
    ({"action": "write", "list_name": "observations", "text": "Døra stod åpen kl. 22"}, "observation", "topic:"),
]


@pytest.mark.parametrize(("args", "kind", "ref"), CASES)
def test_every_note_save_returns_one_receipt(lists, args, kind, ref):
    out = _note(args)
    found = receipts.parse(out)
    assert len(found) == 1 and found[0]["kind"] == kind and found[0]["ref"].startswith(ref), out
    assert receipts.instruction("nb") in out and "user_x" in out


def test_refreshed_kare_note_also_gets_a_receipt(lists):
    _note({"action": "write", "list_name": "kare", "text": "sjekk sandkassen etter slippet"})
    again = _note({"action": "write", "list_name": "kare", "text": "Sjekk sandkassen etter slippet."})
    assert len(receipts.parse(again)) == 1


def test_internal_callers_answer_as_before(lists):
    assert receipts.parse(lister.shopping_add("brød")) == []
    assert receipts.parse(agenda.agenda_add("Noe skjedde i natt", rid="rid-refl-1")) == []


def test_refused_save_has_no_receipt(lists):
    assert receipts.parse(_note({"action": "write", "list_name": "shopping", "text": ""})) == []
