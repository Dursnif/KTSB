"""P139 — YouTube in one round: the code searches YouTube (no key), plays the top hit on a screen — KTSB's own Cast
first (Nest screens, Chromecast, Google TV), an Android TV's own remote second; the LLM only says what and where."""

import asyncio
import json

import pytest
import yaml

from adapters import youtube_adapter as ya
from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em


def _page(videos):
    data = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
        {"videoRenderer": {"videoId": vid, "title": {"runs": [{"text": title}]},
                           "ownerText": {"runs": [{"text": owner}]}, "lengthText": {"simpleText": "3:51"}}}
        for vid, title, owner in videos
    ] + [{"adSlotRenderer": {}}]}}]}}}
    return f"<script>var ytInitialData = {json.dumps(data)};</script>"


def test_parse_results_reads_id_title_channel_in_order():
    html = _page([("l482T0yNkeo", "AC/DC - Highway to Hell (Official Video)", "AC/DC"),
                  ("gEPmA3USJdI", "Highway to Hell (Live)", "AC/DC"), ("bad", "not an id", "x")])
    got = ya.parse_results(html)
    assert [g["id"] for g in got] == ["l482T0yNkeo", "gEPmA3USJdI"]
    assert got[0] == {"id": "l482T0yNkeo", "title": "AC/DC - Highway to Hell (Official Video)", "channel": "AC/DC",
                      "length": "3:51"}
    assert ya.parse_results("<html>changed format</html>") == []


NODES = {
    "nesthub": {"type": "chromecast", "host": "10.0.0.8", "description": "nest hub soverom",
                "channels": {"cast": {"enabled": True}}},
    "example_tv": {"type": "android_tv", "host": "10.0.0.4", "description": "thomson google tv",
                   "channels": {"cast": {"enabled": True}, "atv_remote": {"enabled": True}}},
    "samsung": {"type": "samsung_tv", "host": "10.0.0.5", "channels": {"samsung_ws": {"enabled": True}}},
}
HITS = [{"id": "l482T0yNkeo", "title": "Highway to Hell (Official Video)", "channel": "AC/DC", "length": "3:31"},
        {"id": "gEPmA3USJdI", "title": "Highway to Hell (Live)", "channel": "AC/DC", "length": "4:50"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    p = tmp_path / "nodes.yaml"
    p.write_text(yaml.safe_dump({"nodes": NODES}))
    monkeypatch.setattr(eh, "_NODES_PATH", p)
    monkeypatch.setattr(eh.rooms, "node_place", lambda nid, **k: ("", ""))
    monkeypatch.setattr(em, "get_lang", lambda uid: "en")
    state = {"hits": HITS, "answers": {}, "calls": []}

    async def search(q, limit=5):
        state["calls"].append(("search", q))
        return state["hits"]

    async def call(path, payload, timeout=60.0):
        state["calls"].append((path, payload.get("host") or payload.get("node"), payload.get("video_id") or
                               payload.get("value")))
        return state["answers"].get(path, {"ok": False, "error": "no answer"})

    monkeypatch.setattr(em.youtube_adapter, "search", search)
    monkeypatch.setattr(em.cast_client, "call", call)
    return state


def _run(**args):
    return asyncio.run(em.dispatch("media", {"action": "youtube_play", "_user_id": "en", **args}))


def test_nest_screen_plays_the_top_hit_over_own_cast(env):
    env["answers"]["/cast/youtube"] = {"ok": True, "after": {"app": "YouTube", "state": "PLAYING"}}
    out = _run(query="ac dc highway to hell", client="nest hub")
    assert env["calls"] == [("search", "ac dc highway to hell"), ("/cast/youtube", "10.0.0.8", "l482T0yNkeo")]
    assert out.startswith("OK:") and "Highway to Hell (Official Video)" in out and "youtube_id=gEPmA3USJdI" in out


def test_android_tv_falls_back_to_its_remote_with_the_watch_link(env):
    env["answers"]["/cast/youtube"] = {"ok": False, "error": "timed out"}
    env["answers"]["/tv/act"] = {"ok": True, "outcome": "confirmed", "after": {"app": "com.google.android.youtube.tv"}}
    out = _run(query="highway to hell", client="thomson")
    assert env["calls"][-1] == ("/tv/act", "example_tv", "https://www.youtube.com/watch?v=l482T0yNkeo")
    assert out.startswith("OK:") and "remote" in out and "timed out" in out


def test_given_youtube_id_skips_the_search(env):
    env["answers"]["/cast/youtube"] = {"ok": True, "after": {"state": "PLAYING"}}
    _run(youtube_id="gEPmA3USJdI", client="nest hub")
    assert env["calls"] == [("/cast/youtube", "10.0.0.8", "gEPmA3USJdI")]


def test_search_down_opens_the_search_on_an_android_tv(env):
    env["hits"] = []
    env["answers"]["/cast/youtube"] = {"ok": True}
    env["answers"]["/tv/act"] = {"ok": True, "outcome": "confirmed"}
    out = _run(query="dirty deeds", client="thomson")
    assert out.startswith("SENT:") and env["calls"][-1][2].startswith("https://www.youtube.com/results?search_query=")
    env["calls"].clear()
    assert "Nothing was played" in _run(query="dirty deeds", client="nest hub")


def test_no_screen_and_no_query(env):
    assert "Speakers without a screen" in _run(query="x", client="samsung")  # no Cast, remote cannot pick a video
    assert "needs query" in _run(client="thomson")
