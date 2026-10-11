"""
P121 Phase 2 — tiered retrieval: whitelisted results get the first `trusted_slots` places, the
open web fills the rest (switchable), blocked domains are never used, every source carries its
tier to Kåre; whitelist entries have type/trust (fixed GUI choices) in an English-keyed format.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase2_tiers.py -v
"""

import asyncio
import json
import sys

import pytest
import yaml
from fastapi import HTTPException

sys.path.insert(0, "/kaare")

import pathlib

import adapters.web_fetch as wf
import adapters.web_search_adapter as wsa
import kaare_core.routers.router_settings as rs
import kaare_core.tools.executor_library as lib
from kaare_core.tools.i18n import t

TRUSTED_YAML = {
    "sources": {
        "encyclopedias": [
            {
                "domain": "snl.no",
                "beskrivelse": "Store norske",
                "type": "encyclopedia",
                "trust": "high",
            }
        ],
        "law": [{"domain": "lovdata.no", "description": "Laws"}],
    },
    "blocked": ["spam.example", "https://www.junk.example/x"],
}


@pytest.fixture
def lists(tmp_path, monkeypatch):
    path = tmp_path / "trusted_sources.yaml"
    path.write_text(yaml.dump(TRUSTED_YAML, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(wf, "_TRUSTED_PATH", path)
    monkeypatch.setattr(
        wf,
        "_trust_cache",
        {"mtime": None, "domains": [], "entries": {}, "blocked": [], "state": "unreadable"},
    )
    cfg = {"trusted_slots": 2, "open_web": True}
    monkeypatch.setattr(wf, "get_service", lambda name: cfg)
    monkeypatch.setattr(wsa, "_WS_CFG", {**wsa._WS_CFG, "max_results": 3})
    return cfg


def _raw(*hosts):
    return [{"title": h, "url": f"https://{h}/page"} for h in hosts]


# ── Whitelist entries ───────────────────────────────────────────────────────


def test_entry_defaults_and_legacy_key(lists):
    snl = wf.listed_entry("https://snl.no/x")
    assert (
        snl["description"] == "Store norske"
        and snl["type"] == "encyclopedia"
        and snl["trust"] == "high"
    )
    law = wf.listed_entry("https://lovdata.no/x")
    assert law["type"] == "other_serious" and law["trust"] == "normal"


def test_normalize_entry_rejects_unknown_values():
    e = wf.normalize_entry({"domain": "https://www.x.no/a", "type": "blog", "trust": "absolute"})
    assert e == {"domain": "x.no", "description": "", "type": "other_serious", "trust": "normal"}


def test_blocked_list_normalised(lists):
    assert wf.is_blocked("https://spam.example/a") and wf.is_blocked("https://sub.junk.example/b")
    assert not wf.is_trusted("https://spam.example/a")
    assert not wf.not_blocked("https://junk.example/")


def test_empty_list_lists_nothing(tmp_path, monkeypatch):
    path = tmp_path / "t.yaml"
    path.write_text("sources: {}\n", encoding="utf-8")
    monkeypatch.setattr(wf, "_TRUSTED_PATH", path)
    monkeypatch.setattr(
        wf,
        "_trust_cache",
        {"mtime": None, "domains": [], "entries": {}, "blocked": [], "state": "unreadable"},
    )
    assert wf.is_trusted("https://any.example/") and wf.listed_entry("https://any.example/") is None


# ── Slot selection ──────────────────────────────────────────────────────────


def test_trusted_first_then_open(lists):
    out = wsa._select_sources(
        _raw("blog.example", "snl.no", "forum.example", "lovdata.no", "news.example")
    )[:3]
    assert [o["url"].split("/")[2] for o in out] == ["snl.no", "lovdata.no", "blog.example"]
    assert [o["tier"] for o in out] == ["trusted", "trusted", "open"]
    assert out[0]["type"] == "encyclopedia"


def test_open_web_fills_when_no_trusted(lists):
    out = wsa._select_sources(_raw("gotakanal.se", "sv.wikipedia.org", "visit.example"))
    assert [o["tier"] for o in out] == ["open", "open", "open"]


def test_leftover_places_go_back_to_trusted(lists):
    lists["trusted_slots"] = 1
    out = wsa._select_sources(_raw("snl.no", "lovdata.no", "x.snl.no"))[:3]
    assert len(out) == 3 and all(o["tier"] == "trusted" for o in out)


def test_open_web_off_means_whitelist_only(lists):
    lists["open_web"] = False
    out = wsa._select_sources(_raw("blog.example", "snl.no"))
    assert [o["tier"] for o in out] == ["trusted"]


def test_blocked_and_duplicates_never_used(lists):
    out = wsa._select_sources(_raw("spam.example", "snl.no", "snl.no", "junk.example"))
    assert [o["url"] for o in out] == ["https://snl.no/page"]


def test_one_result_per_site_and_backups_follow(lists):
    raw = [
        {"title": str(i), "url": f"https://www.tripadvisor.example/q{i}"} for i in range(3)
    ] + _raw("blog.example", "visit.example", "forum.example")
    out = wsa._select_sources(raw)
    hosts = [o["url"].split("/")[2] for o in out]
    assert hosts[:3] == ["www.tripadvisor.example", "blog.example", "visit.example"]
    assert hosts[3:] == ["forum.example"]


def test_failed_pages_are_replaced_by_backups(lists, monkeypatch):
    monkeypatch.setattr(
        wsa, "_WS_CFG", {**wsa._WS_CFG, "provider": "brave", "fallback": "", "max_results": 2}
    )
    monkeypatch.setattr(wsa, "_provider_ready", lambda p: True)

    async def provider(chain, query):
        return wsa._select_sources(_raw("blocked-by-site.example", "ok1.example", "ok2.example"))

    async def official(subject, keywords, lang):
        return [], set()

    async def fetch(url, tier="trusted"):
        return "" if "blocked-by-site" in url else "text"

    async def library(query, sources, lang="nb", subject="", keywords=None, stakes="normal"):
        return [s["url"].split("/")[2] for s in sources]

    for name, fn in (
        ("_provider_results", provider),
        ("_official", official),
        ("_fetch_content", fetch),
        ("_ask_library", library),
    ):
        monkeypatch.setattr(wsa, name, fn)
    assert asyncio.run(wsa.web_search("q")) == ["ok1.example", "ok2.example"]


@pytest.mark.parametrize(
    "value,expected", [(True, True), (False, False), ("false", False), ("on", True), (None, True)]
)
def test_open_web_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setattr(
        wf, "get_service", lambda name: {} if value is None else {"open_web": value}
    )
    assert wf.open_web_enabled() is expected


# ── Tier reaches Kåre ───────────────────────────────────────────────────────


def _result(claims):
    return {"text": json.dumps({"answer": "x", "claims": claims, "not_found": False, "lang": "nb"})}


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_tier_label_and_open_web_note(lang):
    sources = [
        {
            "title": "Göta kanal",
            "url": "https://snl.no/a",
            "text": "Kanalen har 58 sluser.",
            "tier": "trusted",
        },
        {
            "title": "Göta Canal",
            "url": "https://www.gotakanal.se/x",
            "text": "The canal is 190 km long.",
            "tier": "open",
        },
    ]
    ps = [{**s, "_norm": lib._norm(s["text"])} for s in sources]
    parsed, _ = lib._check_grounded(
        _result(
            [
                {"claim": "58 sluser", "quote": "Kanalen har 58 sluser", "source": "x"},
                {"claim": "190 km", "quote": "The canal is 190 km long", "source": "x"},
            ]
        ),
        "",
        "",
        ps,
    )
    out = lib._render_grounded(parsed, lang, ps, {}, "")
    assert f"snl.no · {t('lib_tier_trusted', lang)}" in out
    assert f"gotakanal.se · {t('lib_tier_open', lang)}" in out
    assert t("lib_open_web_note", lang) in out


def test_no_open_web_note_when_all_trusted():
    ps = [
        {
            "title": "A",
            "url": "https://snl.no/a",
            "text": "Kanalen har 58 sluser.",
            "tier": "trusted",
            "_norm": lib._norm("Kanalen har 58 sluser."),
        }
    ]
    parsed, _ = lib._check_grounded(
        _result([{"claim": "58", "quote": "Kanalen har 58 sluser", "source": "x"}]), "", "", ps
    )
    assert t("lib_open_web_note", "nb") not in lib._render_grounded(parsed, "nb", ps, {}, "")


# ── fetch_url policy (D-P12) ────────────────────────────────────────────────


def _fake_fetch(monkeypatch):
    calls = []

    async def fake(url, allow=None, transport=None):
        calls.append((url, allow))
        return url, "Page text."

    monkeypatch.setattr(lib.web_fetch, "fetch_page_text", fake)
    monkeypatch.setattr(lib, "_lib_cfg", {**lib._lib_cfg, "enabled": False})
    return calls


def test_fetch_url_open_page_allowed_and_labelled(lists, monkeypatch):
    calls = _fake_fetch(monkeypatch)
    out = asyncio.run(lib._fetch_url("https://www.gotakanal.se/x", {}, "nb"))
    assert out.startswith(
        f"{t('lib_fetch_source', 'nb')}: gotakanal.se · {t('lib_tier_open', 'nb')}"
    )
    assert calls and calls[0][1] is wf.not_blocked


def test_fetch_url_open_page_refused_when_open_web_off(lists, monkeypatch):
    lists["open_web"] = False
    calls = _fake_fetch(monkeypatch)
    out = asyncio.run(lib._fetch_url("https://www.gotakanal.se/x", {}, "nb"))
    assert t("svc_trusted_hint", "nb") in out and not calls


def test_fetch_url_blocked_refused(lists, monkeypatch):
    calls = _fake_fetch(monkeypatch)
    asyncio.run(lib._fetch_url("https://spam.example/x", {}, "nb"))
    assert not calls


def test_fetch_url_trusted_keeps_trusted_policy(lists, monkeypatch):
    calls = _fake_fetch(monkeypatch)
    out = asyncio.run(lib._fetch_url("https://snl.no/x", {}, "nb"))
    assert t("lib_tier_trusted", "nb") in out and calls[0][1] is wf.is_trusted


# ── Settings routes ─────────────────────────────────────────────────────────


def test_get_trusted_returns_current_format(lists, monkeypatch):
    monkeypatch.setattr(rs, "_TRUSTED_PATH", wf._TRUSTED_PATH)
    data = asyncio.run(rs.api_get_trusted_sources(_u=None))
    assert data["encyclopedias"][0] == {
        "domain": "snl.no",
        "description": "Store norske",
        "type": "encyclopedia",
        "trust": "high",
    }


def test_put_trusted_keeps_blocked_and_rejects_bad_type(lists, monkeypatch):
    monkeypatch.setattr(rs, "_TRUSTED_PATH", wf._TRUSTED_PATH)
    asyncio.run(
        rs.api_put_trusted_sources({"general": [{"domain": "nrk.no", "type": "newsroom"}]}, _u=None)
    )
    saved = yaml.safe_load(wf._TRUSTED_PATH.read_text(encoding="utf-8"))
    assert saved["sources"]["general"][0] == {
        "domain": "nrk.no",
        "description": "",
        "type": "newsroom",
        "trust": "normal",
    }
    assert saved["blocked"] == TRUSTED_YAML["blocked"]
    with pytest.raises(HTTPException):
        asyncio.run(
            rs.api_put_trusted_sources({"general": [{"domain": "nrk.no", "type": "blog"}]}, _u=None)
        )


def test_put_blocked_normalises(lists, monkeypatch):
    monkeypatch.setattr(rs, "_TRUSTED_PATH", wf._TRUSTED_PATH)
    r = asyncio.run(
        rs.api_put_blocked_sources(
            {"blocked": ["https://www.Spam.example/x", "spam.example", "b.example"]}, _u=None
        )
    )
    assert r["blocked"] == ["spam.example", "b.example"]
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_blocked_sources({"blocked": "spam.example"}, _u=None))


def test_put_websearch_open_web_must_be_bool():
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_websearch({"open_web": "yes"}, _u=None))


def test_paid_counter(monkeypatch):
    monkeypatch.setattr(wsa, "_paid_calls", {"date": "", "counts": {}})
    wsa._count_paid_call("brave")
    wsa._count_paid_call("brave")
    assert wsa.paid_calls_today() == {"brave": 2}


def test_shipped_template_loads_with_types():
    d = yaml.safe_load(
        pathlib.Path("/kaare/configs_default/trusted_sources.yaml").read_text(encoding="utf-8")
    )
    entries = [e for v in d["sources"].values() for e in v]
    assert all(set(e) == {"domain", "description", "type", "trust"} for e in entries)
    assert all(k.isascii() for k in d["sources"])
    assert any(e["domain"] == "wikipedia.org" for e in entries) and d["blocked"] == []
