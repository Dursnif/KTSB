"""P120 Phase 2 — /api/generate takes the user from the login token; only KTSB's own services
(service token) may name the user. Temporary token file and users.db — never the live ones."""

import pytest

from kaare_core import service_auth
from kaare_core.users import auth, caller, store


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    store.create_user(
        username="anna", display_name="Anna", role="adult", pin="482915", must_change_pin=False
    )
    monkeypatch.setattr(service_auth, "_TOKEN_PATH", tmp_path / "service_token.env")
    monkeypatch.setattr(service_auth, "_cache", {"mtime": None, "token": ""})
    service_auth.ensure_service_token()
    return tmp_path


def _bearer(user, role="adult"):
    return {"authorization": f"Bearer {auth.create_token(user, role)}"}


def test_token_created_once_and_private(env):
    first = service_auth.get_service_token()
    service_auth.ensure_service_token()
    assert first and service_auth.get_service_token() == first
    assert oct((env / "service_token.env").stat().st_mode)[-3:] == "640"


def test_anonymous_request_refused(env):
    assert caller.identify_caller({}, "anna") == (None, False)
    assert caller.identify_caller({}, None) == (None, False)
    assert caller.identify_caller({"authorization": "Bearer garbage"}, "anna") == (None, False)
    assert caller.identify_caller({service_auth.SERVICE_HEADER: "wrong"}, "anna") == (None, False)


def test_user_comes_from_the_token_not_the_body(env):
    assert caller.identify_caller(_bearer("anna"), "admin") == ("anna", False)
    assert caller.identify_caller(_bearer("anna"), None) == ("anna", False)


def test_token_for_deleted_user_refused(env):
    assert caller.identify_caller(_bearer("ghost"), "anna") == (None, False)


@pytest.mark.parametrize(
    "body,expected",
    [
        ("anna", "anna"),
        (None, "global_user"),
        ("global", "global_user"),
        ("unknown_user", "unknown_user"),
        ("made_up", "unknown_user"),
    ],
)
def test_service_may_name_the_user(env, body, expected):
    headers = service_auth.service_headers()
    assert caller.identify_caller(headers, body) == (expected, True)


def test_no_token_file_means_no_service(env, monkeypatch):
    monkeypatch.setattr(service_auth, "_TOKEN_PATH", env / "missing.env")
    monkeypatch.setattr(service_auth, "_cache", {"mtime": None, "token": ""})
    assert service_auth.is_service_request({service_auth.SERVICE_HEADER: ""}) is False
