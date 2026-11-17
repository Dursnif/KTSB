"""The announce tool's receipt comes after the outcome (DT04-S05f).

Seen: «Kunngjøring sendt til 'tv-a'» after 33 ms while nothing played anywhere, and Kåre told the user it was
played. The tool now asks the bridge to wait for the playback to start (wait=true) and says what really happened.
"""

import asyncio

import pytest

from kaare_core.tools import executor


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _client(resp, sent):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json)
            return resp

    return _C


def _say(monkeypatch, resp, target="tv-a"):
    sent = []
    monkeypatch.setattr(executor.httpx, "AsyncClient", _client(resp, sent))
    out = asyncio.run(executor._dispatch("announce", {"action": "say", "text": "test", "target": target,
                                                      "_user_id": "global"}))
    return out, sent


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    monkeypatch.setattr(executor, "get_lang", lambda uid: "en")


def test_asks_the_bridge_to_wait_for_the_outcome(monkeypatch):
    _, sent = _say(monkeypatch, _Resp(200, {"status": "ok", "results": {"tv_a": "playing"}}))
    assert sent and sent[0]["wait"] is True


def test_unknown_target_is_said_with_the_known_ones(monkeypatch):
    body = {"detail": {"error": "unknown_target", "target": "garasje", "known": ["tv_a (TV A)", "local"]}}
    out, _ = _say(monkeypatch, _Resp(404, body), target="garasje")
    assert "garasje" in out and "TV A" in out
    assert "sent" not in out.lower() and "playing" not in out.lower()


def test_failed_playback_is_never_reported_as_sent(monkeypatch):
    out, _ = _say(monkeypatch, _Resp(200, {"status": "failed", "results": {"local": "failed"}}), target="local")
    assert "not" in out.lower() and "sent" not in out.lower()


def test_started_playback_is_reported(monkeypatch):
    out, _ = _say(monkeypatch, _Resp(200, {"status": "ok", "results": {"tv_a": "playing"}}))
    assert "tv_a" in out.lower() and "not" not in out.lower()


def test_partial_names_what_failed(monkeypatch):
    body = {"status": "partial", "results": {"tv_a": "played", "local": "failed"}}
    out, _ = _say(monkeypatch, _Resp(200, body), target="all")
    assert "local" in out and "tv_a" in out
