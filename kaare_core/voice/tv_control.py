"""TV remote control (P139 phase 5) — the one executor every TV action goes through (D4), in the voice bridge (D5).

Drivers per channel: `atv_remote` (Android/Google TV Remote v2, androidtvremote2) and `samsung_ws` (Samsung Tizen,
samsungtvws + REST + UPnP). Every action reads the TV's state before and after and says what really happened (N26):
`confirmed` / `already` / `accepted` (nothing to read back) / `not_confirmed` / `unreachable` / `not_paired` / `error`.
Each action carries `origin` — `requested` today; `intuitive` is reserved for KTSB's later self-development (I40/D3: no
automatic TV actions) — and is logged as one JSONL line without secrets.

Rules from the phase 0 live tests: power is toggled only with a short POWER after reading `is_on` (never blind, never
the long-press full shutdown); a Samsung leaves the network in standby, so «on» is Wake-on-LAN sent until it answers,
then KEY_RETURN closes the Smart Hub bar it wakes up with; Samsung apps start via REST (WebSocket run_app does not).

Pairing secrets (Android TV client cert/key, Samsung token) live in `state/tv_pairing/<host>/` (dir 700, files 600) and
are never logged or shown to the model.
"""

import asyncio
import logging
import os
import re
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from kaare_core import log_buffer
from kaare_core.voice import channels

log = logging.getLogger(__name__)

PAIR_DIR = Path("/kaare/state/tv_pairing")
ACTION_LOG = Path("/kaare/logs/tv_actions.log")
ORIGIN_REQUESTED = "requested"
ORIGIN_INTUITIVE = "intuitive"  # reserved (D3/I40) — nothing sets it yet
ACTIONS = ("status", "power_on", "power_off", "volume_up", "volume_down", "mute", "key", "app")

CONFIRMED, ALREADY, ACCEPTED, NOT_CONFIRMED = "confirmed", "already", "accepted", "not_confirmed"
UNREACHABLE, NOT_PAIRED, ERROR, UNSUPPORTED = "unreachable", "not_paired", "error", "unsupported"
DONE = frozenset({CONFIRMED, ALREADY, ACCEPTED})

# Friendly key names → each protocol's key code.
ATV_KEYS = {
    "home": "HOME", "back": "BACK", "play_pause": "MEDIA_PLAY_PAUSE", "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE",
    "stop": "MEDIA_STOP", "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS", "up": "DPAD_UP", "down": "DPAD_DOWN",
    "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "ok": "DPAD_CENTER", "menu": "MENU", "channel_up": "CHANNEL_UP",
    "channel_down": "CHANNEL_DOWN", "input": "TV_INPUT",
}
SAMSUNG_KEYS = {
    "home": "KEY_HOME", "back": "KEY_RETURN", "exit": "KEY_EXIT", "play_pause": "KEY_PLAY", "play": "KEY_PLAY",
    "pause": "KEY_PAUSE", "stop": "KEY_STOP", "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT",
    "right": "KEY_RIGHT", "ok": "KEY_ENTER", "menu": "KEY_MENU", "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN",
    "input": "KEY_SOURCE",
}
# App names → (app link, the package that comes to the front). Android TV starts apps by link: a `market://launch?id=`
# link (what androidtvremote2 makes of a bare package name) is rejected by the Thomson, and after that rejection the
# same connection ignores every later launch and stops reporting the foreground app (P139 phase 5 live test,
# 2026-10-19). Any other `scheme://` link is passed through and confirmed by a change of foreground app.
ATV_APPS = {
    "youtube": ("https://www.youtube.com", "com.google.android.youtube.tv"),
    "netflix": ("https://www.netflix.com/title", "com.netflix.ninja"),
    "plex": ("plex://", "com.plexapp.android"),
}
_APP_LINK = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s;|&`$<>]*$")
SAMSUNG_APPS = {
    "youtube": "111299001912",  # verified on the AU9075 (phase 0b)
    # P151: the TV's own web browser («Browser») on 2020+ Tizen — verified on the AU9075 2026-11-16 (T34); a REST
    # launch brings it to the front with the last page, it cannot be handed a URL (WebSocket deep links are ignored).
    "browser": "3202010022079",
}


def samsung_app_visible(host: str, app_id: str) -> bool | None:
    """Is this app in front on the Samsung right now (REST `visible`)? None when the TV does not answer."""
    try:
        r = httpx.get(f"http://{host}:8001/api/v2/applications/{app_id}", timeout=3)
        return bool(r.json().get("visible")) if r.status_code == 200 else None
    except Exception:
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def secrets_dir(host: str) -> Path:
    d = PAIR_DIR / host.replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    for p in (PAIR_DIR, d):
        try:
            os.chmod(p, 0o700)
        except PermissionError:
            # Only the owner may chmod: a folder made by another user (a pairing run by hand) stays as it is.
            log.warning("tv_pairing: cannot set 700 on %s (owned by another user)", p)
    return d


def pairing_readable(host: str) -> bool:
    """False when the host's pairing folder exists but this process may not read it (wrong owner/permissions)."""
    d = PAIR_DIR / host.replace("/", "_")
    try:
        os.listdir(d)
    except FileNotFoundError:
        return True
    except PermissionError:
        return False
    return True


_UNREADABLE = ("pairing files for {host} are not readable by KTSB (wrong owner or permissions on state/tv_pairing) — "
               "pair the TV again under Nodes, or ask an admin to give the folder to the KTSB user")


def _not_paired(host: str) -> dict:
    if not pairing_readable(host):
        return _result(NOT_PAIRED, error=_UNREADABLE.format(host=host))
    return _result(NOT_PAIRED, error="not paired")


def _restrict(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def remote_channel(node: dict) -> str:
    """The node's enabled remote-control channel with a driver ('' when none)."""
    chans = channels.enabled_channels(node)
    return next((c for c in ("atv_remote", "samsung_ws") if c in chans), "")


def is_paired(host: str, channel: str) -> bool:
    d = PAIR_DIR / host.replace("/", "_")
    try:
        if channel == "atv_remote":
            return (d / "paired").exists() and (d / "cert.pem").exists() and (d / "key.pem").exists()
        if channel == "samsung_ws":
            tok = d / "samsung_token.txt"
            return tok.exists() and bool(tok.read_text(encoding="utf-8").strip())
    except PermissionError:
        log.warning("tv_pairing: %s is not readable by this process (wrong owner/permissions)", d)
    return False


def _result(outcome: str, before: dict | None = None, after: dict | None = None, error: str = "") -> dict:
    out: dict[str, Any] = {"ok": outcome in DONE, "outcome": outcome, "before": before or {}, "after": after or {}}
    if error:
        out["error"] = error
    return out


# ── Android TV Remote v2 ──────────────────────────────────────────────────────

_atv: dict[str, Any] = {}
_atv_pairing: dict[str, Any] = {}
_atv_locks: dict[str, asyncio.Lock] = {}


def _atv_cls():
    # PLC0415: optional dependency — only the voice bridge has androidtvremote2 (D5, rule 24)
    from androidtvremote2 import AndroidTVRemote  # noqa: PLC0415

    return AndroidTVRemote


def _atv_new(host: str):
    d = secrets_dir(host)
    return _atv_cls()("KTSB", str(d / "cert.pem"), str(d / "key.pem"), host)


def _atv_state(remote) -> dict:
    vol = remote.volume_info or {}
    return {
        "on": remote.is_on,
        "app": remote.current_app,
        "volume": vol.get("level") if isinstance(vol, dict) else None,
        "volume_max": vol.get("max") if isinstance(vol, dict) else None,
        "muted": vol.get("muted") if isinstance(vol, dict) else None,
    }


async def _atv_connected(host: str):
    remote = _atv.get(host)
    if remote is not None and remote.is_on is not None:
        return remote
    if remote is not None:
        remote.disconnect()
    remote = _atv_new(host)
    await remote.async_generate_cert_if_missing()
    await asyncio.wait_for(remote.async_connect(), 8)
    for _ in range(30):  # the first state arrives a moment after connect
        if remote.is_on is not None:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.6)  # a key sent right after connect was swallowed (Thomson, 2026-10-19)
    _atv[host] = remote
    return remote


async def _atv_wait(remote, done, limit: float) -> dict:
    t0 = time.monotonic()
    st = _atv_state(remote)
    while not done(st) and time.monotonic() - t0 < limit:
        await asyncio.sleep(0.25)
        st = _atv_state(remote)
    return st


async def atv_act(host: str, action: str, value: str = "") -> dict:
    if not is_paired(host, "atv_remote"):
        return _not_paired(host)
    lock = _atv_locks.setdefault(host, asyncio.Lock())
    async with lock:
        try:
            remote = await _atv_connected(host)
        except Exception as e:
            _atv.pop(host, None)
            return _result(UNREACHABLE, error=f"{type(e).__name__}: {e}"[:200])
        before = _atv_state(remote)
        try:
            if action == "status":
                return _result(CONFIRMED, before, before)
            if action in ("power_on", "power_off"):
                want = action == "power_on"
                if before["on"] is want:
                    return _result(ALREADY, before, before)
                remote.send_key_command("POWER")  # short press only — never the long-press shutdown
                after = await _atv_wait(remote, lambda s: s["on"] is want, 12)
                return _result(CONFIRMED if after["on"] is want else NOT_CONFIRMED, before, after)
            if action in ("volume_up", "volume_down"):
                remote.send_key_command("VOLUME_UP" if action == "volume_up" else "VOLUME_DOWN")
                after = await _atv_wait(remote, lambda s: s["volume"] != before["volume"], 3)
                changed = before["volume"] is not None and after["volume"] != before["volume"]
                return _result(CONFIRMED if changed else NOT_CONFIRMED, before, after)
            if action == "mute":
                remote.send_key_command("VOLUME_MUTE")
                after = await _atv_wait(remote, lambda s: s["muted"] != before["muted"], 3)
                return _result(CONFIRMED if after["muted"] != before["muted"] else NOT_CONFIRMED, before, after)
            if action == "key":
                code = ATV_KEYS.get(value.strip().lower())
                if not code:
                    return _result(UNSUPPORTED, before, error=f"unknown key {value!r}")
                remote.send_key_command(code)
                await asyncio.sleep(0.5)
                return _result(ACCEPTED, before, _atv_state(remote))
            if action == "app":
                name = value.strip()
                link, package = ATV_APPS.get(name.lower(), (name, ""))
                package = package or next((pkg for lnk, pkg in ATV_APPS.values() if lnk == link), "")
                if not _APP_LINK.match(link) or link.lower().startswith("market:"):
                    return _result(UNSUPPORTED, before,
                                   error=f"unknown app {value!r} (give {', '.join(ATV_APPS)} or an app link like https://…)")
                remote.send_launch_app_command(link)
                if package:
                    after = await _atv_wait(remote, lambda s: s["app"] == package, 10)
                    ok = after["app"] == package
                else:
                    # an unknown link: a new app in front, not the launcher — a late HOME must not count as success
                    def opened(s):
                        return bool(s["app"]) and s["app"] != before["app"] and ".launcher" not in s["app"]

                    after = await _atv_wait(remote, opened, 10)
                    ok = opened(after)
                if not ok:
                    # a connection that stopped taking launches also stops reporting the app — start fresh next time
                    _atv.pop(host, None)
                    remote.disconnect()
                return _result(CONFIRMED if ok else NOT_CONFIRMED, before, after)
        except Exception as e:
            _atv.pop(host, None)
            return _result(ERROR, before, error=f"{type(e).__name__}: {e}"[:200])
        return _result(UNSUPPORTED, before, error=f"unknown action {action!r}")


async def atv_pair_start(host: str) -> dict:
    """Ask the TV to show a pairing code."""
    try:
        old = _atv.pop(host, None)
        if old is not None:
            old.disconnect()
        remote = _atv_new(host)
        await remote.async_generate_cert_if_missing()
        for name in ("cert.pem", "key.pem"):
            _restrict(secrets_dir(host) / name)
        await asyncio.wait_for(remote.async_start_pairing(), 10)
        _atv_pairing[host] = remote
        return {"ok": True, "step": "code"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


async def atv_pair_finish(host: str, code: str) -> dict:
    remote = _atv_pairing.pop(host, None)
    if remote is None:
        return {"ok": False, "error": "no pairing in progress — start again"}
    try:
        await asyncio.wait_for(remote.async_finish_pairing(code.strip()), 15)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    marker = secrets_dir(host) / "paired"
    marker.write_text(_now(), encoding="utf-8")
    _restrict(marker)
    return {"ok": True, "step": "done"}


# ── Samsung (Tizen 2016+) ─────────────────────────────────────────────────────


def _samsung(host: str, timeout: float = 8):
    # PLC0415: optional dependency — only the voice bridge has samsungtvws (D5, rule 24)
    from samsungtvws import SamsungTVWS  # noqa: PLC0415

    tok = secrets_dir(host) / "samsung_token.txt"
    return SamsungTVWS(host=host, port=8002, name="KTSB", token_file=str(tok), timeout=timeout)


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _samsung_power(host: str) -> str | None:
    """'on' / 'standby' from the TV's REST API; None when it is off the network (the AU9075 in standby)."""
    try:
        r = httpx.get(f"http://{host}:8001/api/v2/", timeout=3)
        return str((r.json().get("device") or {}).get("PowerState") or "on")
    except Exception:
        return None


def _upnp(host: str, action: str) -> str | None:
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
        "<InstanceID>0</InstanceID><Channel>Master</Channel>"
        f"</u:{action}></s:Body></s:Envelope>"
    )
    try:
        r = httpx.post(
            f"http://{host}:9197/upnp/control/RenderingControl1",
            content=body,
            headers={"SOAPACTION": f'"urn:schemas-upnp-org:service:RenderingControl:1#{action}"',
                     "Content-Type": 'text/xml; charset="utf-8"'},
            timeout=3,
        )
        tag = "CurrentVolume" if action == "GetVolume" else "CurrentMute"
        return r.text.split(f"<{tag}>", 1)[1].split("<", 1)[0]
    except Exception:
        return None


def _samsung_state(host: str) -> dict:
    power = _samsung_power(host)
    vol = _upnp(host, "GetVolume") if power == "on" else None
    mute = _upnp(host, "GetMute") if power == "on" else None
    return {"on": power == "on", "power": power, "volume": int(vol) if vol and vol.isdigit() else None,
            "muted": (mute == "1") if mute is not None else None}


def wake_on_lan(mac: str, host: str = "") -> None:
    """One magic packet to the LAN broadcast and, when known, straight to the host."""
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + raw * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, ("255.255.255.255", 9))
        if host:
            s.sendto(packet, (host.rsplit(".", 1)[0] + ".255", 9))
            s.sendto(packet, (host, 9))


def _samsung_wait(host: str, done, limit: float) -> dict:
    t0 = time.monotonic()
    st = _samsung_state(host)
    while not done(st) and time.monotonic() - t0 < limit:
        time.sleep(0.7)
        st = _samsung_state(host)
    return st


def samsung_act(host: str, action: str, value: str = "", mac: str = "") -> dict:
    if action != "power_on" and not is_paired(host, "samsung_ws"):
        return _not_paired(host)
    before = _samsung_state(host)
    try:
        if action == "status":
            return _result(CONFIRMED, before, before)
        if action == "power_on":
            if before["on"]:
                return _result(ALREADY, before, before)
            if before["power"] is None:
                if not mac:
                    return _result(UNSUPPORTED, before, error="no MAC address on the node (needed for Wake-on-LAN)")
                t0 = time.monotonic()
                # Phase 0b: a packet sent while the TV is still shutting down is ignored — repeat until it answers.
                while time.monotonic() - t0 < 25 and not _port_open(host, 8001):
                    wake_on_lan(mac, host)
                    time.sleep(2)
            mid = _samsung_wait(host, lambda s: s["power"] is not None, 6)
            if mid["power"] == "standby" and is_paired(host, "samsung_ws"):
                # Network standby (shortly after «off», or right after WoL): the screen needs a POWER key.
                _samsung(host).send_key("KEY_POWER")
            after = _samsung_wait(host, lambda s: s["on"], 12)
            if after["on"] and is_paired(host, "samsung_ws"):
                time.sleep(3)
                _samsung(host).send_key("KEY_RETURN")  # closes the Smart Hub bar the TV wakes up with
            return _result(CONFIRMED if after["on"] else NOT_CONFIRMED, before, after)
        if before["power"] is None:
            return _result(UNREACHABLE, before, error="the TV is off the network (standby)")
        tv = _samsung(host)
        if action == "power_off":
            tv.send_key("KEY_POWER")
            t0 = time.monotonic()
            while time.monotonic() - t0 < 25 and _port_open(host, 8001):
                time.sleep(1)
            after = _samsung_state(host)
            return _result(CONFIRMED if not after["on"] else NOT_CONFIRMED, before, after)
        if action in ("volume_up", "volume_down"):
            tv.send_key("KEY_VOLUP" if action == "volume_up" else "KEY_VOLDOWN")
            after = _samsung_wait(host, lambda s: s["volume"] != before["volume"], 3)
            changed = before["volume"] is not None and after["volume"] != before["volume"]
            return _result(CONFIRMED if changed else NOT_CONFIRMED, before, after)
        if action == "mute":
            tv.send_key("KEY_MUTE")
            after = _samsung_wait(host, lambda s: s["muted"] != before["muted"], 3)
            return _result(CONFIRMED if after["muted"] != before["muted"] else NOT_CONFIRMED, before, after)
        if action == "key":
            code = SAMSUNG_KEYS.get(value.strip().lower())
            if not code:
                return _result(UNSUPPORTED, before, error=f"unknown key {value!r}")
            tv.send_key(code)
            return _result(ACCEPTED, before, _samsung_state(host))
        if action == "app":
            app_id = SAMSUNG_APPS.get(value.strip().lower(), value.strip())
            if not app_id.isdigit():
                return _result(UNSUPPORTED, before, error=f"unknown Samsung app {value!r} (give its numeric app id)")
            r = httpx.post(f"http://{host}:8001/api/v2/applications/{app_id}", timeout=8)
            return _result(ACCEPTED if r.status_code == 200 else ERROR, before, _samsung_state(host),
                           "" if r.status_code == 200 else f"HTTP {r.status_code}")
    except Exception as e:
        return _result(ERROR, before, error=f"{type(e).__name__}: {e}"[:200])
    return _result(UNSUPPORTED, before, error=f"unknown action {action!r}")


def samsung_pair(host: str) -> dict:
    """Open a connection as «KTSB»: the TV shows «Allow?»; the token is saved when someone accepts (≤30 s)."""
    if _samsung_power(host) is None:
        return {"ok": False, "error": "the TV is off the network — turn it on first"}
    try:
        tv = _samsung(host, timeout=30)
        tv.open()
        tv.close()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    _restrict(secrets_dir(host) / "samsung_token.txt")
    return {"ok": is_paired(host, "samsung_ws"), "step": "done",
            **({} if is_paired(host, "samsung_ws") else {"error": "no token — was «Allow» pressed on the TV?"})}


# ── the one executor ──────────────────────────────────────────────────────────


async def act(node_id: str, node: dict, action: str, value: str = "", origin: str = ORIGIN_REQUESTED) -> dict:
    """One TV action on a node through its remote channel; logged with origin (D4). Never raises."""
    host = str(node.get("host") or "").strip()
    channel = remote_channel(node)
    if action not in ACTIONS:
        res = _result(UNSUPPORTED, error=f"unknown action {action!r}")
    elif not host or not channel:
        res = _result(UNSUPPORTED, error="the node has no remote-control channel")
    elif channel == "atv_remote":
        res = await atv_act(host, action, value)
    else:
        res = await asyncio.to_thread(samsung_act, host, action, value, str(node.get("mac") or ""))
    res.update(channel=channel, node=node_id, action=action, origin=origin)
    try:
        log_buffer.write(ACTION_LOG, {"ts": _now(), "node": node_id, "channel": channel, "action": action,
                                      "value": value, "origin": origin, "outcome": res.get("outcome")})
    except Exception:
        pass
    return res


async def pair_start(node: dict) -> dict:
    host, channel = str(node.get("host") or "").strip(), remote_channel(node)
    if channel == "atv_remote":
        return await atv_pair_start(host)
    if channel == "samsung_ws":
        return await asyncio.to_thread(samsung_pair, host)
    return {"ok": False, "error": "the node has no remote-control channel"}


async def pair_finish(node: dict, code: str) -> dict:
    host, channel = str(node.get("host") or "").strip(), remote_channel(node)
    if channel == "atv_remote":
        return await atv_pair_finish(host, code)
    return {"ok": False, "error": "this channel pairs in one step"}


def pairing_status(node: dict) -> dict:
    host, channel = str(node.get("host") or "").strip(), remote_channel(node)
    return {"channel": channel, "paired": bool(host and channel and is_paired(host, channel))}
