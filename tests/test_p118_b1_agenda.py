"""
P118 B1 — learning.db, the dev-meeting agenda inbox, injection filter, promise enforcer.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b1_agenda.py -v
"""

import asyncio
import hashlib
import json
import sys

import pytest

sys.path.insert(0, "/kaare")

import adapters.llm_adapter as llm_adapter
import kaare_core.memory.learning_store as ls
import kaare_core.model_enforcement as me
import kaare_core.reflection_loop as reflection_loop
import kaare_core.routers.router_meetings as router_meetings
import kaare_core.tools.agenda as agenda
import kaare_core.tools.executor as executor
import kaare_core.tools.lister as lister
import kaare_core.tools.shared_tools as shared_tools
from kaare_core.model_enforcement import ToolPromiseDetector, tool_usage_tags


@pytest.fixture(autouse=True)
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", tmp_path / "tool_calls.log")

    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")  # never the live log
    return tmp_path


# ── Store: append-only grouping ─────────────────────────────────────────────


def test_near_duplicate_joins_topic_and_nothing_is_dropped():
    a = ls.add_inbox_item(
        "[Jang 2026-10-02 11:00] Doorbell camera motion flaps on and off again.",
        source="jang",
        rid="rid-refl-1",
    )
    b = ls.add_inbox_item(
        "[Jang 2026-10-03 12:18] Doorbell camera motion flaps on and off again!",
        source="jang",
        rid="rid-refl-2",
    )
    c = ls.add_inbox_item(
        "Miss Library answered in the wrong language.", source="chat", rid="rid-3"
    )
    assert not a["known"] and b["known"] and not c["known"]
    assert a["topic_id"] == b["topic_id"] != c["topic_id"]
    assert b["count"] == 2
    rows = ls.list_inbox()
    assert len(rows) == 3, "a grouped write must still be stored as its own row"
    assert {r["rid"] for r in rows} == {"rid-refl-1", "rid-refl-2", "rid-3"}


def test_row_without_rid_gets_a_pointer():
    ls.add_inbox_item("Something seen", source="admin")
    row = ls.list_inbox()[0]
    assert row["rid"] == "" and row["pointer"] == "source:admin"


def test_empty_text_is_rejected():
    with pytest.raises(ValueError):
        ls.add_inbox_item("   ", source="chat")


# ── Legacy import (T2-D15) ───────────────────────────────────────────────────


def test_legacy_import_once_and_file_untouched(tmp_path):
    src = tmp_path / "kare_huske.json"
    src.write_text(
        json.dumps(
            [
                {
                    "id": "a1",
                    "tekst": "2026-09-01 17:21 — Sensor updates every 10 minutes on cam_a",
                    "dato": "2026-09-01",
                    "pinned": True,
                },
                {
                    "id": "a2",
                    "tekst": "2026-09-02 08:00 — Sensor updates every 10 minutes on cam_a",
                    "dato": "2026-09-02",
                },
                {
                    "id": "a3",
                    "tekst": "Frigate clock is 6 minutes behind",
                    "dato": "2026-09-25",
                    "kontekst": "next dev meeting",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    assert ls.import_legacy_kare_list(src) == 3
    assert ls.import_legacy_kare_list(src) == 0, "import must be idempotent"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    rows = ls.list_inbox()
    assert len(rows) == 3 and {r["source"] for r in rows} == {"legacy"}
    assert {r["pointer"] for r in rows} == {
        "kare_huske.json#a1",
        "kare_huske.json#a2",
        "kare_huske.json#a3",
    }
    assert ls.count_open_topics() == 2


# ── Tool replies (nb/en/de) and dispatch ────────────────────────────────────


@pytest.mark.parametrize(
    "lang,new_word,known_word",
    [
        ("nb", "Lagt på", "allerede"),
        ("en", "Added to", "Already"),
        ("de", "hinzugefügt", "bereits"),
    ],
)
def test_agenda_replies_per_language(lang, new_word, known_word):
    first = agenda.agenda_add("Timer fired twice", lang=lang, rid="rid-1")
    again = agenda.agenda_add("Timer fired twice.", lang=lang, rid="rid-2")
    assert new_word in first and known_word in again and "2" in again


def test_execute_tool_routes_agenda_and_sets_source():
    res = asyncio.run(
        executor.execute_tool(
            "note",
            {
                "action": "write",
                "list_name": "agenda",
                "text": "Fallback flag stuck on",
                "_user_id": "global",
                "_rid": "rid-refl-99",
            },
        )
    )
    assert "#" in res
    row = ls.list_inbox()[0]
    assert row["source"] == "refl" and row["rid"] == "rid-refl-99"
    unknown = asyncio.run(
        executor.execute_tool(
            "note", {"action": "delete", "list_name": "agenda", "_user_id": "global"}
        )
    )
    assert "delete" in unknown and len(ls.list_inbox()) == 1, "agenda rows cannot be deleted"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("remember", "huske"),
        ("huske", "huske"),
        ("shopping", "handle"),
        ("handle", "handle"),
        ("architect", "arkitekt"),
        ("kare", "kare"),
        ("agenda", "agenda"),
        ("Agenda", "agenda"),
        ("huske\n<parameter=text>\nlight on", "huske"),
        ("kare\nR>", "kare"),
        ("kane", "arkitekt"),
        ("", "arkitekt"),
    ],
)
def test_list_name_normalization(raw, expected):
    assert executor.normalize_list_name(raw) == expected


# ── Injection filter (T2-D18) ───────────────────────────────────────────────


def test_agenda_items_are_counted_not_injected(monkeypatch):
    # P133 C3: the kare block is built by memory_prompt (index + lessons + resolved) — the
    # live kare list and knowledge.db must not be read here.
    monkeypatch.setattr("kaare_core.memory.memory_prompt.kare_index", lambda max_chars, lang="nb": "")
    monkeypatch.setattr("kaare_core.memory.memory_prompt.lessons_prompt", lambda lang="nb": "")
    assert llm_adapter._load_kare_reminders("en") == ""
    ls.add_inbox_item("SECRET-AGENDA-TEXT about the vLLM queue", source="chat", rid="r1")
    ls.add_inbox_item("Another topic entirely different", source="chat", rid="r2")
    for lang, word in (("nb", "punkter"), ("en", "items"), ("de", "Punkte")):
        block = llm_adapter._load_kare_reminders(lang)
        assert "SECRET-AGENDA-TEXT" not in block
        assert "2" in block and word in block


# ── Promise enforcer ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Jeg tar det opp på neste dev-møte.",
        "Dette løfter jeg til kommende utviklermøte.",
        "I'll raise this at the next dev meeting.",
        "Ich spreche das beim nächsten Entwicklertreffen an.",
    ],
)
def test_dev_meeting_promise_requires_agenda_write(text):
    det = ToolPromiseDetector()
    assert det.matches(text_out=text, used_tools=[], meta={}, lang="nb")
    assert det._matched_key == "gen_promise_correction_agenda"
    det = ToolPromiseDetector()
    assert det.matches(text_out=text, used_tools=["note"], meta={}, lang="nb"), (
        "a kare note is not enough"
    )
    det = ToolPromiseDetector()
    used = tool_usage_tags("note", {"action": "write", "list_name": "agenda"})
    assert not det.matches(text_out=text, used_tools=used, meta={}, lang="nb")


@pytest.mark.parametrize(
    "text",
    [
        "I nattens dev-møte ble det diskutert kameraene.",
        "Im heutigen Entwicklertreffen ging es um Kameras.",
        "Last night's dev meeting covered the cameras.",
    ],
)
def test_past_meeting_mentions_do_not_trigger(text):
    assert not ToolPromiseDetector().matches(text_out=text, used_tools=[], meta={}, lang="nb")


def test_german_note_promise_detected():
    det = ToolPromiseDetector()
    assert det.matches(text_out="Ich habe es notiert.", used_tools=[], meta={}, lang="de")
    assert det._matched_key == "gen_promise_correction_note"


def test_reflection_loop_forces_agenda_write(monkeypatch, tmp_path):
    thoughts = tmp_path / "inner_thoughts.txt"
    thoughts.write_text("[Jang 2026-10-03 12:00]\nT", encoding="utf-8")
    monkeypatch.setattr(reflection_loop, "_INNER_THOUGHTS_PATH", thoughts)
    monkeypatch.setattr(reflection_loop, "_filter_new_thoughts", lambda raw, since: raw)
    monkeypatch.setattr(reflection_loop, "_read_jang_last_read", lambda: None)
    monkeypatch.setattr(reflection_loop, "_write_jang_last_read", lambda: None)
    monkeypatch.setattr(reflection_loop, "get_lang", lambda uid: "en")
    sent = []
    replies = iter(
        [
            {
                "text": "Nothing to do now; I'll raise the camera flapping at the next dev meeting.",
                "tool_calls": None,
            },
            {
                "text": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "note",
                            "arguments": {
                                "action": "write",
                                "list_name": "agenda",
                                "text": "Camera flapping",
                            },
                        }
                    }
                ],
            },
            {"text": "Done.", "tool_calls": None},
        ]
    )

    async def fake_llm(**kwargs):
        sent.append([m.get("content", "") for m in kwargs["messages"]])
        return next(replies)

    monkeypatch.setattr(reflection_loop, "ask_llm_with_tools", fake_llm)
    asyncio.run(reflection_loop._run_jang_reflection())
    assert len(sent) == 3
    assert "agenda" in sent[1][-1] and "[SYSTEM" in sent[1][-1]
    rows = ls.list_inbox()
    assert len(rows) == 1 and rows[0]["source"] == "refl" and rows[0]["rid"].startswith("rid-refl-")


# ── Admin topic → inbox ─────────────────────────────────────────────────────


def test_admin_dev_topic_becomes_inbox_row(monkeypatch, tmp_path):
    monkeypatch.setattr(router_meetings, "_TOPICS_FILE", tmp_path / "meeting_topics.json")
    router_meetings.api_set_topic("dev", router_meetings._TopicBody(topic="Check the night timing"))
    router_meetings.api_set_topic("dev", router_meetings._TopicBody(topic="Check the night timing"))
    router_meetings.api_set_topic(
        "reflection", router_meetings._TopicBody(topic="Not for the dev slot")
    )
    rows = ls.list_inbox()
    assert (
        len(rows) == 1
        and rows[0]["source"] == "admin"
        and rows[0]["pointer"] == "meeting_topics.json#dev"
    )


def test_reminder_write_without_user_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(lister, "_LISTER_DIR", tmp_path / "lister")
    for lang, word in (("nb", "agenda"), ("en", "agenda"), ("de", "agenda")):
        monkeypatch.setattr(executor, "get_lang", lambda uid, _l=lang: _l)
        res = asyncio.run(
            executor.execute_tool(
                "note",
                {
                    "action": "write",
                    "list_name": "remember",
                    "text": "Car on the east camera",
                    "_user_id": "global",
                    "_rid": "rid-refl-1",
                },
            )
        )
        assert word in res and "kare" in res
    assert not (tmp_path / "lister" / "brukere" / "global_huske.json").exists()
    ok = asyncio.run(
        executor.execute_tool(
            "note",
            {
                "action": "write",
                "list_name": "remember",
                "text": "Buy milk",
                "_user_id": "testuser",
            },
        )
    )
    assert (tmp_path / "lister" / "brukere" / "testuser_huske.json").exists(), ok


@pytest.mark.parametrize(
    "rid,explicit,expected",
    [
        ("rid-1791000000000", "", "user"),
        ("rid-stt-1", "", "stt"),
        ("rid-timer-1", "", "timer"),
        ("rid-meet-1", "", "meet"),
        ("rid-refl-1", "", "refl"),
        ("", "vision", "vision"),
        ("rid-1791000000000", "argus", "argus"),
    ],
)
def test_agenda_source_is_path_neutral(rid, explicit, expected):
    agenda.agenda_add(f"Item via {expected} {rid}", rid=rid, source=explicit)
    assert ls.list_inbox()[0]["source"] == expected
