"""P140 phase 3: Plex listener — ticks → watch store, cache, Argus events; history catch-up; status."""

import asyncio
import json

import httpx
import pytest

from kaare_core.media import plex_listener as pl
from kaare_core.media import watch_store as ws

BASE = "http://pms:32400"

SESSION = {
    "sessionKey": "16",
    "ratingKey": "18179",
    "type": "episode",
    "grandparentRatingKey": "18047",
    "grandparentTitle": "Show",
    "parentIndex": 1,
    "index": 8,
    "title": "Stalked",
    "duration": 2_641_210,
    "viewOffset": 1000,
    "User": {"id": "1", "title": "owner"},
    "Player": {"title": "Workshop TV", "state": "playing"},
}
MARKERS = [
    {"type": "intro", "startTimeOffset": 197_089},
    {"type": "credits", "startTimeOffset": 2_600_000, "final": True},
]


def _history(n: int, start_ts: int = 1_000_000) -> list[dict]:
    return [
        {
            "ratingKey": str(500 + i),
            "type": "episode",
            "grandparentKey": "/library/metadata/400",
            "grandparentTitle": "Old show",
            "parentIndex": 1,
            "index": i + 1,
            "title": f"E{i + 1}",
            "viewedAt": start_ts - i * 100,  # newest first
            "accountID": 1,
        }
        for i in range(n)
    ]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "DB_PATH", tmp_path / "watch.db")
    monkeypatch.setattr(pl, "CACHE_PATH", tmp_path / "plex_cache.json")
    events: list[dict] = []
    monkeypatch.setattr(pl.log_buffer, "write", lambda path, rec: events.append(rec))
    pl._sessions.clear()
    pl._markers.clear()
    pl.set_user_lookup(lambda acc: "userone" if acc == 1 else "")
    calls: list[str] = []
    history = {"items": _history(3)}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/status/sessions":
            return httpx.Response(200, json={"MediaContainer": {"Metadata": [SESSION]}})
        if request.url.path.startswith("/library/metadata/"):
            return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"Marker": MARKERS}]}})
        if request.url.path == "/status/sessions/history/all":
            start = int(request.headers.get("X-Plex-Container-Start", 0))
            size = int(request.headers.get("X-Plex-Container-Size", 50))
            return httpx.Response(
                200, json={"MediaContainer": {"Metadata": history["items"][start : start + size]}}
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield {"client": client, "events": events, "calls": calls, "history": history, "tmp": tmp_path}
    asyncio.run(client.aclose())


def _tick(env, state="playing", offset=1000, rating_key="18179"):
    n = {"sessionKey": "16", "ratingKey": rating_key, "state": state, "viewOffset": offset}
    return asyncio.run(pl.handle_tick(env["client"], BASE, n))


def test_credits_start_prefers_final_marker():
    assert pl.credits_start(MARKERS) == 2_600_000
    assert (
        pl.credits_start(
            [{"type": "credits", "startTimeOffset": 5}, {"type": "credits", "startTimeOffset": 9}]
        )
        == 9
    )
    assert pl.credits_start([{"type": "intro", "startTimeOffset": 1}]) is None


def test_ws_url_never_changes_scheme_wrongly():
    assert pl._ws_url("http://h:32400", "T").startswith(
        "ws://h:32400/:/websockets/notifications?X-Plex-Token="
    )
    assert pl._ws_url("https://h", "T").startswith("wss://h/")


def test_item_from_history_uses_grandparent_key_path():
    item = pl.item_from_metadata(_history(1)[0])
    assert item["show_key"] == "400" and item["episode"] == 1


def test_tick_flow_records_events_once_and_caches(env):
    assert _tick(env) == "started"
    assert _tick(env, offset=11_000) == ""  # plain progress: no event, no extra lookups
    assert env["calls"].count("/status/sessions") == 1
    assert env["calls"].count("/library/metadata/18179") == 1
    assert _tick(env, state="paused", offset=12_000) == "paused"
    assert _tick(env, offset=2_600_500) == "finished"  # reached the credits marker
    assert _tick(env, state="stopped", offset=2_610_000) == "stopped"
    stages = [e["stage"] for e in env["events"]]
    assert stages == ["started", "paused", "finished", "stopped"]
    assert all(e["source"] == "plex-events" and e["ktsb_user"] == "userone" for e in env["events"])
    cache = json.loads((env["tmp"] / "plex_cache.json").read_text(encoding="utf-8"))
    assert cache == {"sessions": []}  # stopped → nothing plays
    status = ws.show_status(1, "18047")["18179"]["status"]
    assert status == "watched"


def test_cache_shows_now_playing_with_ktsb_user(env):
    _tick(env)
    cache = json.loads((env["tmp"] / "plex_cache.json").read_text(encoding="utf-8"))
    assert cache["sessions"] == [
        {"title": "Show – Stalked", "state": "playing", "user": "userone", "player": "Workshop TV"}
    ]


def test_stop_for_unknown_session_is_ignored(env):
    assert _tick(env, state="stopped") == ""
    assert env["calls"] == []


def test_catch_up_pages_and_resumes_from_last_seen(env, monkeypatch):
    monkeypatch.setattr(pl, "_HISTORY_PAGE", 2)
    added = asyncio.run(pl.catch_up(env["client"], BASE))
    assert added == 3
    assert asyncio.run(pl.catch_up(env["client"], BASE)) == 0  # nothing newer than last time
    env["history"]["items"] = _history(1, start_ts=2_000_000) + env["history"]["items"]
    env["history"]["items"][0]["ratingKey"] = "999"
    assert asyncio.run(pl.catch_up(env["client"], BASE)) == 1


def test_not_configured_status(monkeypatch):
    monkeypatch.setattr(pl, "_plex_url", lambda: "")
    monkeypatch.setattr(pl, "_IDLE_RECHECK_S", 0.01)

    async def run_briefly():
        task = asyncio.create_task(pl.run_plex_listener())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())
    assert pl.status()["state"] == "not_configured"


def test_tick_failures_are_logged_once_and_recovery_too(caplog):
    pl._tick_error = None
    with caplog.at_level("INFO", logger="plex_listener"):
        for _ in range(3):
            pl._tick_failed(PermissionError("attempt to write a readonly database"))
        pl._tick_ok()
        pl._tick_ok()
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("recording playback failed" in m for m in msgs) == 1
    assert sum("works again" in m for m in msgs) == 1
