"""P144-s20 item 4 — garden ornaments read as animals by the blind look (s18 + Userone: a swan figure/flower pot by the
steps, a fallen squirrel statue at the lawn edge).

Measured (s20 log, m4): since s16 the look named an animal in 8 of 34 episodes; in 7 of them Frigate had no animal label
anywhere in the episode window (the one with a Frigate «dog» label = a real dog, Userone confirmed ep 86). s18 B9: on empty
step frames the look said «dog» 13/36. Rule — generic, no per-house list, never in the 9B prompt: an animal that **only**
the blind look reports (no Frigate animal label in the episode window, any camera) is «uncertain» — stored on the look,
it does not keep the gate open on its own, and the 27B reads it marked as uncertain. A Frigate animal label in the window
(also a plain, unticked event) keeps today's behaviour.
"""

import asyncio
import json

import pytest

from kaare_core.frigate_user import conclude, frames, settings, store

T0 = 1_793_300_000.0


def _ep(labels=("person",)):
    return store.Episode(episode_id=7, start_ts=T0, end_ts=T0 + 60, cameras=["steps"], labels=list(labels),
                         person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)


def _ev(label="person", eid="e1", start=T0, camera="steps", **kw):
    return store.FrigateUserEvent(event_id=eid, camera=camera, label=label, start_ts=start, end_ts=start + 10, **kw)


ORNAMENT_DOG = {"status": "done", "person": False, "animal": "dog", "vehicle": False, "nothing": False,
                "animal_uncertain": True}


def test_uncertain_animal_does_not_keep_the_gate_open():
    fd = conclude.label_gate(_ep(), [_ev()], {"look": ORNAMENT_DOG}, away=False, lang="nb")
    assert fd is not None
    assert fd["look"]["animal"] == "dog" and fd["look"]["animal_uncertain"] is True


def test_animal_backed_by_frigate_keeps_the_gate_open():
    look = {**ORNAMENT_DOG, "animal_uncertain": False}
    assert conclude.label_gate(_ep(("person", "dog")), [_ev(), _ev("dog", "e2")], {"look": look}, away=False) is None


def test_old_looks_without_the_flag_keep_todays_behaviour():
    look = {k: v for k, v in ORNAMENT_DOG.items() if k != "animal_uncertain"}
    assert conclude.label_gate(_ep(), [_ev()], {"look": look}, away=False) is None


def test_a_person_in_the_look_still_keeps_it_open():
    assert conclude.label_gate(_ep(), [_ev()], {"look": {**ORNAMENT_DOG, "person": True}}, away=False) is None


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_false_detection_line_names_the_uncertain_animal(lang):
    fd = conclude.label_gate(_ep(), [_ev()], {"look": ORNAMENT_DOG}, away=False, lang=lang)
    text = conclude.false_detection_summary(fd, "dag", lang)
    assert "dog" in text or "hund" in text or "Hund" in text


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_27b_reads_the_animal_as_uncertain(lang):
    scene = {"status": "done", "look": {**ORNAMENT_DOG, "person": True}, "scene": "x", "persons": 1, "people": [],
             "animals": [{"kind": "dog", "count": 1}]}
    lines = "\n".join(conclude._scene_lines(scene, lang))
    marker = conclude._t("uncertain_animal", lang)
    assert lines.count(marker) == 2  # the look line and the scene's animal line


def test_27b_reads_a_frigate_backed_animal_plainly():
    scene = {"status": "done", "look": {**ORNAMENT_DOG, "person": True, "animal_uncertain": False}, "scene": "x",
             "persons": 1, "people": [], "animals": [{"kind": "dog", "count": 1}]}
    assert conclude._t("uncertain_animal", "nb") not in "\n".join(conclude._scene_lines(scene, "nb"))


# ── where the flag comes from: Frigate's animal labels in the episode window (store) ──


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    store.reset_for_tests()


def test_window_animal_labels_include_plain_events_on_any_camera(db):
    ep = _ep()
    assert store.record_event(_ev("dog", "d1", T0 + 5, camera="road", episode_rule="none"))  # unticked: no episode
    assert store.record_event(_ev("cat", "c1", T0 - 300, camera="lawn", episode_rule="none"))  # started before, overlaps?
    assert store.record_event(_ev("bird", "b1", T0 + 500, camera="lawn", episode_rule="none"))  # after the window
    labels = conclude._window_animal_labels(ep)
    assert "dog" in labels and "bird" not in labels
    assert "cat" not in labels  # ended (T0-290) before the episode began


def test_scene_step_marks_a_look_only_animal_uncertain(db, monkeypatch):
    monkeypatch.setattr(settings, "enabled", lambda: True)
    ep = _ep()
    ev = _ev()
    assert store.record_event(ev)

    async def fetch(picked):
        return [frames.Frame(ts=T0 + 1, camera="steps", jpeg=b"\xff\xd8x")]

    async def llm(role, prompt, images=None, rid=""):
        if (":look" in rid):
            return {"text": json.dumps({"person": False, "animal": "dog", "vehicle": False, "nothing": False})}
        return {"text": json.dumps({"persons": 0, "people": [], "animals": [], "scene": "trapp"})}

    monkeypatch.setattr(conclude.frames, "fetch_frames", fetch)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude, "_camera_cards", dict)
    monkeypatch.setattr(conclude.store, "save_episode_frames", lambda *a: 1)
    scene, _ = asyncio.run(conclude._scene_step(ep, [ev], "nb"))
    assert scene["look"]["animal_uncertain"] is True
    assert scene["status"] == "skipped" and scene["reason"] == "likely_false_detection"
