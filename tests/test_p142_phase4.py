"""P142 Phase 4 — coverage (merged N24 November part, R6).

A4: a call only covers a claim when it was the right kind of action (a note *read* does not make
«Jeg har notert det» true). A5: one correction per class of problem, not one per request — a
language correction must not leave the corrected answer unchecked for false claims."""

import pytest

import kaare_core.model_enforcement as me
from kaare_core.model_enforcement import CALL_RAN, ModelEnforcer
from kaare_core.tools import trace_reader


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)


def _chat(*calls):
    enf = ModelEnforcer.for_profile("chat", "nb", rid="r")
    for name, args in calls:
        enf.record_call(name, args, CALL_RAN)
    return enf


# ── A4: the right action, not just the tool name ────────────────────────────


@pytest.mark.parametrize(
    ("claim", "call"),
    [
        ("Jeg har notert det.", ("note", {"action": "read", "list_name": "shopping"})),
        ("Jeg tar det opp på neste dev-møte.", ("note", {"action": "read", "list_name": "agenda"})),
        ("Jeg har satt en timer på 10 minutter.", ("timer", {"action": "list"})),
        ("Jeg har satt husstanden til borte.", ("household", {"action": "get_status"})),
    ],
)
def test_a_read_does_not_cover_a_write_claim(claim, call):
    action = _chat(call).on_answer(claim)
    assert action and action.detector_name == "tool_promise"


@pytest.mark.parametrize(
    ("claim", "call"),
    [
        ("Jeg har notert det.", ("note", {"action": "write", "list_name": "kare"})),
        ("Jeg har notert det.", ("note", {"action": "edit", "list_name": "kare"})),
        ("Jeg tar det opp på neste dev-møte.", ("note", {"action": "write", "list_name": "agenda"})),
        ("Jeg har satt en timer på 10 minutter.", ("timer", {"action": "set"})),
        ("Jeg har satt husstanden til borte.", ("household", {"action": "set_away"})),
        ("Jeg har notert det.", ("note", {"list_name": "kare"})),  # no action given: never punished
    ],
)
def test_the_right_action_covers_it(claim, call):
    assert _chat(call).on_answer(claim) is None


# ── A5: one correction per class ────────────────────────────────────────────


def test_corrected_language_answer_is_still_checked_for_claims():
    enf = _chat()
    first = enf.on_answer("Hallo, alles gut hier. [LANG:de]")  # wrong language for nb, no claim
    assert first and first.detector_name == "language_mismatch"
    second = enf.on_answer("Jeg har notert det. [LANG:nb]")  # right language, false claim
    assert second and second.detector_name in ("tool_promise", "claimed_done")


def test_same_class_is_corrected_only_once():
    enf = _chat()
    assert enf.on_answer("Jeg har notert det.")
    assert enf.on_answer("Jeg har notert det igjen.") is None


def test_at_most_two_corrections_per_request():
    enf = _chat()
    assert enf.on_answer("Hallo, alles gut hier. [LANG:de]")  # language
    assert enf.on_answer("Jeg har notert det. [LANG:nb]")  # claim
    assert enf.on_answer("Partial reply <tool_call> [LANG:nb]") is None  # cap reached


# Replay find (2026-10-22): a reflection that *read* its list and wrote «huskelisten min sier …»
# hit the note rule — the old any-call tag hid it. Naming the list is not a claim of a write.
@pytest.mark.parametrize(
    "text",
    [
        "Ingen endringer — og huskelisten min sier at agenda #864 står.",
        "Min huskeliste er tom i dag.",
        "Det står på lista mi fra før.",
    ],
)
def test_naming_my_list_is_not_a_write_claim(text):
    assert _chat(("note", {"action": "read", "list_name": "kare"})).on_answer(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Notatet er lagret på min huskeliste.",
        "Jeg har lagt det på huskelisten min.",
        "Skrevet på lista mi.",
    ],
)
def test_writing_to_my_list_is_still_a_claim(text):
    assert _chat(("note", {"action": "read", "list_name": "kare"})).on_answer(text)


# ── action_claim: per-tool claims judged by the call ledger (shadow by default) ──

_ON = {"chat:action_claim": "on"}


def _chat_on(*calls):
    enf = ModelEnforcer.for_profile("chat", "nb", rid="r")
    enf._modes = dict(_ON)
    for name, args in calls:
        enf.record_call(name, args, CALL_RAN)
    return enf


@pytest.mark.parametrize(
    ("claim", "right_call"),
    [
        ("Jeg har slått på lyset i stua.", ("ha_control", {"action": "turn_on"})),
        ("Jeg skrudde av radioen på kjøkkenet.", ("ha_control", {"action": "turn_off"})),
        ("I've turned off the hallway light.", ("ha_control", {"action": "turn_off"})),
        ("Ich habe das Licht eingeschaltet.", ("ha_control", {"action": "turn_on"})),
        ("Timeren er satt til 10 minutter.", ("timer", {"action": "set"})),
        ("Påminnelse satt kl 15.", ("timer", {"action": "set"})),
        ("Jeg har bedt Mechanic se på det.", ("mechanic", {"action": "delegate"})),
        ("Jeg har oppdatert selvbildet mitt.", ("self_image", {"action": "update"})),
        ("Sagt fra på høyttaleren.", ("announce", {"action": "say"})),
        ("Bekreftet forslag #3.", ("dev_proposals", {"action": "confirm"})),
        ("Jeg har markert den som løst.", ("note", {"action": "resolve"})),
        ("Festet øverst på lista.", ("note", {"action": "pin"})),
        ("Ich habe es mir gemerkt.", ("note", {"action": "write"})),
    ],
)
def test_action_claim_needs_the_right_call(claim, right_call):
    enf = _chat_on()
    action = enf.on_answer(claim)
    assert action and action.detector_name == "action_claim", claim
    assert _chat_on(right_call).on_answer(claim) is None, claim


@pytest.mark.parametrize(
    "text",
    [
        "Lyset i stua er nå slått på.",  # state from home awareness, true without a call (N-D4)
        "Skal jeg slå på lyset i stua?",
        "Jeg har ikke slått på lyset.",
        "Lyset var allerede slått på.",
        "Det spiller NRK P1 på kjøkkenet nå.",  # media state
    ],
)
def test_state_questions_and_negations_are_not_claims(text):
    assert _chat_on().on_answer(text) is None


def test_action_claim_is_shadow_by_default():
    enf = ModelEnforcer.for_profile("chat", "nb", rid="r")
    assert enf.on_answer("Jeg har slått på lyset i stua.") is None


# ── Kåre's own trace shows what the box matched and how it ended (N24 Result gap) ──


def test_trace_shows_phrase_and_outcome():
    parts = {"enforcer_hits": []}
    trace_reader._add_enforce(
        parts, {"ts": "t", "detector": "tool_promise", "event": "retry", "phrase": "neste dev-møte"}
    )
    trace_reader._add_enforce(
        parts, {"ts": "t", "detector": "tool_promise", "event": "resolved", "outcome": "kept_original"}
    )
    assert parts["enforcer_hits"][0]["phrase"] == "neste dev-møte"
    assert parts["enforcer_hits"][1]["outcome"] == "kept_original"


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_kare_sees_corrections_in_his_own_trace(lang):
    trace = {
        "rid": "r",
        "enforcer_hits": [
            {"ts": "t", "detector": "tool_promise", "event": "retry", "phrase": "neste dev-møte", "outcome": ""},
            {"ts": "t", "detector": "tool_promise", "event": "resolved", "phrase": "", "outcome": "kept_original"},
            {"ts": "t", "detector": "action_claim", "event": "shadow", "phrase": "slått på", "outcome": ""},
        ],
    }
    out = trace_reader.format_trace_for_kare(trace, lang)
    assert "tool_promise" in out and "neste dev-møte" in out and "kept_original" in out
    assert "action_claim" in out and "shadow" in out
