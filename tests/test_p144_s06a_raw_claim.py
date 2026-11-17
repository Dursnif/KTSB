"""P144-s06a: Frigate's raw claim reaches only the Frigate user's hook (P144 bug «consent strip at ingestion hides
Frigate's claim from the claim-vs-image check»). The MQTT log line and every other end-event hook keep today's
stripped version; the raw name lives only in RAM for the shadow pipe's check."""

import asyncio
import json

import pytest

from adapters import mqtt_adapter
from kaare_core.users import frigate_consent, store


@pytest.fixture
def household(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    store.create_user(username="personb", display_name="Personb", role="adult", pin="482915")
    store.update_user("personb", frigate_faces=["Person B"])
    store.set_frigate_tracking("personb", False)
    frigate_consent.invalidate()
    yield
    frigate_consent.invalidate()


def _end_event(sub_label) -> bytes:
    before = {
        "id": "1700000000.1-abc", "camera": "inngang", "label": "person", "top_score": 0.9,
        "sub_label": sub_label, "start_time": 1.0, "end_time": 5.0, "has_snapshot": True,
    }
    return json.dumps({"type": "end", "before": before, "after": before}).encode()


@pytest.fixture
def capture(monkeypatch):
    logged, other, frigate_user = [], [], []

    async def other_hook(ev):
        other.append(ev)

    async def frigate_user_hook(ev, raw_name, raw_score):
        frigate_user.append((ev, raw_name, raw_score))

    monkeypatch.setattr(
        mqtt_adapter, "_write_event_log",
        lambda event_type, level, message, extra=None: logged.append((message, extra)),
    )
    monkeypatch.setattr(mqtt_adapter, "_END_EVENT_HOOKS", [other_hook])
    monkeypatch.setattr(mqtt_adapter, "_FRIGATE_USER_HOOKS", [frigate_user_hook], raising=False)
    return logged, other, frigate_user


def _run(payload: bytes) -> None:
    async def main():
        await mqtt_adapter._process_frigate_event(payload)
        await asyncio.sleep(0)  # let the spawned hooks run

    asyncio.run(main())


def test_raw_claim_reaches_frigate_user_hook_only(household, capture):
    logged, other, frigate_user = capture
    _run(_end_event(["Person B", 0.93]))
    # the Frigate user's hook sees the claim …
    ev, raw_name, raw_score = frigate_user[0]
    assert raw_name == "Person B" and raw_score == 0.93
    # … but its event dict is the stripped one, like every other hook's
    assert ev["sub_label"] is None
    # the log and the other hooks never see the name
    message, extra = logged[0]
    assert "Person B" not in message and extra["sub_label"] is None
    assert other[0]["sub_label"] is None and other[0]["sub_label_score"] is None


def test_raw_claim_for_opted_in_face(household, capture):
    _logged, other, frigate_user = capture
    _run(_end_event(["Person C", 0.8]))
    assert frigate_user[0][1] == "Person C"
    assert other[0]["sub_label"] == "Person C"


def test_no_claim_gives_none(household, capture):
    _logged, _other, frigate_user = capture
    _run(_end_event(None))
    assert frigate_user[0][1] is None and frigate_user[0][2] is None


def test_frigate_user_hook_not_called_on_update_events(household, capture):
    _logged, _other, frigate_user = capture
    payload = json.loads(_end_event(["Person B", 0.9]))
    payload["type"] = "update"
    _run(json.dumps(payload).encode())
    assert frigate_user == []
