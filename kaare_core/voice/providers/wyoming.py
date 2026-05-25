import logging
from pathlib import Path

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


class WyomingProvider(BaseVoiceProvider):
    """Wyoming protocol provider.

    Kåre kjører en TCP-server (port 10300) som satellitter kobler seg til.
    Mic-inn: satellitt → Kåre (STT → Kåre API → TTS → lyd tilbake).
    TTS-ut: pushes WAV til allerede-tilkoblet satellitt via etablert forbindelse.
    """

    def __init__(self, config: dict):
        self._config = config

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        host = node_config.get("host", "")
        if not host:
            log.error("Wyoming provider: mangler host i node-config")
            return

        from kaare_core.voice.wyoming_server import speak_to_satellite
        sent = await speak_to_satellite(host, wav_path)
        if not sent:
            log.warning(
                "Wyoming: ingen aktiv forbindelse til %s — announce kan ikke leveres. "
                "Satellitten må være tilkoblet Wyoming-serveren.",
                host,
            )

    def supports_mic(self) -> bool:
        return True
