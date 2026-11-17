"""Is there a local sound backend (PortAudio via sounddevice) on this machine?

KTSB Direct records from this machine's own microphone. In Docker, or on a machine without PortAudio, there is none —
`import sounddevice` then raises OSError («PortAudio library not found») or ImportError. The GUI already reports that as
`no_audio_backend` (voice_bridge /audio_devices); the voice pipeline asks here first so a button press ends with the same
honest reason instead of a traceback (DT04-S05o). Never raises."""

NO_AUDIO_BACKEND = "no_audio_backend"


def missing_reason() -> str | None:
    """None when a sound backend is available, else NO_AUDIO_BACKEND."""
    try:
        # PLC0415: optional dependency — sounddevice needs the PortAudio system library (absent in Docker)
        import sounddevice  # noqa: F401, PLC0415
    except Exception:  # OSError (no PortAudio) or ImportError (package not installed)
        return NO_AUDIO_BACKEND
    return None
