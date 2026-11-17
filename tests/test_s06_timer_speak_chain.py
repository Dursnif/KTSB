"""A timer never goes silent, and every fallback is said (DT04-S05f, kaare-d7): target → local → chat, each step logged.

The bridge no longer plays an unknown target on the local speaker by itself (404). A timer then tries «local»; if that
is missing or fails (a Docker install without aplay), the timer goes to the user's chat with a line saying the speaker
was not available.
"""

import asyncio

import pytest

from kaare_core.tools import timer_service as ts


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _bridge(answers, sent):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json["target"])
            return answers[json["target"]]

    return _C


@pytest.fixture
def env(monkeypatch):
    logged, chat = [], []
    monkeypatch.setattr(ts, "_log", lambda event, **kw: logged.append((event, kw)))
    monkeypatch.setattr(ts, "queue_chat_notification", lambda uid, msg, **kw: chat.append((uid, msg)))
    monkeypatch.setattr(ts, "get_speech_lang", lambda uid: "en")
    monkeypatch.setattr(ts, "get_lang", lambda uid: "en")
    return logged, chat


def _run(monkeypatch, answers, target="tv-a"):
    sent = []
    monkeypatch.setattr(ts.httpx, "AsyncClient", _bridge(answers, sent))
    asyncio.run(ts._deliver_tts({"id": "t1", "tts_text": "Time is up", "target_node": target, "user_id": "userx"}))
    return sent


def test_known_target_plays_there_only(monkeypatch, env):
    _, chat = env
    sent = _run(monkeypatch, {"tv-a": _Resp(200, {"status": "ok", "results": {"tv_a": "playing"}})})
    assert sent == ["tv-a"] and chat == []


def test_unknown_target_tries_local_and_logs_it(monkeypatch, env):
    logged, chat = env
    sent = _run(monkeypatch, {"tv-a": _Resp(404, {"detail": {"error": "unknown_target"}}),
                              "local": _Resp(200, {"status": "ok", "results": {"local": "playing"}})})
    assert sent == ["tv-a", "local"] and chat == []
    assert any(e == "tts_target_unknown" for e, _ in logged)


def test_no_speaker_at_all_goes_to_the_chat_with_a_reason(monkeypatch, env):
    logged, chat = env
    sent = _run(monkeypatch, {"tv-a": _Resp(404, {"detail": {"error": "unknown_target"}}),
                              "local": _Resp(200, {"status": "failed", "results": {"local": "failed"}})})
    assert sent == ["tv-a", "local"]
    assert chat and chat[0][0] == "userx" and "Time is up" in chat[0][1] and "speaker" in chat[0][1].lower()
    assert any(e == "tts_fallback_chat" for e, _ in logged)


def test_bridge_down_goes_to_the_chat(monkeypatch, env):
    _, chat = env

    class _Down:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise ConnectionError("bridge down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ts.httpx, "AsyncClient", _Down)
    asyncio.run(ts._deliver_tts({"id": "t1", "tts_text": "Time is up", "target_node": "local", "user_id": "userx"}))
    assert chat and "Time is up" in chat[0][1]
