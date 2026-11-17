"""DT04-S05l: after an API restart a camera-question receipt said «Hvor: «cam_…»» (raw id) while the offer before the restart
had the camera's name. Both use frigate_adapter.camera_display_name(fetch=False), whose last fallback is Frigate's
friendly_name cache — empty after a restart until something else fetched Frigate's config (GUI camera tab, the pipe).
Fix: a cold/stale cache starts one background refresh (never blocks the event loop), and API startup warms it."""

import ast
import threading
import time
from pathlib import Path

from adapters import frigate_adapter

REAL_WARM = getattr(frigate_adapter, "warm_camera_names", None)  # captured before conftest makes it a no-op per test
CONFIG = {"cameras": {"cam_a1b2": {"friendly_name": "Front Door"}}}


def _real_warm(monkeypatch):
    assert REAL_WARM is not None, "frigate_adapter.warm_camera_names missing"
    monkeypatch.setattr(frigate_adapter, "warm_camera_names", REAL_WARM)


def test_cold_cache_lookup_starts_a_refresh_and_the_name_arrives(monkeypatch):
    _real_warm(monkeypatch)
    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking", lambda: CONFIG)
    assert frigate_adapter.camera_display_name("cam_a1b2", {}, fetch=False) in ("cam_a1b2", "Front Door")
    deadline = time.monotonic() + 3
    while frigate_adapter.camera_display_name("cam_a1b2", {}, fetch=False) != "Front Door":
        assert time.monotonic() < deadline, "name never arrived after a cold-cache lookup"
        time.sleep(0.01)


def test_warm_never_blocks_and_runs_once_at_a_time(monkeypatch):
    _real_warm(monkeypatch)
    calls = []
    gate = threading.Event()

    def slow_fetch():
        calls.append(1)
        gate.wait(2)
        return CONFIG

    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking", slow_fetch)
    t0 = time.monotonic()
    th = frigate_adapter.warm_camera_names()
    frigate_adapter.warm_camera_names()
    frigate_adapter.warm_camera_names()
    assert time.monotonic() - t0 < 0.5  # never waits for Frigate
    gate.set()
    th.join(3)
    assert len(calls) == 1
    assert frigate_adapter.camera_display_name("cam_a1b2", {}, fetch=False) == "Front Door"


def test_fresh_cache_does_not_refetch(monkeypatch):
    _real_warm(monkeypatch)
    monkeypatch.setattr(frigate_adapter, "_friendly", {"cam_a1b2": "Front Door"})
    monkeypatch.setattr(frigate_adapter, "_friendly_ts", time.monotonic())
    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking", lambda: (_ for _ in ()).throw(AssertionError("fetched")))
    assert frigate_adapter.warm_camera_names() is None
    assert frigate_adapter.camera_display_name("cam_a1b2", {}, fetch=False) == "Front Door"


def test_api_startup_warms_the_camera_names():
    tree = ast.parse(Path("/kaare/kaare_api.py").read_text(encoding="utf-8"))
    startup = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_startup")
    assert "warm_camera_names" in ast.unparse(startup)
