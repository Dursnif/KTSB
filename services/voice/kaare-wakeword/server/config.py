"""Server configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    """Voice server configuration."""

    host: str = "0.0.0.0"
    port: int = 8765

    # STT (nb-whisper = NbAiLab Norwegian, nb-whisper-turbo = distil variant)
    whisper_model: str = "nb-whisper"
    whisper_language: str | None = "no"

    # NLU
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "hf.co/NbAiLab/borealis-4b-instruct-preview-gguf:Q8_0"
    ollama_timeout: int = 120  # seconds — first request loads model into GPU
    system_prompt_file: str | None = None  # path to custom system prompt

    # Escalation
    model_chain: tuple[str, ...] = (
        "hf.co/NbAiLab/borealis-4b-instruct-preview-gguf:Q8_0",
        "borealis-12b:latest",
        "borealis-27b:latest",
    )
    escalation_threshold: int = 3

    # Web search
    brave_api_key: str = ""

    # Cloud fallback
    claude_api_key: str = ""

    # TTS
    piper_voice: str = "en_US-lessac-medium"

    # Home Assistant
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""

    # Sonos output
    sonos_enabled: bool = False
    sonos_volume: float = 0.4
    sonos_config_file: str = ""  # JSON file with speakers/satellites/rooms
    sonos_tts_script: str = "script.sonos_tts_norwegian_speak"
    sonos_broadcast_script: str = "script.sonos_broadcast"
