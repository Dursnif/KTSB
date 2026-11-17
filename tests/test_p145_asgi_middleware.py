"""P145: kaare_api's own middleware must not break file responses (camera snapshots, stored images).

Regression 2026-10-27: after starlette 1.7 → 1.10, every FileResponse behind MetricsMiddleware came out as 0 bytes
("Response content shorter than Content-Length"). Since starlette 1.8, FileResponse streams the file while it listens
on receive() for http.disconnect; the old MetricsMiddleware replaced receive with a fake that always answered
http.request, which aborted the stream. These tests run the real middleware classes from kaare_api.
"""

import json

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

import kaare_api
from kaare_core.users import auth


def _app(tmp_path, monkeypatch):
    records = []
    monkeypatch.setattr(kaare_api.log_buffer, "write", lambda path, rec: records.append((path, rec)))
    img = tmp_path / "snap.jpg"
    img.write_bytes(bytes(range(256)) * 1200)  # 307200 bytes, larger than one 64 KiB chunk

    app = FastAPI()

    @app.get("/file")
    async def _file():
        return FileResponse(img, media_type="image/jpeg")

    @app.post("/echo")
    async def _echo(request: Request):
        return {"len": len(await request.body()), "json": await request.json()}

    @app.get("/boom")
    async def _boom():
        raise RuntimeError("boom")

    # same order as kaare_api.py: gate innermost, metrics outermost
    app.add_middleware(kaare_api.PinChangeGateMiddleware)
    app.add_middleware(kaare_api.SecurityHeadersMiddleware)
    app.add_middleware(kaare_api.MetricsMiddleware)
    return app, img, records


def test_file_response_arrives_whole_through_all_middleware(tmp_path, monkeypatch):
    app, img, _ = _app(tmp_path, monkeypatch)
    r = TestClient(app).get("/file")
    assert r.status_code == 200
    assert r.content == img.read_bytes()
    assert r.headers["content-length"] == str(img.stat().st_size)


def test_security_headers_on_file_and_json(tmp_path, monkeypatch):
    app, _, _ = _app(tmp_path, monkeypatch)
    c = TestClient(app)
    for r in (c.get("/file"), c.post("/echo", json={"prompt": "hei"})):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_request_body_still_reaches_endpoint_and_metrics_logs_prompt(tmp_path, monkeypatch):
    app, _, records = _app(tmp_path, monkeypatch)
    r = TestClient(app).post("/echo", json={"prompt": "skru på lyset"})
    assert r.json()["json"] == {"prompt": "skru på lyset"}
    path, rec = records[-1]
    assert path == kaare_api.METRICS_LOG
    assert rec["endpoint"] == "/echo" and rec["status"] == 200
    assert rec["prompt_preview"] == "skru på lyset" and rec["prompt_hash"]
    assert rec["duration_ms"] >= 0


def test_metrics_logs_file_request_without_prompt(tmp_path, monkeypatch):
    app, _, records = _app(tmp_path, monkeypatch)
    TestClient(app).get("/file")
    _, rec = records[-1]
    assert rec["endpoint"] == "/file" and rec["status"] == 200 and rec["prompt_preview"] == ""
    json.dumps(rec)  # the log line stays serialisable


def test_metrics_logs_500_when_endpoint_raises(tmp_path, monkeypatch):
    app, _, records = _app(tmp_path, monkeypatch)
    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    _, rec = records[-1]
    assert rec["endpoint"] == "/boom" and rec["status"] == 500


def test_metrics_logging_failure_never_breaks_the_response(tmp_path, monkeypatch):
    app, img, _ = _app(tmp_path, monkeypatch)

    def _broken(path, rec):
        raise OSError("disk full")

    monkeypatch.setattr(kaare_api.log_buffer, "write", _broken)
    r = TestClient(app).get("/file")
    assert r.status_code == 200 and r.content == img.read_bytes()


def test_pin_change_gate_still_blocks_and_file_still_served(tmp_path, monkeypatch):
    app, img, _ = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "get_user", lambda u: {"must_change_pin": True})
    tok = auth.create_token("admin", "admin", pin_change=True)
    c = TestClient(app)
    blocked = c.get(f"/file?token={tok}")
    assert blocked.status_code == 403 and blocked.json()["pin_change_required"] is True
    assert blocked.headers["x-frame-options"] == "DENY"  # security headers on the 403 too
    normal = auth.create_token("admin", "admin")
    assert c.get(f"/file?token={normal}").content == img.read_bytes()
