"""J24 part 2 — who may load an event's picture, and camera display names in the question rows.

- `/api/frigate_snapshot/{event}`: admin sees every picture (the house is never blind — main session 2026-11-12); a normal
  user only the events of their own «Var det deg?» questions (open in GUI/chat, or answered by them); a normal user never
  an event holding an opted-out (untracked) person; a person with tracking off gets none (their lists are empty).
- camera name: the camera card's display name, unless it is blank or only the raw id → services.yaml `camera_names` →
  Frigate's own `friendly_name` (cached from a config Frigate already sent, read only) → the raw id.
Never touches live state: users.db, frigate.db, weights.db, yaml and the snapshot folder point at tmp_path; no HTTP.
Placeholders only."""

import asyncio

import pytest
from fastapi import HTTPException

from adapters import frigate_adapter
from kaare_core.frigate_user import query, questions
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.routers import router_media
from tests.test_p149_j2_questions import (  # noqa: F401  (env is a fixture)
    NOW,
    H,
    _ev,
    _sensors,
    _store,
    env,
)

ADMIN = {"sub": "boss", "role": "admin"}


def _user(name):
    return {"sub": name, "role": "adult"}


@pytest.fixture
def snaps(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    d = env / "frigate_snapshots"
    d.mkdir()
    monkeypatch.setattr(router_media, "SNAPSHOT_DIR", d)
    return d


def _get(event_id, who):
    return asyncio.run(router_media.api_serve_frigate_snapshot(event_id, _u=who))


def _status(event_id, who):
    try:
        _get(event_id, who)
    except HTTPException as e:
        return e.status_code
    return 200


def _pic(d, *ids):
    for i in ids:
        (d / f"{i}.jpg").write_bytes(b"\xff\xd8jpeg")


# ── access ──

def test_normal_user_sees_only_own_question_events(snaps):
    _store(_ev("mine", NOW - H, sensors=_sensors("Person A", [("det_x", "user_a")])),
           _ev("other", NOW - H, ref="user_b", sensors=_sensors("Person B", [("det_x", "user_b")])))
    _pic(snaps, "mine", "other", "not_in_store")
    assert _status("mine", _user("user_a")) == 200
    assert _status("other", _user("user_a")) == 403
    assert _status("not_in_store", _user("user_a")) == 403


def test_answered_own_question_stays_visible(snaps):
    _store(_ev("mine", NOW - H, sensors=_sensors("Person A", [("det_x", "user_a")])))
    _pic(snaps, "mine")
    assert questions.answer("user_a", "mine", "yes")["ok"]
    assert _status("mine", _user("user_a")) == 200  # the history row keeps its picture


def test_opted_out_caller_sees_nothing(snaps):
    _store(_ev("e1", NOW - H, sensors=_sensors("Person A", [("det_x", "user_c")])))
    _pic(snaps, "e1")
    assert _status("e1", _user("user_c")) == 403


def test_normal_user_never_sees_an_untracked_event(snaps, monkeypatch):
    _store(_ev("u1", NOW - H, ref=None, kind="untracked", sensors=_sensors("Person A", [("det_x", "user_a")])))
    _pic(snaps, "u1")
    # even if it somehow were one of their questions
    monkeypatch.setattr(questions, "candidates", lambda *a, **k: [{"event_id": "u1"}])
    assert _status("u1", _user("user_a")) == 403


def test_admin_sees_every_picture(snaps):
    _store(_ev("u1", NOW - H, ref=None, kind="untracked"), _ev("e2", NOW - H))
    _pic(snaps, "u1", "e2", "frigate_only")
    for i in ("u1", "e2", "frigate_only"):
        assert _status(i, ADMIN) == 200


def test_missing_file_is_404_for_an_allowed_caller(snaps):
    assert _status("nothing", ADMIN) == 404


# ── camera names ──

@pytest.fixture
def names(monkeypatch):
    monkeypatch.setattr(frigate_adapter, "_camera_map", lambda: {"cam_svc": "Fra tjenester"})
    frigate_adapter.camera_types_from_config({"cameras": {"cam_fr": {"friendly_name": "Fra Frigate"},
                                                          "cam_svc": {"friendly_name": "Frigate taper"},
                                                          "cam_card": {"friendly_name": "Frigate taper"}}})
    yield
    frigate_adapter.camera_types_from_config({})


def test_names_are_fetched_once_at_read_time_and_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(frigate_adapter, "_camera_map", dict)
    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking",
                        lambda: calls.append(1) or {"cameras": {"cam_x": {"friendly_name": "Hentet"}}})
    monkeypatch.setattr(frigate_adapter, "_friendly_ts", 0.0)
    assert rfu._cam_name("cam_x", {}) == "Hentet"
    assert rfu._cam_name("cam_x", {}) == "Hentet"
    assert calls == [1]
    monkeypatch.setattr(frigate_adapter, "_friendly_ts", 0.0)
    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking", lambda: None)  # Frigate away
    assert rfu._cam_name("cam_y", {}) == "cam_y"


def test_display_name_order(names):
    cards = {"cam_card": {"display_name": "Kortet"}, "cam_fr": {"display_name": "cam_fr"}, "cam_svc": {"display_name": ""}}
    assert rfu._cam_name("cam_card", cards) == "Kortet"
    assert rfu._cam_name("cam_svc", cards) == "Fra tjenester"
    assert rfu._cam_name("cam_fr", cards) == "Fra Frigate"  # card holds only the raw id
    assert rfu._cam_name("cam_none", cards) == "cam_none"


def test_question_rows_use_the_display_name(env, names, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rfu, "_camera_cards", lambda: {"cam_fr": {"display_name": "cam_fr"}})
    _store(_ev("e1", NOW - H, camera="cam_fr", sensors=_sensors("Person A", [("det_x", "user_a")])))
    rows = rfu.api_my_questions({"sub": "user_a"})["questions"]
    assert [r["camera"] for r in rows] == ["Fra Frigate"]


def test_chat_question_text_uses_cached_name_and_never_fetches(names, monkeypatch):
    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking", lambda: pytest.fail("chat path must not fetch"))
    assert "Fra Frigate" in query._camera_text("cam_fr", None, {"cam_fr": {"display_name": "cam_fr"}}, "nb")
    monkeypatch.setattr(frigate_adapter, "_friendly_ts", 0.0)
    assert "cam_zz" in query._camera_text("cam_zz", None, {}, "nb")
