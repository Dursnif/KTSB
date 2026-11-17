"""P144 R140 / run-queue J8 — Kåre says he sees something although no image reached him in that turn.

Shadow detector `visual_claim_no_image` in the chat profile: the fact comes from code (did the turn carry an image —
the user's own or an image tool's `[VISION:]` round — read from the turn, not from the text); only the visual claim is
recognised in the text, with expressions from i18n (nb/en/de). It logs the rid and the hit, never the answer's text.
Shadow by default; turned on only after Userone has seen the numbers."""

from kaare_core import model_enforcement as me
from kaare_core.tools.i18n import t


def _det():
    return me.VisualClaimWithoutImageDetector()


def test_claim_without_image_matches_in_each_language():
    for lang, text in (("nb", "Jeg ser tydelig dugg på bildet."), ("en", "I can see the bins in the picture."),
                       ("de", "Ich sehe die Tonnen auf dem Bild.")):
        assert _det().matches(text_out=text, used_tools=[], meta={"turn_has_image": False}, lang=lang), lang


def test_with_an_image_in_the_turn_nothing_fires():
    assert not _det().matches(text_out="Jeg ser tydelig dugg på bildet.", used_tools=[],
                              meta={"turn_has_image": True}, lang="nb")


def test_no_visual_claim_nothing_fires():
    assert not _det().matches(text_out="Frigate meldte en søppeldunk kl. 05:24 (83 %).", used_tools=[],
                              meta={"turn_has_image": False}, lang="nb")


def test_missing_fact_never_fires():
    # Without the code fact (an old caller) the detector stays silent rather than guess from the text.
    assert not _det().matches(text_out="Jeg ser tydelig dugg på bildet.", used_tools=[], meta={}, lang="nb")


def test_never_logs_the_text():
    d = _det()
    d.matches(text_out="Jeg ser tydelig dugg på bildet.", used_tools=[], meta={"turn_has_image": False}, lang="nb")
    assert d.matched_phrase == ""


def test_shadow_in_chat_and_listed_for_the_gui():
    assert me.VisualClaimWithoutImageDetector.default_mode == me.MODE_SHADOW
    assert any(isinstance(d, me.VisualClaimWithoutImageDetector) for d in me.default_detectors())
    names = {row["name"] for row in me.detector_catalog()}
    assert "visual_claim_no_image" in names


def test_phrases_and_correction_come_from_i18n():
    for lang in ("nb", "en", "de"):
        assert t("enf_visual_claim_phrases", lang) != "enf_visual_claim_phrases"
        assert t("gen_correction_visual_claim", lang) != "gen_correction_visual_claim"


def test_chat_loop_hands_the_turn_fact_to_the_enforcer():
    # PLC0415: imports the whole chat stack — only this test needs it
    from kaare_core.routers import router_generate as rg  # noqa: PLC0415
    with open(rg.__file__) as f:
        src = f.read()
    assert '"turn_has_image": _turn_has_image' in src
    assert "_turn_has_image = bool(images)" in src and "_turn_has_image = True" in src
