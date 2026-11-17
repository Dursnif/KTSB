"""P151 phase 5: recipe → exact text + card (chat/screen), by code from schema.org data."""

import asyncio
import json

import pytest

from kaare_core.cards import recipe as rc
from kaare_core.cards import store as cs
from kaare_core.tools import definitions as d
from kaare_core.tools import executor_library as el
from kaare_core.voice import screen as screen_core

RECIPE = {
    "@type": "Recipe", "name": "Test Cakes &amp; Sauce",
    "image": [{"@type": "ImageObject", "url": "https://img.example/cake.webp"}],
    "totalTime": "PT1H5M", "recipeYield": ["4", "4 servings"],
    "recipeIngredient": ["400 g flour", "0,25 ts salt", "<b>2</b> eggs"],
    "recipeInstructions": [
        {"@type": "HowToSection", "name": "Dough", "itemListElement": [
            {"@type": "HowToStep", "text": "Mix the flour.\n"}, {"@type": "HowToStep", "text": "Add eggs."}]},
        {"@type": "HowToStep", "text": "Bake <i>20</i> minutes."},
    ],
}
PAGE = ('<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},'
        + json.dumps(RECIPE) + ']}</script></head><body>ads ads ads</body></html>')


def test_find_and_normalize():
    rec = rc.normalize(rc.find_recipe(PAGE), "https://www.example.no/r/1")
    assert rec["name"] == "Test Cakes & Sauce" and rec["minutes"] == 65 and rec["yield"] == "4"
    assert rec["ingredients"] == ["400 g flour", "0,25 ts salt", "2 eggs"]  # exact amounts, tags stripped
    assert rec["steps"] == ["Mix the flour.", "Add eggs.", "Bake 20 minutes."]
    assert rec["image"] == "https://img.example/cake.webp" and rc.source_name(rec["source"]) == "example.no"


def test_no_recipe_or_broken_json():
    assert rc.find_recipe("<html>no data</html>") is None
    assert rc.find_recipe('<script type="application/ld+json">{broken</script>') is None


def test_text_keeps_every_amount_exactly():
    rec = rc.normalize(rc.find_recipe(PAGE))
    text = rc.as_text(rec, "en")
    for line in ("- 400 g flour", "- 0,25 ts salt", "1. Mix the flour.", "3. Bake 20 minutes.", "65 min"):
        assert line in text


@pytest.mark.parametrize("mode", ["screen", "chat"])
def test_render_is_escaped_and_scriptless(mode):
    r = dict(RECIPE, name='<script>alert(1)</script>Nice', recipeIngredient=['1 "x" <img src=x onerror=y>'])
    out = rc.render(rc.normalize(r, "https://e.no/x"), mode, "nb")
    # tags from the page are removed entirely (not just escaped) — only their text is left
    assert "<script" not in out.lower() and "onerror" not in out and "alert(1) Nice" in out
    assert "Ingredienser" in out and "Slik gjør du" in out and "Kilde: e.no" in out
    assert ("grid-template-columns" in out) == (mode == "screen")


def test_card_store_trims_by_size(tmp_path):
    ids = [cs.save("x" * 400, tmp_path, max_bytes=1000) for _ in range(5)]
    left = sorted(p.stem for p in tmp_path.glob("*.html"))
    assert len(left) == 2 and set(left) == set(ids[-2:])  # oldest go first (size, never age)
    assert cs.path_of(ids[-1], tmp_path) and cs.path_of("../etc/passwd", tmp_path) is None


@pytest.fixture
def lib(monkeypatch, tmp_path):
    monkeypatch.setattr(screen_core, "enabled", lambda settings=None: True)
    monkeypatch.setattr(el, "_url_gate", lambda url, lang: (None, "open", ""))

    async def fetch_raw(url, allow=None):
        return "https://www.example.no/r/1", PAGE.encode(), "text/html"

    monkeypatch.setattr(el.web_fetch, "fetch_raw", fetch_raw)
    real_save = cs.save
    monkeypatch.setattr(el.card_store, "save", lambda html: real_save(html, tmp_path))
    calls: list = []

    async def call(path, payload, timeout=60):
        calls.append((path, payload))
        return {"ok": True, "showing": True}

    async def targets(arguments):
        return ["tv_a"], []

    monkeypatch.setattr(el.cast_client, "call", call)
    monkeypatch.setattr(el, "_live_screen_targets", targets)
    return calls, tmp_path


def test_recipe_action_gives_exact_text_and_a_card(lib):
    calls, tmp = lib
    out = asyncio.run(el._recipe("https://www.example.no/r/1", {}, "en"))
    assert out.startswith("RECIPE (exact from example.no") and "- 400 g flour" in out
    cid = out.split("/api/card/")[1].split()[0]
    assert (tmp / f"{cid}.html").is_file() and calls == []  # no screen asked → nothing sent to a screen


def test_recipe_on_a_screen_only_when_confirmed(lib):
    calls, _ = lib
    out = asyncio.run(el._recipe("https://www.example.no/r/1", {"screen": "tv"}, "en"))
    assert calls and calls[0][0] == "/screen/card" and calls[0][1]["node"] == "tv_a"
    assert "grid-template-columns" in calls[0][1]["html"] and "screen confirms it loaded" in out


def test_recipe_page_without_data(lib, monkeypatch):
    async def fetch_raw(url, allow=None):
        return url, b"<html>just text</html>", "text/html"

    monkeypatch.setattr(el.web_fetch, "fetch_raw", fetch_raw)
    assert "No structured recipe" in asyncio.run(el._recipe("https://x.no", {}, "en"))


def test_recipe_is_behind_the_screens_switch(monkeypatch):
    monkeypatch.setattr(screen_core, "enabled", lambda settings=None: False)
    props = d._library_tool("en")["function"]["parameters"]["properties"]
    assert "recipe" not in props["action"]["enum"] and "screen" not in props


# ── recipe by search words: code does the whole job (owner decision 2026-11-16, chat test: Kåre had no URL and guessed one) ──

def test_recipe_by_query_searches_and_takes_the_first_hit_with_data(lib, monkeypatch):
    async def sources(query):
        return [{"url": "https://blog.example/no-data", "tier": "open"},
                {"url": "https://www.example.no/r/1", "tier": "trusted"},
                {"url": "https://other.example/r/2", "tier": "open"}]

    async def fetch_raw(url, allow=None):
        if "no-data" in url:
            return url, b"<html>a blog post</html>", "text/html"
        return url, PAGE.encode(), "text/html"

    async def no_library(*a, **k):
        raise AssertionError("Miss Library must not be asked")

    monkeypatch.setattr(el, "search_sources", sources)
    monkeypatch.setattr(el.web_fetch, "fetch_raw", fetch_raw)
    monkeypatch.setattr(el, "_ask_library", no_library)
    out = asyncio.run(el._recipe("", {"query": "test cakes"}, "en"))
    assert out.startswith("RECIPE (exact from example.no") and "- 400 g flour" in out


def test_recipe_by_query_nothing_found(lib, monkeypatch):
    async def sources(query):
        return []

    monkeypatch.setattr(el, "search_sources", sources)
    assert "No recipe page with readable recipe data" in asyncio.run(el._recipe("", {"query": "xyz"}, "en"))


def test_recipe_needs_query_or_url(lib):
    assert "needs query" in asyncio.run(el._recipe("", {}, "en"))


def test_a_page_that_cannot_be_fetched_is_not_called_a_page_without_data(lib, monkeypatch):
    """BUG 2026-11-16 (live chat test): a guessed URL gave 404, and Kåre was told «the page has no recipe data» — the
    wrong reason. A failed fetch must say the page could not be fetched."""
    async def fetch_raw(url, allow=None):
        raise el.web_fetch.FetchError("status_404")

    monkeypatch.setattr(el.web_fetch, "fetch_raw", fetch_raw)
    out = asyncio.run(el._recipe("https://www.example.no/guess", {}, "en"))
    assert "No structured recipe" not in out and "https://www.example.no/guess" in out
