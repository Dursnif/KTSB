"""P144-s28 item 5 — an animal the blind look reports counts as supported only if Frigate had the same kind in the window.

Ep 115 (Halloween): the look said «horse», Frigate had a «dog» in the window, and the horse was not marked uncertain —
the s20 rule asked for *any* animal label. A word that says only «some animal» is supported by any Frigate animal. A word
code cannot place is uncertain (the safe side: uncertain only means it does not keep the gate open alone).
History on a DB copy: the rule changes ep 115 and 122 only, both with person = true, so no gate outcome changes.
"""

import asyncio
import json

import pytest

from kaare_core.frigate_user import checks, conclude, frames, settings, store

T0 = 1_793_300_000.0


@pytest.mark.parametrize("seen, labels, same", [
    ("horse", ["dog"], False),  # ep 115
    ("dog", ["dog"], True),
    ("Dog", ["dog"], True),
    ("hund", ["dog"], True),
    ("rådyr", ["deer"], True),
    ("hjort", ["deer"], True),
    ("Reh", ["deer"], True),
    ("a small dog", ["dog", "cat"], True),
    ("katt", ["dog"], False),
    ("unclear", ["dog"], True),  # «an animal is there», no species: any Frigate animal supports it
    ("dyr", ["bird"], True),
    ("unclear", [], False),
    ("dog", [], False),  # s20's rule stands: no Frigate animal → uncertain
    ("human", ["dog"], False),
    ("wombat", ["dog"], False),  # a word code cannot place
])
def test_same_animal_kind(seen, labels, same):
    assert checks.same_animal_kind(seen, labels) is same


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _run_look(monkeypatch, animal, frigate_label):
    monkeypatch.setattr(settings, "enabled", lambda: True)
    ep = store.Episode(episode_id=7, start_ts=T0, end_ts=T0 + 60, cameras=["steps"], labels=["person"], person_refs=[],
                       event_count=1, status="closed", conclusion=None, concluded_ts=None)
    ev = store.FrigateUserEvent(event_id="e1", camera="steps", label="person", start_ts=T0, end_ts=T0 + 10)
    assert store.record_event(ev)
    assert store.record_event(store.FrigateUserEvent(event_id="d1", camera="lawn", label=frigate_label, start_ts=T0,
                                                     end_ts=T0 + 10, episode_rule="none"))

    async def fetch(picked):
        return [frames.Frame(ts=T0 + 1, camera="steps", jpeg=b"\xff\xd8x")]

    async def llm(role, prompt, images=None, rid=""):
        if (":look" in rid):
            return {"text": json.dumps({"person": False, "animal": animal, "vehicle": False, "nothing": False})}
        return {"text": json.dumps({"persons": 0, "people": [], "animals": [], "scene": "trapp"})}

    monkeypatch.setattr(conclude, "FRAMES_ON", False)
    monkeypatch.setattr(conclude.frames, "fetch_frames", fetch)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude, "_camera_cards", dict)
    monkeypatch.setattr(conclude.store, "save_episode_frames", lambda *a: 1)
    scene, _ = asyncio.run(conclude._scene_step(ep, [ev], "nb"))
    return scene


def test_ep115_horse_with_a_frigate_dog_is_uncertain(db, monkeypatch):
    scene = _run_look(monkeypatch, "horse", "dog")
    assert scene["look"]["animal_uncertain"] is True
    assert scene["frigate_animals"] == ["dog"]


def test_the_same_kind_stays_supported(db, monkeypatch):
    scene = _run_look(monkeypatch, "dog", "dog")
    assert scene["look"]["animal_uncertain"] is False
    assert scene["status"] != "skipped"  # a real dog keeps the gate open, as before
