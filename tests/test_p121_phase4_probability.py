"""
P121 Phase 4 — probability from sources, computed in code (Stian's model, D4–D10). The anchors
Stian gave are the fasit: one government page 90 %, two 95 %, plus an independent serious site
99 %, never 100 % from the web; five random blogs stay capped; conflicts weigh against each other.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase4_probability.py -v
"""

import json
import sys

import pytest

sys.path.insert(0, "/kaare")

import asyncio

from fastapi import HTTPException

import kaare_core.routers.router_settings as rs
import kaare_core.tools.executor_library as lib
import kaare_core.tools.source_confidence as sc
from kaare_core.tools.i18n import t

GOV1 = {"tier": "trusted", "type": "public_authority", "url": "https://www.regjeringen.no/a"}
GOV2 = {"tier": "trusted", "type": "public_authority", "url": "https://www.skatteetaten.no/b"}
SNL = {"tier": "trusted", "type": "encyclopedia", "url": "https://snl.no/x"}
OFFICIAL = {"tier": "official", "url": "https://www.kopalnia.pl/x"}
WIKI = {"wiki": "no", "title": "Saltgruven Wieliczka"}


def OPEN(host):
    return {"tier": "open", "url": f"https://{host}/x"}


# ── Stian's anchors (D7, D10.2) ─────────────────────────────────────────────


def test_one_government_page_is_90():
    assert sc.combine([GOV1]) == pytest.approx(0.90)


def test_two_government_pages_are_95():
    assert sc.combine([GOV1, GOV2]) == pytest.approx(0.95)


def test_plus_independent_serious_site_is_99():
    assert sc.combine([GOV1, GOV2, SNL]) == pytest.approx(0.99)


def test_never_100_from_the_web():
    assert (
        sc.combine(
            [GOV1, GOV2, SNL, OFFICIAL, {**SNL, "url": "https://britannica.com/x", "trust": "high"}]
        )
        <= 0.99
    )


def test_five_blogs_stay_capped():
    assert sc.combine([OPEN(f"b{i}.example") for i in range(5)]) == pytest.approx(0.85)


def test_wikipedia_alone_capped_at_90():
    assert sc.combine([WIKI, {"tier": "open", "url": "https://en.wikipedia.org/wiki/X"}]) <= 0.90


def test_one_high_calibre_source_capped_at_95():
    assert sc.combine([OFFICIAL, OPEN("a.example"), OPEN("b.example"), OPEN("c.example")]) <= 0.95


def test_copies_count_once():
    a = {**OPEN("a.example"), "_quote": "The tourist route has about 800 steps and takes two hours"}
    b = {**SNL, "_quote": "The tourist route has about 800 steps and takes two hours"}
    assert sc.related(a, b)


@pytest.mark.parametrize("trust,expected", [("high", 0.95), ("normal", 0.90), ("low", 0.80)])
def test_trust_boxes(trust, expected):
    assert sc.source_probability({**GOV1, "trust": trust}) == pytest.approx(expected)


def test_caution_modes(monkeypatch):
    assert sc.source_probability(GOV1, "careful") == pytest.approx(0.85)
    assert sc.source_probability(GOV1, "open") == pytest.approx(0.925)
    monkeypatch.setattr(sc, "get_service", lambda name: {"caution": "nonsense"})
    assert sc.caution() == "balanced"


def test_conflict_weighs_both_sides():
    a, b = sc.combine([OFFICIAL]), sc.combine([WIKI])
    assert sc.word_key(sc.weigh_conflict(a, b)) == "uncertain"
    assert sc.word_key(sc.weigh_conflict(b, a)) == "unlikely"


@pytest.mark.parametrize(
    "p,key",
    [
        (0.99, "highly_likely"),
        (0.98, "likely"),
        (0.75, "likely"),
        (0.74, "uncertain"),
        (0.51, "uncertain"),
        (0.50, "unlikely"),
    ],
)
def test_word_bands(p, key):
    assert sc.word_key(p) == key


# ── Wired into Miss Library's result ────────────────────────────────────────


def _ps(sources):
    return [{**s, "_norm": lib._norm(s["text"])} for s in sources]


def _grounded(claims, lang="nb"):
    return {"text": json.dumps({"answer": "x", "not_found": False, "lang": lang, "claims": claims})}


SOURCES = [
    {
        **OFFICIAL,
        "title": "FAQ",
        "text": "The temperature underground is around 17-18 degrees. There are about 800 steps.",
    },
    {**WIKI, "text": "Gruven har en fast temperatur (14 grader) og stor fuktighet."},
    {**OPEN("ricksteves.com"), "title": "Forum", "text": "There are about 800 steps on the route."},
]


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_conflict_and_agreement_rendered_in_words(lang):
    ps = _ps(SOURCES)
    claims = [
        {
            "claim": "17-18 grader",
            "quote": "temperature underground is around 17-18 degrees",
            "source": "s",
            "quote_lang": lang,
            "topic": "temperatur",
        },
        {
            "claim": "14 grader",
            "quote": "fast temperatur (14 grader)",
            "source": "s",
            "quote_lang": "no",
            "topic": "temperatur",
        },
        {
            "claim": "800 trinn",
            "quote": "There are about 800 steps",
            "source": "s",
            "quote_lang": lang,
            "topic": "trinn",
        },
        {
            "claim": "800 trinn",
            "quote": "about 800 steps on the route",
            "source": "s",
            "quote_lang": lang,
            "topic": "trinn",
        },
    ]
    parsed, _ = lib._check_grounded(_grounded(claims, lang), "", "", ps)
    groups = {g["topic"]: g for g in lib._assess(parsed, ps, {}, lang)}
    assert groups["temperatur"]["conflict"] and len(groups["temperatur"]["sides"]) == 2
    assert not groups["trinn"]["conflict"] and groups["trinn"]["sides"][0]["p"] == pytest.approx(
        0.94
    )
    out = lib._render_grounded(parsed, lang, ps, {}, "")
    assert t("lib_assessment_header", lang) in out and t("lib_conflict", lang) in out
    assert t("prob_likely", lang) in out


def test_off_subject_claims_are_not_assessed():
    ps = _ps(SOURCES)
    claims = [
        {
            "claim": "14 grader",
            "quote": "fast temperatur (14 grader)",
            "source": "s",
            "quote_lang": "no",
            "topic": "temperatur",
        }
    ]
    parsed, _ = lib._check_grounded(_grounded(claims), "", "", ps)
    assert lib._assess(parsed, ps, {1: "other"}, "nb") == []


def test_foreign_paraphrase_never_used_in_assessment():
    src = [{**OFFICIAL, "title": "T", "text": "Na całej trasie znajduje się kilkaset schodów."}]
    ps = _ps(src)
    claims = [
        {
            "claim": "fire- til femhundre trinn",
            "quote": "znajduje się kilkaset schodów",
            "source": "s",
            "quote_lang": "pl",
            "topic": "",
        }
    ]
    parsed, _ = lib._check_grounded(_grounded(claims), "", "", ps)
    out = lib._render_grounded(parsed, "nb", ps, {}, "")
    assert "fire- til femhundre" not in out


def test_schema_asks_for_topic():
    items = lib._GROUNDED_SCHEMA["properties"]["claims"]["items"]
    assert "topic" in items["required"] and items["properties"]["conflicts_with"]["type"] == "array"
    for lang in ("nb", "en", "de"):
        assert "topic" in t("lib_grounded_contract", lang) and "conflicts_with" in t(
            "lib_grounded_contract", lang
        )


def test_compatible_claims_count_together():
    src = [
        {**OFFICIAL, "title": "T", "text": "Na całej trasie znajduje się kilkaset schodów."},
        {**OPEN("trasa.example"), "title": "G", "text": "The descent is covering about 380 steps."},
    ]
    ps = _ps(src)
    claims = [
        {
            "claim": "c",
            "quote": "znajduje się kilkaset schodów",
            "source": "s",
            "quote_lang": "pl",
            "topic": "antall trinn",
        },
        {
            "claim": "c",
            "quote": "covering about 380 steps",
            "source": "s",
            "quote_lang": "en",
            "topic": "antall trinn",
        },
    ]
    parsed, _ = lib._check_grounded(_grounded(claims), "", "", ps)
    groups = lib._assess(parsed, ps, {}, "nb")
    assert (
        len(groups) == 1
        and len(groups[0]["sides"]) == 1
        and len(groups[0]["sides"][0]["sources"]) == 2
    )


# ── stakes effect (Phase 4c) ────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_high_stakes_adds_advice_rule(lang):
    ps = _ps(SOURCES)
    claims = [
        {
            "claim": "800",
            "quote": "There are about 800 steps",
            "source": "s",
            "quote_lang": lang,
            "topic": "trinn",
        }
    ]
    parsed, _ = lib._check_grounded(_grounded(claims, lang), "", "", ps)
    assert t("lib_stakes_high", lang) in lib._render_grounded(parsed, lang, ps, {}, "", "high")
    assert t("lib_stakes_high", lang) not in lib._render_grounded(
        parsed, lang, ps, {}, "", "normal"
    )


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_extraordinary_is_always_unresolved(lang):
    ps = _ps(SOURCES)
    claims = [
        {
            "claim": "800",
            "quote": "There are about 800 steps",
            "source": "s",
            "quote_lang": lang,
            "topic": "trinn",
        },
        {
            "claim": "800",
            "quote": "about 800 steps on the route",
            "source": "s",
            "quote_lang": lang,
            "topic": "trinn",
        },
    ]
    parsed, _ = lib._check_grounded(_grounded(claims, lang), "", "", ps)
    out = lib._render_grounded(parsed, lang, ps, {}, "", "extraordinary")
    assert (
        f"trinn: {t('prob_unresolved', lang)}" in out
        and f"trinn: {t('prob_likely', lang)}" not in out
    )
    assert t("lib_stakes_extraordinary", lang) in out


@pytest.mark.parametrize(
    "value,expected", [("high", "high"), ("HIGH ", "high"), (None, "normal"), ("medium", "normal")]
)
def test_stakes_default(value, expected):
    assert lib._stakes(value) == expected


def test_put_websearch_validates_caution():

    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_websearch({"caution": "reckless"}, _u=None))
