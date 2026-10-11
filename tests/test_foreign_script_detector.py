"""ForeignScriptDetector — a nb/en/de reply with characters from another writing system
(e.g. «rett각ige», «kjører da周期性 avting», seen live 2026-10-05) forces one rewrite."""

from kaare_core import log_buffer, model_enforcement
from kaare_core.model_enforcement import (
    ForeignScriptDetector,
    ModelEnforcer,
    default_detectors,
    foreign_script_chars,
)
from kaare_core.tools.i18n import t as _t


def _matches(text: str, lang: str = "nb") -> bool:
    return ForeignScriptDetector().matches(text_out=text, used_tools=[], meta={}, lang=lang)


def test_fires_on_cjk_inside_norwegian():
    assert _matches("Pumpa kjører da周期性 avting (defrost) — kort tid.")


def test_fires_on_hangul_and_cyrillic():
    assert _matches("den store, flate rett각ige gjenstanden")
    assert _matches("Lyset er slått på в stua.")


def test_fires_on_full_width_punctuation():
    assert _matches("Ferdig，lyset er på.")


def test_clean_norwegian_english_german_do_not_fire():
    assert not _matches("Blåbærsyltetøy på brødskiva — ærlig talt, øl og «fisk» går fint. 21,4 °C.")
    assert not _matches("The heat pump defrosts every 40–60 minutes. [LANG:en]", lang="en")
    assert not _matches("Größe, Übermaß und Fußbodenheizung — café, naïve.", lang="de")


def test_emoji_and_symbols_do_not_fire():
    assert not _matches("Gjort 👍 Lyset er på ✓ → 50 % • €12 © ™")


def test_non_latin_reply_language_is_skipped():
    # Only Latin-script reply languages are checked; a Russian reply is not "foreign".
    assert not _matches("Свет включён.", lang="ru")


def test_norwegian_aliases_are_latin_script():
    assert _matches("Lys på 周期性", lang="no")


def test_correction_message_names_chars_in_all_languages():
    det = ForeignScriptDetector()
    assert det.matches(text_out="da周期性 avting", used_tools=[], meta={}, lang="nb")
    for lang in ("nb", "en", "de"):
        msg = det.correction_message(lang)
        assert "周期性" in msg
        assert "[LANG:xx]" in msg
        assert msg == msg.strip() and msg.startswith("[SYSTEM:")
    assert _t("gen_correction_foreign_script", "nb") != _t("gen_correction_foreign_script", "en")


def test_resolve_prefers_rewrite_but_falls_back_to_original():
    det = ForeignScriptDetector()
    assert det.resolve(original_text="a周", new_text="a", call_results=None, lang="nb") == "a"
    assert det.resolve(original_text="a周", new_text="", call_results=None, lang="nb") == "a周"


def test_registered_last_in_default_detectors():
    names = [d.name for d in default_detectors()]
    assert names[-1] == "foreign_script"
    assert names.count("foreign_script") == 1


def test_enforcer_forces_exactly_one_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(model_enforcement, "_ENFORCE_LOG", tmp_path / "enforcement.log")
    enf = ModelEnforcer("nb", rid="rid-test-foreign")
    action = enf.check(text_out="Pumpa kjører da周期性 avting.", used_tools=[], meta={})
    assert action is not None and action.detector_name == "foreign_script"
    # second check in the same request must not fire again
    assert enf.check(text_out="fortsatt 周期性", used_tools=[], meta={}) is None
    assert (
        enf.resolve("Pumpa kjører da periodisk avising.", None)
        == "Pumpa kjører da periodisk avising."
    )
    log_buffer.flush()
    logged = [ln for ln in (tmp_path / "enforcement.log").read_text().splitlines() if ln]
    assert len(logged) == 2 and '"foreign_script"' in logged[0]


def test_foreign_script_chars_helper():
    assert foreign_script_chars("ok 周期 ok 각") == ["周", "期", "각"]
    assert foreign_script_chars("") == []
