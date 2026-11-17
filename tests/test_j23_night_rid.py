"""J23 — one rid per night step (KTSB-visjon thread 1, memory feedback_rid_and_receipts).

Measured 05–12.11 in llm_calls.log: the nightjob (STM→LTM, the camera memory's day summary), Miss Kåre's portrait and the
weekly knowledge review called the 27B/9B with **no rid** and source «user» — their trace could not be followed. Now every
night step sets `rid-night-<step>-<ms>` and source `night_<step>` for everything it calls; a step that sets its own rid
(the reflection meeting's rid-refl) keeps it; the trace reader maps the prefix to source «night»; nothing leaks after.
No live state, no model call."""

import asyncio

import kaare_night_sequence as ns
from adapters import llm_adapter
from kaare_core.tools import trace_reader


def test_a_night_step_gets_one_rid_and_source():
    seen = []

    async def step():
        seen.append((llm_adapter._current_rid.get(), llm_adapter._current_source.get()))
        await asyncio.sleep(0)
        seen.append((llm_adapter._current_rid.get(), llm_adapter._current_source.get()))

    assert asyncio.run(ns._step("nightjob", step(), 5)) is True
    rid, src = seen[0]
    assert rid.startswith("rid-night-nightjob-") and src == "night_nightjob" and seen[1] == seen[0]
    assert llm_adapter._current_rid.get() == "" and llm_adapter._current_source.get() == ""


def test_a_step_with_its_own_rid_keeps_it():
    seen = []

    async def step():
        llm_adapter._current_rid.set("rid-refl-123")
        seen.append(llm_adapter._current_rid.get())

    asyncio.run(ns._step("reflection", step(), 5))
    assert seen == ["rid-refl-123"]


def test_trace_reader_knows_the_night_prefix():
    assert trace_reader.infer_source("rid-night-nightjob-1") == "night"
