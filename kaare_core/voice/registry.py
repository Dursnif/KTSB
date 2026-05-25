import logging
from pathlib import Path

import yaml

from kaare_core.voice.base import BaseVoiceProvider
from kaare_core.voice.providers.airplay import AirPlayProvider
from kaare_core.voice.providers.chromecast import ChromecastProvider
from kaare_core.voice.providers.dlna import DlnaProvider
from kaare_core.voice.providers.esp32 import Esp32Provider
from kaare_core.voice.providers.ha import HaProvider
from kaare_core.voice.providers.snapcast import SnapcastProvider
from kaare_core.voice.providers.wyoming import WyomingProvider

log = logging.getLogger(__name__)

_PROVIDERS_CONFIG_PATH = Path("/kaare/configs/voice_providers.yaml")


class VoiceProviderRegistry:
    def __init__(self, bridge_host: str, bridge_port: int, ha_url: str, ha_token: str):
        try:
            with open(_PROVIDERS_CONFIG_PATH) as f:
                _cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            log.warning("voice_providers.yaml ikke funnet — bruker tomme defaults")
            _cfg = {}

        media_base_url = f"http://{bridge_host}:{bridge_port}/audio"

        self._providers: dict[str, BaseVoiceProvider] = {
            "ha_media_player": HaProvider(ha_url, ha_token, media_base_url),
            "esp32":           Esp32Provider(media_base_url),
            "wyoming":         WyomingProvider(_cfg.get("wyoming", {})),
            "chromecast":      ChromecastProvider(_cfg.get("chromecast", {}), media_base_url),
            "snapcast":        SnapcastProvider(_cfg.get("snapcast", {}), media_base_url),
            "airplay":         AirPlayProvider(_cfg.get("airplay", {}), media_base_url),
            "dlna":            DlnaProvider(_cfg.get("dlna", {}), media_base_url),
        }
        log.info("VoiceProviderRegistry klar: %s", list(self._providers.keys()))

    def get(self, node_type: str) -> BaseVoiceProvider | None:
        provider = self._providers.get(node_type)
        if provider is None:
            log.warning("Ukjent node-type '%s' — ingen provider registrert", node_type)
        return provider
