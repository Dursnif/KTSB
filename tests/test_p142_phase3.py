"""P142 Phase 3 — more callers in the box: Mechanic, Mechanic's one-shot summary, the dev slot.

Mode per caller («profile:detector»): a caller attached later starts in shadow, so existing rules
are measured there before they may correct it, without weakening chat. Two new rules from P117:
a tool call written as text (C24, also the 27B in chat — N27) and files/paths no source contains
(C19). Both shadow by default."""

import asyncio
import inspect
from contextlib import asynccontextmanager

import pytest

import kaare_core.dev_slot.steps as steps
import kaare_core.model_enforcement as me
import kaare_core.tools.executor_agents as ea
from kaare_core.agents.mechanic import tools as mech
from kaare_core.model_enforcement import (
    CALL_RAN,
    MODE_ON,
    MODE_SHADOW,
    ModelEnforcer,
    ToolCallAsTextDetector,
    UngroundedPathDetector,
)
from kaare_core.tools.i18n import t


@pytest.fixture
def events(monkeypatch):
    out: list[tuple] = []
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: out.append(a[:3]))
    return out


def _modes(enf):
    return {d.name: enf.mode(d) for d in enf._detectors}


# ── Modes per caller ────────────────────────────────────────────────────────


def test_proven_callers_unchanged_new_callers_in_shadow():
    chat = _modes(ModelEnforcer.for_profile("chat", "nb"))
    # DT04-s03: tool_call_as_text was shadow; markup-as-text reached users on a fresh install → now ON
    assert chat.pop("tool_call_as_text") == MODE_ON, "proven by DT04 R2 2.16 — corrects by default"
    assert chat.pop("action_claim") == MODE_SHADOW, "new rule (Phase 4) starts in shadow"
    assert chat.pop("visual_claim_no_image") == MODE_SHADOW, "new rule (R140) starts in shadow"
    assert set(chat.values()) == {MODE_ON}
    for profile in ("mechanic", "mechanic_summary", "dev_slot", "reflection_topic"):
        assert set(_modes(ModelEnforcer.for_profile(profile, "nb")).values()) == {MODE_SHADOW}


def test_override_is_per_caller(monkeypatch):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"mechanic:foreign_script": "on"})
    assert _modes(ModelEnforcer.for_profile("mechanic", "nb"))["foreign_script"] == MODE_ON
    assert _modes(ModelEnforcer.for_profile("dev_slot", "nb"))["foreign_script"] == MODE_SHADOW


def test_plain_name_does_not_switch_a_profile(monkeypatch):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"tool_promise": "off"})
    assert _modes(ModelEnforcer.for_profile("chat", "nb"))["tool_promise"] == MODE_ON


# ── tool_call_as_text (C24) ─────────────────────────────────────────────────


def _as_text(text, offered=("utforsk", "ha_control")):
    return ToolCallAsTextDetector().matches(
        text_out=text, used_tools=[], meta={"offered_tools": list(offered)}, lang="nb"
    )


@pytest.mark.parametrize(
    "text",
    [
        "<tool_call>\n<function=utforsk>\n<parameter=action>les</parameter>",
        'Jeg sjekker: {"name": "utforsk", "arguments": {"action": "les"}}',
        '{"name": "ha_control", "parameters": {"action": "turn_on"}}',
    ],
)
def test_written_call_is_caught(text):
    assert _as_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "FORSLAG: `utforsk` loggen i morgen.",
        '{"name": "Ola", "arguments": "ingen"}',  # not an offered tool
        "Lyset er slått på.",
    ],
)
def test_prose_is_not_a_call(text):
    assert not _as_text(text)


def test_no_offered_tools_never_fires():
    assert not _as_text("<tool_call>", offered=())


def test_enforcer_passes_offered_tools(events):
    enf = ModelEnforcer("nb", rid="r", detectors=[ToolCallAsTextDetector()], modes={})
    enf.offer_tools(["utforsk"])
    # DT04-s03: default is now correction (was shadow → None); a retry message comes back
    assert enf.on_answer("<function=utforsk>") is not None, "corrects by default"
    assert events == [("r", "tool_call_as_text", "retry")]


# ── ungrounded_path (C19) ───────────────────────────────────────────────────


def _ungrounded(text, grounding):
    d = UngroundedPathDetector()
    return d, d.matches(text_out=text, used_tools=[], meta={"grounding_text": grounding}, lang="nb")


def test_invented_file_is_caught():
    d, hit = _ungrounded("Jeg fant feil i kaare.log og /kaare/llm_fallback.py.", "file not found")
    assert hit and d.matched_phrase == "kaare.log, /kaare/llm_fallback.py"


def test_file_from_a_source_is_fine():
    _d, hit = _ungrounded(
        "Feilen står i /kaare/logs/route_decisions.log.",
        "tail of route_decisions.log: ...",  # base name is enough
    )
    assert not hit


def test_no_grounding_never_fires():
    _d, hit = _ungrounded("Se kaare.log.", "")
    assert not hit


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_resolve_adds_an_honest_note_when_it_survives(lang):
    d, _ = _ungrounded("Se kaare.log.", "nothing")
    out = d.resolve(original_text="", new_text="Se fortsatt kaare.log.", call_results=None, lang=lang)
    assert out.endswith(t("gen_ungrounded_path_note", lang, items="kaare.log"))
    clean = d.resolve(original_text="", new_text="Fant ingenting.", call_results=None, lang=lang)
    assert clean == "Fant ingenting."


def test_grounding_is_kept_only_when_a_rule_needs_it():
    chat = ModelEnforcer.for_profile("chat", "nb")
    chat.record_call("ha_read", {}, CALL_RAN, "x" * 1000)
    assert chat._grounding == [], "chat holds no copy of tool output"
    mech_enf = ModelEnforcer.for_profile("mechanic", "nb")
    mech_enf.record_call("utforsk", {}, CALL_RAN, "kaare_api.py")
    assert mech_enf._grounding == ["kaare_api.py"]


# ── Mechanic tool loop ──────────────────────────────────────────────────────


def _fake_mechanic(monkeypatch, replies):
    sent: list[dict] = []

    async def fake_post_chat(client, url, payload, **kwargs):
        sent.append(payload)
        return next(replies)

    @asynccontextmanager
    async def fake_lock(*args, **kwargs):
        yield "http://127.0.0.1:11447"

    async def fake_execute(name, args, lang=None):
        return "route_decisions.log: 3 lines"

    monkeypatch.setattr(mech, "post_chat", fake_post_chat)
    monkeypatch.setattr(mech, "lock_agent_pool", fake_lock)
    monkeypatch.setattr(mech, "execute_tool", fake_execute)
    monkeypatch.setattr(mech, "_log_tool_call", lambda *a, **k: None)
    return sent


def _call(name):
    return {"function": {"name": name, "arguments": {"action": "les", "path": "/kaare/logs"}}}


def test_mechanic_shadow_changes_nothing(monkeypatch, events):
    replies = iter([{"message": {"content": "Feil i kaare.log.", "tool_calls": []}}])
    sent = _fake_mechanic(monkeypatch, replies)
    out = asyncio.run(
        mech.ask_with_tools(
            [{"role": "user", "content": "sjekk"}], tools=mech._tools_by_name("utforsk"), lang="nb"
        )
    )
    assert out == "Feil i kaare.log." and len(sent) == 1
    assert ("", "ungrounded_path", "shadow") in events


def test_mechanic_on_corrects_once_and_notes_what_survives(monkeypatch, events):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"mechanic:ungrounded_path": "on"})
    replies = iter(
        [
            {"message": {"content": "", "tool_calls": [_call("utforsk")]}},
            {"message": {"content": "Feil i kaare.log og route_decisions.log.", "tool_calls": []}},
            {"message": {"content": "Feil i kaare.log.", "tool_calls": []}},
        ]
    )
    sent = _fake_mechanic(monkeypatch, replies)
    out = asyncio.run(
        mech.ask_with_tools(
            [{"role": "user", "content": "sjekk"}],
            tools=mech._tools_by_name("utforsk"),
            lang="nb",
            rid="r",
        )
    )
    assert len(sent) == 3
    correction = sent[2]["messages"][-1]["content"]
    assert "kaare.log" in correction and "route_decisions.log" not in correction
    assert out.endswith(t("gen_ungrounded_path_note", "nb", items="kaare.log"))
    assert ("r", "ungrounded_path", "retry") in events


def test_mechanic_answer_round_after_correction_with_calls(monkeypatch):
    monkeypatch.setattr(me, "get_enforcer_modes", lambda: {"mechanic:ungrounded_path": "on"})
    replies = iter(
        [
            {"message": {"content": "Feil i kaare.log.", "tool_calls": []}},
            {"message": {"content": "", "tool_calls": [_call("utforsk")]}},  # checks with a tool
            {"message": {"content": "Fant den i route_decisions.log.", "tool_calls": []}},
        ]
    )
    sent = _fake_mechanic(monkeypatch, replies)
    out = asyncio.run(
        mech.ask_with_tools(
            [{"role": "user", "content": "sjekk"}], tools=mech._tools_by_name("utforsk"), lang="nb"
        )
    )
    assert out == "Fant den i route_decisions.log."
    assert sent[2]["tools"] == [], "the answer round has no tools (N24 A2)"


# ── Wiring of the other callers ─────────────────────────────────────────────


def test_summary_and_dev_slot_use_their_profiles():
    summary = inspect.getsource(ea._mechanic_search)
    assert 'ModelEnforcer.for_profile("mechanic_summary"' in summary
    assert "enforcer.add_grounding(user_msg)" in summary and "enforcer.finish(" in summary
    slot = inspect.getsource(steps.kare_json)
    assert 'ModelEnforcer.for_profile("dev_slot"' in slot
    assert "enforcer.record_call(" in slot and "enforcer.on_answer(" in slot


# ── Test isolation (FIX-log 2026-10-22) ─────────────────────────────────────


def test_tests_never_write_the_live_enforcement_log():
    # conftest.py redirects the log for every test; without it this test is red.
    assert me.Path("/kaare/logs/enforcement.log") != me._ENFORCE_LOG
