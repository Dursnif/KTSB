"""T32b job 2 — household names never leave the house in web queries from the night meeting / dev slot.

Red case: the 2026-11-12 reflection meeting leader sent «Ola Kåre refleksjonsmøte lukkepunkt brytende tidslinje».
Names below are placeholders patched in — the tests never read the house's user store, faces or settings.
"""

import asyncio
import logging

import pytest

import kaare_reflection
from adapters import llm_adapter
from kaare_core.agents.mechanic import tools as mech
from kaare_core.tools import query_scrub

NAMES = {"Ola", "Kåre", "Gjest", "Brukerx"}


@pytest.fixture
def names(monkeypatch):
    monkeypatch.setattr(query_scrub, "household_names", lambda: set(NAMES))


@pytest.fixture
def sent(monkeypatch):
    calls: list[str] = []

    async def fake_search(q, *a, **k):
        calls.append(q)
        return "RESULTS"

    monkeypatch.setattr(kaare_reflection, "web_search", fake_search)
    monkeypatch.setattr(mech, "web_search", fake_search)
    return calls


def test_reflection_case_2026_11_12_names_removed(names, sent):
    asyncio.run(
        kaare_reflection._execute_leder_tool(
            "nettsøk", {"query": "Ola Kåre refleksjonsmøte lukkepunkt brytende tidslinje"}
        )
    )
    assert sent == ["refleksjonsmøte lukkepunkt brytende tidslinje"]


def test_reflection_query_of_only_names_is_not_sent(names, sent):
    out = asyncio.run(kaare_reflection._execute_leder_tool("nettsøk", {"query": "Ola og Kåre"}))
    assert sent == []
    assert isinstance(out, str) and out


def test_genitive_and_case(names):
    q, n = query_scrub.scrub("OLAS ønsker og kåre's råd om brukerx", NAMES)
    assert "ola" not in q.lower() and "kåre" not in q.lower() and "brukerx" not in q.lower()
    assert n == 3


def test_name_inside_other_word_is_kept(names):
    q, n = query_scrub.scrub("Olavsvern gjestehus", NAMES)
    assert q == "Olavsvern gjestehus" and n == 0


def test_mechanic_in_dev_slot_is_scrubbed(names, sent):
    token = llm_adapter._current_rid.set("rid-meet-123")
    try:
        asyncio.run(mech.execute_tool("nettsøk", {"query": "Ola Hjemme sone home assistant"}, "en"))
    finally:
        llm_adapter._current_rid.reset(token)
    assert sent == ["Hjemme sone home assistant"]


def test_mechanic_in_chat_is_unchanged(names, sent):
    token = llm_adapter._current_rid.set("rid-1794000000000")
    try:
        asyncio.run(mech.execute_tool("nettsøk", {"query": "Ola Hjemme sone"}, "en"))
    finally:
        llm_adapter._current_rid.reset(token)
    assert sent == ["Ola Hjemme sone"]


def test_log_has_rid_never_names(names, sent, caplog):
    token = llm_adapter._current_rid.set("rid-refl-42")
    try:
        with caplog.at_level(logging.INFO, logger="kaare_core.tools.query_scrub"):
            asyncio.run(kaare_reflection._execute_leder_tool("nettsøk", {"query": "Ola lukkepunkt tidslinje"}))
    finally:
        llm_adapter._current_rid.reset(token)
    text = caplog.text
    assert "rid-refl-42" in text
    assert "Ola" not in text and "Kåre" not in text


def test_household_names_reads_sources(monkeypatch):
    monkeypatch.setattr(
        query_scrub._store,
        "list_users",
        lambda: [{"username": "brukerx", "display_name": "Brukerx Etternavn", "frigate_faces": ["Face_A"]}],
    )
    monkeypatch.setattr(query_scrub._faces, "configured_faces", lambda: ["Gjest_B"])
    monkeypatch.setattr(query_scrub, "get_settings", lambda: {"assistant_name": "Botto"})
    got = query_scrub.household_names()
    assert {"brukerx", "Brukerx", "Etternavn", "Face", "Gjest", "Botto"} <= got


def test_failing_source_does_not_block_others(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(query_scrub._store, "list_users", boom)
    monkeypatch.setattr(query_scrub._faces, "configured_faces", lambda: ["Gjest_B"])
    monkeypatch.setattr(query_scrub, "get_settings", dict)
    assert "Gjest" in query_scrub.household_names()
