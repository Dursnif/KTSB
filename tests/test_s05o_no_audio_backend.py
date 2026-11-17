"""DT04-S05o: a KTSB Direct trigger on a machine without a sound device (Docker) ended in «PortAudio library not found» + a
traceback from record_audio. The GUI already says `no_audio_backend` for the same machine; the pipeline now checks first
and stops with that honest reason — no ack, no recording, no traceback."""

import ast
import builtins
from pathlib import Path

from kaare_core.voice import audio_backend

BRIDGE = Path("/kaare/services/voice/voice_bridge.py")


def _importer_raising(exc):
    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "sounddevice":
            raise exc
        return real(name, *args, **kwargs)

    return fake


def test_portaudio_missing_is_no_audio_backend(monkeypatch):
    # sounddevice raises OSError at import when the PortAudio system library is absent (what Docker logged)
    monkeypatch.setattr(builtins, "__import__", _importer_raising(OSError("PortAudio library not found")))
    assert audio_backend.missing_reason() == audio_backend.NO_AUDIO_BACKEND


def test_sounddevice_not_installed_is_no_audio_backend(monkeypatch):
    monkeypatch.setattr(builtins, "__import__", _importer_raising(ImportError("No module named 'sounddevice'")))
    assert audio_backend.missing_reason() == audio_backend.NO_AUDIO_BACKEND


def test_the_reason_matches_the_gui_word():
    assert audio_backend.NO_AUDIO_BACKEND == "no_audio_backend"


def _fn(name):
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_pipeline_checks_the_backend_before_ack_and_recording():
    body = _fn("_run_pipeline").body
    src = [ast.unparse(s) for s in body]
    check = next(i for i, s in enumerate(src) if "missing_reason" in s)
    ack = next(i for i, s in enumerate(src) if "WAKE_ACK_WAV" in s)
    rec = next(i for i, s in enumerate(src) if "record_audio" in s)
    assert check < ack < rec
    # the If right after the check stops the pipeline (returns) before the ack, and logs the reason
    stmt = body[check + 1]
    assert check + 1 < ack
    assert isinstance(stmt, ast.If) and "reason" in ast.unparse(stmt.test)
    assert any(isinstance(s, ast.Return) for s in stmt.body)
    assert "reason" in ast.unparse(stmt.body[0])
