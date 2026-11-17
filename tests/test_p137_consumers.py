"""P137 phase 4 — other consumers of the place register: Kåre's node context and `announce display` targets."""

import asyncio

from kaare_core import context_builder as cb
from kaare_core import rooms
from kaare_core.tools import executor as ex

HA = {"areas": {"kjokken": {"name": "Kjøkken"}, "verksted": {"name": "Verksted"}}}
REG = rooms.build({"places": {"kjokken": {"synonyms": ["kjøkkenet"]}, "verksted": {"synonyms": ["verkstedet"]}}}, HA)


def test_node_context_uses_place_name_and_note(monkeypatch):
    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: REG)
    monkeypatch.setattr(rooms, "node_place", lambda nid, **k: ("kjokken", "på benken") if nid == "k" else ("", ""))
    assert cb._node_room("k") == "Kjøkken (på benken)"


def test_display_target_resolves_place_words(monkeypatch):
    monkeypatch.setattr(rooms, "get_register", lambda *a, **k: REG)
    monkeypatch.setattr(rooms, "node_place", lambda nid, **k: ("verksted", "") if nid == "tv1" else ("", ""))
    sent = []

    async def fake_nodes():
        return [{"id": "tv1", "type": "google_tv", "room": "workshop_tv"}, {"id": "tv2", "room": "stue"}]

    async def fake_send(nid, **kw):
        sent.append(nid)
        return {"ok": True}

    monkeypatch.setattr(ex, "get_display_nodes", fake_nodes)
    monkeypatch.setattr(ex, "send_display", fake_send)
    asyncio.run(ex.execute_tool("announce", {"action": "display", "text": "hei", "target": "verkstedet",
                                             "_user_id": "test"}))
    assert sent == ["tv1"]
