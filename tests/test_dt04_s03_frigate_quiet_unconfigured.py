"""DT04 R3a: no Frigate configured → the start-up recovery does nothing and warns about nothing."""

import asyncio
import logging
import sqlite3

from adapters import frigate_adapter
from kaare_core.frigate_user import pipe


def _run_start(monkeypatch, url, caplog):
    called = []

    async def boom(*a, **k):
        called.append(1)
        raise sqlite3.OperationalError("no such table: events")

    monkeypatch.setattr(frigate_adapter, "_svc", lambda *a, **k: url)
    monkeypatch.setattr(pipe.settings, "enabled", lambda: True)
    monkeypatch.setattr(pipe, "recover_pending", boom)
    with caplog.at_level(logging.WARNING):
        asyncio.run(pipe._recover_at_start())
    return called


def test_unconfigured_frigate_is_silent(monkeypatch, caplog):
    called = _run_start(monkeypatch, "", caplog)
    assert not called
    assert not [r for r in caplog.records if "FRIGATE_USER" in r.getMessage()]


def test_configured_frigate_still_warns_on_failure(monkeypatch, caplog):
    called = _run_start(monkeypatch, "http://frigate.example:5000", caplog)
    assert called
    assert [r for r in caplog.records if "recovering pending events failed" in r.getMessage()]
