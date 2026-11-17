"""P139 — NRK TV lookup (psapi.nrk.no, no key): channel, programme id, news series' newest available episode,
sequential series by season + episode, strict free-text search."""

import asyncio

import httpx
import pytest

from adapters import nrk_adapter as na

AVAILABLE = {"status": "available"}
ROUTES = {
    "/playback/manifest/channel/nrk1": {"ok": 1},
    "/playback/metadata/program/NNFA19101926": {"playability": "playable",
                                                "preplay": {"titles": {"title": "Dagsrevyen", "subtitle": "I dag"}}},
    "/playback/metadata/program/MUHH50000313": {"playability": "playable",
                                                "preplay": {"titles": {"title": "Side om side", "subtitle": "3."}}},
    "/tv/catalog/series/dagsrevyen": {"_embedded": {"instalments": {"_embedded": {"instalments": [
        {"prfId": "NNFA19102026", "availability": {"status": "coming"}},
        {"prfId": "NNFA19101926", "availability": AVAILABLE}]}}}},
    "/tv/catalog/series/side-om-side": {"_embedded": {"seasons": [{}, {}]}},
    "/tv/catalog/series/side-om-side/seasons/1": {"_embedded": {"episodes": [
        {"prfId": "MUHH50000313", "sequenceNumber": 3, "availability": AVAILABLE}]}},
    "/search": {"hits": [{"type": "serie", "hit": {"title": "XYZ", "url": "serie/xyz"}},
                         {"type": "serie", "hit": {"title": "Dagsrevyen", "url": "serie/dagsrevyen"}}]},
}


@pytest.fixture(autouse=True)
def api(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        body = ROUTES.get(req.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(na.httpx, "AsyncClient", client)


def run(*a):
    return asyncio.run(na.resolve_tv(*a))


def test_channel_programme_and_news_series():
    assert run("NRK1")["kind"] == "live"
    assert run("NNFA19101926") == {"id": "NNFA19101926", "kind": "programme", "title": "Dagsrevyen", "subtitle": "I dag"}
    hit = run("Dagsrevyen")
    assert (hit["id"], hit["kind"], hit["title"]) == ("NNFA19101926", "episode", "Dagsrevyen")  # «coming» skipped


def test_sequential_series_needs_and_takes_season_and_episode():
    assert run("Side om side")["kind"] == "needs_episode"
    assert run("Side om side", 1, 3)["id"] == "MUHH50000313"
    assert run("Side om side", 1, 9)["kind"] == "no_episode"


def test_search_only_takes_a_series_with_exactly_that_name():
    assert run("finnes ikke tull xyz") is None
    assert na.slug_of("Side om side") == "side-om-side" and na.slug_of("Blåsemafian") == "blasemafian"
