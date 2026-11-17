"""J21b reader 4 — dev slot evidence `frigate:<event_id>` resolves from frigate.db first, the old analysis log second.

- an event in frigate.db → its camera display name, label, times, episode, module 1's still text; the person only where
  stored by the gate and not hidden since; an untracked event never carries a person;
- an event only in the old log (before 5c) still resolves from the log; an event in neither → no_such_event.
No live state: frigate.db, users.db, weights.db, yaml and the old log point at tmp_path. Placeholders only."""

import json

import pytest

from kaare_core.dev_slot import verify
from kaare_core.frigate_user import store as fstore
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


@pytest.fixture
def ev_env(env, monkeypatch):  # noqa: F811  (env: the shared fixture imported above)
    log = env / "frigate_analysis.log"
    log.write_text(json.dumps({"event_id": "old-1", "label": "deer", "analysis": "Ingen hjort synlig."}) + "\n")
    monkeypatch.setattr(verify, "FRIGATE_LOG", log)
    monkeypatch.setattr(verify.camera_cards, "camera_config", lambda: {"cameras": CAMS})
    return env


def _still(e, text):
    e.check = {**e.check, "still": {"text": text, "ts": e.start_ts}}
    return e


def _res(eid):
    return verify._resolve(f"frigate:{eid}", verify._Evidence(set()))


def test_db_event_resolves_with_camera_name_and_still_text(ev_env):
    _store(_still(_ev("e1", NOW - H, claim="confirmed", sensors=_sensors("Person A", [("det_x", "user_a")], "agree")),
                  "En person går mot døra."))
    r = _res("e1")
    data = json.loads(r["text"])
    assert r["ok"] and data["camera"] == "Inngang" and data["analysis"] == "En person går mot døra."
    assert data["person"] == "user_a"
    assert verify.quote_in("går mot døra", r["text"])


def test_a_new_event_carries_the_l3_words(ev_env):
    """P144 phase 6: module 1 is gone, so new events have no `check.still` — the evidence carries the image model's L3
    words (description + analysis) instead; an old still text, where one is stored, still wins."""
    e = _ev("n1", NOW - H)
    e.description, e.analysis = "Ei katt på trappa.", "Sitter stille."
    _store(e)
    data = json.loads(_res("n1")["text"])
    assert data["analysis"] == "Ei katt på trappa. Sitter stille."


def test_person_hidden_since_and_untracked_carry_no_person(ev_env):
    _store(_ev("b1", NOW - H, ref="user_b", claim="confirmed", sensors=_sensors("Person B", [("det_x", "user_b")], "agree")),
           _ev("u1", NOW - H, ref=None, kind="untracked"))
    ustore.set_frigate_tracking("user_b", False)
    frigate_consent.invalidate()
    assert "person" not in json.loads(_res("b1")["text"])
    assert "person" not in json.loads(_res("u1")["text"])


def test_old_log_still_resolves_and_missing_is_refused(ev_env):
    r = _res("old-1")
    assert r["ok"] and "Ingen hjort synlig." in r["text"]
    assert _res("nope")["why"] == "no_such_event"


def test_fstore_is_the_test_db(ev_env):
    assert str(fstore.DB_PATH).startswith(str(ev_env))
