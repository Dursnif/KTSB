"""P142 Phase 0 — the enforcer must never leave the user with an empty reply.

2026-10-22 (rid-1792664909839): the agenda rule fired on a description of already-tracked items,
the retry answered with only «[LANG:nb]», the tag passed the emptiness check, and the user got an
empty chat bubble instead of a good answer."""

import inspect

import pytest

import kaare_core.model_enforcement as me
import kaare_core.routers.router_generate as rg
from kaare_core.model_enforcement import (
    ModelEnforcer,
    ToolPromiseDetector,
    TruncatedThinkDetector,
    mentions_agenda_promise,
)
from kaare_core.tools.i18n import t as _t_i18n


@pytest.fixture(autouse=True)
def _capture_enforce_log(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: events.append((*a, k.get("outcome", ""))))
    return events


_TRACKED = "Alt det andre (48 forslag til) ligger sporet med fast nummer til neste dev-møte."


def _fires(text, used_tools=()):
    return ToolPromiseDetector().matches(text_out=text, used_tools=list(used_tools), meta={}, lang="nb")


# ── Agenda rule: descriptions of tracked items are not promises ─────────────


@pytest.mark.parametrize(
    "text",
    [
        _TRACKED,
        "Resten står på listen til neste dev-møte.",
        "The other 48 proposals are tracked for the next dev meeting.",
        "Die übrigen Vorschläge liegen für das nächste Entwicklertreffen bereit.",
    ],
)
def test_tracked_items_do_not_trigger_agenda_rule(text):
    assert not _fires(text)
    assert not mentions_agenda_promise(text)


@pytest.mark.parametrize(
    "text",
    [
        "Jeg tar det opp på neste dev-møte.",
        "I'll raise this at the next dev meeting.",
        "Camera clock 12:21 vs 14:21 — check at the next dev meeting.",
    ],
)
def test_real_agenda_promises_still_trigger(text):
    assert _fires(text)
    assert mentions_agenda_promise(text)


def test_promise_next_to_tracked_sentence_still_triggers():
    text = f"{_TRACKED} Kamerafeilen tar jeg opp på neste dev-møte."
    assert _fires(text)


# ── resolve(): never empty, never the claim itself ──────────────────────────


def _enforcer_on(original):
    enf = ModelEnforcer("nb", rid="r")
    assert enf.check(text_out=original, used_tools=[], meta={})
    return enf


def test_lang_tag_only_retry_keeps_rest_of_original(_capture_enforce_log):
    original = "Konklusjonene:\n- Musikken: en HA-automatisering. (#86)\nJeg tar det opp på neste dev-møte. [LANG:nb]"
    out = _enforcer_on(original).resolve("[LANG:nb]", None)
    assert "Musikken: en HA-automatisering" in out
    assert "neste dev-møte" not in out, "the unkept promise itself must not survive"
    assert "[LANG" not in out
    assert ("r", "tool_promise", "resolved", "kept_original") in _capture_enforce_log


def test_retry_text_still_wins():
    out = _enforcer_on("Jeg tar det opp på neste dev-møte.").resolve("Lagt på agendaen. [LANG:nb]", None)
    assert out == "Lagt på agendaen."


@pytest.mark.parametrize("retry", ["", "   ", "[LANG:nb]", None])
def test_claim_only_original_gives_no_response_text(retry, _capture_enforce_log):
    out = _enforcer_on("Jeg har notert det.").resolve(retry, None)
    assert out == _t_i18n("gen_no_response", "nb")
    assert ("r", "tool_promise", "resolved", "no_response") in _capture_enforce_log


def test_unusable_original_is_never_kept():
    enf = ModelEnforcer("nb", rid="r", detectors=[TruncatedThinkDetector()])
    assert enf.check(text_out="", used_tools=[], meta={"think_truncated": True})
    assert enf.resolve("[LANG:nb]", None) == _t_i18n("gen_no_response", "nb")


# ── Correction texts allow an honest text answer ────────────────────────────

_TOOL_ONLY = ("Kun tool-kall", "Tool call only", "Nur Werkzeugaufruf", "tool call only", "nur Werkzeugaufruf")


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
@pytest.mark.parametrize(
    "key",
    [
        "gen_promise_correction_note",
        "gen_promise_correction_agenda",
        "gen_promise_correction_timer",
        "gen_promise_correction_household",
        "gen_promise_correction_generic",
    ],
)
def test_promise_corrections_allow_honest_answer(key, lang):
    text = _t_i18n(key, lang)
    assert not any(t in text for t in _TOOL_ONLY)
    assert {"nb": "ærlig", "en": "honestly", "de": "ehrlich"}[lang] in text


# ── Router glue ─────────────────────────────────────────────────────────────


def test_router_passes_raw_text_to_the_block():
    src = inspect.getsource(rg.handle_generate)
    # Phase 1: finish() wraps resolve(); it must still get the raw text, not a fallback string.
    assert "enforcer.finish(_raw_text)" in src
    assert 'text_out = _raw_text or _t_i18n("gen_no_response", lang)' not in src
