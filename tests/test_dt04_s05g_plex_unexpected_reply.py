"""DT04-S05g (2026-11-16, Windows test): «spill Test-filmen på verksted-TV-en» → the Plex answer had no `key`
(probably the Plex stand-in on Windows) → `KeyError: 'key'` → the tool said «Failed: <tv>: 'key'. If the device is
switched off completely (not standby), it has to be turned on with its remote.» — and Kåre told the user the TV IS off.

Now: (1) a Plex answer that lacks a field is an honest «Plex gave an unexpected answer (missing: key)», not a bare
KeyError; (2) failure texts say what the code knows and never suggest a cause it does not know (thread 9,
feedback_prompt_no_leading_examples). Placeholders only; Plex and the bridge are fakes."""

import asyncio

import httpx
import pytest
import yaml

from adapters import plex_adapter as pa
from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em
from kaare_core.tools.i18n import t

GUESS_WORDS = {"nb": "slått helt av", "en": "switched off completely", "de": "ganz ausgeschaltet"}


@pytest.fixture
def plex_without_key(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/":
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "m1", "version": "1"}})
        if req.url.path.startswith("/library/metadata/"):
            return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"type": "movie", "title": "Test"}]}})
        return httpx.Response(200, json={"MediaContainer": {"playQueueID": 7}})

    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(pa.httpx, "AsyncClient", client)
    monkeypatch.setattr(pa, "_plex_cfg", lambda: {"url": "http://plex.test:32400"})
    monkeypatch.setattr(pa, "_token", lambda: "tok")


def test_a_plex_answer_without_key_is_an_honest_unexpected_reply(plex_without_key):
    with pytest.raises(pa.PlexUnexpectedReply) as err:
        asyncio.run(pa.cast_session("123"))
    assert "key" in err.value.missing


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_failure_texts_suggest_no_cause(lang):
    for key, kw in (("media_cast_failed", {"tried": "tv: x"}), ("media_tv_unreachable", {"node": "tv", "error": "x"})):
        assert GUESS_WORDS[lang] not in t(key, lang, **kw)


NODES = {"example_tv": {"type": "android_tv", "host": "10.0.0.4", "description": "example tv",
                        "channels": {"cast": {"enabled": True}}}}


def test_the_tool_reply_names_the_unexpected_answer_and_guesses_nothing(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: ("", ""))
    monkeypatch.setattr(em, "get_lang", lambda uid: "nb")

    async def find(q, season=None, episode=None):
        return {"rating_key": "123", "label": "Test", "type": "movie"}

    async def session(rk, offset=0, resume=False):
        raise pa.PlexUnexpectedReply(["key"])

    monkeypatch.setattr(em, "_plex_find_item", find)
    monkeypatch.setattr(em, "_plex_cast_session", session)
    out = asyncio.run(em.dispatch("media", {"action": "plex_play", "query": "Test", "client": "example tv",
                                            "_user_id": "u"}))
    assert t("media_plex_unexpected_reply", "nb", missing="key") in out
    assert GUESS_WORDS["nb"] not in out
