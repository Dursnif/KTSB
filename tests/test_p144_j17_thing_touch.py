"""J17 (from P144-s31 B1): a still thing a person was at — «handled» (its outline changed while the person was there and
was as before afterwards) or «moved» — with the time span, as code facts for the 27B (never the 9B).

Real truth, measured on Frigate's recording frames (run-queue J17 log): ep 218 (night, the household car's boot opened and
closed — handled), ep 266 (dawn, the bin pulled to the road — moved). Here synthetic frames in the same shape (s27's
textured scene), timings from those episodes. No real HTTP: Frigate is a fake."""
import asyncio
import io
import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

from kaare_core.frigate_user import conclude, thing_touch
from kaare_core.frigate_user import store as fstore

T0 = 1_800_000_000.0
SIZE = (854, 480)
CAR = [0.2, 0.1, 0.3, 0.25]  # x, y, w, h — like ep 218's car box on nedside vest


def _jpeg(seed=1, patch=None, patch_seed=7, grey=False, bright=1.0, burnt=None):
    """A textured scene; `patch` = a relative x, y, w, h box repainted with other texture (the boot lid open, the bin gone)."""
    rnd = random.Random(seed)
    im = Image.new("RGB", SIZE, (70, 70, 70) if grey else (70, 75, 60))
    d = ImageDraw.Draw(im)
    for _ in range(600):
        x, y, v = rnd.randint(0, SIZE[0]), rnd.randint(0, SIZE[1]), rnd.randint(20, 200)
        d.rectangle([x, y, x + rnd.randint(5, 40), y + rnd.randint(5, 40)],
                    fill=(v, v, v) if grey else (v, max(0, v - 20), min(255, v + 10)))
    if patch:
        r2 = random.Random(patch_seed)
        x0, y0 = patch[0] * SIZE[0], patch[1] * SIZE[1]
        x1, y1 = x0 + patch[2] * SIZE[0], y0 + patch[3] * SIZE[1]
        d.rectangle([x0, y0, x1, y1], fill=(120, 120, 130))
        for _ in range(200):
            x, y, v = r2.uniform(x0, x1), r2.uniform(y0, y1), r2.randint(0, 255)
            d.rectangle([x, y, x + 8, y + 8], fill=(v, v, v))
    if burnt:
        d.rectangle([burnt[0] * SIZE[0], burnt[1] * SIZE[1], burnt[2] * SIZE[0], burnt[3] * SIZE[1]], fill=(255, 255, 255))
    im = im.filter(ImageFilter.GaussianBlur(1))
    if bright != 1.0:
        im = im.point(lambda p: min(255, int(p * bright)))
    if grey:
        im = im.convert("L").convert("RGB")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


SCENE = _jpeg()
OPEN = _jpeg(patch=[0.25, 0.1, 0.2, 0.15])  # part of the car box looks different (lid up, someone in front)
GONE = _jpeg(patch=CAR)  # the whole box is something else now


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    """Rule 4: never the house's frigate.db."""
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    yield
    fstore.reset_for_tests()


def _during(n, changed_from=0, changed_to=None, frame=OPEN):
    changed_to = n if changed_to is None else changed_to
    return [(T0 + i, frame if changed_from <= i < changed_to else SCENE) for i in range(n)]


# ── the judgement ───────────────────────────────────────────────────────────────────────────────────────────────

def test_ep218_shape_handled():
    v = thing_touch.judge([SCENE, SCENE], _during(14, 2, 13), [SCENE, SCENE], CAR)
    assert v["outcome"] == "handled" and v["changed_s"] == 11 and v["first_changed"] == T0 + 2


def test_ep266_shape_moved():
    v = thing_touch.judge([SCENE, SCENE], _during(20, 2, 20, GONE), [GONE, GONE], CAR)
    assert v["outcome"] == "moved" and v["first_changed"] == T0 + 2


def test_person_at_it_but_nothing_changed_is_near():
    v = thing_touch.judge([SCENE, SCENE], _during(10, 0, 1), [SCENE, SCENE], CAR)  # one odd second is not «handled»
    assert v["outcome"] == "near"


def test_light_change_between_the_references_says_nothing():
    v = thing_touch.judge([SCENE, _jpeg(seed=3)], _during(5), [SCENE, SCENE], CAR)
    assert v["outcome"] == "unknown" and v["why"] == "light"


def test_ir_blobs_and_missing_frames_say_nothing():
    blob = _jpeg(grey=True, burnt=[0.0, 0.0, 0.4, 0.4])
    assert thing_touch.judge([blob, blob], _during(5), [blob, blob], CAR)["why"] == "ir"
    assert thing_touch.judge([SCENE, None], _during(5), [SCENE, SCENE], CAR)["why"] == "no_frames"
    assert thing_touch.judge([SCENE, SCENE], _during(5), [SCENE, SCENE], [0.5, 0.5, 0.01, 0.01])["why"] == "small"


# ── which things, which touch ───────────────────────────────────────────────────────────────────────────────────

def _thing(eid, label, ts, key, boxes=None, stationary=True, drives=False, end=None):
    return fstore.FrigateUserEvent(event_id=eid, camera="vest", label=label, start_ts=ts, end_ts=end, boxes=boxes or [],
                                   stationary=stationary, drives=drives,
                                   check={"gate": {"verdict": "unchanged", "key": key, "thing_id": None}})


def test_things_are_fresh_still_non_living_with_a_gate_key():
    evs = [_thing("car1", "car", T0 - 900, "vest|car|3,2", boxes=[[T0 - 900, *CAR, 1.5, 9, 1]]),
           _thing("old", "waste_bin", T0 - 3 * 3600, "vest|waste_bin|5,1"),  # last seen 3 h ago → stale
           _thing("drive", "car", T0 - 600, "vest|car|8,8", stationary=False, drives=True),  # a car driving through
           fstore.FrigateUserEvent(event_id="p", camera="vest", label="person", start_ts=T0 - 60,
                                   check={"gate": {"key": "vest|person|1,1"}})]
    got = thing_touch.things(evs, "vest", since=T0 - thing_touch.FRESH_S)
    assert [(t["event_id"], t["box"]) for t in got] == [("car1", CAR)]


def test_touch_from_j19_boxes_else_the_path():
    by_box = fstore.FrigateUserEvent(event_id="a", camera="vest", label="person", start_ts=T0,
                                     boxes=[[T0 + 1, 0.9, 0.5, 0.05, 0.2, 0.3, 0, 0], [T0 + 5, 0.45, 0.2, 0.05, 0.2, 0.3, 0, 0],
                                            [T0 + 18, 0.44, 0.2, 0.05, 0.2, 0.3, 4, 0]])
    assert thing_touch.touch_span([by_box], CAR) == (T0 + 5, T0 + 18)
    by_path = fstore.FrigateUserEvent(event_id="b", camera="vest", label="person", start_ts=T0,
                                      path=[[0.9, 0.9, T0], [0.3, 0.3, T0 + 3], [0.31, 0.3, T0 + 6]])
    assert thing_touch.touch_span([by_path], CAR) == (T0 + 3, T0 + 6)
    assert thing_touch.touch_span([by_path], [0.8, 0.0, 0.1, 0.1]) is None


def test_for_episode_end_to_end_with_a_fake_frigate(monkeypatch):
    """ep 218's shape: a person at the car 13 s, the box differs 2–12 s, as before after."""
    gets = []

    async def frame(camera, ts, height=480):
        gets.append(ts)
        return OPEN if T0 + 2 <= ts < T0 + 12 else SCENE

    monkeypatch.setattr(thing_touch.frigate_adapter, "fetch_recording_snapshot", frame)
    ep = fstore.Episode(episode_id=218, start_ts=T0 - 5, end_ts=T0 + 20, cameras=["vest"], labels=["person"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    person = fstore.FrigateUserEvent(event_id="p", camera="vest", label="person", start_ts=T0 - 5, end_ts=T0 + 20,
                                     boxes=[[T0 + i, 0.3, 0.15, 0.06, 0.2, 0.3, 0, 0] for i in range(14)])
    car = _thing("car1", "car", T0 - 900, "vest|car|3,2", boxes=[[T0 - 900, *CAR, 1.5, 9, 1]])
    out = asyncio.run(thing_touch.for_episode(ep, [person], [car]))
    assert len(out) == 1
    t = out[0]
    assert (t["label"], t["outcome"], t["near_s"], t["changed_s"]) == ("car", "handled", 13.0, 10)
    assert len(gets) == 2 + 14 + 2  # two references, one per second, two after — nothing more


def test_for_episode_never_raises(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("frigate down")

    monkeypatch.setattr(thing_touch.frigate_adapter, "fetch_recording_snapshot", boom)
    ep = fstore.Episode(1, T0, T0 + 10, ["vest"], ["person"], [], 1, "closed", None, None)
    person = fstore.FrigateUserEvent(event_id="p", camera="vest", label="person", start_ts=T0,
                                     boxes=[[T0 + 1, 0.3, 0.15, 0.06, 0.2, 0.3, 0, 0]])
    car = _thing("car1", "car", T0 - 60, "vest|car|3,2", boxes=[[T0 - 60, *CAR, 1.5, 9, 1]])
    out = asyncio.run(thing_touch.for_episode(ep, [person], [car]))
    assert out[0]["outcome"] == "unknown"


# ── the 27B's lines ─────────────────────────────────────────────────────────────────────────────────────────────

def _fact(outcome, **kw):
    return {"camera": "vest", "label": "car", "key": "vest|car|3,2", "start": T0, "end": T0 + 13, "near_s": 13.0,
            "outcome": outcome, "changed_s": 10, "first_changed": T0 + 2, **kw}


def test_lines_for_the_27b_in_three_languages():
    nb = conclude._touch_lines([_fact("handled")], {}, "nb")[0]
    assert "bil" in nb and "13 s" in nb and "var som før" in nb
    assert "moved or taken away" in conclude._touch_lines([_fact("moved", label="waste_bin")], {}, "en")[0]
    assert "keine Veränderung" in conclude._touch_lines([_fact("near")], {}, "de")[0]
    assert conclude._touch_lines([_fact("unknown")], {}, "nb") == []  # unknown says nothing


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_every_text_has_all_languages(lang):
    for key in ("touch_handled", "touch_moved", "touch_near", "h_touched"):
        assert conclude._TEXT[key][lang]


# ── wiring ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_episode_prompt_carries_the_touch_facts():
    ep = fstore.Episode(218, T0, T0 + 20, ["vest"], ["person"], [], 1, "closed", None, None,
                        scene={"status": "done", "by": "frames", "timeline": [], "people": [],
                               "changes": {"regions": [], "guards": {}, "touched": [_fact("handled")]}})
    p = conclude.build_episode_prompt(ep, [], "nb", away=False)
    assert conclude._t("h_touched", "nb") in p and "var som før" in p


def test_changes_step_runs_the_touch_step(monkeypatch):
    async def regions(*a, **kw):
        return {"regions": [], "guards": {}, "crops": [], "after_frames": []}

    async def touched(ep, events):
        return [_fact("handled")]

    monkeypatch.setattr(conclude.frames, "change_regions", regions)
    monkeypatch.setattr(conclude, "_touch_step", touched)
    ep = fstore.Episode(1, T0, T0 + 10, ["vest"], ["person"], [], 1, "closed", None, None)
    found = asyncio.run(conclude._changes_step(ep, [], []))
    assert found["touched"][0]["outcome"] == "handled"


def test_a_long_touch_is_measured_on_at_most_max_seconds_frames():
    ts = thing_touch.seconds(T0, T0 + 600)  # someone stands at the car for 10 min
    assert len(ts) == thing_touch.MAX_SECONDS and ts[0] == T0 and ts[-1] == T0 + 600
