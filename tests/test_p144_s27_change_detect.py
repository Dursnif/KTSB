"""P144-s27 item 3 — the before/after change detector (code only, no model).

Walk test ep 102/103: a jacket left on the car roof was missed by every model. Code compares two frames before the episode
with two after it, on structure, not brightness. Synthetic images; the real pairs are in the s27 log.
"""

import io
import random

from PIL import Image, ImageDraw, ImageFilter

from kaare_core.frigate_user import change_detect as cd

SIZE = (854, 480)


def _scene(seed=1, grey=False, bright=1.0, thing=None, burnt=None):
    """A textured scene (same texture for the same seed); `thing` = a rel box with a textured object in it."""
    rnd = random.Random(seed)
    im = Image.new("RGB", SIZE, (70, 75, 60) if not grey else (70, 70, 70))
    d = ImageDraw.Draw(im)
    for _ in range(500):
        x, y = rnd.randint(0, SIZE[0]), rnd.randint(0, SIZE[1])
        v = rnd.randint(20, 200)
        d.rectangle([x, y, x + rnd.randint(5, 40), y + rnd.randint(5, 40)],
                    fill=(v, v, v) if grey else (v, max(0, v - 20), min(255, v + 10)))
    im = im.filter(ImageFilter.GaussianBlur(1))
    d = ImageDraw.Draw(im)
    if thing:
        x0, y0, x1, y1 = thing[0] * SIZE[0], thing[1] * SIZE[1], thing[2] * SIZE[0], thing[3] * SIZE[1]
        d.rectangle([x0, y0, x1, y1], fill=(235, 235, 225))
        for i in range(6):
            yy = y0 + (y1 - y0) * i / 6
            d.line([x0, yy, x1, yy + 6], fill=(20, 20, 20), width=3)
    if burnt:
        d.rectangle([burnt[0] * SIZE[0], burnt[1] * SIZE[1], burnt[2] * SIZE[0], burnt[3] * SIZE[1]], fill=(255, 255, 255))
    if bright != 1.0:
        im = im.point(lambda p: min(255, int(p * bright)))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


THING = (0.30, 0.20, 0.38, 0.34)
NEAR = [[0.25, 0.15, 0.2, 0.3]]  # Frigate box (x, y, w, h): someone was there


def test_a_left_object_is_found_where_someone_was():
    r = cd.detect([_scene(), _scene()], [_scene(thing=THING), _scene(thing=THING)], near=NEAR)
    assert r["guard"] is None
    assert len(r["regions"]) == 1
    x0, y0, x1, y1 = r["regions"][0]["box"]
    assert x0 < THING[2] and x1 > THING[0] and y0 < THING[3] and y1 > THING[1]


def test_a_removed_object_is_found_too():
    r = cd.detect([_scene(thing=THING), _scene(thing=THING)], [_scene(), _scene()], near=NEAR)
    assert len(r["regions"]) == 1


def test_identical_scene_gives_nothing():
    assert cd.detect([_scene(), _scene()], [_scene(), _scene()], near=NEAR)["regions"] == []


def test_brightness_change_alone_gives_nothing():
    """Structure, not intensity: the prototype's 15 false regions came from light."""
    r = cd.detect([_scene(), _scene()], [_scene(bright=1.25), _scene(bright=1.25)], near=[[0, 0, 1, 1]])
    assert r["regions"] == []


def test_something_only_in_one_after_frame_is_not_kept():
    """Passing headlights / a person walking through: gone in the second after frame."""
    r = cd.detect([_scene(), _scene()], [_scene(thing=THING), _scene()], near=NEAR)
    assert r["regions"] == []


def test_something_in_one_reference_only_is_not_kept():
    """Ep 102: he stands in the −5 s frame before Frigate's track starts. Not a change compared with the other reference."""
    r = cd.detect([_scene(), _scene(thing=THING)], [_scene(), _scene()], near=NEAR)
    assert r["regions"] == []


def test_far_from_any_track_is_dropped():
    """Something left has someone next to it (dark bushes, sky: nobody was there)."""
    r = cd.detect([_scene(), _scene()], [_scene(thing=THING), _scene(thing=THING)], near=[[0.8, 0.7, 0.1, 0.2]])
    assert r["regions"] == []


def test_no_track_at_all_gives_nothing():
    r = cd.detect([_scene(), _scene()], [_scene(thing=THING), _scene(thing=THING)], near=[])
    assert r["regions"] == []


def test_frigate_motion_mask_is_respected():
    mask = "0.2,0.1,0.5,0.1,0.5,0.5,0.2,0.5"
    r = cd.detect([_scene(), _scene()], [_scene(thing=THING), _scene(thing=THING)], near=NEAR, masks=[mask])
    assert r["regions"] == []


def test_a_box_still_there_after_is_skipped():
    """A tracked object still in view after the end is Frigate's, not a change."""
    r = cd.detect([_scene(), _scene()], [_scene(thing=THING), _scene(thing=THING)], near=NEAR,
                  skip=[[0.28, 0.18, 0.12, 0.18]])
    assert r["regions"] == []


def test_whole_scene_change_is_a_light_guard_not_regions():
    """Colour ↔ IR at dusk: a709 10-30 17:19, 270 cells at once."""
    r = cd.detect([_scene(1), _scene(1)], [_scene(2), _scene(2)], near=[[0, 0, 1, 1]])
    assert r["guard"] == "light"
    assert r["regions"] == []


def test_colour_to_ir_is_a_light_guard():
    r = cd.detect([_scene(), _scene()], [_scene(grey=True), _scene(grey=True)], near=[[0, 0, 1, 1]])
    assert r["guard"] == "light"


def test_one_large_thing_is_not_a_light_change():
    """Ep 72: a loaded trailer drove off — 15 % of the frame in one connected piece."""
    big = (0.2, 0.2, 0.7, 0.7)
    r = cd.detect([_scene(thing=big), _scene(thing=big)], [_scene(), _scene()], near=[[0, 0, 1, 1]])
    assert r["guard"] is None
    assert len(r["regions"]) >= 1


def test_ir_with_burnt_blob_at_the_lens_is_guarded():
    """Ep 134/139: spider and blobs at an IR lens. Measured: a709 IR without blobs ≤ 2 % burnt."""
    blob = (0.0, 0.0, 0.4, 0.3)
    r = cd.detect([_scene(grey=True), _scene(grey=True)],
                  [_scene(grey=True, thing=THING, burnt=blob), _scene(grey=True, thing=THING, burnt=blob)], near=NEAR)
    assert r["guard"] == "ir_lens"
    assert r["regions"] == []


def test_colour_frame_with_a_lamp_is_not_guarded():
    blob = (0.7, 0.0, 1.0, 0.2)
    r = cd.detect([_scene(burnt=blob), _scene(burnt=blob)], [_scene(burnt=blob, thing=THING), _scene(burnt=blob, thing=THING)],
                  near=NEAR)
    assert r["guard"] is None
    assert len(r["regions"]) == 1


def test_unreadable_frame_gives_no_regions_and_no_crash():
    r = cd.detect([b"nope", _scene()], [_scene(), _scene()], near=NEAR)
    assert r == {"regions": [], "guard": "unreadable"}


def test_regions_are_capped_and_largest_first():
    bits = [(0.05, 0.05, 0.13, 0.2), (0.3, 0.05, 0.45, 0.3), (0.55, 0.05, 0.63, 0.2), (0.75, 0.05, 0.83, 0.2),
            (0.05, 0.6, 0.13, 0.75)]

    def many():
        im = Image.open(io.BytesIO(_scene()))
        d = ImageDraw.Draw(im)
        for b in bits:
            d.rectangle([b[0] * SIZE[0], b[1] * SIZE[1], b[2] * SIZE[0], b[3] * SIZE[1]], fill=(240, 240, 230))
            d.line([b[0] * SIZE[0], b[1] * SIZE[1], b[2] * SIZE[0], b[3] * SIZE[1]], fill=(0, 0, 0), width=4)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=92)
        return out.getvalue()

    r = cd.detect([_scene(), _scene()], [many(), many()], near=[[0, 0, 1, 1]])
    assert len(r["regions"]) == cd.MAX_REGIONS
    cells = [x["cells"] for x in r["regions"]]
    assert cells == sorted(cells, reverse=True)


def test_crop_is_neutral_and_bigger_than_the_region():
    jpeg = _scene(thing=THING)
    out = cd.crop(jpeg, THING)
    im = Image.open(io.BytesIO(out))
    assert im.width >= cd.CROP_MIN_SIDE or im.height >= cd.CROP_MIN_SIDE
    assert cd.crop(b"nope", THING) is None


def test_motion_masks_from_frigate_config_shapes():
    """0.18: {name: {coordinates, enabled}}; older: a string or a list of strings."""
    new = {"motion": {"mask": {"a": {"coordinates": "0,0,1,0,1,1", "enabled": True},
                               "b": {"coordinates": "0,0,1,1,0,1", "enabled": False}}}}
    assert cd.masks_from_camera(new) == ["0,0,1,0,1,1"]
    assert cd.masks_from_camera({"motion": {"mask": "0,0,1,0,1,1"}}) == ["0,0,1,0,1,1"]
    assert cd.masks_from_camera({"motion": {"mask": ["0,0,1,0,1,1", "0,0,1,1,0,1"]}}) == ["0,0,1,0,1,1", "0,0,1,1,0,1"]
    assert cd.masks_from_camera({}) == []
    assert cd.masks_from_camera({"motion": {"mask": {"a": {"coordinates": "garbage"}}}}) == []
