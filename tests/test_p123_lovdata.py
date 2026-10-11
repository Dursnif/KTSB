"""
P123 — Norwegian law from Lovdata's open dataset: parse per paragraph (UTF-8, short title, public
URL, change notes left out), atomic FTS5 index, prefix search, refresh that keeps yesterday's
index on failure and never consults lovdata.no robots for the documented dataset.
No network: a tiny archive with the real file shape is built in the test.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p123_lovdata.py -v
"""

import asyncio
import io
import sys
import tarfile

import pytest

sys.path.insert(0, "/kaare")

import json

import adapters.lovdata_adapter as L
import kaare_core.tools.executor_library as lib
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import t

DOC = """<!DOCTYPE html><html lang="nb"><head><title>Lov om straff (straffeloven)</title><base href="https://lovdata.no/" /></head><body>
<header class="documentHeader"><dl class="data-document-key-info"><dt class="dokid">DokumentID</dt><dd class="dokid">NL/lov/2005-05-20-28</dd>
<dt class="titleShort">Korttittel</dt><dd class="titleShort">Straffeloven – strl.</dd></dl></header>
<main class="documentBody">
<article class="legalArticle" data-lovdata-URL="NL/lov/2005-05-20-28/§351" data-name="§351" id="p1"><h4 class="legalArticleHeader"><span class="legalArticleValue">§ 351</span>. <span class="legalArticleTitle">Skadeverk</span></h4>
<article class="legalP" id="p1-1">Med bot eller fengsel inntil 1 år straffes den som skader, ødelegger, gjør ubrukelig eller forspiller en gjenstand som helt eller delvis tilhører en annen.</article>
<article class="changesToParent">Tilføyd ved lov 19 juni 2009 nr. 74.</article></article>
<article class="legalArticle" data-lovdata-URL="NL/lov/2005-05-20-28/§352" data-name="§352" id="p2"><h4 class="legalArticleHeader"><span class="legalArticleValue">§ 352</span>. <span class="legalArticleTitle">Grovt skadeverk</span></h4>
<article class="legalP" id="p2-1">Grovt skadeverk straffes med bot eller fengsel inntil 6 år.</article></article>
</main></body></html>"""


def _archive(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


ARCHIVE = _archive({"nl/": "", "nl/nl-20050520-028.xml": DOC})


def test_parse_document_shape():
    info, paras = L.parse_document(DOC.encode("utf-8"))
    assert info["short"] == "Straffeloven – strl." and info["dokid"] == "NL/lov/2005-05-20-28"
    p = paras[0]
    assert p["para"] == "§ 351" and p["heading"] == "Skadeverk"
    assert p["url"] == "https://lovdata.no/lov/2005-05-20-28/§351"
    assert "ødelegger" in p["body"]  # UTF-8, not latin-1 mojibake
    assert "Tilføyd ved lov" not in p["body"]  # change notes left out
    assert not p["body"].startswith("§ 351")  # header not repeated in body


def test_build_and_search(tmp_path):
    db = tmp_path / "lovdata.db"
    stats = L.build_index([ARCHIVE], db)
    assert stats["documents"] == "1" and stats["paragraphs"] == "2"
    hits = L.search("strafferamme hærverk", ["skadeverk"], 5, db)
    assert {h["title"] for h in hits} == {
        "Straffeloven – strl. § 351 Skadeverk",
        "Straffeloven – strl. § 352 Grovt skadeverk",
    }
    assert all(h["url"].startswith("https://lovdata.no/lov/") for h in hits)


def test_prefix_search_finds_inflections(tmp_path):
    db = tmp_path / "lovdata.db"
    L.build_index([ARCHIVE], db)
    assert L.search("", ["skadeverk"], 5, db)  # «skadeverk*» also matches «skadeverket»


def test_short_words_and_empty_query_return_nothing(tmp_path):
    db = tmp_path / "lovdata.db"
    L.build_index([ARCHIVE], db)
    assert L.search("og i på", [], 5, db) == []


def test_failed_build_keeps_previous_index(tmp_path):
    db = tmp_path / "lovdata.db"
    L.build_index([ARCHIVE], db)
    with pytest.raises(RuntimeError):
        L.build_index([_archive({"readme.txt": "no laws"})], db)
    assert L.status(db)["paragraphs"] == 2 and not (tmp_path / "lovdata.db.tmp").exists()


def test_status_without_index(tmp_path):
    assert L.status(tmp_path / "none.db") == {"built_at": None, "documents": 0, "paragraphs": 0}


def test_refresh_end_to_end(tmp_path, monkeypatch):
    async def fake_fetch(url, **kw):
        assert kw.get("respect_robots") is False and "x-bzip2" in " ".join(kw.get("accept", ()))
        return url, ARCHIVE, "application/x-bzip2"

    real_build = L.build_index
    monkeypatch.setattr(L.web_fetch, "fetch_raw", fake_fetch)
    monkeypatch.setattr(
        L, "config", lambda: {"enabled": True, "datasets": ["https://api.lovdata.no/x.tar.bz2"]}
    )
    monkeypatch.setattr(
        L, "build_index", lambda archives: real_build(archives, tmp_path / "lovdata.db")
    )
    stats = asyncio.run(L.refresh())
    assert stats["paragraphs"] == "2"


def test_failed_download_keeps_index(tmp_path, monkeypatch):
    db = tmp_path / "lovdata.db"
    L.build_index([ARCHIVE], db)

    async def boom(url, **kw):
        raise L.web_fetch.FetchError("network_ConnectError")

    monkeypatch.setattr(L.web_fetch, "fetch_raw", boom)
    monkeypatch.setattr(
        L, "config", lambda: {"enabled": True, "datasets": ["https://api.lovdata.no/x.tar.bz2"]}
    )
    with pytest.raises(L.web_fetch.FetchError):
        asyncio.run(L.refresh())
    assert L.status(db)["paragraphs"] == 2


def test_attribution_names_licence_and_licensor():
    assert (
        "NLOD" in L.ATTRIBUTION
        and "Lovdata" in L.ATTRIBUTION
        and "data.norge.no/nlod" in L.ATTRIBUTION
    )


# ── library(action="law") (Phase 3) ─────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_law_action_when_off_says_where_to_switch_on(lang, monkeypatch):
    monkeypatch.setattr(lib.lovdata_adapter, "enabled", lambda: False)
    out = asyncio.run(lib._ask_library_law("hærverk", {}, lang))
    assert out == t("law_not_enabled", lang)
    assert {"nb": "Nettsøk/Vær", "en": "Web Search & Weather", "de": "Websuche & Wetter"}[
        lang
    ] in out


def test_law_action_uses_index_high_stakes_and_attribution(tmp_path, monkeypatch):
    db = tmp_path / "lovdata.db"
    L.build_index([ARCHIVE], db)
    monkeypatch.setattr(lib.lovdata_adapter, "enabled", lambda: True)
    real_search = L.search
    monkeypatch.setattr(lib.lovdata_adapter, "search", lambda q, kw: real_search(q, kw, 6, db))
    seen = {}

    async def fake_synth(
        question, sources, lang, keywords=None, stakes="normal", subject="", quote_only=False
    ):
        seen.update(
            sources=sources,
            stakes=stakes,
            keywords=keywords,
            quote_only=quote_only,
            question=question,
        )
        return "ANSWER"

    monkeypatch.setattr(lib, "synthesize_web_results", fake_synth)
    out = asyncio.run(
        lib._ask_library_law("strafferamme hærverk", {"keywords": ["skadeverk"]}, "nb")
    )
    assert out.startswith("ANSWER") and L.ATTRIBUTION in out
    assert (
        seen["stakes"] == "high"
        and seen["keywords"] == ["skadeverk"]
        and seen["quote_only"] is True
    )
    assert t("lib_law_find_task", "nb") in seen["question"]
    assert all(s["tier"] == "trusted" and s["type"] == "public_authority" for s in seen["sources"])
    assert {s["url"] for s in seen["sources"]} == {
        "https://lovdata.no/lov/2005-05-20-28/§351",
        "https://lovdata.no/lov/2005-05-20-28/§352",
    }


def test_law_no_hits_gives_retry_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(lib.lovdata_adapter, "enabled", lambda: True)
    monkeypatch.setattr(lib.lovdata_adapter, "search", lambda q, kw: [])
    out = asyncio.run(lib._ask_library_law("noe", {}, "nb"))
    assert t("law_no_hits", "nb") in out and t("lib_retry_hint", "nb") in out


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_law_in_library_schema(lang):
    for tool in get_tools(lang):
        if tool["function"]["name"] == "library":
            props = tool["function"]["parameters"]["properties"]
            assert "law" in props["action"]["enum"] and props["keywords"]["type"] == "array"
            assert "'law'" in props["action"]["description"]


def test_paragraph_number_from_source_title_is_allowed():
    ps = [
        {
            "title": "Straffeloven – strl. § 351 Skadeverk",
            "url": "https://lovdata.no/lov/2005-05-20-28/§351",
            "text": "§ 351. Skadeverk. Med bot eller fengsel inntil 1 år straffes den som skader en gjenstand.",
            "_norm": lib._norm(
                "§ 351. Skadeverk. Med bot eller fengsel inntil 1 år straffes den som skader en gjenstand."
            ),
        }
    ]
    result = {
        "text": json.dumps(
            {
                "answer": "x",
                "not_found": False,
                "lang": "nb",
                "claims": [
                    {
                        "claim": "Etter § 351 er strafferammen bot eller fengsel inntil 1 år",
                        "quote": "Med bot eller fengsel inntil 1 år",
                        "source": "s",
                        "quote_lang": "no",
                        "topic": "strafferamme",
                    }
                ],
            }
        )
    }
    parsed, meta = lib._check_grounded(result, "", "", ps)
    assert parsed["claims"][0]["_verified"] and not meta["unsupported_claims"]


def test_summary_with_unbacked_number_is_dropped():
    text = "§ 352. Grovt skadeverk. Grovt skadeverk straffes med bot eller fengsel inntil 6 år."
    ps = [
        {
            "title": "Straffeloven – strl. § 352 Grovt skadeverk",
            "url": "u",
            "text": text,
            "_norm": lib._norm(text),
        }
    ]
    claims = [
        {
            "claim": "Grovt skadeverk: inntil 6 år",
            "quote": "Grovt skadeverk straffes med bot eller fengsel inntil 6 år",
            "source": "s",
            "quote_lang": "no",
            "topic": "grovt",
        }
    ]
    bad = {
        "text": json.dumps(
            {
                "answer": "Fengsel inntil 3 år for grovt skadeverk.",
                "not_found": False,
                "lang": "nb",
                "claims": claims,
            }
        )
    }
    parsed, _ = lib._check_grounded(bad, "", "", ps)
    assert "3 år" not in lib._render_grounded(parsed, "nb", ps, {}, "")
    good = {
        "text": json.dumps(
            {
                "answer": "Grovt skadeverk kan gi inntil 6 år (§ 352).",
                "not_found": False,
                "lang": "nb",
                "claims": claims,
            }
        )
    }
    parsed, _ = lib._check_grounded(good, "", "", ps)
    assert lib._render_grounded(parsed, "nb", ps, {}, "").startswith(
        "Grovt skadeverk kan gi inntil 6 år"
    )


# ── Fixes from the moped-car live run ───────────────────────────────────────


def test_blocks_do_not_run_together():
    doc = DOC.replace(
        '<article class="legalP" id="p2-1">Grovt',
        '<article class="legalP" id="p2-1"><span>Grovt</span>',
    )
    _, paras = L.parse_document(doc.encode("utf-8"))
    assert "Skadeverk" not in paras[0]["body"][:12]
    assert all("  " not in p["body"] for p in paras)


def test_coverage_beats_one_common_word(tmp_path):
    common = """<html><head><title>Lov om pensjon</title></head><body><dl><dd class="titleShort">Pensjonsloven</dd></dl>
<article class="legalArticle" data-lovdata-URL="NL/lov/2000-01-01-1/§3"><h4 class="legalArticleHeader"><span class="legalArticleValue">§ 3</span>. <span class="legalArticleTitle">Aldersgrense</span></h4>
<article class="legalP">Aldersgrensen for aldersgrense er aldersgrense 70 år.</article></article></body></html>"""
    # As in the real collection, «aldersgrense» is common (20 documents) and «skadeverk» rare.
    files = {
        "nl/a.xml": DOC,
        **{f"nl/p{i}.xml": common.replace("2000-01-01-1", f"2000-01-01-{i}") for i in range(20)},
    }
    db = tmp_path / "lovdata.db"
    L.build_index([_archive(files)], db)
    hits = L.search("aldersgrense for grovt skadeverk", ["skadeverk", "grovt"], 3, db)
    assert hits[0]["title"].startswith("Straffeloven")


@pytest.mark.parametrize("name", ["Kåre", "Jarvis"])
def test_summary_naming_the_assistant_is_dropped(name, monkeypatch):
    monkeypatch.setattr(lib, "_get_settings", lambda: {"assistant_name": name})
    text = "§ 3-1. Klasse AM – moped – 16 år"
    ps = [
        {"title": "Førerkortforskriften § 3-1", "url": "u", "text": text, "_norm": lib._norm(text)}
    ]
    claims = [
        {
            "claim": "AM 16 år",
            "quote": "Klasse AM – moped – 16 år",
            "source": "s",
            "quote_lang": "no",
            "topic": "alder",
        }
    ]
    r = {
        "text": json.dumps(
            {
                "answer": f"{name} sier at alderen er 16 år.",
                "not_found": False,
                "lang": "nb",
                "claims": claims,
            }
        )
    }
    parsed, _ = lib._check_grounded(r, "", "", ps)
    assert f"{name} sier" not in lib._render_grounded(parsed, "nb", ps, {}, "")


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_law_quotes_shown_verbatim_without_9b_paraphrase_or_summary(lang, monkeypatch):
    text = "§ 2-1. Krav til elev under øvingskjøring. Likevel må øvingskjøring ikke finne sted før fylte 15 år."
    ps = [
        {
            "title": "Trafikkopplæringsforskriften § 2-1",
            "url": "https://lovdata.no/forskrift/x/§2-1",
            "text": text,
            "tier": "trusted",
            "type": "public_authority",
        }
    ]

    async def fake_chat(messages, schema=None):
        return {
            "text": json.dumps(
                {
                    "answer": "Man kan øvingskjøre fra 14 år.",
                    "not_found": False,
                    "lang": lang,
                    "claims": [
                        {
                            "claim": "Fra 14 år",
                            "quote": "Likevel må øvingskjøring ikke finne sted før fylte 15 år",
                            "source": "s",
                            "quote_lang": "no",
                            "topic": "alder",
                        }
                    ],
                }
            ),
            "meta": {},
        }

    monkeypatch.setattr(lib, "_library_chat", fake_chat)
    out = asyncio.run(
        lib._grounded_answer("sys", text, "msg", lang, task="q", sources=ps, quote_only=True)
    )
    assert "«Likevel må øvingskjøring ikke finne sted før fylte 15 år»" in out
    assert "Fra 14 år" not in out and "fra 14 år" not in out
    assert out.rstrip().endswith(t("lib_interpret_yourself", lang))
