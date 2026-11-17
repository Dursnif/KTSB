import logging
from pathlib import Path

import yaml

from adapters.display.base import BaseDisplayProvider
from adapters.display.chromecast import ChromecastDisplayProvider
from adapters.display.dlna import DlnaDisplayProvider
from adapters.display.ha import HaDisplayProvider
from adapters.display.tvoverlay import TvOverlayDisplayProvider
from kaare_core.voice import channels

log = logging.getLogger(__name__)

_SERVICES_PATH = Path("/kaare/configs/services.yaml")
# Channels that replace what plays on the screen. After one reports «busy» (another app plays — P-D1) only a channel
# that draws on top (TvOverlay) may still try.
_REPLACING = {"cast", "dlna", "ha"}
_HA_TOKEN_PATH = Path("/kaare/configs/ha_token.env")


def _load_ha() -> tuple[str, str]:
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text())
        ha_url = svc.get("home_assistant", {}).get("url", "")
        token = ""
        if _HA_TOKEN_PATH.exists():
            for line in _HA_TOKEN_PATH.read_text().splitlines():
                if line.startswith("HA_TOKEN="):
                    token = line.split("=", 1)[1].strip()
        return ha_url, token
    except Exception:
        return "", ""


class DisplayProviderRegistry:
    def __init__(self):
        ha_url, ha_token = _load_ha()
        self._ha = HaDisplayProvider(ha_url, ha_token)
        self._chromecast = ChromecastDisplayProvider()  # P139 phase 3: through the voice bridge
        self._dlna = DlnaDisplayProvider()
        self._tvoverlay = TvOverlayDisplayProvider()
        # channel → provider (P139 phase 2: the node's channels decide, not its type)
        self._by_channel: dict[str, BaseDisplayProvider] = {
            "tvoverlay": self._tvoverlay,
            "cast": self._chromecast,
            "dlna": self._dlna,
            "ha": self._ha,
        }

    async def send(self, node_id: str, node_config: dict, job: str = "notice", **kwargs) -> dict:
        """Try the node's enabled channels for this job, best first. The reply names the channel that worked and
        every channel tried before it — a fallback is never silent (N26)."""
        order = channels.order_for(job, node_config)
        if not order:
            return {"ok": False, "error": "no display channel", "tried": []}
        tried: list[dict] = []
        busy = ""
        entries = channels.node_channels(node_config)
        for name in order:
            if busy and name in _REPLACING:
                continue
            cfg = dict(node_config)
            if name == "tvoverlay":
                cfg["tvoverlay_port"] = channels.port_of(name, entries.get(name))
            try:
                result = await self._by_channel[name].send(cfg, **kwargs)
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            if result.get("ok"):
                return {**result, "channel": name, "tried": tried}
            busy = busy or str(result.get("busy") or "")
            tried.append({"channel": name, "error": str(result.get("error") or "unknown error")})
            log.debug("Display channel %s failed for %s: %s", name, node_id, tried[-1]["error"])
        return {
            "ok": False,
            "error": "; ".join(f"{t['channel']}: {t['error']}" for t in tried),
            "tried": tried,
            **({"busy": busy} if busy else {}),
        }
