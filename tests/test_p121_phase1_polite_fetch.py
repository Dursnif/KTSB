"""
P121 Phase 1 — polite fetching and search plumbing: honest versioned User-Agent, robots.txt
(RFC 9309 status rules), per-host pacing, memory page cache, total search deadline, DuckDuckGo
removed (old "ddg" configs move to SearXNG), provider chain, settings validation.
No real network: DNS and HTTP are mocked.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase1_polite_fetch.py -v
"""

import asyncio
import sys
import time

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, "/kaare")

import pathlib

import yaml

import adapters.web_fetch as wf
import adapters.web_search_adapter as wsa
import kaare_core.routers.router_settings as rs
import kaare_core.tools.executor_library as lib
from kaare_core.tools.i18n import t
from kaare_core.version import get_version

SETTINGS = {
    "max_download_bytes": 100_000,
    "fetch_timeout_s": 5,
    "per_host_interval_s": 0.001,
    "page_cache_ttl_s": 3600,
    "page_cache_max_entries": 3,
    "search_total_timeout_s": 60,
}


@pytest.fixture
def net(monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.215.14"]

    monkeypatch.setattr(wf, "_resolve", fake_resolve)
    cfg = dict(SETTINGS)
    monkeypatch.setattr(wf, "get_service", lambda name: cfg)
    monkeypatch.setattr(wf, "_robots_cache", {})
    monkeypatch.setattr(wf, "_page_cache", {})
    monkeypatch.setattr(wf, "_host_next", {})
    return cfg


class Site:
    """MockTransport with per-URL responses and a request log."""

    def __init__(self, routes):
        self.routes = routes
        self.seen: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        self.seen.append(request)
        make = self.routes.get(str(request.url))
        return make() if make else httpx.Response(404)

    def hits(self, url):
        return sum(1 for r in self.seen if str(r.url) == url)


def HTML(body):
    return lambda: httpx.Response(200, headers={"content-type": "text/html"}, content=body)


def ROBOTS(text):
    return lambda: httpx.Response(
        200, headers={"content-type": "text/plain"}, content=text.encode()
    )


# ── Identity ────────────────────────────────────────────────────────────────


def test_version_from_env(monkeypatch):
    monkeypatch.setenv("KTSB_VERSION", "v1.9.0")
    assert get_version() == "v1.9.0"
    monkeypatch.delenv("KTSB_VERSION")
    assert get_version() == "dev"


def test_user_agent_is_honest(monkeypatch):
    monkeypatch.setenv("KTSB_VERSION", "v1.9.0")
    ua = wf.user_agent()
    assert "KTSB/v1.9.0" in ua and "github.com/Dursnif/KTSB" in ua and "Kaare/1.0" not in ua


def test_requests_carry_the_user_agent(net):
    site = Site({"https://a.example/": HTML(b"<p>x</p>")})
    asyncio.run(wf.fetch_raw("https://a.example/", transport=site.transport))
    assert all("KTSB/" in r.headers["user-agent"] for r in site.seen)


# ── robots.txt ──────────────────────────────────────────────────────────────


def test_robots_disallow_is_obeyed(net):
    site = Site(
        {
            "https://a.example/robots.txt": ROBOTS("User-agent: *\nDisallow: /private\n"),
            "https://a.example/private/x": HTML(b"<p>secret</p>"),
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/private/x", transport=site.transport))
    assert e.value.reason == "robots"
    assert site.hits("https://a.example/private/x") == 0


def test_robots_rule_for_ktsb_token(net):
    site = Site(
        {
            "https://a.example/robots.txt": ROBOTS(
                "User-agent: KTSB\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
            ),
            "https://a.example/page": HTML(b"<p>x</p>"),
        }
    )
    with pytest.raises(wf.FetchError):
        asyncio.run(wf.fetch_raw("https://a.example/page", transport=site.transport))


def test_robots_404_means_allowed(net):
    site = Site({"https://a.example/page": HTML(b"<p>x</p>")})
    asyncio.run(wf.fetch_raw("https://a.example/page", transport=site.transport))
    assert site.hits("https://a.example/page") == 1


def test_robots_5xx_means_disallowed(net):
    site = Site(
        {
            "https://a.example/robots.txt": lambda: httpx.Response(503),
            "https://a.example/page": HTML(b"<p>x</p>"),
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/page", transport=site.transport))
    assert e.value.reason == "robots"


def test_robots_fetched_once_per_host(net):
    site = Site({"https://a.example/p1": HTML(b"1"), "https://a.example/p2": HTML(b"2")})

    async def two():
        await wf.fetch_raw("https://a.example/p1", transport=site.transport)
        await wf.fetch_raw("https://a.example/p2", transport=site.transport)

    asyncio.run(two())
    assert site.hits("https://a.example/robots.txt") == 1


def test_robots_checked_on_redirect_target(net):
    site = Site(
        {
            "https://a.example/go": lambda: httpx.Response(
                302, headers={"location": "https://b.example/no"}
            ),
            "https://b.example/robots.txt": ROBOTS("User-agent: *\nDisallow: /no\n"),
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/go", transport=site.transport))
    assert e.value.reason == "robots"


# ── Pacing + cache ──────────────────────────────────────────────────────────


def test_same_host_is_paced(net):
    net["per_host_interval_s"] = 0.2
    site = Site({"https://a.example/p1": HTML(b"1"), "https://a.example/p2": HTML(b"2")})

    async def two():
        t0 = time.monotonic()
        await wf.fetch_raw("https://a.example/p1", transport=site.transport)
        await wf.fetch_raw("https://a.example/p2", transport=site.transport)
        return time.monotonic() - t0

    assert asyncio.run(two()) >= 0.4  # robots + p1 + p2 → at least two full intervals


def test_page_cache_serves_second_call(net):
    body = (
        b"<html><body><article><p>"
        + b"Cached text about the canal locks. " * 5
        + b"</p></article></body></html>"
    )
    site = Site({"https://a.example/c": HTML(body)})

    async def twice():
        a = await wf.fetch_page_text("https://a.example/c", transport=site.transport)
        b = await wf.fetch_page_text("https://a.example/c", transport=site.transport)
        return a, b

    a, b = asyncio.run(twice())
    assert a == b and site.hits("https://a.example/c") == 1


def test_cached_page_still_obeys_allow(net):
    wf._cache_put("https://a.example/c", "https://other.example/c", "text")
    site = Site({})
    with pytest.raises(wf.FetchError):
        asyncio.run(
            wf.fetch_page_text(
                "https://a.example/c", allow=lambda u: "a.example" in u, transport=site.transport
            )
        )


def test_page_cache_is_capped(net):
    for i in range(5):
        wf._cache_put(f"https://a.example/{i}", f"https://a.example/{i}", "x")
    assert len(wf._page_cache) == 3


def test_setting_falls_back_on_bad_values(monkeypatch):
    monkeypatch.setattr(
        wf, "get_service", lambda name: {"fetch_timeout_s": "abc", "max_download_bytes": -5}
    )
    assert wf.setting("fetch_timeout_s") == 8.0
    assert wf.setting("max_download_bytes") == 3_000_000


# ── Search plumbing ─────────────────────────────────────────────────────────


def _cfg_file(tmp_path, monkeypatch, web_search: dict, template_url="http://searxng:8080"):
    svc = tmp_path / "services.yaml"
    svc.write_text(yaml.dump({"web_search": web_search}), encoding="utf-8")
    tpl = tmp_path / "tpl.yaml"
    tpl.write_text(yaml.dump({"web_search": {"searxng_url": template_url}}), encoding="utf-8")
    monkeypatch.setattr(wsa, "_SERVICES_PATH", svc)
    monkeypatch.setattr(wsa, "_TEMPLATE_SERVICES_PATH", tpl)


def test_old_ddg_config_moves_to_searxng(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch, {"provider": "ddg", "fallback": "ddg", "searxng_url": ""})
    cfg = wsa.load_websearch_config()
    assert (
        cfg["provider"] == "searxng"
        and cfg["searxng_url"] == "http://searxng:8080"
        and cfg["fallback"] == ""
    )


def test_configured_brave_is_kept(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch, {"provider": "brave", "fallback": "brave"})
    cfg = wsa.load_websearch_config()
    assert cfg["provider"] == "brave" and cfg["fallback"] == "brave"


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_no_ready_provider_says_how_to_fix(monkeypatch, lang):
    monkeypatch.setattr(
        wsa,
        "_WS_CFG",
        {**wsa._WS_CFG, "provider": "searxng", "fallback": "brave", "searxng_url": ""},
    )
    monkeypatch.setattr(wsa, "_BRAVE_KEY", "")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(wf, "trust_list_state", lambda: "ok")
    monkeypatch.setattr(wsa, "get_lang", lambda uid: lang)
    out = asyncio.run(wsa.web_search("q"))
    assert out == t("web_search_not_configured", lang)
    assert {"nb": "Nettsøk/Vær", "en": "Web Search & Weather", "de": "Websuche & Wetter"}[
        lang
    ] in out


def test_searxng_http_error_is_caught(monkeypatch):
    monkeypatch.setattr(wsa, "_WS_CFG", {**wsa._WS_CFG, "searxng_url": "http://searxng.test"})

    class Boom(httpx.AsyncClient):
        async def get(self, *a, **k):
            return httpx.Response(403, request=httpx.Request("GET", "http://searxng.test/search"))

    monkeypatch.setattr(wsa.httpx, "AsyncClient", Boom)
    assert asyncio.run(wsa._searxng_search("q", 5)) == []


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_total_deadline_stops_search(monkeypatch, lang):
    async def slow(*a, **k):
        await asyncio.sleep(5)
        return "never"

    monkeypatch.setattr(lib, "_søk_nett", slow)
    monkeypatch.setattr(lib.web_fetch, "setting", lambda key: 0.05)
    monkeypatch.setattr(lib, "get_lang", lambda uid: lang)
    monkeypatch.setattr(
        lib,
        "get_ltm",
        lambda: type("L", (), {"log_agent_message": staticmethod(lambda **k: asyncio.sleep(0))})(),
    )
    out = asyncio.run(lib._dispatch("web_search", {"query": "q", "near_home": False}))
    assert out.startswith(t("web_search_timeout", lang, s=0)) and t("lib_retry_hint", lang) in out


# ── Settings route validation ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "ddg"},
        {"fallback": "duckduckgo"},
        {"fetch_timeout_s": -1},
        {"max_download_bytes": "lots"},
        {"unknown_key": 1},
    ],
)
def test_put_websearch_rejects(payload):
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_websearch(dict(payload), _u=None))


def test_put_websearch_saves_and_coerces(tmp_path, monkeypatch):
    svc = tmp_path / "services.yaml"
    svc.write_text("web_search: {}\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_SERVICES_PATH", svc)
    monkeypatch.setattr(rs, "reload_core_config", list)
    monkeypatch.setattr(rs, "reload_websearch_config", lambda: None)
    asyncio.run(
        rs.api_put_websearch(
            {
                "provider": "searxng",
                "fallback": "",
                "max_download_bytes": "2000000",
                "per_host_interval_s": 0.5,
            },
            _u=None,
        )
    )
    saved = yaml.safe_load(svc.read_text(encoding="utf-8"))["web_search"]
    assert saved["max_download_bytes"] == 2000000 and saved["per_host_interval_s"] == 0.5


def test_no_ddg_left_in_code():
    src = pathlib.Path("/kaare/adapters/web_search_adapter.py").read_text(encoding="utf-8")
    assert "duckduckgo_search" not in src and "_ddg_search" not in src


def test_network_errors_become_fetch_errors(net):
    def boom(request):
        raise httpx.ReadTimeout("", request=request)

    with pytest.raises(wf.FetchError) as e:
        asyncio.run(
            wf.fetch_raw(
                "https://slow.example/", transport=httpx.MockTransport(boom), respect_robots=False
            )
        )
    assert e.value.reason.startswith("network_")


def test_unreachable_robots_means_disallow(net):
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    assert (
        asyncio.run(wf.robots_allows("https://down.example/x", transport=httpx.MockTransport(boom)))
        is False
    )
