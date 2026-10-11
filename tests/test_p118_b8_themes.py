"""
P118 B8 — agenda themes set at write time (writer's choice, code rules as fallback) and the S3
selection that takes one topic per theme before filling up to topics_per_meeting.
LLM calls are faked; storage is real (temp paths, never the live learning.db or logs).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b8_themes.py -v
"""

import asyncio
import json
import sys

import pytest
import yaml

sys.path.insert(0, "/kaare")

import kaare_core.dev_slot.steps as steps
import kaare_core.memory.learning_store as ls
import kaare_core.model_enforcement as me
import kaare_core.routers.router_settings as rs
import kaare_core.tools.agenda as agenda
import kaare_core.tools.shared_tools as shared_tools
import kaare_core.tools.trace_reader as tr
import kaare_night_sequence as night
from kaare_core.dev_slot.config import get_dev_slot_config_from
from kaare_core.memory.agenda_themes import THEMES, resolve_theme
from kaare_core.tools.definitions import _notat_tool

RID = "rid-meet-1791100000000"


@pytest.fixture(autouse=True)
def tmp_env(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    tool_log = tmp_path / "tool_calls.log"
    tool_log.write_text("")
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", tool_log)
    monkeypatch.setattr(tr, "_TOOL_LOG", tool_log)
    for name in ("_ROUTE_LOG", "_LLM_LOG", "_THINK_CACHE", "_ENFORCE_LOG"):
        p = tmp_path / f"{name}.log"
        p.write_text("")
        monkeypatch.setattr(tr, name, p)
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")

    async def no_9b(claim, evidence, rid="", lang=""):
        return {"status": "skipped"}

    monkeypatch.setattr(steps.verifier, "check_claim", no_9b)
    return tmp_path


def _tool_line(rid, tool):
    return json.dumps({"ts": "2026-10-09T10:00:00+00:00", "rid": rid, "tool": tool, "args": {}})


# ── resolve_theme: writer first, code rules as fallback ─────────────────────


def test_writer_theme_wins_over_everything():
    assert (
        resolve_theme("memory", text="camera sensor flaps", source="user", rid_tools=["camera"])
        == "memory"
    )


def test_unknown_writer_theme_falls_back_to_rules():
    assert resolve_theme("cameras", text="Frigate kamera flakker") == "house"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Bevegelsessensoren på kameraet flakker", "house"),
        ("The doorbell camera detection is unstable", "house"),
        ("Die Kamera-Erkennung am Garage ist instabil", "house"),
        ("Hovedmodellen gikk tom for tokens, fallback til 9B", "system"),
        ("vLLM latency spikes after restart", "system"),
        ("Jang-refleksjon avbrutt midt i", "agents"),
        ("Semantisk minne finner ikke gamle samtaler", "memory"),
        ("Alias-oppløsning bør skje før LLM får entity", "tools"),
        ("Svaret kom på feil språk", "answers"),
        ("Noe helt annet uten nøkkelord", "other"),
    ],
)
def test_keyword_fallback_nb_en_de(text, expected):
    assert resolve_theme(None, text=text, source="refl") == expected


def test_speaker_tag_is_not_the_topic():
    text = "[Jang 2026-10-03 17:44] MENNESKER-kanalen rapporterer biler på cam_a709d724"
    assert resolve_theme(None, text=text, source="refl") == "house"
    assert (
        resolve_theme(None, text="09:30 (Jang): tilhenger sett som bil", source="refl") == "house"
    )
    assert resolve_theme(None, text="Jang tankestrøm mangler innhold", source="refl") == "agents"


def test_unlabeled_problem_is_described_by_its_first_row():
    tid = _problem("Verktøyfeil: set_var er ikke gyldig", "tools")
    ls.apply_topic_verdict(tid, "problem", label="")
    conn = ls._get_conn()
    conn.execute("UPDATE topics SET label = '' WHERE id = ?", (tid,))
    conn.commit()
    conn.close()
    assert ls.pending_problems()[0]["label"].startswith("Verktøyfeil")


def test_whole_words_only_bil_is_not_bilde():
    assert resolve_theme(None, text="Bildet ble lagret feil", source="refl") == "other"
    assert resolve_theme(None, text="Bilen står i oppkjørselen", source="refl") == "house"


def test_review_rows_are_answers_unless_a_tool_problem():
    assert resolve_theme(None, text="Kåre misforsto spørsmålet", source="review") == "answers"
    assert resolve_theme(None, text="Camera snapshot failed", source="review") == "answers"
    assert resolve_theme(None, text="ha_control tool call failed", source="review") == "tools"


def test_rid_tools_only_count_for_live_turns():
    assert resolve_theme(None, text="noe rart", source="user", rid_tools=["memory"]) == "memory"
    # The reflection loop calls review tools whatever the topic is — no hint there.
    assert resolve_theme(None, text="noe rart", source="refl", rid_tools=["memory"]) == "other"


# ── Storage ─────────────────────────────────────────────────────────────────


def test_row_and_new_topic_get_the_theme():
    r = ls.add_inbox_item("Miss Library svarte på engelsk", source="refl", theme="agents")
    assert r["theme"] == "agents"
    row = ls.list_inbox()[0]
    assert row["theme"] == "agents"
    assert ls.topic_info([r["topic_id"]])[r["topic_id"]]["theme"] == "agents"


def test_joined_topic_keeps_its_first_theme_and_row_keeps_its_own():
    a = ls.add_inbox_item(
        "Doorbell camera motion flaps on and off again.", source="refl", theme="house"
    )
    b = ls.add_inbox_item(
        "Doorbell camera motion flaps on and off again!", source="refl", theme="system"
    )
    assert a["topic_id"] == b["topic_id"] and b["theme"] == "house"
    themes = sorted(r["theme"] for r in ls.list_inbox())
    assert themes == ["house", "system"], "each row stores what its writer said"


def test_tools_in_same_rid_set_theme_for_a_user_turn(tmp_env):
    (tmp_env / "tool_calls.log").write_text(
        _tool_line("rid-42", "memory") + "\n" + _tool_line("rid-42", "note") + "\n"
    )
    r = ls.add_inbox_item("Dette må vi se på", source="user", rid="rid-42")
    assert r["theme"] == "memory"


def test_agenda_tool_passes_theme_and_names_it_in_the_reply():
    reply = agenda.agenda_add(
        "Tom refleksjonskategori", rid="rid-refl-9", theme="agents", lang="nb"
    )
    assert "tema: Agentene" in reply
    assert ls.list_inbox()[0]["theme"] == "agents"
    listing = agenda.agenda_read("nb")
    assert "· agents]" in listing


def test_backfill_is_code_only_and_idempotent():
    r = ls.add_inbox_item("Frigate kamera flakker igjen", source="legacy", theme="house")
    conn = ls._get_conn()
    conn.execute("UPDATE inbox_items SET theme = ''")
    conn.execute("UPDATE topics SET theme = ''")
    conn.commit()
    conn.close()
    assert ls.backfill_themes() == 1
    assert ls.topic_info([r["topic_id"]])[r["topic_id"]]["theme"] == "house"
    assert ls.list_inbox()[0]["theme"] == "house"
    assert ls.backfill_themes() == 0, "second run finds nothing to do"


# ── Queue order + selection ─────────────────────────────────────────────────


def _problem(text, theme):
    r = ls.add_inbox_item(text, source="refl", theme=theme)
    ls.apply_topic_verdict(r["topic_id"], "problem", label=text[:40])
    return r["topic_id"]


def test_tried_topic_moves_behind_untried_ones():
    a = _problem("First camera problem aaaa", "house")
    b = _problem("Second memory problem bbbb", "memory")
    assert [p["id"] for p in ls.pending_problems()][:2] == [a, b]
    ls.mark_developed(a)
    assert [p["id"] for p in ls.pending_problems()][:2] == [b, a]


def test_select_s3_one_per_theme_then_fill():
    info = {
        1: {"theme": "house", "first_seen": "2026-10-01"},
        2: {"theme": "house", "first_seen": "2026-10-02"},
        3: {"theme": "house", "first_seen": "2026-10-03"},
        4: {"theme": "tools", "first_seen": "2026-10-05"},
        5: {"theme": "system", "first_seen": "2026-10-06"},
        6: {"theme": "", "first_seen": "2026-10-07"},
    }
    picked, counts = steps.select_s3([1, 2, 3, 4, 5, 6], info, 5)
    assert picked == [4, 1, 5, 6, 2], "tools, house, system, other first; then house again"
    assert counts["house"]["queued"] == 3 and counts["other"]["queued"] == 1
    assert steps.select_s3([1, 2, 3, 4, 5, 6], info, 2)[0] == [4, 1]
    assert steps.select_s3([1, 2, 3, 4, 5, 6], info, None)[0] == [4, 1, 5, 6, 2, 3]


def test_select_s3_prefers_least_recently_developed_within_theme():
    info = {
        1: {"theme": "house", "first_seen": "2026-10-01", "last_developed": "2026-10-09T01:00"},
        2: {"theme": "house", "first_seen": "2026-10-05", "last_developed": None},
    }
    assert steps.select_s3([1, 2], info, 1)[0] == [2]


def test_run_b3_takes_topics_per_meeting_across_themes(monkeypatch):
    house = [
        _problem(text, "house")
        for text in (
            "Doorbell motion flaps at night",
            "Trailer seen as a car on the east camera",
            "Garage light toggles by itself",
            "Person detections from a parked car",
        )
    ]
    tools = _problem("Alias resolution before the LLM", "tools")
    developed = []

    async def kare(messages, tools=None, rid="", user_id="", source="", **kw):
        developed.append(messages[0]["content"])
        return {"ok": True, "tool_calls": None, "text": json.dumps({"findings": []})}

    async def janus(role, messages, **kw):
        return {"ok": True, "text": json.dumps({"priorities": []})}

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)
    monkeypatch.setattr(steps, "call_llm_chat", janus)
    out = asyncio.run(
        steps.run_b3(
            {"inbox": {"topics": []}, "traces": {"items": []}},
            run_id="run-b8",
            rid=RID,
            lang="en",
            budget=steps.Budget(10),
            round_cap=1,
            topics_per_meeting=2,
        )
    )
    assert [r["topic_id"] for r in out["s3"]] == [tools, house[0]]
    assert out["themes"]["house"] == {"queued": 4, "developed": 1}
    assert out["themes"]["tools"] == {"queued": 1, "developed": 1}
    assert len(out["not_selected"]) == 3
    info = ls.topic_info([tools, house[0], house[1]])
    assert info[tools]["last_developed"] and info[house[0]]["last_developed"]
    assert info[house[1]]["last_developed"] is None


def test_report_shows_theme_counts_and_finding_theme():
    report = steps.build_report(
        date="2026-10-10",
        lang="nb",
        summary_text="ok",
        findings=[
            {
                "topic_id": 7,
                "topic_label": "Alias",
                "topic_theme": "tools",
                "text": "x",
                "verified": True,
                "evidence_refs": "[]",
                "verifier": {"status": "ok"},
            }
        ],
        notes={},
        proposals=[],
        b3={
            "s1": [],
            "s2": [],
            "skipped_for_time": ["problem:9"],
            "not_selected": ["problem:10", "problem:11"],
            "themes": {
                "house": {"queued": 4, "developed": 1},
                "tools": {"queued": 1, "developed": 1},
            },
        },
    )
    assert "**#7 Alias** _(Verktøy og HA-styring)_" in report
    assert "Verktøy og HA-styring 1/1 · Hus, sensorer og kamera 1/4" in report
    assert "ikke rukket (venter til neste natt): 3" in report


# ── Config, settings API, night-sequence timeout ────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({}, 3),
        ({"topics_per_meeting": 6}, 6),
        ({"topics_per_meeting": 0}, 3),
        ({"topics_per_meeting": "x"}, 3),
    ],
)
def test_topics_per_meeting_config(raw, expected):
    assert get_dev_slot_config_from({"dev_slot": raw})["topics_per_meeting"] == expected


def test_settings_put_accepts_topics_per_meeting(monkeypatch, tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.dump({"dev_meeting": {}}))
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    asyncio.run(rs.api_put_dev_meeting({"topics_per_meeting": 6}, _u=None))
    assert yaml.safe_load(p.read_text())["dev_slot"]["topics_per_meeting"] == 6
    with pytest.raises(rs.HTTPException):
        asyncio.run(rs.api_put_dev_meeting({"topics_per_meeting": 0}, _u=None))


def test_night_timeout_follows_time_budget(monkeypatch):
    monkeypatch.setattr(night, "get_dev_slot_config", lambda: {"time_budget_minutes": 120})
    assert night._dev_slot_timeout() == 120 * 60 + night._DEV_SLOT_MARGIN_S
    monkeypatch.setattr(night, "get_dev_slot_config", lambda: {"time_budget_minutes": 12})
    assert night._dev_slot_timeout() == night._TIMEOUT_DEV_MEETING


def test_theme_list_matches_note_tool_enum():
    enum = _notat_tool("en")["function"]["parameters"]["properties"]["theme"]["enum"]
    assert tuple(enum) == THEMES
