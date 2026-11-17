"""
Plex listener (P140): one websocket to the Plex server instead of polling.

Plex pushes a `playing` notification every ~10 s per active session (sessionKey, ratingKey, state, viewOffset —
no user). The listener looks a session up once in /status/sessions (account, player, item) and the item's credits
marker once in /library/metadata, then feeds every tick to the watch store (watch_store.py). Out of it come:

- state/plex_cache.json — what plays now (read by context_builder for Kåre's situational context);
- logs/plex_events.log  — one JSON line per meaningful event (started, paused, resumed, stopped, finished) for
                          Argus, never the 10 s ticks;
- catch-up from Plex history at connect and after every stop, so KTSB's own index has no holes (P140 D5).

Plex unconfigured → idle. Token rejected / server unreachable → status says so, one log line per change, retry
with backoff. Never raises (rule 2). The token is never logged — it rides in the websocket URL.
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

from adapters.plex_adapter import SESSIONS_PATH, get_token
from kaare_core import log_buffer
from kaare_core.config import get_service
from kaare_core.media import watch_store
from kaare_core.tools import executor_ha

logger = logging.getLogger("plex_listener")

CACHE_PATH = Path("/kaare/state/plex_cache.json")
EVENTS_LOG = Path("/kaare/logs/plex_events.log")

_HTTP_TIMEOUT = 10.0
_BACKOFF_START_S = 5.0
_BACKOFF_MAX_S = 300.0
_IDLE_RECHECK_S = 60.0  # Plex not configured: look again this often (a GUI change wakes it at once)
_HISTORY_PAGE = 200
_HISTORY_META_KEY = "plex_history_viewed_at"

_status: dict[str, Any] = {"state": "starting", "detail": "", "since": time.time()}
_wake = asyncio.Event()

_sessions: dict[
    str, dict
] = {}  # sessionKey → {rating_key, account_id, user, player, player_id, screen, item, state, play_id}
_markers: dict[str, int | None] = {}  # ratingKey → credits start (ms) or None
# set by set_user_lookup(): account_id → KTSB username ("" if not linked)
_ktsb_user_lookup: Any = None
_expected: dict[
    str, tuple[str, float, str]
] = {}  # ratingKey → (KTSB user who asked, deadline, screen node) — plex_play
_EXPECT_WINDOW_S = 180.0


def status() -> dict:
    """Listener state for the GUI: not_configured | connecting | connected | token_rejected | unreachable."""
    return dict(_status)


def wake() -> None:
    """Config changed (GUI saved the Plex URL or token) — reconnect now instead of after the backoff."""
    _wake.set()


def expect_ktsb_start(rating_key: str, ktsb_user: str, screen: str = "") -> None:
    """KTSB just asked Plex to play this item — the play that starts within a few minutes is source «ktsb», on
    this screen (node id) when the player's address did not already tell."""
    if rating_key:
        _expected[str(rating_key)] = (ktsb_user, time.time() + _EXPECT_WINDOW_S, screen)


def set_user_lookup(fn: Any) -> None:
    global _ktsb_user_lookup
    _ktsb_user_lookup = fn


def _set_status(state: str, detail: str = "") -> None:
    if _status.get("state") == state and _status.get("detail") == detail:
        return
    if state in ("token_rejected", "unreachable"):
        logger.warning("[PLEX] %s %s", state, detail)
    else:
        logger.info("[PLEX] %s %s", state, detail)
    _status.update(state=state, detail=detail, since=time.time())


def _plex_url() -> str:
    try:
        return ((get_service("media", "plex") or {}).get("url") or "").rstrip("/")
    except Exception:
        return ""


def _ws_url(base: str, token: str) -> str:
    scheme = "wss" if base.startswith("https") else "ws"
    host = base.split("://", 1)[-1]
    return f"{scheme}://{host}/:/websockets/notifications?X-Plex-Token={token}"


def _ktsb_user(account_id: int) -> str:
    try:
        return (_ktsb_user_lookup(account_id) if _ktsb_user_lookup else "") or ""
    except Exception:
        return ""


def _key_from_path(path: str | None) -> str:
    return (path or "").rstrip("/").rsplit("/", 1)[-1] if path else ""


def item_from_metadata(m: dict) -> dict:
    """The watch-store item fields from a Plex Metadata object (session or history)."""
    show_key = str(m.get("grandparentRatingKey") or _key_from_path(m.get("grandparentKey")) or "")
    return {
        "rating_key": str(m.get("ratingKey") or ""),
        "type": m.get("type") or "",
        "show_key": show_key,
        "show_title": m.get("grandparentTitle") or "",
        "season": m.get("parentIndex"),
        "episode": m.get("index"),
        "title": m.get("title") or "",
        "duration_ms": m.get("duration"),
    }


def credits_start(markers: list[dict]) -> int | None:
    """Start of the end credits: the `final` credits marker if Plex has one, else the last credits marker."""
    credits = [
        m
        for m in markers or []
        if m.get("type") == "credits" and m.get("startTimeOffset") is not None
    ]
    if not credits:
        return None
    final = [m for m in credits if m.get("final")]
    chosen = final[0] if final else max(credits, key=lambda m: m["startTimeOffset"])
    return int(chosen["startTimeOffset"])


async def _get(client: httpx.AsyncClient, base: str, path: str, **kwargs: Any) -> dict:
    resp = await client.get(f"{base}{path}", **kwargs)
    resp.raise_for_status()
    return resp.json().get("MediaContainer", {}) or {}


async def _lookup_session(client: httpx.AsyncClient, base: str, session_key: str) -> dict | None:
    container = await _get(client, base, SESSIONS_PATH)
    for m in container.get("Metadata") or []:
        if str(m.get("sessionKey")) == session_key:
            item = item_from_metadata(m)
            rk = item["rating_key"]
            if rk not in _markers:
                try:
                    meta = await _get(
                        client, base, f"/library/metadata/{rk}", params={"includeMarkers": 1}
                    )
                    items = meta.get("Metadata") or [{}]
                    _markers[rk] = credits_start(items[0].get("Marker") or [])
                except Exception:
                    _markers[rk] = None
            item["credits_ms"] = _markers[rk]
            try:
                account_id = int((m.get("User") or {}).get("id"))
            except (TypeError, ValueError):
                account_id = 0
            player = m.get("Player") or {}
            # P140 bug A: the screen = the node at the player's address (Cast and Plex apps alike)
            screen = await asyncio.to_thread(executor_ha.node_for_address, str(player.get("address") or ""))
            return {
                "rating_key": rk,
                "account_id": account_id,
                "user": (m.get("User") or {}).get("title") or "",
                "player": player.get("title") or "",
                "player_id": str(player.get("machineIdentifier") or ""),
                "screen": screen,
                "item": item,
            }
    return None


def _write_cache() -> None:
    sessions = []
    for s in _sessions.values():
        if s.get("state") not in ("playing", "buffering", "paused"):
            continue
        it = s["item"]
        title = (
            f"{it['show_title']} – {it['title']}" if it.get("show_title") else it.get("title", "")
        )
        sessions.append(
            {
                "title": title,
                "state": s["state"],
                "user": s.get("ktsb_user") or s["user"],
                "player": s["player"],
            }
        )
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(CACHE_PATH)
    except Exception as e:
        logger.debug("[PLEX] cache write error: %s", e)


def _log_event(event: str, s: dict, offset_ms: int) -> None:
    it = s["item"]
    log_buffer.write(
        EVENTS_LOG,
        {
            "ts": datetime.now(UTC).isoformat(),
            "source": "plex-events",
            "subsystem": "media",
            "stage": event,
            "account_id": s["account_id"],
            "plex_user": s["user"],
            "ktsb_user": s.get("ktsb_user", ""),
            "player": s["player"],
            "screen": s.get("screen", ""),
            "rating_key": it["rating_key"],
            "type": it["type"],
            "show": it["show_title"],
            "season": it["season"],
            "episode": it["episode"],
            "title": it["title"],
            "offset_ms": offset_ms,
            "duration_ms": it["duration_ms"],
        },
    )


async def handle_tick(client: httpx.AsyncClient, base: str, n: dict) -> str:
    """One PlaySessionStateNotification → watch store, cache, events. Returns the event ("" if none)."""
    session_key = str(n.get("sessionKey") or "")
    rating_key = str(n.get("ratingKey") or "")
    state = n.get("state") or ""
    offset = int(n.get("viewOffset") or 0)
    if not session_key or not rating_key:
        return ""
    s = _sessions.get(session_key)
    if s is None or s["rating_key"] != rating_key:
        if state == "stopped":
            return ""  # a stop for a session we never saw — nothing to record
        found = await _lookup_session(client, base, session_key)
        if found is None:
            return ""
        found["ktsb_user"] = _ktsb_user(found["account_id"])
        _sessions[session_key] = s = found
    play_id, event = await asyncio.to_thread(
        watch_store.record_progress,
        account_id=s["account_id"],
        session_key=session_key,
        item=s["item"],
        state=state,
        offset_ms=offset,
        player=s["player"],
        ktsb_user=s.get("ktsb_user", ""),
        screen=s.get("screen", ""),
        player_id=s.get("player_id", ""),
    )
    s["play_id"] = play_id
    if event == "started":
        expected = _expected.pop(rating_key, None)
        if expected and expected[1] >= time.time():
            await asyncio.to_thread(watch_store.mark_source, play_id, "ktsb", expected[0], expected[2])
            if not s.get("screen"):
                s["screen"] = expected[2]
            if not s.get("ktsb_user"):
                s["ktsb_user"] = expected[0]
    changed = s.get("state") != state
    s["state"] = state
    if event:
        _log_event(event, s, offset)
    if state == "stopped":
        _sessions.pop(session_key, None)
    if changed or event:
        _write_cache()
    return event


async def catch_up(client: httpx.AsyncClient, base: str) -> int:
    """Import Plex history newer than the last entry seen (all accounts). Returns rows added."""
    try:
        since = float(await asyncio.to_thread(watch_store.get_meta, _HISTORY_META_KEY, "0") or 0)
    except ValueError:
        since = 0.0
    entries: list[dict] = []
    start = 0
    newest = since
    while True:
        container = await _get(
            client,
            base,
            "/status/sessions/history/all",
            params={"sort": "viewedAt:desc"},
            headers={
                "X-Plex-Container-Start": str(start),
                "X-Plex-Container-Size": str(_HISTORY_PAGE),
            },
        )
        batch = container.get("Metadata") or []
        stop = False
        for m in batch:
            viewed = float(m.get("viewedAt") or 0)
            if viewed <= since:
                stop = True
                break
            newest = max(newest, viewed)
            if m.get("type") not in ("episode", "movie"):
                continue
            entries.append(
                {**item_from_metadata(m), "viewed_at": viewed, "account_id": m.get("accountID")}
            )
        if stop or len(batch) < _HISTORY_PAGE:
            break
        start += _HISTORY_PAGE
    added = 0
    by_account: dict[int, list[dict]] = {}
    for e in entries:
        try:
            by_account.setdefault(int(e.pop("account_id")), []).append(e)
        except (TypeError, ValueError):
            continue
    for account_id, items in by_account.items():
        added += await asyncio.to_thread(watch_store.import_history, account_id, items)
    if newest > since:
        await asyncio.to_thread(watch_store.set_meta, _HISTORY_META_KEY, str(newest))
    if added:
        logger.info("[PLEX] catch-up: %d plays from Plex history", added)
    return added


async def _snapshot(client: httpx.AsyncClient, base: str) -> None:
    """At connect: what already plays (the first websocket tick can be ~10 s away)."""
    container = await _get(client, base, SESSIONS_PATH)
    for m in container.get("Metadata") or []:
        n = {
            "sessionKey": m.get("sessionKey"),
            "ratingKey": m.get("ratingKey"),
            "state": (m.get("Player") or {}).get("state", ""),
            "viewOffset": m.get("viewOffset"),
        }
        await handle_tick(client, base, n)
    _write_cache()


_tick_error: str | None = (
    None  # last tick failure; logged once when it starts and once when it clears
)


def _tick_failed(e: Exception) -> None:
    """A failing tick must be visible (P140 phase 7: an unwritable watch.db failed silently for minutes)."""
    global _tick_error
    msg = f"{type(e).__name__}: {e}"
    if msg != _tick_error:
        logger.warning("[PLEX] recording playback failed: %s", msg)
        _tick_error = msg


def _tick_ok() -> None:
    global _tick_error
    if _tick_error is not None:
        logger.info("[PLEX] recording playback works again")
        _tick_error = None


def _settle(*futures: asyncio.Future) -> None:
    """Never leave a future behind: cancel what is pending, mark errors of finished ones as retrieved."""
    for fut in futures:
        if not fut.done():
            fut.cancel()
        elif not fut.cancelled():
            fut.exception()


async def _run_once(base: str, token: str) -> None:
    headers = {
        "X-Plex-Token": token,
        "Accept": "application/json",
        "X-Plex-Client-Identifier": "kaare",
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
        try:
            await _get(client, base, "/identity")
            await _get(client, base, "/")  # needs a valid token
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise PermissionError(f"HTTP {e.response.status_code}") from None
            raise
        _set_status("connecting")
        async with websockets.connect(
            _ws_url(base, token), open_timeout=15, ping_interval=30
        ) as ws:
            _set_status("connected")
            for coro in (_snapshot(client, base), catch_up(client, base)):
                try:
                    await coro
                except Exception as e:
                    logger.warning("[PLEX] start-up step failed: %s", type(e).__name__)
            while True:
                recv = asyncio.ensure_future(ws.recv())
                woke = asyncio.ensure_future(_wake.wait())
                try:
                    done, _pending = await asyncio.wait(
                        {recv, woke}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    _settle(recv, woke)
                if woke in done:
                    _wake.clear()
                    if (_plex_url(), get_token()) != (base, token):
                        return  # reconnect with the new config
                    continue
                try:
                    msg = json.loads(recv.result())
                except (ValueError, TypeError):
                    continue
                container = msg.get("NotificationContainer") or {}
                if container.get("type") != "playing":
                    continue
                for n in container.get("PlaySessionStateNotification") or []:
                    try:
                        event = await handle_tick(client, base, n)
                        if event == "stopped":
                            await catch_up(client, base)
                        _tick_ok()
                    except Exception as e:
                        _tick_failed(e)


async def run_plex_listener() -> None:
    """Background task (kaare_api lifespan). Never raises."""
    backoff = _BACKOFF_START_S
    while True:
        base, token = _plex_url(), get_token()
        if not base or not token:
            _set_status("not_configured", "url" if not base else "token")
            try:
                await asyncio.wait_for(_wake.wait(), timeout=_IDLE_RECHECK_S)
            except TimeoutError:
                pass
            _wake.clear()
            continue
        try:
            await _run_once(base, token)
            backoff = _BACKOFF_START_S
            continue
        except asyncio.CancelledError:
            raise
        except PermissionError as e:
            _set_status("token_rejected", str(e))
            wait = _BACKOFF_MAX_S
        except Exception as e:
            _set_status("unreachable", type(e).__name__)
            wait = backoff
            backoff = min(backoff * 2, _BACKOFF_MAX_S)
        _sessions.clear()
        _write_cache()  # a stale «now playing» would mislead Kåre
        try:
            await asyncio.wait_for(_wake.wait(), timeout=wait)
        except TimeoutError:
            pass
        _wake.clear()
