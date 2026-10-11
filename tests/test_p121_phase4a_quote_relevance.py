"""
P121 D-P15 — quote relevance by rule: when no verified quote shares a word stem with Kåre's
keywords (or the task), and the sources hold sentences that do, IrrelevantQuoteDetector forces one
correction listing those sentences. Replays the kopalnia.pl case from 2026-10-04.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase4a_quote_relevance.py -v
"""

import json
import sys

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.model_enforcement as me
import kaare_core.tools.executor_library as lib
from kaare_core.model_enforcement import IrrelevantQuoteDetector, ModelEnforcer, grounded_detectors
from kaare_core.tools.i18n import t

KOPALNIA = (
    "Goście podziwiać będą niespotykane na powierzchni krajobrazy, np. solankowe jeziora oraz "
    "przepiękne podziemne komory. Trasa nie wymaga ponadprzeciętnej kondycji fizycznej. "
    "Na całej trasie znajduje się kilkaset schodów, dlatego zalecamy założenie wygodnego obuwia."
)
SOURCES = [
    {
        "title": "Trasa turystyczna",
        "url": "https://www.kopalnia.pl/t",
        "tier": "official",
        "text": KOPALNIA,
    }
]
KEYWORDS = ["steps", "stairs", "tourist route", "accessibility", "schody", "trasa turystyczna"]


def _per_source():
    return [{**s, "_norm": lib._norm(s["text"])} for s in SOURCES]


def _result(quote):
    return {
        "text": json.dumps(
            {
                "answer": "x",
                "not_found": False,
                "lang": "nb",
                "claims": [{"claim": "c", "quote": quote, "source": "s", "quote_lang": "pl"}],
            }
        )
    }


def _focus():
    return lib.focus_stems(KEYWORDS, "Wieliczka salt mine stairs slippery", "Wieliczka Salt Mine")


def test_stems_join_inflections():
    assert lib._stems("schody") & lib._stems("schodów")


def test_subject_words_are_not_focus():
    assert not (lib._stems("Wieliczka Salt Mine") & _focus())


def test_kopalnia_lakes_quote_is_flagged_with_the_stairs_sentence():
    _, meta = lib._check_grounded(
        _result("solankowe jeziora oraz przepiękne podziemne komory"),
        "",
        "",
        _per_source(),
        _focus(),
    )
    assert meta["relevant_unquoted"]
    assert "kilkaset schodów" in meta["relevant_unquoted"][0]


def test_relevant_quote_passes():
    _, meta = lib._check_grounded(
        _result("znajduje się kilkaset schodów"), "", "", _per_source(), _focus()
    )
    assert meta["relevant_unquoted"] == []


def test_no_focus_no_check():
    _, meta = lib._check_grounded(_result("solankowe jeziora"), "", "", _per_source(), set())
    assert "relevant_unquoted" not in meta


def test_nothing_relevant_in_sources_does_not_fire():
    ps = [
        {
            "title": "x",
            "url": "https://a.example",
            "tier": "open",
            "text": "Only lakes and chambers here. Nothing else at all.",
            "_norm": lib._norm("Only lakes and chambers here. Nothing else at all."),
        }
    ]
    _, meta = lib._check_grounded(_result("Only lakes and chambers here"), "", "", ps, _focus())
    assert meta["relevant_unquoted"] == []


def test_task_words_used_when_no_keywords():
    focus = lib.focus_stems([], "Hvor mange trappetrinn har turistruta?", "")
    assert lib._stems("trappetrinn") <= focus


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_detector_correction_lists_sentences(lang):
    d = IrrelevantQuoteDetector()
    assert d.matches(
        text_out="",
        used_tools=[],
        meta={"relevant_unquoted": ["A sentence about stairs."]},
        lang=lang,
    )
    msg = d.correction_message(lang)
    assert "«A sentence about stairs.»" in msg
    assert msg.startswith(t("lib_correction_irrelevant", lang, sentences="").split("\n")[0][:20])


def test_one_good_source_does_not_hide_a_missed_one():
    other = {
        "title": "Forum",
        "url": "https://forum.example/x",
        "tier": "open",
        "text": "The steps are descending, not ascending.",
    }
    ps = [*_per_source(), {**other, "_norm": lib._norm(other["text"])}]
    result = {
        "text": json.dumps(
            {
                "answer": "x",
                "not_found": False,
                "lang": "nb",
                "claims": [
                    {
                        "claim": "c",
                        "quote": "solankowe jeziora oraz przepiękne podziemne komory",
                        "source": "s",
                        "quote_lang": "pl",
                    },
                    {
                        "claim": "c",
                        "quote": "The steps are descending",
                        "source": "s",
                        "quote_lang": "en",
                    },
                ],
            }
        )
    }
    _, meta = lib._check_grounded(result, "", "", ps, _focus())
    assert meta["relevant_unquoted"] and "schodów" in meta["relevant_unquoted"][0]


def test_detector_is_in_grounded_set_and_fires_once(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")  # never the live log
    assert any(isinstance(d, IrrelevantQuoteDetector) for d in grounded_detectors())
    enf = ModelEnforcer("nb", rid="r", detectors=grounded_detectors())
    meta = {"relevant_unquoted": ["Stairs here."]}
    assert enf.check(text_out="{}", used_tools=[], meta=meta) is not None
    assert enf.check(text_out="{}", used_tools=[], meta=meta) is None


# ── Clock times + honest removal message (found by the eval run 2026-10-04) ──


@pytest.mark.parametrize(
    "claim,quote,gaps",
    [
        ("Åpent 10:00–16:00.", "10 bis 16 Uhr", []),
        ("Åpent 10.00–16.00.", "von 10:00 bis 16:00 Uhr", []),
        ("Åpent 10:30–16:00.", "10 bis 16 Uhr", ["10:30"]),
        ("Turen er 3.5 km.", "about 3.5 km long", []),
    ],
)
def test_clock_times_compare_as_hours(claim, quote, gaps):
    got = lib._claim_gaps(claim, quote, lib._norm(quote), "")
    assert (got == []) == (gaps == [])


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_removed_message_says_why(lang):
    ps = [
        {
            "title": "S",
            "url": "https://a.example",
            "text": "Open 10 to 16 daily.",
            "_norm": lib._norm("Open 10 to 16 daily."),
        }
    ]
    result = {
        "text": json.dumps(
            {
                "answer": "x",
                "not_found": False,
                "lang": lang,
                "claims": [
                    {
                        "claim": "Open 10–16 and costs 25 euro",
                        "quote": "Open 10 to 16 daily",
                        "source": "s",
                        "quote_lang": "en",
                    },
                    {
                        "claim": "c",
                        "quote": "a sentence that is not there",
                        "source": "s",
                        "quote_lang": "en",
                    },
                ],
            }
        )
    }
    parsed, _ = lib._check_grounded(result, "", "", ps)
    out = lib._render_grounded(parsed, lang, ps, {}, "")
    assert t("lib_grounded_removed", lang, n=1) in out
    assert t("lib_grounded_removed_unsupported", lang, n=1) in out


# ── Retry hint for Kåre (P121 D11) ──────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_thin_results_carry_retry_hint(lang):
    ps = [
        {
            "title": "S",
            "url": "https://a.example",
            "text": "Open 10 to 16 daily.",
            "_norm": lib._norm("Open 10 to 16 daily."),
        }
    ]
    bad = {
        "text": json.dumps(
            {
                "answer": "x",
                "not_found": False,
                "lang": lang,
                "claims": [
                    {"claim": "c", "quote": "not there at all", "source": "s", "quote_lang": "en"}
                ],
            }
        )
    }
    parsed, _ = lib._check_grounded(bad, "", "", ps)
    assert t("lib_retry_hint", lang) in lib._render_grounded(parsed, lang, ps, {}, "")
    nf = {"text": json.dumps({"answer": "", "not_found": True, "lang": lang, "claims": []})}
    parsed, _ = lib._check_grounded(nf, "", "", ps)
    assert t("lib_retry_hint", lang) in lib._render_grounded(parsed, lang, ps, {}, "")


def test_good_result_has_no_retry_hint():
    ps = [
        {
            "title": "S",
            "url": "https://a.example",
            "text": "Open 10 to 16 daily.",
            "_norm": lib._norm("Open 10 to 16 daily."),
        }
    ]
    ok = {
        "text": json.dumps(
            {
                "answer": "x",
                "not_found": False,
                "lang": "en",
                "claims": [
                    {
                        "claim": "Open 10 to 16",
                        "quote": "Open 10 to 16 daily",
                        "source": "s",
                        "quote_lang": "en",
                    }
                ],
            }
        )
    }
    parsed, _ = lib._check_grounded(ok, "", "", ps)
    assert t("lib_retry_hint", "en") not in lib._render_grounded(parsed, "en", ps, {}, "")
