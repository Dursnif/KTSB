"""P138 phase 2 — image out through tools: with no target named, show it on the screen where the user is."""

import asyncio

import yaml

from kaare_core import rooms
from kaare_core.tools import executor as ex
from kaare_core.tools import executor_ha as eh
from kaare_core.tools import executor_media as em

NODES = {
    "nodes": {
        "atom": {"type": "wyoming", "host": "10.0.0.5"},
        "tv_bed": {
            "type": "ha_media_player",
            "entity_id": "media_player.tv_bed",
            "has_display": True,
        },
        "tv_living": {
            "type": "ha_media_player",
            "entity_id": "media_player.tv_living",
            "has_display": True,
        },
        "pe": {"type": "esp32", "host": "10.0.0.6"},
    }
}
PLACES = {"atom": "soverom", "tv_bed": "soverom", "tv_living": "stue", "pe": "kjokken"}


def setup(monkeypatch, tmp_path, **node_over):
    data = {"nodes": {k: dict(v) for k, v in NODES["nodes"].items()}}
    for nid, fields in node_over.items():
        data["nodes"][nid].update(fields)
    path = tmp_path / "nodes.yaml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(eh, "_NODES_PATH", path)
    monkeypatch.setattr(rooms, "node_place", lambda nid, **k: (PLACES.get(nid, ""), ""))


def run_display(monkeypatch, source_node: str, target: str = "") -> list[str]:
    sent: list[str] = []

    async def fake_nodes():
        return [{"id": "tv_bed"}, {"id": "tv_living"}]

    async def fake_send(nid, **kw):
        sent.append(nid)
        return {"ok": True}

    monkeypatch.setattr(ex, "get_display_nodes", fake_nodes)
    monkeypatch.setattr(ex, "send_display", fake_send)
    args = {
        "action": "display",
        "text": "hei",
        "target": target,
        "_user_id": "test",
        "_source_node": source_node,
    }
    asyncio.run(ex.execute_tool("announce", args))
    return sent


def test_display_without_target_goes_to_screen_in_same_place(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    assert run_display(monkeypatch, "atom") == ["tv_bed"]


def test_display_explicit_image_out(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, atom={"image_out": ["tv_living"]})
    assert run_display(monkeypatch, "atom") == ["tv_living"]


def test_display_named_target_still_wins(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    assert run_display(monkeypatch, "atom", target="tv_living") == ["tv_living"]


def test_display_no_screen_near_keeps_old_default_all(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    assert run_display(monkeypatch, "pe") == ["tv_bed", "tv_living"]
    assert run_display(monkeypatch, "") == ["tv_bed", "tv_living"]


def test_node_image_targets_reads_nodes_file(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    assert eh.node_image_targets("atom") == ["tv_bed"]
    assert eh.node_image_targets("") == []
    assert eh.node_entity("tv_bed") == "media_player.tv_bed"
    assert eh.node_entity("ghost") is None


def test_plex_play_without_client_uses_screen_where_user_is(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    seen = {}

    async def fake_meta(key):
        seen["entity"] = em.node_entity(em.node_image_targets("atom")[0])
        return None  # stop right after the client is resolved

    monkeypatch.setattr(em, "_plex_get_metadata", fake_meta)
    out = asyncio.run(
        em.dispatch(
            "media",
            {"action": "plex_play", "rating_key": "1", "_source_node": "atom", "_user_id": "test"},
        )
    )
    assert seen["entity"] == "media_player.tv_bed"
    assert out != em.t("media_no_client", em.get_lang("test"))  # reached the metadata step


def test_plex_play_without_client_and_no_screen_asks_for_client(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    out = asyncio.run(
        em.dispatch("media", {"action": "plex_play", "rating_key": "1", "_source_node": "pe"})
    )
    assert out == em.t("media_no_client", em.get_lang("global"))
