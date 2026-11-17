"""J24 follow-up — the camera's display name, never its raw id, in the text the models get (KTSB-visjon thread 11: «where»).

`route.camera_name` (the 27B route/summary text, the scene and look prompts) and the camera tool's analysis list
(`executor_camera._camera_display`) use the same chain as the GUI: card display name (unless blank or just the id) →
services.yaml camera_names → Frigate's friendly_name (cache only — this code runs on the event loop) → the raw id.
No HTTP, no live state. Placeholders only."""

import pytest

from adapters import frigate_adapter
from kaare_core.frigate_user import conclude, frames, route
from kaare_core.tools import executor_camera

CARDS = {"cam_fr": {"display_name": "cam_fr", "role": "front_door"}, "cam_ok": {"display_name": "Kortnavn"}}


@pytest.fixture
def frigate_names(monkeypatch):
    monkeypatch.setattr(frigate_adapter, "_camera_map", dict)
    monkeypatch.setattr(frigate_adapter, "_fetch_config_blocking", lambda: pytest.fail("event-loop path must not fetch"))
    frigate_adapter.camera_types_from_config({"cameras": {"cam_fr": {"friendly_name": "Fra Frigate"}}})


def test_route_camera_name_uses_frigate_name_when_card_has_only_the_id(frigate_names):
    text = route.camera_name("cam_fr", CARDS["cam_fr"], None, "nb")
    assert "Fra Frigate" in text and "cam_fr" not in text
    assert "Kortnavn" in route.camera_name("cam_ok", CARDS["cam_ok"], None, "nb")
    assert "cam_none" in route.camera_name("cam_none", None, None, "nb")  # no name anywhere → the id


def test_27b_scene_prompt_has_the_name_not_the_id(frigate_names):
    picked = [frames.Frame(ts=1_800_000_000.0, camera="cam_fr", jpeg=b"")]
    prompt = conclude.build_scene_prompt(picked, "route", "nb", CARDS, {})
    assert "Fra Frigate" in prompt and "cam_fr" not in prompt


def test_camera_tool_analysis_list_has_the_name(frigate_names, monkeypatch):
    monkeypatch.setattr(executor_camera, "camera_config", lambda: {"cameras": CARDS})
    assert executor_camera._camera_display("cam_fr") == "Fra Frigate"
    assert executor_camera._camera_display("cam_ok") == "Kortnavn"
