"""R137 (P144 v2, observed 2026-11-06): KTSB's camera page read only Frigate's global `objects.track`, so a label turned on
for one camera only (Frigate's per-camera list) never showed as a tick in KTSB."""

from adapters import frigate_adapter


def test_a_label_tracked_on_one_camera_only_is_offered():
    cfg = {
        "objects": {"track": ["person", "car"]},
        "cameras": {
            "door": {"objects": {"track": ["person", "waste_bin"]}},
            "road": {"objects": {"track": ["car", "person"]}},
        },
    }
    assert frigate_adapter.tracked_labels_from_config(cfg) == ["person", "car", "waste_bin"]


def test_missing_or_odd_parts_never_raise():
    assert frigate_adapter.tracked_labels_from_config({}) == []
    assert frigate_adapter.tracked_labels_from_config({"cameras": {"x": None, "y": {"objects": None}}}) == []
    assert frigate_adapter.tracked_labels_from_config({"objects": {"track": ["dog", 3, ""]}}) == ["dog"]
