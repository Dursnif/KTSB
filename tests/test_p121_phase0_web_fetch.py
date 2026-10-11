"""
P121 Phase 0 — safe web fetching: trusted list (one loader, real www-prefix, fail-closed),
SSRF address guard on every hop, manual redirects under the caller's policy, byte cap,
content-type check. No real network: DNS and HTTP are mocked.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p121_phase0_web_fetch.py -v
"""

import asyncio
import sys

import httpx
import pytest

sys.path.insert(0, "/kaare")

import os

import yaml
from fastapi import HTTPException

import adapters.web_fetch as wf
import kaare_core.routers.router_settings as rs
from kaare_core.tools.i18n import t

PUBLIC_IP = "93.184.215.14"


@pytest.fixture
def trusted_file(tmp_path, monkeypatch):
    path = tmp_path / "trusted_sources.yaml"
    monkeypatch.setattr(wf, "_TRUSTED_PATH", path)
    monkeypatch.setattr(wf, "_trust_cache", {"mtime": None, "domains": [], "state": "unreadable"})
    return path


@pytest.fixture
def public_dns(monkeypatch):
    """Every host resolves to a public address unless listed in the returned dict."""
    overrides: dict[str, list[str]] = {}

    async def fake_resolve(host, port):
        return overrides.get(host, [PUBLIC_IP])

    monkeypatch.setattr(wf, "_resolve", fake_resolve)
    monkeypatch.setattr(
        wf,
        "get_service",
        lambda name: {
            "max_download_bytes": 1000,
            "fetch_timeout_s": 5,
            "per_host_interval_s": 0.001,
        },
    )
    monkeypatch.setattr(wf, "_robots_cache", {})
    monkeypatch.setattr(wf, "_page_cache", {})
    monkeypatch.setattr(wf, "_host_next", {})
    return overrides


def _write(path, domains):
    lines = ["sources:", "  general:"] + [f"  - domain: {d}" for d in domains]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Trusted list ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entry,host",
    [
        ("snl.no", "snl.no"),
        ("www.snl.no", "snl.no"),
        ("https://www.snl.no/path?q=1", "snl.no"),
        ("github.com/dursnif/ktsb", "github.com"),
        ("WIRED.com", "wired.com"),
        ("web.dev", "web.dev"),
        ("wwwexample.com", "wwwexample.com"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_domain(entry, host):
    assert wf.normalize_domain(entry) == host


def test_www_prefix_bug_is_gone(trusted_file):
    # Old lstrip("www.") turned wired.com into ired.com, so ired.com matched the list.
    _write(trusted_file, ["wired.com"])
    assert wf.is_trusted("https://www.wired.com/story")
    assert not wf.is_trusted("https://ired.com/")


def test_subdomains_match_but_lookalikes_do_not(trusted_file):
    _write(trusted_file, ["nrk.no"])
    assert wf.is_trusted("https://tv.nrk.no/x")
    assert not wf.is_trusted("https://evilnrk.no/x")
    assert not wf.is_trusted("https://nrk.no.evil.com/x")


def test_missing_file_fails_closed(trusted_file):
    assert wf.trust_list_state() == "unreadable"
    assert not wf.is_trusted("https://snl.no/")


def test_broken_file_fails_closed(trusted_file):
    trusted_file.write_text("sources: [unclosed\n", encoding="utf-8")
    assert wf.trust_list_state() == "unreadable"
    assert not wf.is_trusted("https://snl.no/")


def test_empty_valid_list_means_no_whitelist(trusted_file):
    trusted_file.write_text("sources: {}\n", encoding="utf-8")
    assert wf.trust_list_state() == "empty"
    assert wf.is_trusted("https://anything.example/")


def test_list_reloads_when_file_changes(trusted_file):
    _write(trusted_file, ["snl.no"])
    assert not wf.is_trusted("https://nrk.no/")
    _write(trusted_file, ["snl.no", "nrk.no"])
    st = trusted_file.stat()
    os.utime(trusted_file, (st.st_atime, st.st_mtime + 5))
    assert wf.is_trusted("https://nrk.no/")


# ── Address guard ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ip,public",
    [
        ("93.184.215.14", True),
        ("127.0.0.1", False),
        ("192.168.0.232", False),
        ("10.0.0.5", False),
        ("172.16.3.1", False),
        ("169.254.169.254", False),
        ("100.64.1.1", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fe80::1%eth0", False),
        ("::ffff:192.168.0.100", False),
        ("2606:4700::1111", True),
        ("not-an-ip", False),
    ],
)
def test_ip_is_public(ip, public):
    assert wf.ip_is_public(ip) is public


@pytest.mark.parametrize(
    "url,reason",
    [
        ("ftp://example.com/x", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("http://example.com:8000/", "port"),
        ("https://example.com:11440/", "port"),
        ("http://127.0.0.1/", "private_address"),
        ("http://[::1]/", "private_address"),
        ("http://192.168.0.100/api/", "private_address"),
    ],
)
def test_check_public_url_refuses(url, reason, monkeypatch):
    async def real_like(host, port):
        return [host] if host[0].isdigit() or ":" in host else [PUBLIC_IP]

    monkeypatch.setattr(wf, "_resolve", real_like)
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.check_public_url(url))
    assert e.value.reason == reason


def test_hostname_resolving_to_lan_is_refused(public_dns):
    public_dns["ha.example.com"] = ["192.168.0.100"]
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.check_public_url("https://ha.example.com/"))
    assert e.value.reason == "private_address"


def test_mixed_public_and_private_answer_is_refused(public_dns):
    public_dns["mixed.example.com"] = [PUBLIC_IP, "10.0.0.1"]
    with pytest.raises(wf.FetchError):
        asyncio.run(wf.check_public_url("https://mixed.example.com/"))


# ── Fetching ────────────────────────────────────────────────────────────────


def _transport(routes):
    def handler(request: httpx.Request):
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404)
        return routes[key]()

    return httpx.MockTransport(handler)


def test_plain_fetch(public_dns):
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<p>hi</p>"
            )
        }
    )
    final, body, ctype = asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert final == "https://a.example/" and body == b"<p>hi</p>" and ctype == "text/html"


def test_redirect_into_lan_is_refused(public_dns):
    public_dns["internal.example"] = ["192.168.0.232"]
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                302, headers={"location": "https://internal.example/x"}
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason == "private_address"


def test_redirect_to_ip_literal_on_odd_port_is_refused(public_dns):
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                301, headers={"location": "http://127.0.0.1:8000/api"}
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason in ("port", "private_address")


def test_redirect_must_pass_callers_policy(public_dns):
    tr = _transport(
        {
            "https://trusted.example/": lambda: httpx.Response(
                302, headers={"location": "https://other.example/"}
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(
            wf.fetch_raw(
                "https://trusted.example/", allow=lambda u: "trusted.example" in u, transport=tr
            )
        )
    assert e.value.reason == "not_allowed"


def test_relative_redirect_followed(public_dns):
    tr = _transport(
        {
            "https://a.example/old": lambda: httpx.Response(301, headers={"location": "/new"}),
            "https://a.example/new": lambda: httpx.Response(
                200, headers={"content-type": "text/plain"}, content=b"ok"
            ),
        }
    )
    final, body, _ = asyncio.run(wf.fetch_raw("https://a.example/old", transport=tr))
    assert final == "https://a.example/new" and body == b"ok"


def test_redirect_loop_stops(public_dns):
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                302, headers={"location": "https://a.example/"}
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason == "too_many_redirects"


def test_byte_cap_streamed(public_dns):
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"x" * 5000
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason == "too_large"


def test_declared_length_over_cap_refused_early(public_dns):
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                200, headers={"content-type": "text/html", "content-length": "999999"}, content=b"x"
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason == "too_large"


@pytest.mark.parametrize(
    "ctype", ["application/pdf", "application/octet-stream", "image/png", "video/mp4"]
)
def test_non_text_refused(public_dns, ctype):
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                200, headers={"content-type": ctype}, content=b"x"
            )
        }
    )
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason == "content_type"


def test_non_200_refused(public_dns):
    tr = _transport({"https://a.example/": lambda: httpx.Response(500)})
    with pytest.raises(wf.FetchError) as e:
        asyncio.run(wf.fetch_raw("https://a.example/", transport=tr))
    assert e.value.reason == "status_500"


def test_fetch_page_text_extracts_html(public_dns):
    html = (
        b"<html><body><article><p>"
        + b"Wieliczka has about 800 steps on the tourist route. " * 5
        + b"</p></article></body></html>"
    )
    tr = _transport(
        {
            "https://a.example/": lambda: httpx.Response(
                200, headers={"content-type": "text/html"}, content=html
            )
        }
    )
    _final, text = asyncio.run(wf.fetch_page_text("https://a.example/", transport=tr))
    assert "800 steps" in text


# ── Messages ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_unreadable_message_names_real_gui_path(lang):
    msg = t("web_trust_list_unreadable", lang)
    expected = {"nb": "Nettsøk/Vær", "en": "Web Search & Weather", "de": "Websuche & Wetter"}[lang]
    assert expected in msg
    assert expected in t("svc_trusted_hint", lang)


# ── GUI save normalises domains ─────────────────────────────────────────────


def test_put_trusted_sources_stores_bare_hosts(tmp_path, monkeypatch):
    path = tmp_path / "trusted_sources.yaml"
    path.write_text("sources: {}\n", encoding="utf-8")
    monkeypatch.setattr(rs, "_TRUSTED_PATH", path)
    payload = {
        "general": [
            {"domain": "https://www.snl.no/x", "beskrivelse": "d"},
            {"domain": "github.com/dursnif/ktsb"},
        ]
    }
    asyncio.run(rs.api_put_trusted_sources(payload, _u=None))
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))["sources"]["general"]
    assert [e["domain"] for e in saved] == ["snl.no", "github.com"]
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_trusted_sources({"general": [{"domain": "  "}]}, _u=None))
