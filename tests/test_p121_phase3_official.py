"""
P121 Phase 3 — official-source tier: subject → Wikidata P856 → safe redirect → robots.txt →
sitemap(s) → rank URLs by Kåre's keywords → fetch; official hosts trusted for this request only.
No real network: Wikidata, DNS and HTTP are mocked.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase3_official.py -v
"""

import asyncio
import gzip
import sys

import httpx
import pytest

sys.path.insert(0, "/kaare")

import json

import adapters.official_source as off
import adapters.web_fetch as wf
import adapters.web_search_adapter as wsa
import kaare_core.tools.executor_library as lib
import kaare_core.tools.wikidata as wikidata
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import t


def HTML(body):
    return lambda: httpx.Response(200, headers={"content-type": "text/html"}, content=body)


def XML(body):
    return lambda: httpx.Response(200, headers={"content-type": "application/xml"}, content=body)


PAGE = (
    b"<html><body><article><p>"
    + b"The tourist route has about 800 steps; wear sturdy shoes. " * 4
    + b"</p></article></body></html>"
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    async def fake_resolve(host, port):
        return ["93.184.215.14"]

    monkeypatch.setattr(wf, "_resolve", fake_resolve)
    monkeypatch.setattr(
        wf,
        "get_service",
        lambda name: {"per_host_interval_s": 0.001, "max_download_bytes": 100_000},
    )
    for name, value in (("_robots_cache", {}), ("_page_cache", {}), ("_host_next", {})):
        monkeypatch.setattr(wf, name, value)
    monkeypatch.setattr(off, "_sitemap_cache", {})
    path = tmp_path / "trusted.yaml"
    path.write_text(
        "sources: {general: [{domain: snl.no}]}\nblocked: [blocked.example]\n", encoding="utf-8"
    )
    monkeypatch.setattr(wf, "_TRUSTED_PATH", path)
    monkeypatch.setattr(
        wf,
        "_trust_cache",
        {"mtime": None, "domains": [], "entries": {}, "blocked": [], "state": "unreadable"},
    )

    async def subj(subject, lang, limit=3):
        return [{"qid": "Q454019", "names": ["Wieliczka Salt Mine"]}]

    monkeypatch.setattr(wikidata, "subject_entities", subj)


def _site(routes):
    def handler(request):
        make = routes.get(str(request.url))
        return make() if make else httpx.Response(404)

    return httpx.MockTransport(handler)


def _official(monkeypatch, urls):
    async def p856(qid):
        return urls

    monkeypatch.setattr(wikidata, "official_websites", p856)


# ── Ranking ─────────────────────────────────────────────────────────────────


def test_rank_pages_by_keyword_overlap():
    urls = [
        "https://m.example/en/shop/tickets",
        "https://m.example/en/tourist-route",
        "https://m.example/en/useful-information/tourist-route-accessibility",
        "https://m.example/en/events",
    ]
    ranked = off.rank_pages(urls, ["tourist route", "accessibility", "steps"])
    assert ranked[0].endswith("tourist-route-accessibility") and "tickets" not in " ".join(ranked)


def test_rank_pages_needs_keywords():
    assert off.rank_pages(["https://m.example/a"], []) == []


def test_rank_pages_decodes_url_words():
    assert off.rank_pages(["https://k.example/turysta/trasa-tury%C5%9Bci"], ["turyści"])


# ── gzip guard ──────────────────────────────────────────────────────────────


def test_gunzip_plain_passthrough():
    assert off._gunzip(b"<urlset/>") == b"<urlset/>"


def test_gunzip_bomb_refused():
    bomb = gzip.compress(b"0" * (off._SITEMAP_MAX_BYTES + 10))
    with pytest.raises(wf.FetchError):
        off._gunzip(bomb)


# ── Full lookup ─────────────────────────────────────────────────────────────


def test_official_pages_via_redirect_and_sitemap_index(env, monkeypatch):
    _official(monkeypatch, ["http://www.kopalnia.example"])
    routes = {
        "http://www.kopalnia.example": lambda: httpx.Response(
            301, headers={"location": "https://www.mine.example/"}
        ),
        "https://www.mine.example/": HTML(b"<p>home</p>"),
        "https://www.mine.example/robots.txt": lambda: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"User-agent: *\nAllow: /\nSitemap: https://www.mine.example/sitemap.xml\n",
        ),
        "https://www.mine.example/sitemap.xml": XML(
            b"<sitemapindex><sitemap><loc>https://www.mine.example/pages.xml.gz</loc></sitemap></sitemapindex>"
        ),
        "https://www.mine.example/pages.xml.gz": lambda: httpx.Response(
            200,
            headers={"content-type": "application/gzip"},
            content=gzip.compress(
                b"<urlset><url><loc>https://www.mine.example/en/tourist-route</loc></url>"
                b"<url><loc>https://www.mine.example/en/shop</loc></url>"
                b"<url><loc>https://evil.example/en/tourist-route</loc></url></urlset>"
            ),
        ),
        "https://www.mine.example/en/tourist-route": HTML(PAGE),
    }
    sources, hosts = asyncio.run(
        off.find_official_pages(
            "Wieliczka Salt Mine", ["tourist route", "steps"], "en", transport=_site(routes)
        )
    )
    assert hosts == {"kopalnia.example", "mine.example"}
    assert [s["url"] for s in sources] == ["https://www.mine.example/en/tourist-route"]
    assert sources[0]["tier"] == "official" and "800 steps" in sources[0]["content"]


def test_no_p856_means_nothing(env, monkeypatch):
    _official(monkeypatch, [])
    assert asyncio.run(off.find_official_pages("X", ["a"], "en", transport=_site({}))) == (
        [],
        set(),
    )


def test_unreachable_official_site_means_no_pages(env, monkeypatch):
    _official(monkeypatch, ["https://down.example"])
    sources, _ = asyncio.run(
        off.find_official_pages(
            "X", ["a"], "en", transport=_site({"https://down.example": lambda: httpx.Response(503)})
        )
    )
    assert sources == []


def test_official_site_on_lan_is_refused(env, monkeypatch):
    async def lan(host, port):
        return ["192.168.0.10"]

    monkeypatch.setattr(wf, "_resolve", lan)
    _official(monkeypatch, ["https://intranet.example"])
    sources, _ = asyncio.run(off.find_official_pages("X", ["a"], "en", transport=_site({})))
    assert sources == []


def test_fallback_to_sitemap_xml_without_robots_line(env, monkeypatch):
    _official(monkeypatch, ["https://c.example/"])
    routes = {
        "https://c.example/": HTML(b"<p>home</p>"),
        "https://c.example/sitemap.xml": XML(
            b"<urlset><url><loc>https://c.example/en/locks</loc></url></urlset>"
        ),
        "https://c.example/en/locks": HTML(PAGE),
    }
    sources, _ = asyncio.run(
        off.find_official_pages("Göta Canal", ["locks"], "en", transport=_site(routes))
    )
    assert [s["url"] for s in sources] == ["https://c.example/en/locks"]


# ── Wiring ──────────────────────────────────────────────────────────────────


def test_search_results_on_official_host_become_official(monkeypatch):
    monkeypatch.setattr(wsa, "_WS_CFG", {**wsa._WS_CFG, "provider": "brave", "fallback": ""})
    monkeypatch.setattr(wsa, "_provider_ready", lambda p: True)
    monkeypatch.setattr(wf, "trust_list_state", lambda: "ok")

    async def provider(chain, query):
        return [
            {"title": "Route", "url": "https://www.mine.example/en/route", "tier": "open"},
            {"title": "Blog", "url": "https://blog.example/x", "tier": "open"},
        ]

    async def official(subject, keywords, lang):
        return [
            {
                "title": "FAQ",
                "url": "https://www.mine.example/faq",
                "tier": "official",
                "content": "800 steps",
            }
        ], {"mine.example"}

    seen = {}

    async def fetch(url, tier="trusted"):
        seen[url] = tier
        return "text"

    async def library(query, sources, lang="nb", subject="", keywords=None, stakes="normal"):
        return [(s["url"], s["tier"]) for s in sources]

    monkeypatch.setattr(wsa, "_provider_results", provider)
    monkeypatch.setattr(wsa, "_official", official)
    monkeypatch.setattr(wsa, "_fetch_content", fetch)
    monkeypatch.setattr(wsa, "_ask_library", library)
    out = asyncio.run(wsa.web_search("q", subject="Wieliczka Salt Mine", keywords=["steps"]))
    assert out == [
        ("https://www.mine.example/faq", "official"),
        ("https://www.mine.example/en/route", "official"),
        ("https://blog.example/x", "open"),
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        (["steps", " stairs ", ""], ["steps", "stairs"]),
        ("steps, stairs", ["steps", "stairs"]),
        (None, []),
        (5, []),
    ],
)
def test_keyword_list(value, expected):
    assert lib._keyword_list(value) == expected


def test_official_source_counts_as_subject_match(monkeypatch):
    async def subj(subject, lang, limit=3):
        return [{"qid": "Q1", "names": ["Something Else Entirely"]}]

    monkeypatch.setattr(wikidata, "subject_entities", subj)
    src = [
        {
            "title": "FAQ",
            "url": "https://mine.example/faq",
            "text": "nothing named",
            "tier": "official",
        }
    ]
    assert asyncio.run(lib._subject_match(src, "Wieliczka Salt Mine", "en")) == {0: "match"}


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_official_label_and_keywords_param(lang):
    assert t("lib_tier_official", lang) in lib._source_label(
        {"title": "FAQ", "url": "https://mine.example/faq", "tier": "official"}, lang
    )
    ws = next(tl for tl in get_tools(lang) if tl["function"]["name"] == "web_search")
    kw = ws["function"]["parameters"]["properties"]["keywords"]
    assert kw["type"] == "array" and kw["items"] == {"type": "string"} and kw["description"]


# ── Translation is for the big model (Stian 2026-10-03) ─────────────────────


def _grounded(claims):
    return {
        "text": json.dumps(
            {"answer": "Model summary", "claims": claims, "not_found": False, "lang": "nb"}
        )
    }


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_foreign_quote_passed_verbatim_without_9b_paraphrase(lang):
    src = [
        {
            "title": "Trasa",
            "url": "https://www.kopalnia.pl/t",
            "tier": "official",
            "text": "Na całej trasie znajduje się kilkaset schodów.",
        }
    ]
    ps = [{**s, "_norm": lib._norm(s["text"])} for s in src]
    parsed, _ = lib._check_grounded(
        _grounded(
            [
                {
                    "claim": "Omtrent fire- til femhundre trinn",
                    "source": "x",
                    "quote": "znajduje się kilkaset schodów",
                    "quote_lang": "pl",
                }
            ]
        ),
        "",
        "",
        ps,
    )
    out = lib._render_grounded(parsed, lang, ps, {0: "match"}, "Wieliczka Salt Mine")
    assert "fire- til femhundre" not in out and "Model summary" not in out
    assert "«znajduje się kilkaset schodów»" in out
    assert t("lib_quote_translate_yourself", lang, src_lang="pl") in out


def test_same_language_quote_keeps_claim_and_summary():
    src = [
        {
            "title": "SNL",
            "url": "https://snl.no/g",
            "tier": "trusted",
            "text": "Kanalen har 58 sluser.",
        }
    ]
    ps = [{**s, "_norm": lib._norm(s["text"])} for s in src]
    parsed, _ = lib._check_grounded(
        _grounded(
            [
                {
                    "claim": "58 sluser",
                    "source": "x",
                    "quote": "Kanalen har 58 sluser",
                    "quote_lang": "no",
                }
            ]
        ),
        "",
        "",
        ps,
    )
    out = lib._render_grounded(parsed, "nb", ps, {}, "")
    assert out.startswith("Model summary") and "58 sluser — «Kanalen har 58 sluser»" in out


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_contract_asks_for_quote_lang(lang):
    assert "quote_lang" in t("lib_grounded_contract", lang)
    assert "quote_lang" in lib._GROUNDED_SCHEMA["properties"]["claims"]["items"]["required"]
