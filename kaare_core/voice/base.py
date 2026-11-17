from abc import ABC, abstractmethod
from pathlib import Path


class BaseVoiceProvider(ABC):
    @abstractmethod
    async def speak(self, wav_path: Path, node_config: dict) -> bool:
        """Play the WAV file on the target node. True when the node took the audio (P138) —
        False when it could not be reached or refused, so the caller can tell the user."""

    async def set_volume(self, volume: float, node_config: dict) -> None:
        """Set volume before playback. No-op unless overridden."""
        return

    def supports_mic(self) -> bool:
        return False

    async def is_available(self, node_config: dict) -> bool:
        """Cheap reachability check before TTS is sent (P138). Default: assume reachable —
        speak()'s own result still reports a failed delivery."""
        return True
