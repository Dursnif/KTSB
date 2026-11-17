"""P144 phase 6c — module 7 starts its own away alert from the MQTT end events (no longer from the old responder).

- `alerts.on_end_event(event)` calls `maybe_notify_away(camera, label, score)` with exactly what the old responder passed;
- the old responder is gone (phase 6, List B) and nothing registers it — the starter is the only sender;
- `kaare_api` registers the starter on the end-event hook list;
- the same calls with module 0 (frigate_user.enabled) on and off — the starter never asks it.
No MQTT, no HTTP; the alert itself is faked."""

import asyncio
import inspect
from pathlib import Path

import pytest

from kaare_core.frigate_user import alerts

ROOT = Path(__file__).resolve().parent.parent
EVENTS = [{"event_id": "e1", "camera": "cam_door", "label": "person", "score": 0.91},
          {"event_id": "e2", "camera": "cam_yard", "label": "car", "score": 0.72},
          {"event_id": "e3", "camera": "cam_yard", "label": "", "score": "0.65"}]


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def m7(camera, label, score):
        out.append((camera, label, score))

    monkeypatch.setattr(alerts, "maybe_notify_away", m7)
    return out


@pytest.mark.parametrize("module0", [True, False])
def test_module_7_starts_its_own_alert(sent, monkeypatch, module0):
    monkeypatch.setattr("kaare_core.frigate_user.settings.enabled", lambda: module0)
    for ev in EVENTS:
        asyncio.run(alerts.on_end_event(ev))
    assert sent == [("cam_door", "person", 0.91), ("cam_yard", "car", 0.72), ("cam_yard", "", 0.65)]


def test_the_old_responder_is_gone_and_not_registered():
    assert not (ROOT / "kaare_core" / "domain" / "frigate_responder.py").exists()
    src = inspect.getsource(__import__("kaare_api")._start_mqtt)
    assert "register_end_event_callback" not in src and "handle_end_event" not in src


def test_the_api_registers_the_starter():
    src = inspect.getsource(__import__("kaare_api")._start_mqtt)
    assert "register_end_event_hook(alerts.on_end_event)" in src
