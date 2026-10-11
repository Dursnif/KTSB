"""
P121 Phase A — honest attribution: each quote is traced to ONE source, titles/URLs never verify,
and sources about another subject (Wikidata QID mismatch) are flagged. Replays the Wieliczka /
Soledar failure from 2026-10-03 (rid-1791053769239) without any network or LLM.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase_a_attribution.py -v
"""

import asyncio
import json
import sys

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.tools.executor_library as lib
import kaare_core.tools.wikidata as wikidata
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import t

WIELICZKA = {
    "title": "Wieliczka Salt Mine",
    "wiki": "en",
    "text": "The mine reaches a depth of 327 metres. The tourist route is about 3.5 km long.",
}
SOLEDAR = {
    "title": "Soledar Salt Mine",
    "wiki": "en",
    "text": "The temperature in the mine is constantly 14–15 °C and the humidity is around 60 %.",
}
SOURCES = [WIELICZKA, SOLEDAR]


def _per_source(sources):
    return [{**s, "_norm": lib._norm(s["text"])} for s in sources]


def _result(claims):
    return {"text": json.dumps({"answer": "x", "claims": claims, "not_found": False, "lang": "en"})}


def _claim(claim, quote, source="Wieliczka Salt Mine"):
    return {"claim": claim, "quote": quote, "source": source}


# ── Attribution ─────────────────────────────────────────────────────────────


def test_quote_is_traced_to_the_one_article_it_is_in():
    ps = _per_source(SOURCES)
    assert lib._locate_quote("constantly 14–15 °C", ps) == 1
    assert lib._locate_quote("depth of 327 metres", ps) == 0
    assert lib._locate_quote("not in any source", ps) is None


def test_model_source_label_is_ignored_code_decides():
    # Miss Library attributed the Soledar quote to Wieliczka — code must not believe that.
    ps = _per_source(SOURCES)
    parsed, _ = lib._check_grounded(
        _result(
            [_claim("Temperature is 14–15 °C", "constantly 14–15 °C", source="Wieliczka Salt Mine")]
        ),
        "",
        "",
        ps,
    )
    c = parsed["claims"][0]
    assert c["_verified"] and c["_src"] == 1


def test_title_or_url_only_quote_does_not_verify():
    ps = _per_source(
        [
            {
                "title": "Wieliczka Salt Mine",
                "url": "https://example.org/w",
                "text": "Body text only.",
            }
        ]
    )
    parsed, meta = lib._check_grounded(
        _result([_claim("It is a salt mine", "Wieliczka Salt Mine")]), "", "", ps
    )
    assert not parsed["claims"][0]["_verified"]
    assert meta["unverified_quotes"]


def test_quote_spanning_two_sources_does_not_verify():
    ps = _per_source(SOURCES)
    assert lib._locate_quote("327 metres … humidity is around 60", ps) is None


def test_names_are_checked_against_the_quotes_own_source():
    # "Soledar" occurs only in the other article's title, not in Wieliczka's text.
    ps = _per_source(SOURCES)
    parsed, meta = lib._check_grounded(
        _result([_claim("The Soledar mine is 327 metres deep", "depth of 327 metres")]), "", "", ps
    )
    assert not parsed["claims"][0]["_verified"]
    assert meta["unsupported_claims"]


def test_legacy_path_without_sources_unchanged():
    blob = lib._norm("[Wieliczka]\nThe mine reaches a depth of 327 metres.")
    parsed, _ = lib._check_grounded(_result([_claim("327 m", "depth of 327 metres")]), blob)
    assert parsed["claims"][0]["_verified"] and "_src" not in parsed["claims"][0]


# ── Rendering ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_other_subject_is_flagged_and_summary_dropped(lang):
    ps = _per_source(SOURCES)
    parsed, _ = lib._check_grounded(
        _result(
            [
                _claim("The tourist route is 3.5 km", "about 3.5 km long"),
                _claim("Temperature is 14–15 °C", "constantly 14–15 °C"),
            ]
        ),
        "",
        "",
        ps,
    )
    parsed["answer"] = "Blended summary that must not reach Kåre"
    out = lib._render_grounded(parsed, lang, ps, {0: "match", 1: "other"}, "Wieliczka Salt Mine")
    assert "Blended summary" not in out
    assert "[wiki en: Soledar Salt Mine]" in out
    assert (
        t("lib_claim_other_subject", lang, title="Soledar Salt Mine", subject="Wieliczka Salt Mine")
        in out
    )
    assert t("lib_grounded_other_subject_note", lang, subject="Wieliczka Salt Mine") in out


def test_all_matching_keeps_summary_and_has_no_flags():
    ps = _per_source(SOURCES)
    parsed, _ = lib._check_grounded(
        _result([_claim("Route 3.5 km", "about 3.5 km long")]), "", "", ps
    )
    out = lib._render_grounded(parsed, "nb", ps, {0: "match"}, "Wieliczka Salt Mine")
    assert out.startswith("x")
    assert "⚠" not in out


def test_web_source_label_shows_domain():
    ps = _per_source(
        [
            {
                "title": "Tourist route",
                "url": "https://www.wieliczka-saltmine.com/a",
                "text": "800 steps",
            }
        ]
    )
    parsed, _ = lib._check_grounded(_result([_claim("800 steps", "800 steps")]), "", "", ps)
    out = lib._render_grounded(parsed, "en", ps, {0: "unmentioned"}, "Wieliczka Salt Mine")
    assert "[Tourist route — wieliczka-saltmine.com]" in out
    assert t("lib_claim_unmentioned", "en", subject="Wieliczka Salt Mine") in out


# ── Subject match (Wikidata mocked) ─────────────────────────────────────────


def _mock_wikidata(monkeypatch, subject_ents, article_map):
    async def subj(subject, lang, limit=3):
        return subject_ents

    async def arts(site, titles):
        return {t_: article_map[(site, t_)] for t_ in titles if (site, t_) in article_map}

    monkeypatch.setattr(wikidata, "subject_entities", subj)
    monkeypatch.setattr(wikidata, "article_qids", arts)


def test_subject_match_wiki_by_qid(monkeypatch):
    _mock_wikidata(
        monkeypatch,
        [{"qid": "Q454019", "names": ["Wieliczka Salt Mine", "Kopalnia soli Wieliczka"]}],
        {
            ("enwiki", "Wieliczka Salt Mine"): "Q454019",
            ("enwiki", "Soledar Salt Mine"): "Q18331668",
        },
    )
    out = asyncio.run(lib._subject_match(SOURCES, "Wieliczka Salt Mine", "en"))
    assert out == {0: "match", 1: "other"}


def test_subject_match_web_by_names(monkeypatch):
    _mock_wikidata(
        monkeypatch,
        [{"qid": "Q454019", "names": ["Wieliczka Salt Mine", "Kopalnia soli Wieliczka"]}],
        {},
    )
    web = [
        {
            "title": "Visit",
            "url": "https://a.example",
            "text": "Tips for the Wieliczka salt mine tour.",
        },
        {"title": "Other", "url": "https://b.example", "text": "Soledar is in Ukraine."},
    ]
    assert asyncio.run(lib._subject_match(web, "Wieliczka Salt Mine", "en")) == {
        0: "match",
        1: "unmentioned",
    }


def test_subject_unknown_marks_nothing(monkeypatch):
    _mock_wikidata(monkeypatch, [], {})
    assert asyncio.run(lib._subject_match(SOURCES, "Nonexistent thing", "en")) == {}


def test_unknown_article_qid_is_left_unmarked(monkeypatch):
    _mock_wikidata(
        monkeypatch,
        [{"qid": "Q454019", "names": ["Wieliczka Salt Mine"]}],
        {("enwiki", "Wieliczka Salt Mine"): "Q454019"},
    )
    assert asyncio.run(lib._subject_match(SOURCES, "Wieliczka Salt Mine", "en")) == {0: "match"}


# ── Schema + i18n ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_subject_param_on_library_and_web_search(lang):
    tools = {tl["function"]["name"]: tl for tl in get_tools(lang)}
    for name in ("library", "web_search"):
        props = tools[name]["function"]["parameters"]["properties"]
        assert props["subject"]["type"] == "string" and props["subject"]["description"]
        assert "subject" not in tools[name]["function"]["parameters"].get("required", [])


@pytest.mark.parametrize(
    "key",
    [
        "lib_claim_other_subject",
        "lib_claim_unmentioned",
        "lib_grounded_other_subject_note",
        "tool_subject_desc",
    ],
)
@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_i18n_keys(key, lang):
    assert t(key, lang, title="T", subject="S").strip()


def test_evidence_header_no_longer_says_known():
    assert "fra kildene" not in t("lib_grounded_evidence", "nb").lower()
