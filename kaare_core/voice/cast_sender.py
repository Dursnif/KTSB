"""KTSB's own Cast sender (P139 phase 3) — the voice bridge talks to Cast devices directly, Home Assistant is only a
fallback (D5/D6, rule 24: HA is never required).

Connects by IP without mDNS: the device's UUID comes from its `http://<ip>:8008/setup/eureka_info` (`ssdp_udn`), then
`get_chromecast_from_host` — 0.2 s, and it works inside Docker where mDNS often does not. `get_listed_chromecasts(
known_hosts=…)`, which the older paths used, finds nothing for a Google TV (P139 phase 3 live test, 2026-10-19).

Every action reads the device status before and after and says what is on screen (N26): app, title, player state,
standby. Starting an app on a TV in standby wakes it (that is all HA's «turn on» does); a TV switched completely off
is not on the network and is reported as unreachable.

pychromecast is blocking and optional (rule 24): the functions here are sync and imported lazily; callers run them in
a thread (`run`). One lock per host keeps two jobs from fighting over the same device.
"""

import asyncio
import json
import logging
import threading
import time
import urllib.request
import uuid
from typing import Any

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10.0
WAIT_PLAYING_S = 25.0  # NRK live took ~10 s to reach PLAYING on the Thomson
WAIT_PICTURE_S = 15.0
_POLL_S = 0.5

_casts: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _pychromecast():
    # PLC0415: optional dependency — only the voice bridge has pychromecast (D5, rule 24)
    import pychromecast  # noqa: PLC0415

    return pychromecast


def _lock(host: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(host, threading.Lock())


def _uuid_of(host: str) -> tuple[uuid.UUID, str]:
    with urllib.request.urlopen(f"http://{host}:8008/setup/eureka_info", timeout=4) as r:
        info = json.load(r)
    return uuid.UUID(str(info["ssdp_udn"])), str(info.get("name") or host)


def _connect(host: str):
    """A connected Chromecast for this host, reused while its socket is alive."""
    cast = _casts.get(host)
    if cast is not None and cast.socket_client.is_connected:
        return cast
    if cast is not None:
        try:
            cast.disconnect(timeout=1)
        except Exception:
            pass
    pc = _pychromecast()
    dev_uuid, name = _uuid_of(host)
    cast = pc.get_chromecast_from_host((host, 8009, dev_uuid, None, name), tries=1, timeout=CONNECT_TIMEOUT_S)
    cast.wait(timeout=CONNECT_TIMEOUT_S)
    _casts[host] = cast
    return cast


def _snapshot(cast) -> dict:
    """What the device says now — the truth reported back (N26)."""
    st = cast.status
    out: dict[str, Any] = {
        "standby": getattr(st, "is_stand_by", None),
        "active_input": getattr(st, "is_active_input", None),
        "app": getattr(st, "display_name", None),
        "app_id": getattr(st, "app_id", None),
        "volume": round(float(st.volume_level), 2) if st and st.volume_level is not None else None,
        "muted": getattr(st, "volume_muted", None),
    }
    try:
        ms = cast.media_controller.status
        out.update(state=ms.player_state, title=ms.title, content=ms.content_id)
    except Exception:
        pass
    return out


# Apps a Cast device shows when nothing runs: a Nest Hub falls back to its photo frame (Backdrop) after an app is
# closed, so «no app» never comes (P139 live test, 2026-10-19).
IDLE_APP_IDS = {"E8C28D3C"}


def _idle(snap: dict) -> bool:
    return not snap.get("app_id") or snap.get("app_id") in IDLE_APP_IDS


DEFAULT_MEDIA_RECEIVER_ID = "CC1AD845"


def before_summary(snap: dict | None) -> dict:
    """DT04-S05q: what a screen showed before KTSB took it, from a _snapshot — {"kind": "standby"|"idle"|"app"|"unknown",
    "app": name or None}. KTSB's own receivers and the backdrop count as idle. Says nothing about what an app played:
    the restore relaunches the app, never its content. Never raises."""
    try:
        if not snap:
            return {"kind": "unknown", "app": None}
        if snap.get("standby"):
            return {"kind": "standby", "app": None}
        app_id = snap.get("app_id")
        if _idle(snap) or app_id in (DEFAULT_MEDIA_RECEIVER_ID, DASHCAST_APP_ID):
            return {"kind": "idle", "app": None}
        return {"kind": "app", "app": str(snap.get("app") or app_id)}
    except Exception:
        return {"kind": "unknown", "app": None}


def other_app(snap: dict) -> str | None:
    """The name of an app that is running on the device and is not KTSB's own (Default Media Receiver for speech,
    pictures and radio; DashCast for the screen page) — None when the device is idle (P-D1, 2026-11-16).
    KTSB never loads speech or a picture over it: the LOAD would replace what plays (YouTube was replaced 20:54)."""
    app_id = snap.get("app_id")
    if _idle(snap) or app_id in (DEFAULT_MEDIA_RECEIVER_ID, DASHCAST_APP_ID):
        return None
    return str(snap.get("app") or app_id)


def _busy(app: str, before: dict) -> dict:
    return {"ok": False, "busy": app, "error": f"busy: {app} is playing", "before": before, "after": before}


def _fail(error: str, before: dict | None = None) -> dict:
    return {"ok": False, "error": error, "before": before or {}, "after": {}}


def _wait(cast, done, limit: float) -> dict:
    t0 = time.monotonic()
    snap = _snapshot(cast)
    while not done(snap) and time.monotonic() - t0 < limit:
        time.sleep(_POLL_S)
        snap = _snapshot(cast)
    return snap


def status(host: str) -> dict:
    """{ok, after: snapshot} — or ok False when the device does not answer (switched off, wrong address)."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        return {"ok": True, "before": {}, "after": _snapshot(cast)}


def show_picture(host: str, url: str, mime: str = "image/png", title: str = "Kåre") -> dict:
    """A full-screen picture through the Default Media Receiver; confirmed when the receiver reports that URL."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        if (app := other_app(before)) is not None:
            return _busy(app, before)
        try:
            cast.media_controller.play_media(url, mime, title=title)
            after = _wait(cast, lambda s: s.get("content") == url, WAIT_PICTURE_S)
        except Exception as e:
            return _fail(str(e), before)
        return {"ok": after.get("content") == url, "before": before, "after": after,
                **({} if after.get("content") == url else {"error": "the picture was not confirmed by the device"})}


def clear_picture(host: str, url: str) -> dict:
    """Close a picture after its time — only while the device still shows that picture (something started since then
    is left alone). P139: a Cast picture otherwise stays until something else replaces it."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        if before.get("content") != url:
            return {"ok": True, "before": before, "after": before, "skipped": "another app or picture is on screen"}
        try:
            cast.quit_app()
            after = _wait(cast, _idle, 6.0)
        except Exception as e:
            return _fail(str(e), before)
        return {"ok": _idle(after), "before": before, "after": after}


def play_audio(host: str, url: str, mime: str = "audio/wav", title: str = "Kåre") -> dict:
    """Speech: play a wav the bridge serves. Confirmed when the receiver loaded it (it may already have finished)."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        if (app := other_app(before)) is not None:
            return _busy(app, before)
        try:
            cast.media_controller.play_media(url, mime, title=title)
            after = _wait(cast, lambda s: s.get("content") == url, 8.0)
        except Exception as e:
            return _fail(str(e), before)
        return {"ok": after.get("content") == url, "before": before, "after": after}


DASHCAST_APP_ID = "84912283"


def show_url(host: str, url: str, force: bool = False) -> dict:
    """A web page full-screen through DashCast (iframe; `force` for pages that refuse to be framed). The device only
    confirms that DashCast runs — not that the page rendered.

    An http:// page is always forced: DashCast's receiver is https, so an http page in its frame is blocked silently
    (P139 bug 2026-11-16, found showing a recipe). A forced page replaces DashCast's own receiver, so a later load_url
    would be ignored — any running app is quit first and DashCast starts fresh."""
    force = force or url.startswith("http://")
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        try:
            # PLC0415: optional dependency — pychromecast (see _pychromecast)
            from pychromecast.controllers.dashcast import DashCastController  # noqa: PLC0415

            if force and not _idle(before):
                cast.quit_app()
                _wait(cast, _idle, WAIT_PICTURE_S)
            ctrl = DashCastController()
            cast.register_handler(ctrl)
            ctrl.load_url(url, force=force)
            after = _wait(cast, lambda s: s.get("app") == "DashCast", WAIT_PICTURE_S)
        except Exception as e:
            return _fail(str(e), before)
        return {"ok": after.get("app") == "DashCast", "before": before, "after": after}


def restore(host: str, before: dict) -> dict:
    """P151: put a Cast screen back the way it was before KTSB showed something (P151 D12, owner decision
    2026-11-16: a timeout puts it back as it was). Quits KTSB's page; relaunches the app that ran before (the app,
    not what it played); a screen that was in standby gets `needs_standby` — Cast has no power command, so the caller
    uses the TV's remote channel if it has one. If someone started another app meanwhile, nothing is touched."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return {**_fail(f"unreachable: {e}"), "needs_standby": False}
        now = _snapshot(cast)
        if now.get("app_id") not in (DASHCAST_APP_ID, None) and now.get("app") != "DashCast" and not _idle(now):
            return {"ok": True, "skipped": f"screen now shows {now.get('app')!r} — left alone", "before": before,
                    "after": now, "needs_standby": False}
        out: dict[str, Any] = {"ok": True, "before": before, "needs_standby": bool(before.get("standby"))}
        try:
            if not _idle(now):
                cast.quit_app()
                now = _wait(cast, _idle, WAIT_PICTURE_S)
            app_id = before.get("app_id")
            if app_id and not before.get("standby") and app_id not in IDLE_APP_IDS and app_id != DASHCAST_APP_ID:
                cast.start_app(app_id)
                now = _wait(cast, lambda s: s.get("app_id") == app_id, WAIT_PICTURE_S)
                out["relaunched"] = before.get("app") or app_id
        except Exception as e:
            return {**_fail(str(e), before), "needs_standby": bool(before.get("standby"))}
        out["after"] = now
        return out


NRK_APPS = {"tv": "nrktv", "radio": "nrkradio"}


def play_nrk(host: str, kind: str, media_id: str) -> dict:
    """NRK TV / NRK Radio through their own Cast apps: a live channel slug (`nrk1`, `p1`) or a programme/episode id.
    Confirmed when the player reaches PLAYING."""
    app = NRK_APPS.get(kind)
    if not app:
        return _fail(f"unknown NRK kind {kind!r}")
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        try:
            # PLC0415: optional dependency — pychromecast (see _pychromecast)
            from pychromecast.quick_play import quick_play  # noqa: PLC0415

            data: dict[str, Any] = {"media_id": media_id}
            if kind == "radio":
                data["media_type"] = "audio/mpeg"
            quick_play(cast, app, data, timeout=WAIT_PLAYING_S)
            after = _wait(cast, lambda s: s.get("state") == "PLAYING", WAIT_PLAYING_S)
        except Exception as e:
            return _fail(str(e), before)
        playing = after.get("state") == "PLAYING"
        return {"ok": playing, "before": before, "after": after,
                **({} if playing else {"error": f"NRK did not start playing (state {after.get('state')})"})}


YOUTUBE_ID = r"[A-Za-z0-9_-]{11}"


def play_youtube(host: str, video_id: str) -> dict:
    """A YouTube video on a Cast device with a screen (Nest Hub, Chromecast, Google TV) through the YouTube Cast app.
    Confirmed when the player reaches PLAYING; audio-only speakers do not run the YouTube app and fail here."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        try:
            # PLC0415: optional dependency — pychromecast (see _pychromecast)
            from pychromecast.quick_play import quick_play  # noqa: PLC0415

            quick_play(cast, "youtube", {"media_id": video_id}, timeout=WAIT_PLAYING_S)
            after = _wait(cast, lambda s: s.get("state") == "PLAYING", WAIT_PLAYING_S)
        except Exception as e:
            return _fail(f"{type(e).__name__}: {e}"[:200], before)
        playing = after.get("state") == "PLAYING"
        return {"ok": playing, "before": before, "after": after,
                **({} if playing else {"error": f"YouTube did not start playing (state {after.get('state')})"})}


class _PlexServer:
    """Just enough of a plexapi PlexServer for pychromecast's PlexController (it reads these attributes); built from
    `plex_adapter.cast_session` so no plexapi dependency is needed (P139 phase 4)."""

    def __init__(self, session: dict):
        self._baseurl = f"{session['protocol']}://{session['address']}:{session['port']}"
        self._token = session["token"]
        self.machineIdentifier = session["machine_id"]
        self.version = session.get("version") or "1.10.1.4602"
        self.myPlexUsername = session.get("username") or ""
        self.myPlexSubscription = bool(session.get("plex_pass"))


class _PlexPlayQueue:
    """A play queue already created on the server (plex_adapter.cast_session) in plexapi's shape."""

    TYPE = "playqueue"

    def __init__(self, session: dict):
        self._server = _PlexServer(session)
        self.playQueueID = session["play_queue_id"]
        self.selectedItem = type("Item", (), {"key": session["key"]})()
        self.items = [type("Item", (), {"listType": session.get("list_type") or "video"})()]


def play_plex(host: str, session: dict) -> dict:
    """Plex on a Cast device through the Plex Cast app (what HA's Plex→Cast path does, proven on the Thomson). Starting
    the app wakes a TV from standby. The device's PLAYING is only a first sign — the caller confirms with the Plex
    server's own sessions (a stuck receiver reports PLAYING with nothing on screen)."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        try:
            # PLC0415: optional dependency — pychromecast (see _pychromecast)
            from pychromecast.controllers.plex import PlexController  # noqa: PLC0415

            if not _idle(before):
                # Close what runs first — a Plex receiver left in a bad state takes the LOAD, says PLAYING and shows
                # a black screen (P139 phase 4 live test); HA's turn_on quits the running app the same way.
                cast.quit_app()
                _wait(cast, _idle, 8.0)
            ctrl = PlexController()
            cast.register_handler(ctrl)
            ctrl.play_media(_PlexPlayQueue(session), offset=int(session.get("offset") or 0))
            after = _wait(cast, lambda s: s.get("state") == "PLAYING", WAIT_PLAYING_S + 10)
        except Exception as e:
            return _fail(str(e), before)
        playing = after.get("state") == "PLAYING"
        return {"ok": playing, "before": before, "after": after,
                **({} if playing else {"error": f"Plex did not start playing (state {after.get('state')})"})}


def set_volume(host: str, level: float) -> dict:
    """Device volume 0–1 (the receiver's own volume, not a media stream's)."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        try:
            cast.set_volume(max(0.0, min(1.0, float(level))))
            after = _wait(cast, lambda s: s.get("volume") is not None and abs(s["volume"] - level) <= 0.02, 3.0)
        except Exception as e:
            return _fail(str(e), before)
        return {"ok": True, "before": before, "after": after}


_MEDIA_STATES = {"PLAYING", "PAUSED", "BUFFERING"}


def _media_toggle(host: str, verb: str, want: str) -> dict:
    """Pause / resume the media session of the app on the device (P139 reopened #6: a Nest Hub without HA can be
    paused too). Confirmed only when the device itself reports `want` (N26); nothing playing = an honest failure."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        if before.get("state") not in _MEDIA_STATES:
            try:  # the media status can lag right after a fresh connection
                cast.media_controller.update_status()
            except Exception:
                pass
            before = _wait(cast, lambda s: s.get("state") in _MEDIA_STATES, 2.0)
        if before.get("state") not in _MEDIA_STATES:
            return _fail("nothing is playing on the device", before)
        if before.get("state") == want:
            return {"ok": True, "before": before, "after": before}
        try:
            getattr(cast.media_controller, verb)()
            after = _wait(cast, lambda s: s.get("state") == want, 6.0)
        except Exception as e:
            return _fail(str(e), before)
        ok = after.get("state") == want
        return {"ok": ok, "before": before, "after": after,
                **({} if ok else {"error": f"the device reports {after.get('state')}"})}


def pause(host: str) -> dict:
    """Pause what plays on the device; confirmed by PAUSED."""
    return _media_toggle(host, "pause", "PAUSED")


def play(host: str) -> dict:
    """Resume paused playback on the device; confirmed by PLAYING."""
    return _media_toggle(host, "play", "PLAYING")


def stop(host: str) -> dict:
    """Close whatever app runs on the device."""
    with _lock(host):
        try:
            cast = _connect(host)
        except Exception as e:
            return _fail(f"unreachable: {e}")
        before = _snapshot(cast)
        try:
            cast.quit_app()
            after = _wait(cast, _idle, 6.0)
        except Exception as e:
            return _fail(str(e), before)
        return {"ok": _idle(after), "before": before, "after": after}


async def run(fn, *args, timeout: float = 45.0, **kwargs) -> dict:
    """Run one of the blocking functions above in a thread; a hang becomes a failed result, never an exception."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout)
    except TimeoutError:
        return _fail(f"timed out after {timeout:.0f} s")
    except Exception as e:
        return _fail(str(e))
