"""R146 (2026-11-12: option B) — module 7's away alert reads the normalcy baseline for the event's own camera, and a
person never gets a lower urgency than HIGH while the house is away (the baseline is learned while people are home). Other
labels keep the graduation (a known-normal pattern at a confident baseline → MEDIUM). No HTTP; notify_user is faked.
The 1 621 recorded person events of 27.10–12.11 were checked against the real baseline on a copy (run-queue log)."""

import asyncio

import pytest

from kaare_core.frigate_user import alerts


@pytest.fixture
def world(monkeypatch):
    seen = {"hints": [], "sent": []}
    monkeypatch.setattr(alerts, "_cfg", lambda: {"cameras": {"cam_x": {"role": "front_door"}}})
    monkeypatch.setattr(alerts, "list_users", lambda: [{"username": "user_a", "role": "admin"}])
    monkeypatch.setattr(alerts, "get_lang", lambda uid: "nb")
    monkeypatch.setattr(alerts, "write_notify_log", lambda entry: None)

    async def notify(uid, msg, urgency, source_key):
        seen["sent"].append(str(urgency))

    monkeypatch.setattr(alerts, "notify_user", notify)

    def hints(deviation, confidence):
        def derive(event, role):
            seen["hints"].append(event)
            return {"away_mode": True, "priority": "high", "deviation_score": deviation, "baseline_confidence": confidence}
        monkeypatch.setattr(alerts, "derive_hints", derive)
    seen["set"] = hints
    return seen


def test_the_lookup_gets_the_camera(world):
    world["set"](0.5, 0.0)
    asyncio.run(alerts.maybe_notify_away("cam_x", "person", 0.9))
    assert world["hints"][0]["camera"] == "cam_x"


@pytest.mark.parametrize(("deviation", "confidence"), [(0.0, 1.0), (0.1, 0.97), (0.29, 0.5), (0.9, 1.0), (0.5, 0.0)])
def test_a_person_is_always_high_while_away(world, deviation, confidence):
    world["set"](deviation, confidence)
    asyncio.run(alerts.maybe_notify_away("cam_x", "person", 0.9))
    assert world["sent"] == ["high"]


def test_other_labels_keep_the_graduation(world):
    world["set"](0.1, 0.97)  # a known-normal pattern for this camera
    asyncio.run(alerts.maybe_notify_away("cam_x", "car", 0.9))
    assert world["sent"] == ["medium"] and world["hints"][0]["camera"] == "cam_x"
    world["sent"].clear()
    world["set"](0.8, 0.97)
    asyncio.run(alerts.maybe_notify_away("cam_x", "car", 0.9))
    assert world["sent"] == ["high"]
