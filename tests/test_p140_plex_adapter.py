"""P140 phase 1 (N31): Plex sessions endpoint, shared token reader, poller state logging, i18n."""

import asyncio

import httpx
import pytest

import adapters.plex_adapter as plex

_REAL_CLIENT = httpx.AsyncClient

_SESSION = {
    "MediaContainer": {
        "size": 1,
        "Metadata": [
            {
                "type": "episode",
                "grandparentTitle": "Show",
                "title": "Pilot",
                "parentIndex": 2,
                "index": 5,
                "viewOffset": 60_000,
                "duration": 120_000,
                "User": {"title": "someone"},
                "Player": {"title": "TV", "state": "playing"},
            }
        ],
    }
}


def _mock_client(monkeypatch, module, handler):
    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _REAL_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


@pytest.fixture
def plex_cfg(monkeypatch, tmp_path):
    env = tmp_path / "plex.env"
    env.write_text('PLEX_TOKEN="tok123"\n', encoding="utf-8")
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.setattr(plex, "_PLEX_ENV_PATH", env)
    monkeypatch.setattr(plex, "_plex_cfg", lambda: {"url": "http://pms:32400", "timeout": 5})
    return env


def test_fmt_se_tolerates_missing_numbers():
    assert plex.fmt_se(2, 5) == "S02E05"
    assert plex.fmt_se("3", "11") == "S03E11"
    assert plex.fmt_se(None, "?") == "S?E?"


def test_get_token_reads_gui_file_and_env_wins(plex_cfg, monkeypatch):
    assert plex.get_token() == "tok123"
    monkeypatch.setenv("PLEX_TOKEN", "fromenv")
    assert plex.get_token() == "fromenv"


def test_get_token_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.setattr(plex, "_PLEX_ENV_PATH", tmp_path / "nope.env")
    assert plex.get_token() == ""


def test_sessions_uses_status_sessions_and_translates(plex_cfg, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.headers["X-Plex-Token"] == "tok123"
        return httpx.Response(200, json=_SESSION)

    _mock_client(monkeypatch, plex, handler)
    out_en = asyncio.run(plex.get_sessions(lang="en"))
    out_de = asyncio.run(plex.get_sessions(lang="de"))
    assert seen == ["/status/sessions", "/status/sessions"]
    assert "Active Plex sessions (1)" in out_en and "S02E05" in out_en and "▶ playing" in out_en
    assert "Aktive Plex-Sitzungen" in out_de and "▶ läuft" in out_de
    assert "Ukjent" not in out_en


def test_episode_without_numbers_does_not_crash(plex_cfg, monkeypatch):
    data = {"MediaContainer": {"Metadata": [{**_SESSION["MediaContainer"]["Metadata"][0]}]}}
    del data["MediaContainer"]["Metadata"][0]["parentIndex"]
    _mock_client(monkeypatch, plex, lambda r: httpx.Response(200, json=data))
    assert "S?E05" in asyncio.run(plex.get_sessions(lang="nb"))
