import logging
from pathlib import Path
from typing import Optional

import yaml

from adapters.display.base import BaseDisplayProvider
from adapters.display.chromecast import ChromecastDisplayProvider
from adapters.display.dlna import DlnaDisplayProvider
from adapters.display.ha import HaDisplayProvider
from adapters.display.tvoverlay import TvOverlayDisplayProvider

log = logging.getLogger(__name__)

_SERVICES_PATH = Path("/kaare/configs/services.yaml")
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
    def __init__(self, media_base_url: str):
        ha_url, ha_token = _load_ha()
        self._ha = HaDisplayProvider(ha_url, ha_token)
        self._chromecast = ChromecastDisplayProvider(media_base_url)
        self._dlna = DlnaDisplayProvider(media_base_url)
        self._tvoverlay = TvOverlayDisplayProvider()

        # primary → fallback per type
        self._map: dict[str, list[BaseDisplayProvider]] = {
            "chromecast":      [self._chromecast, self._ha],
            "google_tv":       [self._tvoverlay, self._ha],
            "android_tv":      [self._tvoverlay, self._ha],
            "fire_tv":         [self._ha],
            "samsung_tv":      [self._dlna, self._ha],
            "lg_tv":           [self._dlna, self._ha],
            "apple_tv":        [self._dlna, self._ha],
            "projector":       [self._dlna, self._ha],
            "ha_media_player": [self._ha],
        }

    async def send(self, node_id: str, node_config: dict, **kwargs) -> dict:
        node_type = node_config.get("type", "")
        providers = self._map.get(node_type, [self._ha])
        last_error = "no provider"
        for provider in providers:
            try:
                result = await provider.send(node_config, **kwargs)
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            if result.get("ok"):
                return result
            last_error = result.get("error", "unknown error")
            log.debug("Display provider %s failed for %s: %s", type(provider).__name__, node_id, last_error)
        return {"ok": False, "error": last_error}
