from abc import ABC, abstractmethod
from pathlib import Path


class BaseVoiceProvider(ABC):
    @abstractmethod
    async def speak(self, wav_path: Path, node_config: dict) -> None:
        """Play the WAV file on the target node."""

    async def set_volume(self, volume: float, node_config: dict) -> None:
        """Set volume before playback. No-op unless overridden."""

    def supports_mic(self) -> bool:
        return False

    async def is_available(self, node_config: dict) -> bool:
        return True
