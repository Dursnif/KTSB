"""Who is calling /api/generate (P120 Phase 2) — a logged-in user or a KTSB service."""

from fastapi import HTTPException

from kaare_core.service_auth import is_service_request
from kaare_core.users import store as _user_store
from kaare_core.users.auth import decode_token


def identify_caller(headers, body_user_id: str | None) -> tuple[str | None, bool]:
    """(user_id, is_service) for a /api/generate request, or (None, False) to refuse it.

    - a valid user token (JWT) → its user; the body's user_id is ignored. A token for a user
      that no longer exists counts as no token.
    - the service token → the body's user_id, resolved (unknown → unknown_user, none →
      global_user — store.resolve_request_user)
    - neither → refused"""
    is_service = is_service_request(headers)
    jwt_user = None
    auth_header = headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            jwt_user = decode_token(auth_header.split(" ", 1)[1]).get("sub")
        except HTTPException:
            jwt_user = None
    if jwt_user and not _user_store.get_user(jwt_user):
        jwt_user = None
    if jwt_user:
        return jwt_user, is_service
    if is_service:
        return _user_store.resolve_request_user(body_user_id), True
    return None, False
