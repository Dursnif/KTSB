"""J21b reader 3 (server side) — what Jing may know about the people the cameras saw.

- a stable pseudonym id per person (`person-<hex>`): the same on every call, the same for a face name and its household
  user, different per person, not containing the name; only kaare_core maps it back;
- the context lines: id + kind + camera display name; never a name; an opted-out person (untracked, or hidden since) is
  only «en person» with no kind; an unnamed person is «en person (ukjent)»;
- GET /api/inner-voices/context: the push token (Bearer) or 401 — also when no token is configured; a wrong source 403;
  module 0 off → available false.
No live state: users.db, frigate.db, weights.db, yaml point at tmp_path. Placeholders only."""

import asyncio

import pytest
from fastapi import HTTPException

from kaare_core.domain import frigate_store as camera_cards
from kaare_core.frigate_user import inner_context, pseudonyms, query
from kaare_core.routers import router_settings
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    CAMS,
    NOW,
    H,
    _ev,
    _sensors,
    _store,
    env,
)

NAMES = ("user_a", "USER_A", "Person A", "user_b", "USER_B", "Person B", "user_c", "USER_C", "Person C")


@pytest.fixture
def ctx(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    monkeypatch.setattr(camera_cards, "camera_config", lambda: {"cameras": CAMS})
    return env


def _named(eid, start, ref="user_a"):
    return _ev(eid, start, ref=ref, claim="confirmed",
               sensors=_sensors({"user_a": "Person A", "user_b": "Person B"}[ref], [("det_x", ref)], "agree"))


def test_pseudonym_is_stable_per_person_and_not_the_name(ctx):
    a = pseudonyms.pid("user_a")
    assert a == pseudonyms.pid("user_a") == pseudonyms.pid("Person A")  # face name → its household user
    assert a.startswith("person-") and len(a) == len("person-") + 6
    assert a != pseudonyms.pid("user_b")
    assert not any(n.casefold() in a.casefold() for n in NAMES)
    assert pseudonyms.owner(a) == "user_a" and pseudonyms.owner("person-000000") is None


def test_context_lines_carry_ids_never_names(ctx):
    _store(_named("a1", NOW - H), _ev("u1", NOW - H + 60, ref=None, kind="untracked"),
           _ev("n1", NOW - H + 120, ref=None, kind="unknown"), _named("b1", NOW - H + 180, ref="user_b"))
    ustore.set_frigate_tracking("user_b", False)  # opted out after storage → hidden
    frigate_consent.invalidate()
    lines = inner_context.people(NOW - 2 * H, "nb", now=NOW)
    assert len(lines) == 4
    assert lines[0].endswith(f"{pseudonyms.pid('user_a')} (husstand) — Inngang")
    assert lines[1].endswith("en person — Inngang")  # untracked: no id, no kind
    assert lines[2].endswith("en person (ukjent) — Inngang")
    assert lines[3].endswith("en person — Inngang")  # hidden since: no id, no kind
    assert not any(n.casefold() in " ".join(lines).casefold() for n in NAMES)


# ── the endpoint ──

class _Req:
    def __init__(self, token=None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


@pytest.fixture
def api(ctx, monkeypatch):
    svc = ctx / "services.yaml"
    svc.write_text("inner_voices:\n  push_token: tok-123\n")
    monkeypatch.setattr(router_settings, "_SERVICES_PATH", svc)
    monkeypatch.setattr(router_settings.time, "time", lambda: NOW)
    monkeypatch.setattr(inner_context.time, "time", lambda: NOW)
    return svc


def _call(token, source="jing", since=NOW - 2 * H):
    return asyncio.run(router_settings.api_inner_voices_context(_Req(token), source=source, since=since, lang="nb"))


def _code(*a, **k):
    try:
        _call(*a, **k)
    except HTTPException as e:
        return e.status_code
    return 200


def test_endpoint_fails_closed(api):
    assert _code(None) == 401 and _code("wrong") == 401
    assert _code("tok-123", source="argus") == 403
    api.write_text("inner_voices:\n  push_token: ''\n")  # no token configured → nobody gets in
    assert _code("") == 401 and _code("tok-123") == 401


def test_endpoint_gives_ids_and_reports_module_off(api, monkeypatch):
    _store(_named("a1", NOW - H))
    out = _call("tok-123")
    assert out["available"] is True and pseudonyms.pid("user_a") in out["people"][0]
    monkeypatch.setattr(query, "available", lambda: False)
    assert _call("tok-123") == {"available": False, "people": []}
