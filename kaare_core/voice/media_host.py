"""The LAN address speakers (ESP32, Chromecast, HA media players, DLNA …) fetch Kåre's audio from (P127).

Set in the GUI (Settings → LLM → Piper, services.yaml `voice.media_host`). Empty = detect this machine's
LAN address. Inside a container the detected address is the container's own (unreachable from the LAN),
so Docker users must set it — the GUI suggests the address the browser used to reach KTSB.
"""

import logging
import socket
from pathlib import Path

from kaare_core.config import _read_yaml_live

log = logging.getLogger(__name__)

# TEST-NET-1 (RFC 5737): connect() on a UDP socket only consults the routing table — no packet is sent.
_PROBE_ADDR = ("192.0.2.1", 9)


def in_container() -> bool:
    return Path("/.dockerenv").exists()


def detect_lan_address() -> str:
    """This machine's address on the default route, or '' if there is none."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(_PROBE_ADDR)
            return s.getsockname()[0]
    except OSError:
        return ""


def configured_media_host() -> str:
    return str(
        ((_read_yaml_live("services.yaml").get("voice") or {}).get("media_host")) or ""
    ).strip()


def media_host() -> str:
    """The configured address, else the detected one (else 127.0.0.1, which only local playback can use)."""
    return configured_media_host() or detect_lan_address() or "127.0.0.1"
