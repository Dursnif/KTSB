"""N29 — which reachability check each node gets (the Nodes page's WiFi icon)."""

import pytest

from kaare_core.voice import node_status as ns


@pytest.mark.parametrize(
    ("node", "plan"),
    [
        ({"type": "direct"}, ("direct",)),
        (
            {"type": "ha_media_player", "entity_id": "media_player.nest"},
            ("ha", "media_player.nest"),
        ),
        # api_port of a Wyoming node is Kåre's server port — the device itself answers on ESPHome / satellite ports
        (
            {"type": "wyoming", "host": "10.0.0.5", "api_port": 10300},
            ("tcp", "10.0.0.5", (6053, 10700)),
        ),
        ({"type": "esp32", "host": "10.0.0.6", "api_port": 6053}, ("tcp", "10.0.0.6", (6053,))),
        ({"type": "chromecast", "host": "10.0.0.7"}, ("channels", "10.0.0.7")),  # P139: per channel
        ({"type": "google_tv", "entity_id": "media_player.tv"}, ("ha", "media_player.tv")),
        ({"type": "android_tv", "host": "10.0.0.4"}, ("channels", "10.0.0.4")),
        # the GUI stores ADB 5555 as api_port on TVs — not what KTSB talks to (Thomson TV, 2026-10-18)
        (
            {"type": "android_tv", "host": "10.0.0.4", "api_port": 5555, "tvoverlay_port": 5001},
            ("channels", "10.0.0.4"),
        ),
        (
            {"type": "android_tv", "host": "10.0.0.4", "tvoverlay_port": 7980},
            ("channels", "10.0.0.4"),
        ),
        ({"type": "snapcast", "host": "10.0.0.8", "api_port": 1705}, ("tcp", "10.0.0.8", (1705,))),
        ({"type": "projector", "host": "10.0.0.9"}, ("channels", "10.0.0.9")),  # ping is the fallback
        ({"type": "dlna"}, ("none",)),
    ],
)
def test_plan_probe(node, plan):
    assert ns.plan_probe(node) == plan
