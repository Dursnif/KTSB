"""P139 reopened #3 (D7: everything local in KTSB, other dependencies are fallbacks): whether a speaker «was playing»
before a spoken reply (N30 lead-in: longer silence when a stream gets replaced) comes from KTSB's own Cast first, HA
only as the fallback. Before this the bridge asked HA only, and only for `ha_media_player` nodes — a Cast node without
HA (a stranger's Nest, rule 24) never got the longer lead-in. No network: the status readers are fakes."""
import asyncio

from kaare_core.voice import lead_in

CAST_NODE = {"type": "chromecast", "host": "192.0.2.10"}
NEST_WITH_CAST = {"type": "ha_media_player", "host": "192.0.2.11", "entity_id": "media_player.example",
                  "channels": {"cast": {"enabled": True, "port": 8009}}}
NEST_HA_ONLY = {"type": "ha_media_player", "entity_id": "media_player.example"}


def _run(node, cast_state=None, cast_ok=True, ha_state=""):
    calls = []

    async def cast_status(host):
        calls.append(("cast", host))
        return {"ok": cast_ok, "after": {"state": cast_state} if cast_ok else {}}

    async def ha(entity):
        calls.append(("ha", entity))
        return ha_state

    return asyncio.run(lead_in.was_playing(node, cast_status, ha)), calls


def test_cast_node_without_ha_reads_its_own_cast():
    playing, calls = _run(CAST_NODE, cast_state="PLAYING")
    assert playing is True
    assert calls == [("cast", "192.0.2.10")]


def test_own_cast_goes_first_for_a_nest_that_also_is_in_ha():
    playing, calls = _run(NEST_WITH_CAST, cast_state="PAUSED", ha_state="idle")
    assert playing is True
    assert calls[0] == ("cast", "192.0.2.11")
    assert ("ha", "media_player.example") not in calls


def test_idle_cast_is_not_playing():
    playing, _ = _run(CAST_NODE, cast_state="IDLE")
    assert playing is False


def test_unreachable_cast_falls_back_to_ha():
    playing, calls = _run(NEST_WITH_CAST, cast_ok=False, ha_state="playing")
    assert playing is True
    assert calls == [("cast", "192.0.2.11"), ("ha", "media_player.example")]


def test_ha_only_node_still_uses_ha():
    playing, calls = _run(NEST_HA_ONLY, ha_state="playing")
    assert playing is True
    assert calls == [("ha", "media_player.example")]


def _run_source(node, cast_state=None, cast_ok=True, ha_state=""):
    async def cast_status(host):
        return {"ok": cast_ok, "after": {"state": cast_state} if cast_ok else {}}

    async def ha(entity):
        return ha_state

    return asyncio.run(lead_in.playing_source(node, cast_status, ha))


def test_the_answer_names_its_source():
    # kaare-d7 2026-11-16: the log line must prove where «was playing» came from (Cast, HA fallback or nobody)
    assert _run_source(NEST_WITH_CAST, cast_state="PLAYING") == (True, "cast")
    assert _run_source(NEST_WITH_CAST, cast_ok=False, ha_state="playing") == (True, "ha")
    assert _run_source(NEST_HA_ONLY, ha_state="idle") == (False, "ha")
    assert _run_source({"type": "esp32"}) == (False, "none")


def test_node_with_neither_asks_nobody():
    playing, calls = _run({"type": "esp32"})
    assert playing is False
    assert calls == []
