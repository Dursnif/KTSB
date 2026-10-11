"""Meeting endpoints require an admin token (BUG-log 2026-10-03).

Write endpoints are only called without a valid admin token, so the test never
touches state/ or starts a meeting.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.routers import router_meetings

ADMIN_ONLY = [
    ("get", "/api/dev-meetings"),
    ("get", "/api/dev-meetings/2026-10-03"),
    ("get", "/api/meetings/topic/dev"),
    ("post", "/api/meetings/topic/dev"),
    ("get", "/api/meetings/comment/dev/2026-10-03"),
    ("post", "/api/meetings/comment/dev/2026-10-03"),
    ("get", "/api/meetings/status"),
    ("post", "/api/reflections/start"),
    ("post", "/api/dev-meetings/start"),
]

_BODY = {"topic": "x", "comment": "x"}


@pytest.fixture
def client(monkeypatch):
    # touch_last_seen writes to users.db — keep the test side-effect free
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    app = FastAPI()
    app.include_router(router_meetings.router)
    return TestClient(app)


def _hdr(role):
    return {"Authorization": f"Bearer {auth.create_token('test_user', role)}"}


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_rejected_without_token(client, method, path):
    r = getattr(client, method)(path, **({"json": _BODY} if method == "post" else {}))
    assert r.status_code in (401, 403), (path, r.status_code)


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_rejected_for_non_admin(client, method, path):
    r = getattr(client, method)(
        path, headers=_hdr("adult"), **({"json": _BODY} if method == "post" else {})
    )
    assert r.status_code == 403, (path, r.status_code)


@pytest.mark.parametrize("path", ["/api/dev-meetings", "/api/meetings/topic/dev"])
def test_admin_can_read(client, path):
    r = client.get(path, headers=_hdr("admin"))
    assert r.status_code == 200, (path, r.status_code)
