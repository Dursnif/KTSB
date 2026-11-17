"""P144 / run-queue J8 — Kåre's camera tool.

R141: an event's score comes from where Frigate puts it (`data.top_score` / `data.score`), never «0 %» for a scored event;
R142: the user's word («søppeldunk», «trash», «dunk», «Mülltonne») finds the label the cameras track; an unknown word lists
      the known labels instead of «nothing found»;
(d)  action='stationary' gives the stationary gate's facts: what stands where, unchanged since when / changed when;
(e)  the tool text tells Kåre to fetch a frame before describing how something looks now.
Frigate and frigate.db are fakes / tmp files."""

import asyncio

import pytest

from kaare_core.frigate_user import labels, things
from kaare_core.frigate_user import store as fstore
from kaare_core.tools import definitions, executor_camera
from kaare_core.tools.i18n import t

KNOWN = ["bicycle", "car", "cat", "deer", "dog", "license_plate", "person", "postnord", "waste_bin"]


# ── R141 ──

def test_score_is_read_from_frigates_data():
    assert executor_camera.event_score({"top_score": None, "data": {"top_score": 0.72, "score": 0.56}}) == 0.72
    assert executor_camera.event_score({"data": {"score": 0.83}}) == 0.83
    assert executor_camera.event_score({"top_score": 0.9}) == 0.9
    assert executor_camera.event_score({}) == 0.0


def test_frigate_action_shows_the_real_score(monkeypatch):
    async def fetch(camera=None, label=None, limit=10):
        return [{"id": "e1", "camera": "cam", "label": "waste_bin", "start_time": 1793939081.5, "top_score": None,
                 "data": {"top_score": 0.83, "score": 0.8}}]

    monkeypatch.setattr(executor_camera, "fetch_events", fetch)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(labels, "known_labels", lambda: KNOWN)
    out = asyncio.run(executor_camera._kamera({"action": "frigate", "count": 5}))
    assert "(83%)" in out and "(0%)" not in out


# ── R142 ──

@pytest.mark.parametrize("word", ["søppeldunk", "trash", "dunk", "Mülltonne", "waste bin", "waste_bin", "WASTE_BIN"])
def test_user_words_find_the_label(word):
    assert labels.resolve(word, KNOWN) == ["waste_bin"]


def test_other_words():
    assert labels.resolve("hund", KNOWN) == ["dog"]
    assert labels.resolve("bilen", KNOWN) == ["car"]
    assert labels.resolve("postnord", KNOWN) == ["postnord"]  # a label with no names matches by its key
    assert labels.resolve("rakett", KNOWN) == [] and labels.resolve("", KNOWN) == []
    assert labels.resolve("pakke", KNOWN) == []  # never a label the cameras do not track


def test_frigate_action_maps_the_word_and_lists_labels_when_unknown(monkeypatch):
    asked = []

    async def fetch(camera=None, label=None, limit=10):
        asked.append(label)
        return []

    monkeypatch.setattr(executor_camera, "fetch_events", fetch)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(labels, "known_labels", lambda: KNOWN)
    asyncio.run(executor_camera._kamera({"action": "frigate", "label": "søppeldunk"}))
    assert asked == ["waste_bin"]
    out = asyncio.run(executor_camera._kamera({"action": "frigate", "label": "rakett"}, "nb"))
    assert "waste_bin" in out and "car" in out and asked == ["waste_bin"]  # no fetch for an unknown word


# ── (d) stationary facts ──

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(things.query, "_camera_cards", lambda: {"cam_e": {"display_name": "Nedside øst"}})
    fstore.reset_for_tests()
    yield
    fstore.reset_for_tests()


def _ev(eid, ts, verdict, key="cam_e|waste_bin|0,4", label="waste_bin"):
    assert fstore.record_event(fstore.FrigateUserEvent(
        event_id=eid, camera="cam_e", label=label, start_ts=ts, end_ts=ts + 10,
        check={"gate": {"verdict": verdict, "key": key, "thing_id": None}}))


def test_stationary_facts_say_since_when(db):
    now = 1_800_000_000.0
    _ev("a", now - 5000, "changed")
    _ev("b", now - 4000, "unchanged")
    _ev("c", now - 1000, "unchanged")
    _ev("d", now - 900, "changed", key="cam_e|car|5,5", label="car")
    rows = {r["key"]: r for r in things.things(now)}
    assert rows["cam_e|waste_bin|0,4"]["verdict"] == "unchanged" and rows["cam_e|waste_bin|0,4"]["since_ts"] == now - 4000
    assert rows["cam_e|car|5,5"]["since_ts"] is None
    text = things.text("nb", now)
    assert "søppeldunk" in text and "Nedside øst" in text and "bil" in text


def test_stationary_facts_empty(db):
    assert things.text("en", 1_800_000_000.0) == t("ft_none", "en", hours=things.WINDOW_H)


def test_camera_tool_offers_stationary_and_the_frame_rule():
    tool = definitions._kamera_tool("nb")["function"]
    assert "stationary" in tool["parameters"]["properties"]["action"]["enum"]
    for lang in ("nb", "en", "de"):
        desc = definitions._kamera_tool(lang)["function"]["description"]
        assert "action='stationary'" in desc and "action='snapshot'" in desc
        assert t("tool_kamera_look_now", lang) in desc
