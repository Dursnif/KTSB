"""Run-queue J30 — Kåre guessed rids for self_trace (2026-11-13 05:40: he called fetch_trace with rid-1794544624154 and
rid-1794544739615; the real rids of the conversation just before were rid-1794544619075 and rid-1794544682015). Fix with
code facts:
- `self_trace action='recent'`: the caller's own recent requests (rid, local time, the first characters of the message) —
  read from LTM with the caller's user_id, never another user's;
- `fetch_trace` that finds nothing lists the caller's nearest real rids in time (a rid is a ms timestamp);
- the per-turn rid note names the tool that exists (`self_trace`, was the removed `inspect_system`) and carries the
  previous turn's rid for the same user (RAM, per user).
No live logs or DB: LTM and the trace reader are faked. Placeholders only."""

import asyncio

import pytest

from kaare_core.routers import router_generate as rg
from kaare_core.tools import executor
from kaare_core.tools.i18n import t

ROWS = {"user_a": [{"ts": "2026-11-13T04:38:02+00:00", "rid": "rid-1794544682015", "prompt": "Kan du sjekke hva som skjedde?"},
                   {"ts": "2026-11-13T04:36:59+00:00", "rid": "rid-1794544619075", "prompt": "Slå av lyset i stua"},
                   {"ts": "2026-11-13T04:30:00+00:00", "rid": "", "prompt": "uten rid"}],
        "user_b": [{"ts": "2026-11-13T04:37:30+00:00", "rid": "rid-1794544650000", "prompt": "B sin hemmelighet"}]}


class _Ltm:
    def __init__(self):
        self.asked = []

    def get_recent(self, limit=20, user_id=None):
        self.asked.append(user_id)
        return list(ROWS.get(user_id, [])) if user_id is not None else [r for v in ROWS.values() for r in v]


@pytest.fixture
def ltm(monkeypatch):
    fake = _Ltm()
    monkeypatch.setattr(executor, "get_ltm", lambda: fake, raising=False)
    monkeypatch.setattr(executor, "get_trace", lambda rid: {})
    monkeypatch.setattr(executor, "get_local_tz", lambda: __import__("datetime").timezone.utc)
    return fake


def _run(args, uid="user_a"):
    return asyncio.run(executor.execute_tool("self_trace", {**args, "_user_id": uid}))


def test_recent_lists_only_the_callers_own_rids(ltm):
    out = _run({"action": "recent"})
    assert "rid-1794544682015" in out and "rid-1794544619075" in out
    assert "rid-1794544650000" not in out and "hemmelighet" not in out
    assert ltm.asked == ["user_a"]
    assert out.index("rid-1794544682015") < out.index("rid-1794544619075")  # newest first
    assert "Slå av lyset" in out and "04:36:59" in out


def test_not_found_gives_the_nearest_real_rids_of_the_caller(ltm):
    out = _run({"action": "fetch_trace", "rid": "rid-1794544624154"})  # the guessed rid from the case
    assert "rid-1794544624154" in out
    near = out[out.index("rid-1794544624154") + 20:]
    assert near.index("rid-1794544619075") < near.index("rid-1794544682015")  # nearest first
    assert "rid-1794544650000" not in out  # user_b's rid is nearer in time but not theirs


def test_the_rid_note_names_self_trace(monkeypatch):
    for lang in ("nb", "en", "de"):
        note = t("rid_note", lang, rid="rid-1")
        assert "self_trace" in note and "inspect_system" not in note


def test_the_rid_note_carries_the_previous_turn_of_the_same_user(monkeypatch):
    monkeypatch.setattr(rg, "_LAST_RID", {})
    first = rg._rid_note("user_a", "rid-100", "nb")
    assert "rid-100" in first and "forrige" not in first.lower()
    rg._rid_note("user_b", "rid-150", "nb")
    second = rg._rid_note("user_a", "rid-200", "nb")
    assert "rid-200" in second and "rid-100" in second and "rid-150" not in second


# ── J30b (a): fetch_trace has an owner check — a user gets only their own rids, admin all, internal callers all ──

TRACE = {"stages": [{"ts": "x", "stage": "generate_in"}], "llm_calls": [], "tool_calls": []}


@pytest.fixture
def owned(ltm, monkeypatch):
    owners = {"rid-1794544650000": "user_b", "rid-1794544619075": "user_a"}
    monkeypatch.setattr(ltm, "owns_rid", lambda uid, rid: owners.get(rid) == uid, raising=False)
    monkeypatch.setattr(executor, "get_trace", lambda rid: TRACE if rid in owners else {})
    monkeypatch.setattr(executor, "format_trace_for_kare", lambda trace, lang="nb": "TRACE-BODY")
    monkeypatch.setattr(executor, "_user_role", lambda uid: {"boss": "admin"}.get(uid, "adult"), raising=False)


def _fetch(rid, uid, source="gui"):
    args = {"action": "fetch_trace", "rid": rid, "_user_id": uid}
    if source is not None:
        args["_source"] = source
    return asyncio.run(executor.execute_tool("self_trace", args))


def test_a_user_never_gets_another_users_trace(owned):
    out = _fetch("rid-1794544650000", "user_a")  # user_b's rid
    assert "TRACE-BODY" not in out
    assert out.startswith(t("inspiser_system_hent_trace_ikke_funnet", "nb", rid="rid-1794544650000"))  # same as missing
    assert all("rid-1794544650000" not in line for line in out.splitlines()[1:])  # never offered as a suggestion


def test_a_user_gets_their_own_trace(owned):
    assert _fetch("rid-1794544619075", "user_a") == "TRACE-BODY"


def test_admin_gets_every_trace(owned):
    assert _fetch("rid-1794544650000", "boss") == "TRACE-BODY"


def test_internal_callers_without_a_chat_source_get_every_trace(owned):
    assert _fetch("rid-1794544650000", "kare", source=None) == "TRACE-BODY"  # the dev meeting
    assert _fetch("rid-1794544650000", "global", source="meet") == "TRACE-BODY"  # the dev slot


def test_an_unknown_voice_user_global_in_chat_gets_nothing(owned):
    assert "TRACE-BODY" not in _fetch("rid-1794544650000", "global", source="stt")
