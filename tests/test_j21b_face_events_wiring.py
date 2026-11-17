"""J21b reader 2 wiring: action='events' and the old alias les_kamerahendelser both answer through `_face_events`."""

import asyncio

from kaare_core.tools import executor_camera


def test_events_action_and_alias_use_the_new_reader(monkeypatch):
    seen = []

    async def fake(arguments, uid, lang):
        seen.append((arguments.get("action"), uid))
        return "ok"

    monkeypatch.setattr(executor_camera, "_face_events", fake)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    assert asyncio.run(executor_camera.dispatch("camera", {"action": "events", "_user_id": "user_x"})) == "ok"
    assert asyncio.run(executor_camera.dispatch("les_kamerahendelser", {"_user_id": "user_x"})) == "ok"
    assert seen == [("events", "user_x"), (None, "user_x")]
