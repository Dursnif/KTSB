"""«Frigate may track me» = no → KTSB drops that person's face name where Frigate events enter
(P144-s02). The event itself is kept as an anonymous «person»; plates and opted-in faces pass."""

import asyncio
import json

import pytest

from adapters import frigate_adapter, mqtt_adapter
from kaare_core.users import frigate_consent, store


@pytest.fixture
def household(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    for name in ("userb", "userone"):
        store.create_user(username=name, display_name=name.title(), role="adult", pin="482915")
    store.update_user("userb", frigate_faces=["Userb"])
    store.update_user("userone", frigate_faces=["Userone"])
    store.set_frigate_tracking("userb", False)
    frigate_consent.invalidate()
    yield
    frigate_consent.invalidate()


def _mqtt_end_event(label: str, sub_label) -> bytes:
    before = {
        "id": "1700000000.1-abc",
        "camera": "inngang",
        "label": label,
        "top_score": 0.9,
        "sub_label": sub_label,
        "start_time": 1.0,
        "end_time": 5.0,
        "has_snapshot": True,
    }
    return json.dumps({"type": "end", "before": before, "after": before}).encode()


@pytest.fixture
def mqtt_capture(monkeypatch):
    logged, hooked = [], []
    monkeypatch.setattr(
        mqtt_adapter,
        "_write_event_log",
        lambda event_type, level, message, extra=None: logged.append((message, extra)),
    )
    monkeypatch.setattr(mqtt_adapter, "_END_EVENT_HOOKS", [hooked.append])
    monkeypatch.setattr(mqtt_adapter, "spawn", lambda _r: None)
    return logged, hooked


def _run_mqtt(payload: bytes) -> None:
    asyncio.run(mqtt_adapter._process_frigate_event(payload))


@pytest.mark.parametrize("sub_label", [["Userb", 0.93], "Userb", ["userb", 0.8]])
def test_mqtt_opted_out_face_is_anonymous(household, mqtt_capture, sub_label):
    logged, hooked = mqtt_capture
    _run_mqtt(_mqtt_end_event("person", sub_label))
    message, extra = logged[0]
    assert "Userb" not in message and "userb" not in message.lower()
    assert extra["sub_label"] is None and extra["sub_label_score"] is None
    assert extra["label"] == "person"  # the event is kept
    assert hooked[0]["sub_label"] is None and hooked[0]["sub_label_score"] is None


def test_mqtt_opted_in_face_untouched(household, mqtt_capture):
    logged, hooked = mqtt_capture
    _run_mqtt(_mqtt_end_event("person", ["Userone", 0.91]))
    assert logged[0][1]["sub_label"] == "Userone"
    assert hooked[0]["sub_label"] == "Userone" and hooked[0]["sub_label_score"] == 0.91


def test_mqtt_car_plate_untouched(household, mqtt_capture):
    logged, hooked = mqtt_capture
    _run_mqtt(_mqtt_end_event("car", ["AB12345", 0.88]))
    assert logged[0][1]["sub_label"] == "AB12345"
    assert hooked[0]["sub_label"] == "AB12345"


def test_unlinked_face_untouched(household, mqtt_capture):
    """A Frigate face nobody in KTSB is linked to is not anyone's opt-out."""
    logged, _ = mqtt_capture
    _run_mqtt(_mqtt_end_event("person", ["Tove", 0.9]))
    assert logged[0][1]["sub_label"] == "Tove"


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, *_a, **_k):
        return _FakeResponse(json.loads(json.dumps(self._data)))


def _rest_events():
    return [
        {"id": "a", "label": "person", "sub_label": "Userb", "data": {"sub_label": "Userb"}},
        {"id": "b", "label": "person", "sub_label": "Userone", "data": {}},
        {"id": "c", "label": "car", "sub_label": "AB12345", "data": {}},
    ]


def test_rest_events_strip_opted_out(household, monkeypatch):
    monkeypatch.setattr(
        frigate_adapter.httpx, "AsyncClient", lambda **_k: _FakeClient(_rest_events())
    )
    events = {e["id"]: e for e in asyncio.run(frigate_adapter.fetch_events(limit=10))}
    assert events["a"]["sub_label"] is None and events["a"]["data"]["sub_label"] is None
    assert events["b"]["sub_label"] == "Userone"
    assert events["c"]["sub_label"] == "AB12345"


def test_rest_face_events_leave_out_opted_out(household, monkeypatch):
    monkeypatch.setattr(
        frigate_adapter.httpx, "AsyncClient", lambda **_k: _FakeClient(_rest_events())
    )
    faces = [e["_face_name"] for e in asyncio.run(frigate_adapter.fetch_face_events(limit=10))]
    assert "Userb" not in faces and "Userone" in faces


def test_rest_single_event_strip(household, monkeypatch):
    monkeypatch.setattr(
        frigate_adapter.httpx, "AsyncClient", lambda **_k: _FakeClient(_rest_events()[0])
    )
    ev = asyncio.run(frigate_adapter.get_event("a"))
    assert ev["sub_label"] is None and ev["label"] == "person"


def test_opt_back_in_restores_name(household, mqtt_capture):
    logged, _ = mqtt_capture
    store.set_frigate_tracking("userb", True)
    frigate_consent.invalidate()
    _run_mqtt(_mqtt_end_event("person", ["Userb", 0.9]))
    assert logged[0][1]["sub_label"] == "Userb"
