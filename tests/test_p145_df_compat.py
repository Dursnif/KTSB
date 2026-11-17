"""P145: DeepFilterNet must import on a clean install with current torchaudio (no hand-edited site-packages).

DeepFilterNet 0.5.6 imports `torchaudio.backend.common.AudioMetaData`, removed in torchaudio 2.3. The house worked only
because df/io.py had been edited by hand inside the voice venv. services/voice/df_compat.ensure() registers a stand-in.
"""

import sys
import types

from services.voice import df_compat


def _no_backend(monkeypatch):
    """Simulate torchaudio >= 2.3: torchaudio exists, torchaudio.backend does not."""
    if sys.modules.get("torchaudio") is None:
        monkeypatch.setitem(sys.modules, "torchaudio", types.ModuleType("torchaudio"))
    monkeypatch.setitem(sys.modules, "torchaudio.backend", None)
    monkeypatch.setitem(sys.modules, "torchaudio.backend.common", None)


def test_without_ensure_the_df_import_line_fails(monkeypatch):
    _no_backend(monkeypatch)
    try:
        # PLC0415: the import itself is what is under test (runs after sys.modules is patched)
        from torchaudio.backend.common import AudioMetaData  # noqa: F401, PLC0415
    except ImportError:
        return
    raise AssertionError("expected ImportError — the simulation does not reproduce torchaudio >= 2.3")


def test_ensure_makes_the_df_import_line_work(monkeypatch):
    _no_backend(monkeypatch)
    df_compat.ensure()
    # PLC0415: the import itself is what is under test (runs after ensure())
    from torchaudio.backend.common import AudioMetaData  # noqa: PLC0415

    meta = AudioMetaData(sample_rate=48000, num_frames=10, num_channels=1)
    assert meta.sample_rate == 48000 and meta.num_frames == 10 and meta.num_channels == 1


def test_ensure_leaves_a_real_backend_alone(monkeypatch):
    real = types.ModuleType("torchaudio.backend.common")
    real.AudioMetaData = object
    monkeypatch.setitem(sys.modules, "torchaudio.backend.common", real)
    df_compat.ensure()
    assert sys.modules["torchaudio.backend.common"] is real


def test_ensure_is_idempotent(monkeypatch):
    _no_backend(monkeypatch)
    df_compat.ensure()
    first = sys.modules["torchaudio.backend.common"]
    df_compat.ensure()
    assert sys.modules["torchaudio.backend.common"] is first
