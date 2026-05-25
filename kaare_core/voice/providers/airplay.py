import asyncio
import logging
from pathlib import Path

from kaare_core.voice.base import BaseVoiceProvider

log = logging.getLogger(__name__)


class AirPlayProvider(BaseVoiceProvider):
    """AirPlay 2 provider via pyatv (RAOP-protokoll).

    Streamer WAV direkte til AirPlay-enheten over RAOP — ingen HTTP-serving.
    Kobler via IP (ingen mDNS-avhengighet).

    Dersom enheten krever pairing, legg RAOP-credentials i node_config 'token'-feltet.
    Credentials hentes via: atvremote --id <ip> pair --protocol RAOP
    """

    def __init__(self, config: dict, media_base_url: str):
        self._config = config
        self._media_base_url = media_base_url

    async def speak(self, wav_path: Path, node_config: dict) -> None:
        import pyatv
        from pyatv import const

        host = node_config.get("host", "")
        if not host:
            log.error("AirPlay provider: mangler host i node-config")
            return

        credentials = node_config.get("token")
        loop = asyncio.get_event_loop()

        try:
            results = await pyatv.scan(
                loop,
                hosts=[host],
                timeout=5,
                protocol=const.Protocol.RAOP,
            )
        except Exception as exc:
            log.error("AirPlay: scan feilet på %s: %s", host, exc)
            return

        if not results:
            log.error(
                "AirPlay: ingen RAOP-enhet funnet på %s. "
                "Sjekk at enheten er på nett og støtter AirPlay.",
                host,
            )
            return

        config = results[0]
        if credentials:
            config.set_credentials(const.Protocol.RAOP, credentials)

        atv = None
        try:
            atv = await pyatv.connect(config, loop, protocol=const.Protocol.RAOP)
            log.info("AirPlay '%s' (%s) <- %s", config.name, host, wav_path.name)
            await atv.stream.stream_file(str(wav_path))
        except Exception as exc:
            log.error("AirPlay provider feil på %s: %s", host, exc)
        finally:
            if atv:
                atv.close()
