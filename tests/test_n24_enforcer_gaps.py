"""N24 — ModelEnforcer gaps found after DT03 R8: false claims that survived a retry, «✓» on a
failed write, sentences that slipped through, and questions/offers that forced unwanted writes."""

import pytest

import kaare_core.model_enforcement as me
from kaare_core.model_enforcement import (
    ClaimedDoneDetector,
    ModelEnforcer,
    ToolPromiseDetector,
    tool_usage_tags,
)

_REAL_LOG = me._log_enforcement  # captured before the autouse fixture replaces it


@pytest.fixture(autouse=True)
def _no_enforce_log(monkeypatch):
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)


def _fires(detector, text, used_tools=()):
    return detector.matches(text_out=text, used_tools=list(used_tools), meta={}, lang="nb")


# ── A1 / A2: the original claim never reaches the user ─────────────────────


def test_a1_text_only_retry_uses_new_answer():
    enf = ModelEnforcer("nb", rid="r")
    assert enf.check(text_out="Jeg har notert det. [LANG:nb]", used_tools=[], meta={})
    out = enf.resolve("Beklager, det ble ikke notert.", None)
    assert out == "Beklager, det ble ikke notert."


def test_a2_no_check_mark_after_tool_round():
    enf = ModelEnforcer("nb", rid="r")
    enf.check(text_out="Jeg har notert det.", used_tools=[], meta={})
    out = enf.resolve("Notatet kunne ikke lagres: listen finnes ikke.", None)
    assert "✓" not in out
    assert out.startswith("Notatet kunne ikke lagres")


def test_a1_empty_answer_round_never_returns_the_claim():
    enf = ModelEnforcer("nb", rid="r")
    enf.check(text_out="Jeg har notert det.", used_tools=[], meta={})
    out = enf.resolve("", None)
    assert "notert det" not in out
    assert out


def test_claimed_done_resolve_uses_new_answer():
    enf = ModelEnforcer("nb", rid="r")
    action = enf.check(text_out="Ferdig – skrevet til lessons.", used_tools=[], meta={})
    assert action and action.detector_name == "claimed_done"
    assert enf.resolve("Det ble ikke skrevet ennå.", None) == "Det ble ikke skrevet ennå."


# ── Safety net: the three R8 phrases and their kin ─────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Ferdig – skrevet til lessons.",
        "Lagt på agendaen som sak #4 — tema: system.",
        "Markert som løst med konklusjon.",
        "Det har jeg notert.",
        "Notert!",
        "Nå har jeg lagt det til på handlelista.",
        "Noted!",
        "I have added it to my list.",
        "I’ve saved that to my notes.",
        "Done — marked as resolved.",
        "Hab ich notiert.",
        "Erledigt – auf die Agenda eingetragen.",
    ],
)
def test_claimed_done_fires_without_tools(text):
    assert _fires(ClaimedDoneDetector(), text)


@pytest.mark.parametrize(
    "text",
    [
        "Notert!",
        "Ferdig – skrevet til lessons.",
    ],
)
def test_claimed_done_silent_when_a_tool_ran(text):
    assert not _fires(ClaimedDoneDetector(), text, used_tools=["note"])


@pytest.mark.parametrize(
    "text",
    [
        # state, not a new write (hovedøkta's measured false hit)
        "Ja, en tilhenger var allerede registrert, og den er notert fra før.",
        "Det er lagret i minnet ditt.",
        "That is already saved in your profile.",
        # questions / offers / conditions
        "Skal jeg notere det?",
        "Vil du at jeg legger det til på handlelista?",
        "Hvis du vil, kan det bli lagt til på agendaen.",
        "Should I add it to my list?",
        # negation close to the claim
        "Det er ikke notert noe om det.",
        "Ich habe es nicht notiert.",
        # the user did it
        "Du har lagt til melk på handlelista.",
        # device state is not a write verb (house state is in the context)
        "Lyset i stua er slått på.",
    ],
)
def test_claimed_done_does_not_fire(text):
    assert not _fires(ClaimedDoneDetector(), text)


# ── Tool-promise rules: false positives that forced unwanted writes ────────


@pytest.mark.parametrize(
    "text",
    [
        "Skal jeg sette en timer?",
        "Vil du at jeg noterer det?",
        "Ja, jeg husker det godt – du sa det i går.",
        "Jeg følger opp hvis du vil.",
        "Should I set a timer?",
        "Jeg noterte det ikke.",
    ],
)
def test_tool_promise_does_not_fire(text):
    assert not _fires(ToolPromiseDetector(), text)


@pytest.mark.parametrize(
    "text",
    [
        "Jeg har notert det.",
        "I’ve added it to my list.",
        "Jeg setter en timer på 10 minutter.",
        "Jeg tar det opp på neste dev-møte.",
        "Klart. Jeg har notert det, og så sjekker vi i morgen.",
    ],
)
def test_tool_promise_still_fires(text):
    assert _fires(ToolPromiseDetector(), text)


def test_matched_phrase_recorded():
    det = ToolPromiseDetector()
    assert _fires(det, "Greit. Jeg har notert det.")
    assert "notert" in det.matched_phrase.lower()
    net = ClaimedDoneDetector()
    assert _fires(net, "Lagt på agendaen som sak #4.")
    assert net.matched_phrase.lower().startswith("lagt")


def test_phrase_goes_to_enforcement_log(monkeypatch):
    seen = []
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: seen.append(a))
    ModelEnforcer("nb", rid="r").check(text_out="Notert!", used_tools=[], meta={})
    assert seen and seen[0][1] == "claimed_done" and seen[0][3].lower() == "notert"


def test_enforcement_log_line_has_phrase(monkeypatch):
    written = []
    monkeypatch.setattr(me.log_buffer, "write", lambda path, rec: written.append(rec))
    _REAL_LOG("r", "claimed_done", "retry", "notert")
    _REAL_LOG("r", "claimed_done", "resolved")
    assert written[0]["phrase"] == "notert"
    assert "phrase" not in written[1]


def test_language_tag_and_curly_apostrophe_do_not_hide_a_claim():
    assert _fires(ToolPromiseDetector(), "I’ve noted that. [LANG:en]")


# ── A3: only executed calls count (tags unchanged, router decides) ─────────


def test_tool_usage_tags_unchanged():
    assert tool_usage_tags("note", {"list_name": "agenda"}) == ["note", "note:agenda"]
    assert tool_usage_tags("timer", {}) == ["timer"]


@pytest.mark.parametrize(
    "text",
    [
        # replay finds (house corpus 2026-10-10): earlier writes described as state
        "Samme ustabilitet som har pågått siden august; det er allerede notert til neste dev-møte.",
        "ARGUS er allerede fanget i huskelisten min (ba9266dd).",
        # the old note rule forced a write on this apology (interactions.db, 2026-05-19)
        "Unnskyld! Det var en feil fra meg. Takk for at du rettet opp i meg – jeg husker det nå.",
    ],
)
def test_replay_false_hits_stay_silent(text):
    assert not _fires(ToolPromiseDetector(), text)
    assert not _fires(ClaimedDoneDetector(), text)
