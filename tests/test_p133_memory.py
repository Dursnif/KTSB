"""
P133 — Kåre's memory drawers, index in the prompt, locked «resolved» lifecycle, Janus look-back.
Storage is real (temp paths, never the live lists or databases); LLM and embedding calls faked.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p133_memory.py -v
"""

import asyncio
import json
import sys

import numpy as np
import pytest
import yaml

sys.path.insert(0, "/kaare")

import adapters.llm_adapter as llm_adapter
import kaare_core.dev_slot.steps as steps
import kaare_core.memory.knowledge_store as ks
import kaare_core.memory.learning_store as ls
import kaare_core.memory.memory_prompt as mp
import kaare_core.memory.relevance as relevance
import kaare_core.model_enforcement as me
import kaare_core.routers.router_settings as rs
import kaare_core.tools.agenda as agenda
import kaare_core.tools.executor as executor
import kaare_core.tools.lessons as lessons
import kaare_core.tools.lister as lister
import kaare_core.tools.shared_tools as shared_tools
import kaare_core.tools.trace_reader as tr
from kaare_core.config import MEMORY_PROMPT_DEFAULTS

LIVE_RID = "rid-1791500000000"  # a chat turn (trace_reader: source 'user')


@pytest.fixture(autouse=True)
def tmp_lists(monkeypatch, tmp_path):
    monkeypatch.setattr(lister, "_KARE_PATH", tmp_path / "kare_huske.json")
    monkeypatch.setattr(lister, "_get_settings", lambda: {"kare_reminders": {}})
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    monkeypatch.setattr(relevance, "DB_PATH", tmp_path / "relevance.db")
    monkeypatch.setattr(relevance, "_cache_loaded_for", None)
    monkeypatch.setattr(relevance, "provider", lambda: None)  # no network in tests
    tool_log = tmp_path / "tool_calls.log"
    tool_log.write_text("")
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", tool_log)
    monkeypatch.setattr(tr, "_TOOL_LOG", tool_log)
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")
    return tmp_path


def _role(monkeypatch, role):
    monkeypatch.setattr(agenda.user_store, "get_user", lambda uid: {"role": role})


def _note(args):
    return asyncio.run(executor.execute_tool("note", {"_user_id": "global", **args}))


def _write_notes(tmp_path, notes):
    (tmp_path / "kare_huske.json").write_text(json.dumps(notes, ensure_ascii=False))


# ── C0: one note can never take the whole block; date stamps do not grow ──


def test_long_pinned_note_leaves_room_for_others(tmp_lists):
    notes = [{"id": "big", "tekst": "x" * 2500, "dato": "2026-09-01", "pinned": True}] + [
        {"id": f"n{i}", "tekst": f"Oppfølging {i}", "dato": "2026-10-09", "pinned": False}
        for i in range(5)
    ]
    _write_notes(tmp_lists, notes)
    block = lister.kare_notes_for_prompt(max_chars=2000, lang="nb")
    assert all(f"[n{i}]" in block for i in range(5))
    big_line = next(line for line in block.splitlines() if "[big]" in line)
    assert len(big_line) < 400 and "…" in big_line


def test_edit_strips_copied_date_stamps(tmp_lists):
    _write_notes(tmp_lists, [{"id": "a1", "tekst": "gammel", "dato": "2026-09-01"}])
    lister.kare_note_edit("a1", "2026-09-01 — 2026-09-01 — ny tekst", lang="nb")
    stored = json.loads((tmp_lists / "kare_huske.json").read_text())[0]["tekst"]
    assert stored == "ny tekst"


def test_add_strips_copied_date_stamp(tmp_lists):
    lister.kare_note_add("2026-10-09 — følg opp sandkassen", lang="nb")
    stored = json.loads((tmp_lists / "kare_huske.json").read_text())[0]["tekst"]
    assert stored == "følg opp sandkassen"


# ── C1: drawers ─────────────────────────────────────────────────────────────


def test_observation_goes_to_its_own_drawer_not_the_dev_queue():
    res = _note(
        {
            "action": "write",
            "list_name": "observations",
            "title": "Motion flap",
            "text": "cam_a709d724 motion on/off three times 02:20",
            "_rid": "rid-refl-1",
        }
    )
    assert "observasjon" in res.lower() or "observation" in res.lower()
    row = ls.list_inbox()[0]
    assert row["kind"] == "observation" and row["title"] == "Motion flap"
    assert ls.open_topics() == [], "the dev slot never sees observations"
    assert ls.count_open_items() == 0 and ls.count_open_items("observation") == 1


def test_grouping_stays_within_a_drawer():
    a = ls.add_inbox_item("Doorbell camera motion flaps again.", source="refl", kind="agenda")
    b = ls.add_inbox_item("Doorbell camera motion flaps again!", source="refl", kind="observation")
    assert a["topic_id"] != b["topic_id"]


def test_lessons_drawer_add_read_edit_retire_and_prompt_cap():
    out = _note(
        {
            "action": "write",
            "list_name": "lessons",
            "title": "Lov",
            "text": "Lovspørsmål går via lovdata, aldri web_search.",
        }
    )
    fid = int(out.split("[")[1].split("]")[0])
    assert "lovdata" in _note({"action": "read", "list_name": "lessons"})
    _note(
        {
            "action": "edit",
            "list_name": "lessons",
            "note_id": str(fid),
            "text": "Lov via library(action='law').",
        }
    )
    assert "library(action='law')" in lessons.lessons_for_prompt(1500)
    assert "x" * 700 not in _note({"action": "write", "list_name": "lessons", "text": "x" * 700})
    _note({"action": "delete", "list_name": "lessons", "note_id": str(fid)})
    assert lessons.lessons_for_prompt(1500) == ""


def test_kare_note_cap_points_to_observations_and_keeps_title(tmp_lists):
    refusal = lister.kare_note_add("y" * 700, lang="nb")
    assert "observations" in refusal
    lister.kare_note_add("Sjekk sandkassen etter slippet", lang="nb", title="Sandkasse")
    item = json.loads((tmp_lists / "kare_huske.json").read_text())[0]
    assert item["tittel"] == "Sandkasse" and lister.note_title(item) == "Sandkasse"
    assert lister.note_title({"tekst": "2026-09-01 — lang tekst " * 10}).startswith("lang tekst")


# ── C2: resolved lifecycle ──────────────────────────────────────────────────


def _topic(text="Tilhengeren gir bil-deteksjoner på cam_a709d724"):
    return ls.add_inbox_item(text, source="refl", kind="observation")["topic_id"]


def test_background_loop_cannot_resolve(monkeypatch):
    _role(monkeypatch, "admin")
    tid = _topic()
    res = agenda.resolve(tid, "Tilhengeren er vår", user_id="stian", rid="rid-refl-5", lang="nb")
    assert "samtale" in res
    assert ls.topic_info([tid])[tid]["label"] == "" and not ls.resolved_topics()


def test_role_not_in_settings_cannot_resolve(monkeypatch):
    _role(monkeypatch, "adult")
    tid = _topic()
    res = agenda.resolve(tid, "Tilhengeren er vår", user_id="kona", rid=LIVE_RID, lang="nb")
    assert "Assistent-innstillinger" in res and not ls.resolved_topics()


def test_admin_resolves_then_text_echo_does_not_reopen(monkeypatch):
    _role(monkeypatch, "admin")
    tid = _topic()
    res = _note(
        {
            "action": "resolve",
            "list_name": "observations",
            "topic_id": tid,
            "conclusion": "Tilhengeren er vår og står fast — forventet",
            "_user_id": "stian",
            "_rid": LIVE_RID,
        }
    )
    assert f"#{tid}" in res
    assert ls.list_inbox()[0]["status"] == "resolved", "open rows leave the queue"
    again = agenda.observation_add(
        "Tilhengeren gir bil-deteksjoner på cam_a709d724!", rid="rid-refl-6", lang="nb"
    )
    assert "allerede løst" in again and "forventet" in again
    newest = ls.list_inbox()[0]
    assert newest["status"] == "echo" and newest["topic_id"] == tid
    assert ls.resolved_topics()[0]["echoes"] == 1


def test_meaning_echo_via_relevance(monkeypatch):
    _role(monkeypatch, "admin")
    tid = _topic()
    agenda.resolve(tid, "Tilhengeren er vår", user_id="stian", rid=LIVE_RID)
    monkeypatch.setattr(relevance, "match_resolved_sync", lambda text, resolved: (tid, 0.7))
    res = agenda.agenda_add("Ansikts-detektoren sa tilhenger 99 % i natt", rid="rid-refl-7")
    assert "allerede løst" in res and ls.list_inbox()[0]["status"] == "echo"


def test_reopen_is_a_proposal_a_human_confirms(monkeypatch):
    _role(monkeypatch, "admin")
    tid = _topic()
    agenda.resolve(tid, "Tilhengeren er vår", user_id="stian", rid=LIVE_RID)
    res = _note(
        {
            "action": "reopen",
            "list_name": "observations",
            "topic_id": tid,
            "text": "Ny bil står der nå",
        }
    )
    pid = int(res.split("#")[-1].split(";")[0])
    assert ls.resolved_topics(), "still resolved until a human confirms"
    ls.decide_proposal(pid, "confirmed", "stian")
    assert not ls.resolved_topics()
    assert ls.topic_info([tid])[tid]["last_developed"] is None


def test_reopen_of_open_topic_is_refused():
    tid = _topic()
    assert "ikke løst" in agenda.reopen(tid, "x")


# ── C3: index, prose, relevance ─────────────────────────────────────────────


def _cfg(monkeypatch, **over):
    cfg = {**MEMORY_PROMPT_DEFAULTS, **over}
    for mod in (mp, relevance, agenda, lessons):
        monkeypatch.setattr(mod, "get_memory_prompt_config", lambda: cfg)
    return cfg


def test_kare_index_shows_titles_not_prose(tmp_lists):
    _write_notes(
        tmp_lists,
        [{"id": "a1", "tittel": "Sandkasse", "tekst": "Lang tekst " * 40, "dato": "2026-10-09"}]
        + [{"id": f"n{i}", "tekst": f"Notat {i}", "dato": "2026-10-09"} for i in range(60)],
    )
    block = mp.kare_index(400, "nb")
    assert "- [a1] Sandkasse" in block and "Lang tekst Lang" not in block
    assert "og " in block.splitlines()[-1] and "til" in block.splitlines()[-1], (
        "capped with a count"
    )


def test_world_prompt_core_prose_then_index(monkeypatch):
    _cfg(monkeypatch, core_facts_max_chars=120, index_max_chars=2000)
    for i, imp in enumerate((0.9, 0.8, 0.3, 0.2)):
        ks.add_fact(
            domain="world",
            section="Cameras",
            content=f"Fakta nummer {i} " + "x" * 30,
            importance=imp,
        )
    block = mp.world_prompt("nb")
    assert "Fakta nummer 0" in block.split("#")[0] or "Fakta nummer 0 " in block
    index_part = block.split("(indeks")[1]
    assert "Fakta nummer 3" in index_part and "Fakta nummer 0 " not in index_part


def test_reminders_block_has_lessons_and_resolved_as_prose(monkeypatch):
    _role(monkeypatch, "admin")
    lessons.lesson_add("Lov via lovdata.", "Lov")
    tid = _topic()
    agenda.resolve(tid, "Tilhengeren er vår — forventet", user_id="stian", rid=LIVE_RID)
    block = llm_adapter._load_kare_reminders("nb")
    assert "Lov via lovdata." in block and "Tilhengeren er vår — forventet" in block


def test_relevant_block_uses_hits_and_caches_per_query(monkeypatch, tmp_lists):
    _cfg(monkeypatch)
    _write_notes(tmp_lists, [{"id": "c3", "tekst": "Tilhengeren er vår", "dato": "2026-10-07"}])
    monkeypatch.setattr(relevance, "provider", lambda: {"name": "fake", "url": ""})
    monkeypatch.setattr(mp.relevance, "has_stale", lambda docs: False)
    calls = []

    async def fake_relevant(query, docs, k=None):
        calls.append(query)
        return [{**docs[0], "score": 0.6}]

    monkeypatch.setattr(mp.relevance, "relevant", fake_relevant)
    a = asyncio.run(mp.relevant_block("tilhengeren?", "nb", include_world=False))
    b = asyncio.run(mp.relevant_block("tilhengeren?", "nb", include_world=False))
    assert "[c3] Tilhengeren er vår" in a and a == b and len(calls) == 1


def test_relevant_block_empty_without_provider():
    assert asyncio.run(mp.relevant_block("hva som helst", "nb")) == ""


def test_relevance_ranking_respects_min_score(monkeypatch):
    _cfg(monkeypatch, min_score=0.5, top_k=3)
    monkeypatch.setattr(relevance, "provider", lambda: {"name": "fake", "url": ""})
    vecs = {"a": [1.0, 0.0], "b": [0.6, 0.8], "q": [1.0, 0.0]}
    relevance._store(
        [
            (k, relevance._hash(k), "fake", np.array(v, dtype=np.float32))
            for k, v in vecs.items()
            if k != "q"
        ]
    )

    async def fake_embed(texts, prov, timeout=0):
        return relevance._unit([vecs["q"]])

    monkeypatch.setattr(relevance, "embed_async", fake_embed)
    docs = [{"key": "a", "text": "a"}, {"key": "b", "text": "b"}]
    hits = asyncio.run(relevance.relevant("q", docs))
    assert [h["key"] for h in hits] == ["a", "b"]
    _cfg(monkeypatch, min_score=0.7)
    assert [h["key"] for h in asyncio.run(relevance.relevant("q", docs))] == ["a"]


def test_last_user_text_handles_multimodal():
    msgs = [
        {"role": "user", "content": "gammel"},
        {"role": "assistant", "content": "svar"},
        {"role": "user", "content": [{"type": "text", "text": "se her"}, {"type": "image_url"}]},
        {"role": "tool", "content": "resultat"},
    ]
    assert llm_adapter._last_user_text(msgs) == "se her"


# ── C4: Janus looks back ────────────────────────────────────────────────────


def _resolved_and_new(monkeypatch):
    _role(monkeypatch, "admin")
    old = _topic("Tilhengeren gir bil-deteksjoner på cam_a709d724")
    agenda.resolve(old, "Tilhengeren er vår — forventet", user_id="stian", rid=LIVE_RID)
    new = ls.add_inbox_item("Ansikts-detektor: tilhenger 99 % i natt", source="refl")["topic_id"]
    return old, new


def _run(monkeypatch, janus_answer, new, old, shown=True):
    async def fake_matches(topics):
        return (
            {new: [{"resolved_id": old, "conclusion": "x", "resolved_at": "", "score": 0.7}]}
            if shown
            else {}
        )

    async def janus(role, messages, **kw):
        return {"ok": True, "text": json.dumps({"priorities": [janus_answer]})}

    async def kare(messages, tools=None, rid="", user_id="", source="", **kw):
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps({"kind": "known", "label": "x", "summary": "x"}),
        }

    monkeypatch.setattr(steps, "resolved_matches", fake_matches)
    monkeypatch.setattr(steps, "call_llm_chat", janus)
    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)
    bundle = {"inbox": {"topics": ls.open_topics()}, "traces": {"items": []}}
    return asyncio.run(
        steps.run_b3(
            bundle, run_id="r", rid="rid-meet-1", lang="nb", budget=steps.Budget(5), round_cap=1
        )
    )


def test_janus_same_as_resolved_links_as_echo(monkeypatch):
    old, new = _resolved_and_new(monkeypatch)
    out = _run(
        monkeypatch, {"topic_id": new, "priority": "same_as_resolved", "resolved_id": old}, new, old
    )
    assert out["resolved_links"] == [{"topic_id": new, "resolved_id": old, "rows": 1}]
    assert ls.resolved_topics()[0]["id"] == old, "the resolved topic stays resolved"
    assert ls.list_inbox()[0]["status"] == "echo"
    assert out["s1"] == [], "not judged again"


def test_janus_cannot_link_to_a_resolved_topic_he_was_not_shown(monkeypatch):
    old, new = _resolved_and_new(monkeypatch)
    out = _run(
        monkeypatch,
        {"topic_id": new, "priority": "same_as_resolved", "resolved_id": old},
        new,
        old,
        shown=False,
    )
    assert out["resolved_links"] == [] and [r["topic_id"] for r in out["s1"]] == [new]


def test_janus_contradicts_makes_a_reopen_proposal_and_judges_the_topic(monkeypatch):
    old, new = _resolved_and_new(monkeypatch)
    out = _run(
        monkeypatch,
        {"topic_id": new, "priority": "contradicts", "resolved_id": old, "reason": "ny bil"},
        new,
        old,
    )
    assert out["reopen_proposals"][0]["resolved_id"] == old
    assert [p["kind"] for p in ls.list_proposals()] == ["reopen"]
    assert [r["topic_id"] for r in out["s1"]] == [new]
    assert ls.resolved_topics(), "still resolved until a human confirms"


def test_proposal_followup_counts_rows_since_decision():
    tid = ls.add_inbox_item("HA svarer 200 OK for ukjent enhet", source="gui")["topic_id"]
    fid = ls.add_finding("r", "funn", [], True, topic_id=tid)
    pid = ls.add_proposal(fid, "Sjekk at enheten finnes før 200")
    ls.decide_proposal(pid, "confirmed", "stian")
    conn = ls._get_conn()  # timestamps have second resolution: put the decision in the past
    conn.execute(
        "UPDATE proposals SET decided_at = '2026-10-01T00:00:00+00:00' WHERE id = ?", (pid,)
    )
    conn.execute("UPDATE inbox_items SET ts = '2026-09-30T00:00:00+00:00'")
    conn.commit()
    conn.close()
    ls.add_inbox_item("HA svarer 200 OK for ukjent enhet igjen!", source="gui")
    rows = ls.proposal_followups("2000-01-01T00:00:00+00:00")
    assert rows[0]["id"] == pid and rows[0]["new_rows_since"] == 1


def test_report_shows_resolved_section():
    report = steps.build_report(
        date="2026-10-10",
        lang="nb",
        summary_text="ok",
        findings=[],
        notes={},
        proposals=[],
        b3={
            "s1": [],
            "s2": [],
            "resolved_links": [{"topic_id": 240, "resolved_id": 71, "rows": 2}],
            "reopen_proposals": [{"topic_id": 241, "resolved_id": 71, "proposal_id": 14}],
            "echoes_24h": {71: 5},
            "proposal_followups": [
                {"id": 9, "topic_id": 37, "decided_at": "2026-10-09T10:00", "new_rows_since": 0}
            ],
        },
    )
    assert "## Sett mot løste saker" in report
    assert "Sak #240 er det samme som løst sak #71" in report
    assert "forslag #14 om gjenåpning" in report
    assert "Løst sak #71: 5 ekko" in report
    assert "Bekreftet forslag #9 (sak #37, 2026-10-09): 0 nye rader" in report


# ── C6: settings API (GUI → settings.yaml) ──────────────────────────────────


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.dump({"kare_reminders": {"max_entries": 100}, "other": {"keep": 1}}))
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    monkeypatch.setattr(rs, "reload_core_config", lambda: None)
    monkeypatch.setattr(rs._llm, "reload_config", lambda: None)
    return p


def test_memory_settings_put_writes_both_blocks(settings_file):
    asyncio.run(
        rs.api_put_memory_prompt(
            {
                "resolve_roles": ["admin", "adult"],
                "top_k": 4,
                "min_score": 0.55,
                "relevance_enabled": False,
                "kare_reminders": {"write_max_chars": 800},
            },
            _u=None,
        )
    )
    data = yaml.safe_load(settings_file.read_text())
    assert data["memory_prompt"] == {
        "resolve_roles": ["admin", "adult"],
        "top_k": 4,
        "min_score": 0.55,
        "relevance_enabled": False,
    }
    assert data["kare_reminders"] == {"max_entries": 100, "write_max_chars": 800}
    assert data["other"] == {"keep": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {"resolve_roles": ["stranger"]},
        {"resolve_roles": "admin"},
        {"top_k": 0},
        {"min_score": 1.5},
        {"kare_reminders": {"write_max_chars": 5}},
    ],
)
def test_memory_settings_reject_bad_values(settings_file, payload):
    with pytest.raises(rs.HTTPException):
        asyncio.run(rs.api_put_memory_prompt(payload, _u=None))


def test_memory_settings_get_has_defaults_and_roles(settings_file):
    d = asyncio.run(rs.api_get_memory_prompt(_u=None))
    assert d["resolve_roles"] == ["admin"] or isinstance(d["resolve_roles"], list)
    assert d["kare_reminders"]["write_max_chars"] == 600 and "teen" in d["roles"]


# ── C5 support: index read + move ───────────────────────────────────────────


def test_read_without_filter_is_an_index_of_all(tmp_lists):
    _write_notes(
        tmp_lists,
        [{"id": f"n{i}", "tekst": "lang tekst " * 30, "dato": "2026-10-09"} for i in range(100)],
    )
    out = _note({"action": "read", "list_name": "kare"})
    assert out.count("[n") == 100 and len(out) < 100 * 120, "titles only, the whole list fits"
    full = _note({"action": "read", "list_name": "kare", "note_ids": ["n3", "n7"]})
    assert "[n3]" in full and "[n7]" in full and "[n4]" not in full
    assert "lang tekst lang tekst lang tekst" in full


def test_full_read_is_capped(tmp_lists):
    _write_notes(
        tmp_lists,
        [{"id": f"n{i}", "tekst": f"sensor {i}", "dato": "2026-10-09"} for i in range(40)],
    )
    out = _note({"action": "read", "list_name": "kare", "search": "sensor"})
    assert out.count("[n") == 15 and "25" in out


def test_move_to_observations_writes_then_removes(tmp_lists):
    _write_notes(
        tmp_lists, [{"id": "a1", "tekst": "2026-10-01 — Kamera flakker", "dato": "2026-10-01"}]
    )
    out = _note(
        {
            "action": "move",
            "list_name": "kare",
            "note_id": "a1",
            "to": "observations",
            "title": "Flakk",
            "theme": "house",
            "_rid": LIVE_RID,
        }
    )
    assert "a1" in out and "observations #" in out
    row = ls.list_inbox()[0]
    assert (
        row["kind"] == "observation" and row["text"] == "Kamera flakker" and row["title"] == "Flakk"
    )
    assert row["pointer"] == "kare_huske.json#a1"
    assert json.loads((tmp_lists / "kare_huske.json").read_text()) == []


def test_move_to_lessons_and_failed_move_keeps_the_note(tmp_lists):
    _write_notes(
        tmp_lists,
        [
            {"id": "l1", "tekst": "Lov via lovdata", "dato": "2026-10-04"},
            {"id": "l2", "tekst": "x" * 700, "dato": "2026-10-04"},
        ],
    )
    assert "lessons [" in _note(
        {"action": "move", "list_name": "kare", "note_id": "l1", "to": "lessons"}
    )
    failed = _note({"action": "move", "list_name": "kare", "note_id": "l2", "to": "lessons"})
    assert "urørt" in failed
    left = [i["id"] for i in json.loads((tmp_lists / "kare_huske.json").read_text())]
    assert left == ["l2"], "the long note stays until a shorter text is given"
    _note(
        {
            "action": "move",
            "list_name": "kare",
            "note_id": "l2",
            "to": "lessons",
            "text": "Kort regel.",
        }
    )
    assert json.loads((tmp_lists / "kare_huske.json").read_text()) == []


def test_move_rejects_unknown_target(tmp_lists):
    _write_notes(tmp_lists, [{"id": "a1", "tekst": "x", "dato": "2026-10-01"}])
    assert "observations" in _note(
        {"action": "move", "list_name": "kare", "note_id": "a1", "to": "world"}
    )
    assert len(json.loads((tmp_lists / "kare_huske.json").read_text())) == 1


# BUG-log 2026-10-09 (DT03 R8.3): KTSB's own echo hint asks for `note action='reopen', topic_id=N` without a list,
# and that call used to fall into the default list → «Ukjent action». resolve/reopen go by topic number now when no
# list (or 'resolved') is given; an explicit other list is still refused (P133 C2).


@pytest.mark.parametrize("list_args", [{}, {"list_name": "resolved"}])
def test_reopen_works_without_a_list_and_from_resolved(monkeypatch, list_args):
    _role(monkeypatch, "admin")
    tid = _topic()
    agenda.resolve(tid, "Tilhengeren er vår", user_id="stian", rid=LIVE_RID)
    res = _note({"action": "reopen", "topic_id": tid, "text": "Ny bil står der nå", **list_args})
    assert "Ukjent action" not in res
    assert [p["kind"] for p in ls.list_proposals()] == ["reopen"]


def test_resolve_without_a_list_goes_by_topic_number(monkeypatch):
    _role(monkeypatch, "admin")
    tid = _topic()
    res = _note({"action": "resolve", "topic_id": tid, "conclusion": "Forventet", "_user_id": "stian", "_rid": LIVE_RID})
    assert f"#{tid}" in res and ls.resolved_topics()[0]["id"] == tid


def test_resolve_on_an_explicit_other_list_is_still_refused(monkeypatch):
    _role(monkeypatch, "admin")
    tid = _topic()
    _note({"action": "resolve", "list_name": "kare", "topic_id": tid, "conclusion": "x", "_user_id": "stian", "_rid": LIVE_RID})
    assert not ls.resolved_topics(), "a kare-list call never resolves a learning.db topic"


# ── N25: lesson length from the GUI, «Relevant nå» status ───────────────────


def test_lesson_length_follows_setting(monkeypatch):
    _cfg(monkeypatch, lessons_write_max_chars=120)
    assert "120" in _note({"action": "write", "list_name": "lessons", "text": "x" * 150})
    assert lessons.lesson_read() == lessons.t("lesson_empty", "nb")
    _cfg(monkeypatch, lessons_write_max_chars=200)
    _note({"action": "write", "list_name": "lessons", "text": "y" * 150})
    assert "y" * 150 in lessons.lesson_read()


def test_lessons_write_max_chars_put_and_range(settings_file):
    asyncio.run(rs.api_put_memory_prompt({"lessons_write_max_chars": 900}, _u=None))
    saved = yaml.safe_load(settings_file.read_text())
    assert saved["memory_prompt"]["lessons_write_max_chars"] == 900
    with pytest.raises(rs.HTTPException):
        asyncio.run(rs.api_put_memory_prompt({"lessons_write_max_chars": 50}, _u=None))


@pytest.mark.parametrize("prov", [None, {"name": "bge-m3", "url": "http://x"}])
def test_memory_settings_get_reports_relevance_active(settings_file, monkeypatch, prov):
    monkeypatch.setattr(rs._relevance, "provider", lambda: prov)
    d = asyncio.run(rs.api_get_memory_prompt(_u=None))
    assert d["relevance_active"] is (prov is not None)
    assert d["lessons_write_max_chars"] > 0
