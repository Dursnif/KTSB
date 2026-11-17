"""P144-s20 item 2 — the scene decides where/what (Bug «27B takes position/activity from L3», Userone ep 93).

Measured (s20 log, m2): the conclusion prompt replayed on 8 real episodes, real 27B, 2 runs per variant. Ep 93 (Userone
leaned out of an open window): today's prompt, L3 text removed, a «where/what = scene» rule, and a neutral door/window
label alone all wrote «stood by the trailer» (0/14 window); only all three together wrote «leaning out of a window»
2/2, and kept the far-away person in ep 88 2/2. Built:
  - the 27B's event lines no longer carry L3's single-image `description`/`analysis` when the episode scene is done
    (L3's attribute fields stay — the claim check needs them); a failed/skipped scene keeps them (only source);
  - one rule line: where people were and what they did comes from the scene (all frames) and the route;
  - the scene's `at_door` field is shown as «at a door or window» — it is the 9B's answer to «what happens at the
    door», and in ep 93 it described a window on a camera with no front door.
The urgent prompt (one event, the 27B sees the image itself, no scene) keeps L3's text.
"""

import pytest

from kaare_core.frigate_user import conclude, store

T0 = 1_793_300_000.0


def _ep(scene):
    return store.Episode(episode_id=93, start_ts=T0, end_ts=T0 + 20, cameras=["vest"], labels=["person"],
                         person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None,
                         scene=scene, route="Kamerarekkefølge: vest.")


EV = store.FrigateUserEvent(event_id="e1", camera="vest", label="person", start_ts=T0, end_ts=T0 + 10,
                            attributes={"persons": 1, "beard": "full", "glasses": "yes"},
                            description="Mann i mørk genser.", analysis="Mannen står ved en trailer og peker ned.")
SCENE = {"status": "done", "scene": "driveway", "persons": 1, "people": [], "animals": [],
         "at_door": "window open, man leaning out", "look": {"status": "done", "person": True, "animal": None}}


def _prompt(scene, lang="nb"):
    return conclude.build_episode_prompt(_ep(scene), [EV], lang, away=False, cameras={}, hidden=set(), facts=[],
                                         topics=[])


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_l3_text_is_not_a_second_description_when_the_scene_is_done(lang):
    p = _prompt(SCENE, lang)
    assert "peker ned" not in p and "mørk genser" not in p
    assert '"glasses": "yes"' in p  # the attribute fields stay for the claim check


def test_failed_scene_keeps_the_l3_text():
    p = _prompt({"status": "failed", "reason": "no_frames"})
    assert "peker ned" in p and "mørk genser" in p


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_rule_where_what_comes_from_the_scene(lang):
    assert conclude._t("rule_scene", lang) in _prompt(SCENE, lang)


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_at_door_is_labelled_door_or_window(lang):
    lines = conclude._scene_lines(SCENE, lang)
    line = next(ln for ln in lines if "window open" in ln)
    assert line.startswith(conclude._t("scene_at_door", lang))
    assert {"nb": "vindu", "en": "window", "de": "Fenster"}[lang] in conclude._t("scene_at_door", lang)


def test_urgent_prompt_keeps_the_l3_text():
    p = conclude.build_urgent_prompt(EV, "label:smoke", "nb", away=False)
    assert "peker ned" in p
