"""P144: routine Frigate traffic must not drown `journalctl -u kaare.service -f` (2026-10-29).

- an event that drives nothing (unticked/below threshold, no claim) logs at DEBUG only — the facts are in frigate.db;
- an event that drives or carries a claim still logs its summary at INFO;
- a Frigate REST 404 (event Frigate did not keep) is DEBUG; any other failure stays INFO and names the status code;
- the GUI's episode frame fetches are quiet in the access log; httpx request lines are quiet.
Never touches live state: s06a's fixture points frigate.db / users.db / configs at tmp_path.
"""

import asyncio
import logging

import httpx

import kaare_api
from kaare_core.frigate_user import pipe
from tests import test_p144_s06a_pipe as s06a
from tests import test_p144_s12_camera_settings as s12

env = s06a.env
cams = s12.cams


def _info_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == pipe.log.name and r.levelno >= logging.INFO]


def test_routine_event_is_debug_only(cams, caplog):
    caplog.set_level(logging.DEBUG, logger=pipe.log.name)
    s06a._process(s12._ev("c1", label="car"))  # car unticked on «inngang»
    assert s06a._stored("c1") is not None
    assert _info_lines(caplog) == []
    assert any("c1" in r.getMessage() for r in caplog.records)  # still there at DEBUG


def test_driving_event_keeps_its_info_summary(cams, caplog):
    caplog.set_level(logging.INFO, logger=pipe.log.name)
    s06a._process(s12._ev("p1", score=0.9))
    assert any("p1" in m and "decision=" in m for m in _info_lines(caplog))


def _http_error(code):
    req = httpx.Request("GET", "http://frigate/api/events/x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))


def test_rest_404_is_debug_other_errors_name_the_code(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=pipe.log.name)

    async def gone(event_id):
        raise _http_error(404)

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", gone)
    assert asyncio.run(pipe._fetch_event("e404")) == {}
    assert _info_lines(caplog) == []

    async def broken(event_id):
        raise _http_error(500)

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", broken)
    assert asyncio.run(pipe._fetch_event("e500")) == {}
    assert any("e500" in m and "500" in m for m in _info_lines(caplog))


def test_episode_frame_fetches_are_quiet_in_access_log():
    f = kaare_api._SuppressPolls()

    def rec(msg):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, msg, None, None)

    assert not f.filter(rec('1.2.3.4:5 - "GET /api/frigate_user/episodes/80/frames/3 HTTP/1.1" 200'))
    assert not f.filter(rec('1.2.3.4:5 - "OPTIONS /api/frigate_user/episodes/80/frames/3 HTTP/1.1" 200'))
    assert f.filter(rec('1.2.3.4:5 - "GET /api/frigate_user/episodes/80 HTTP/1.1" 200'))


def test_httpx_request_lines_are_quiet():
    assert logging.getLogger("httpx").level >= logging.WARNING  # own level: pytest's root is WARNING already
