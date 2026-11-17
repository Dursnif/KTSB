"""DT04 R2 2.16: a tool call written as text is corrected by default, never shown as the answer."""

from kaare_core.model_enforcement import MODE_ON, ModelEnforcer, ToolCallAsTextDetector

MARKUP = "<tool_call><timer><seconds>40</seconds></timer></tool_call>"


def _enf():
    enf = ModelEnforcer.for_profile("chat", "nb", rid="dt04")
    enf.offer_tools(["timer", "note"])
    return enf


def test_default_mode_is_correction():
    assert ToolCallAsTextDetector().default_mode == MODE_ON
    enf = _enf()
    det = next(d for d in enf._detectors if d.name == "tool_call_as_text")
    assert enf.mode(det) == MODE_ON


def test_markup_gets_a_retry_message():
    action = _enf().on_answer(MARKUP)
    assert action is not None and action.correction_message


def test_retry_with_real_answer_is_used():
    enf = _enf()
    assert enf.on_answer(MARKUP)
    assert enf.resolve("Timeren er satt.", [("timer", "ok")]) == "Timeren er satt."


def test_retry_empty_gives_honest_not_done():
    enf = _enf()
    assert enf.on_answer(MARKUP)
    out = enf.resolve("", None)
    assert "tool_call" not in out and out.strip()


def test_retry_that_repeats_the_markup_is_never_shown():
    enf = _enf()
    assert enf.on_answer(MARKUP)
    out = enf.resolve(MARKUP, None)
    assert "<tool_call>" not in out


# 2026-11-16 retest: the 27B wrote the call as a bracketed line, no <tool_call> tag
import pytest  # noqa: E402


def _hit(text):
    return ToolCallAsTextDetector().matches(
        text_out=text, used_tools=[], meta={"offered_tools": ["note", "timer"]}, lang="nb"
    )


@pytest.mark.parametrize(
    "text",
    [
        "[Note tool call: action=read, list_name=architect]",
        "Jeg sjekker. [timer tool call: seconds=40]",
        "[Verktøykall: note action=read]",
        "[Tool-Aufruf: timer seconds=40]",
        "[tool_call: note action=read]",
    ],
)
def test_bracketed_call_is_caught(text):
    assert _hit(text)


@pytest.mark.parametrize(
    "text",
    [
        "Jeg bruker note-verktøyet for å lese listen.",
        "A tool call: that is what the model emits when it is confused.",
        "FORSLAG: `note` listen i morgen [se logg].",
        "Timeren er satt [40 sekunder].",
        "Et verktøykall kan feile.",
    ],
)
def test_prose_naming_a_tool_is_not_caught(text):
    assert not _hit(text)
