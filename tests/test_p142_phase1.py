"""P142 Phase 1 — the enforcer is one block per request.

Callers run their own loop; the enforcer owns every decision: which detectors run (profile), what
counts as a used tool (call ledger, D8), which correction is sent, whether an answer-only round is
needed, and the final reply (never empty). Behaviour must be unchanged from before the move."""

import inspect

import pytest

import kaare_core.model_enforcement as me
import kaare_core.reflection_loop as rl
import kaare_core.routers.router_generate as rg
import kaare_core.tools.executor_library as el
from kaare_core.model_enforcement import (
    CALL_CORRECTED,
    CALL_ERROR,
    CALL_RAN,
    CALL_REFUSED,
    PROFILES,
    ModelEnforcer,
    default_detectors,
    grounded_detectors,
)
from kaare_core.tools.i18n import t as _t_i18n


@pytest.fixture(autouse=True)
def _no_enforce_log(monkeypatch):
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)


_PROMISE = "Jeg tar det opp på neste dev-møte."
_AGENDA_WRITE = {"action": "write", "list_name": "agenda"}


# ── Profiles ────────────────────────────────────────────────────────────────


def _names(enf):
    return [d.name for d in enf._detectors]


def test_profiles_match_the_old_detector_sets():
    assert _names(ModelEnforcer.for_profile("chat", "nb")) == [d.name for d in default_detectors()]
    assert _names(ModelEnforcer.for_profile("reflection", "nb")) == ["tool_promise"]
    assert _names(ModelEnforcer.for_profile("grounded", "nb")) == [
        d.name for d in grounded_detectors()
    ]


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_profile_gives_fresh_instances(profile):
    a = ModelEnforcer.for_profile(profile, "nb")
    b = ModelEnforcer.for_profile(profile, "nb")
    assert not {id(d) for d in a._detectors} & {id(d) for d in b._detectors}


def test_unknown_profile_fails_loudly():
    with pytest.raises(KeyError):
        ModelEnforcer.for_profile("no-such-caller", "nb")


# ── Call ledger (D8) ────────────────────────────────────────────────────────


def test_ledger_keeps_facts_and_only_ran_calls_count():
    enf = ModelEnforcer.for_profile("chat", "nb")
    enf.record_call("note", _AGENDA_WRITE, CALL_REFUSED)
    enf.record_call("note", _AGENDA_WRITE, CALL_CORRECTED)
    enf.record_call("note", _AGENDA_WRITE, CALL_ERROR)
    assert enf.used_tools == []
    enf.record_call("note", _AGENDA_WRITE, CALL_RAN)
    assert enf.used_tools == ["note", "note:write", "note:agenda"]  # «:write» since Phase 4 (A4)
    last = enf.calls[-1]
    assert (last.tool, last.action, last.list_name, last.status) == ("note", "write", "agenda", "ran")


def test_ledger_tolerates_bad_args():
    enf = ModelEnforcer.for_profile("chat", "nb")
    enf.record_call("timer", None, CALL_RAN)
    enf.record_call("timer", "not a dict", CALL_RAN)  # type: ignore[arg-type]
    assert enf.used_tools == ["timer", "timer:write", "timer", "timer:write"]  # no action = write (A4)


def test_on_answer_judges_by_the_ledger():
    enf = ModelEnforcer.for_profile("chat", "nb")
    enf.record_call("note", _AGENDA_WRITE, CALL_REFUSED)
    assert enf.on_answer(_PROMISE), "a refused call does not keep the promise"
    enf = ModelEnforcer.for_profile("chat", "nb")
    enf.record_call("note", _AGENDA_WRITE, CALL_RAN)
    assert enf.on_answer(_PROMISE) is None


# ── Correction flow ─────────────────────────────────────────────────────────


def test_correction_with_tools_left():
    action = ModelEnforcer.for_profile("chat", "nb").on_answer(_PROMISE)
    assert action.tools_left
    assert action.correction_message == _t_i18n("gen_promise_correction_agenda", "nb")


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_last_round_correction_asks_for_honest_answer(lang):
    action = ModelEnforcer.for_profile("chat", lang).on_answer(_PROMISE, final_round=True)
    assert not action.tools_left
    assert action.correction_message == _t_i18n("gen_correction_no_tools_left", lang)


def test_only_one_correction_per_request():
    enf = ModelEnforcer.for_profile("chat", "nb")
    assert enf.on_answer(_PROMISE)
    assert enf.on_answer(_PROMISE) is None


def test_answer_round_only_after_calls_in_the_correction_round():
    enf = ModelEnforcer.for_profile("chat", "nb")
    enf.record_call("ha_read", {"action": "status"}, CALL_RAN)  # before the correction
    assert not enf.needs_answer_round()
    assert enf.on_answer(_PROMISE)
    assert not enf.needs_answer_round(), "earlier calls do not count"
    enf.record_call("note", _AGENDA_WRITE, CALL_RAN)
    assert enf.needs_answer_round()
    assert enf.finish("Lagt på agendaen.") == "Lagt på agendaen."
    assert not enf.needs_answer_round(), "resolved once"


# ── finish(): never an empty reply ──────────────────────────────────────────


@pytest.mark.parametrize("raw", ["", "  ", "[LANG:nb]"])
def test_finish_without_correction_never_empty(raw):
    enf = ModelEnforcer.for_profile("chat", "nb")
    assert enf.finish(raw) == _t_i18n("gen_no_response", "nb")
    assert enf.finish(raw, "gen_timeout") == _t_i18n("gen_timeout", "nb")


def test_finish_without_correction_keeps_text_and_tag_for_the_router():
    # The router strips the tag itself on every exit path; finish() must not change normal text.
    assert ModelEnforcer.for_profile("chat", "nb").finish("Hei! [LANG:nb]") == "Hei! [LANG:nb]"


def test_finish_after_correction_resolves():
    enf = ModelEnforcer.for_profile("chat", "nb")
    enf.on_answer("Svar.\n" + _PROMISE)
    assert enf.finish("[LANG:nb]") == "Svar."


# ── The glue lives in the block, not in the callers ─────────────────────────


def test_router_has_no_own_enforcement_glue():
    src = inspect.getsource(rg.handle_generate)
    assert 'ModelEnforcer.for_profile("chat"' in src
    for old in ("used_tools", "enforcer.check(", "enforcer.resolve(", "tool_usage_tags("):
        assert old not in src, f"router re-implements the block: {old}"
    assert "enforcer.record_call(" in src and "enforcer.needs_answer_round()" in src
    assert 'enforcer.finish(text_out, "gen_timeout")' in src, "round 7 must resolve a pending correction"


def test_reflection_and_library_use_profiles():
    refl = inspect.getsource(rl)
    assert 'ModelEnforcer.for_profile("reflection"' in refl
    assert "used_tools" not in refl and "enforcer.check(" not in refl
    assert "enforcer.record_call(name, args, CALL_ERROR)" in refl
    lib = inspect.getsource(el)
    assert 'ModelEnforcer.for_profile("grounded"' in lib and "enforcer.check(" not in lib
