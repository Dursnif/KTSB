"""ha_control action=ha_history (BUG-log 2026-10-10): long-term statistics come over HA's WebSocket API.

Before, the tool POSTed to `/api/recorder/statistics_during_period`, which is not a REST endpoint — every call got
HTTP 404. Now: statistics via the WebSocket command; entities without statistics (switches …) fall back to the raw
REST history; an unknown entity says so instead of «HTTP 404». HA is faked — no network.
"""

import asyncio

import httpx
import pytest

from kaare_core.tools import executor_ha as ex

DAY_MS = 86_400_000


@pytest.fixture
def ha(monkeypatch):
    """Fake HA: `state` = REST /api/states status + unit, `stats` = WebSocket result, `history` = REST history."""
    cfg = {"state": 200, "unit": "°C", "stats": {}, "history": [], "ws_calls": []}

    async def fake_ws(ha_url, token, message, timeout=10.0):
        cfg["ws_calls"].append(message)
        if isinstance(cfg["stats"], Exception):
            raise cfg["stats"]
        return cfg["stats"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/states/"):
            return httpx.Response(cfg["state"], json={"attributes": {"unit_of_measurement": cfg["unit"]}})
        if request.url.path.startswith("/api/history/period/"):
            return httpx.Response(200, json=cfg["history"])
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ex, "_ha_ws_call", fake_ws)
    monkeypatch.setattr(ex.httpx, "AsyncClient", client)
    monkeypatch.setattr(ex, "_read_ha_url", lambda: "http://ha.test:8123")
    monkeypatch.setattr(ex, "get_ha_token", lambda: "tok")
    return cfg


def _run(entity="sensor.t", days=3, period="day", lang="nb"):
    return asyncio.run(ex._ha_sensor_history(entity, days, period, lang))


def test_statistics_come_over_websocket_with_epoch_ms_starts(ha):
    ha["stats"] = {
        "sensor.t": [
            {"start": 1_791_324_000_000, "mean": 26.1, "min": 26.0, "max": 27.0},
            {"start": 1_791_324_000_000 + DAY_MS, "mean": 25.9, "min": 25.0, "max": 26.0},
        ]
    }
    out = _run()
    assert ha["ws_calls"][0]["type"] == "recorder/statistics_during_period"
    assert ha["ws_calls"][0]["statistic_ids"] == ["sensor.t"]
    assert "2026-10-0" in out and "snitt 26.1 °C" in out and "maks 27.0 °C" in out
    assert "404" not in out


def test_entity_without_statistics_falls_back_to_state_changes(ha):
    ha["stats"] = {}
    ha["history"] = [[
        {"state": "on", "last_changed": "2026-10-09T16:08:00+00:00"},
        {"state": "off", "last_changed": "2026-10-09T17:48:00+00:00"},
    ]]
    out = _run("switch.s", days=1)
    assert "Tilstandsendringer" in out and "→ on" in out and "→ off" in out


def test_no_history_either_says_so(ha):
    ha["stats"] = {}
    ha["history"] = []
    assert "Ingen registrert historikk" in _run("switch.s", days=1)


def test_unknown_entity_is_named_not_a_404(ha):
    ha["state"] = 404
    out = _run("sensor.does_not_exist")
    assert "ikke funnet" in out and "404" not in out


def test_long_history_is_capped(ha):
    ha["stats"] = {}
    ha["history"] = [[{"state": str(i % 2), "last_changed": f"2026-10-09T10:{i % 60:02d}:00+00:00"} for i in range(55)]]
    out = _run("switch.s", days=1)
    assert out.count("→") == 40 and "40 av 55" in out


def test_websocket_failure_is_reported(ha):
    ha["stats"] = RuntimeError("HA WebSocket authentication failed")
    out = _run()
    assert "authentication failed" in out


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_fallback_texts_exist_in_all_languages(ha, lang):
    ha["stats"] = {}
    ha["history"] = [[{"state": "on", "last_changed": "2026-10-09T16:08:00+00:00"}]]
    out = _run("switch.s", days=1, lang=lang)
    assert "{" not in out and "→ on" in out


def test_parse_ha_time_accepts_both_forms():
    a = ex._parse_ha_time(1_791_324_000_000)
    b = ex._parse_ha_time("2026-10-06T22:00:00+00:00")
    assert a == b
