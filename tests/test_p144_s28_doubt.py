"""P144-s28 items 1–3 — the doubt flag (code, no model) and the 27B's look on doubt as one more sensor vote.

Ep 134/135/137: a white, burnt-out shape right at an IR camera's lens tracked as a «person»; nothing in the chain
disagreed. The flag (IR frame + burnt-out share inside Frigate's box) sends such an episode to the 27B; the answer is one
more vote that **code** decides on: at the lens / nothing with no other person → likely false detection, frames kept;
person → normal conclusion; unclear → normal conclusion with lowered certainty. The prompt carries no label, no name, no
claim. Only when idle, after the frame run. Never touches live state (tmp DB, fake LLM, synthetic images).
"""

import asyncio
import io
import json

import pytest
from PIL import Image, ImageDraw

from kaare_core.frigate_user import conclude, doubt, frames, settings, stm, store
from kaare_core.frigate_user import episode_state as es

T0 = 1_793_300_000.0
BOX = [0.7, 0.4, 0.15, 0.6]
FRAME = (854, 480)


def _jpeg(colour=False, blob=False, blob_colour=(255, 255, 255)) -> bytes:
    bg = (70, 40, 20) if colour else (60, 60, 60)
    im = Image.new("RGB", FRAME, bg)
    if colour:  # enough colour everywhere that the frame is not IR
        ImageDraw.Draw(im).rectangle([0, 0, 400, 200], fill=(20, 80, 30))
    if blob:
        d = ImageDraw.Draw(im)
        x, y, w, h = BOX
        d.rectangle([x * FRAME[0] + 10, y * FRAME[1] + 10, (x + w) * FRAME[0] - 10, (y + h) * FRAME[1]], fill=blob_colour)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


def _track(event_id="e1", camera="cam", label="person", box=None):
    return es.Track(event_id, camera, label, T0, T0 + 40, [(T0, "visible", box or BOX)])


def _frames(jpeg, n=4, camera="cam"):
    return [frames.Frame(ts=T0 + 2 + i * 3, camera=camera, jpeg=jpeg) for i in range(n)]


# ── the flag ───────────────────────────────────────────────────────────────


def test_burnt_blob_on_ir_is_flagged():
    d = doubt.assess(_frames(_jpeg(blob=True)), [_track()])
    assert d["flag"] is True
    assert d["signals"] == ["ir_burnt_box"]
    t = d["tracks"][0]
    assert t["event_id"] == "e1" and t["frames"] == 4 and t["doubtful"] == 4 and t["burnt"] > 0.5


def test_ir_person_without_burnt_box_is_not_flagged():
    d = doubt.assess(_frames(_jpeg()), [_track()])
    assert d["flag"] is False and d["tracks"][0]["doubtful"] == 0


def test_colour_frame_is_never_flagged_however_bright_the_box():
    d = doubt.assess(_frames(_jpeg(colour=True, blob=True)), [_track()])
    assert d["flag"] is False  # night lamps and sky: 37 real tracks in the measurement


def test_one_burnt_frame_of_four_is_not_a_majority():
    fs = [frames.Frame(T0 + 2, "cam", _jpeg(blob=True)), *[frames.Frame(T0 + 5 + i, "cam", _jpeg()) for i in range(3)]]
    assert doubt.assess(fs, [_track()])["flag"] is False


def test_frames_outside_the_track_or_on_another_camera_do_not_count():
    assert doubt.assess(_frames(_jpeg(blob=True), camera="other"), [_track()])["flag"] is False
    late = [frames.Frame(T0 + 500, "cam", _jpeg(blob=True))]
    assert doubt.assess(late, [_track()])["flag"] is False


def test_vehicle_tracks_are_not_asked():
    assert doubt.assess(_frames(_jpeg(blob=True)), [_track(label="car")])["flag"] is False


def test_animal_track_is_asked_too():
    assert doubt.assess(_frames(_jpeg(blob=True)), [_track(label="deer")])["flag"] is True


def test_unreadable_frame_and_no_box_never_raise_or_flag():
    assert doubt.assess(_frames(b"\xff\xd8x"), [_track()])["flag"] is False
    assert doubt.assess(_frames(_jpeg(blob=True)), [es.Track("e1", "cam", "person", T0, T0 + 40, [])])["flag"] is False
    assert doubt.assess([], [])["flag"] is False


def test_look_material_is_from_the_doubtful_camera_with_a_crop_of_the_largest_box():
    d = doubt.assess([*_frames(_jpeg(blob=True)), frames.Frame(T0 + 3, "other", _jpeg())], [_track()])
    images, crop_of = doubt.look_material([*_frames(_jpeg(blob=True)), frames.Frame(T0 + 3, "other", _jpeg())],
                                          [_track()], d)
    assert len(images) == 5  # 4 frames + the crop
    assert 1 <= crop_of <= 4
    crop = Image.open(io.BytesIO(images[-1]))
    assert crop.width < FRAME[0] and crop.height >= doubt.CROP_MIN_SIDE


def test_material_is_capped_at_four_frames():
    d = doubt.assess(_frames(_jpeg(blob=True), n=9), [_track()])
    images, _ = doubt.look_material(_frames(_jpeg(blob=True), n=9), [_track()], d)
    assert len(images) == doubt.LOOK_FRAMES + 1


# ── the answer ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, answer", [
    ('{"answer": "at_lens"}', "at_lens"),
    ('```json\n{"answer": "person"}\n```', "person"),
    ('Basert på bildene ser jeg noe.\n{"answer": "nothing"}', "nothing"),
    ('{"answer": "UNCLEAR"}', "unclear"),
    ('{"answer": "animal"}', "animal"),
])
def test_parse_look_answer(text, answer):
    assert doubt.parse_answer(text) == (answer, "")


@pytest.mark.parametrize("text", ["", "bare prosa", '{"answer": "maybe"}', '{"svar": "person"}', "[1]"])
def test_bad_answers_are_refused(text):
    answer, reason = doubt.parse_answer(text)
    assert answer is None and reason


# ── the prompt: no label, no name, no claim, no example ────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_prompt_is_closed_and_carries_no_claim(lang):
    p = conclude.build_doubt_prompt(5, 2, lang)
    for option in doubt.ANSWERS:
        assert f'"{option}"' in p
    low = p.lower()
    for leak in ("frigate", "edderkopp", "spider", "spinne", "regn", "rain", "støv", "dust"):
        assert leak not in low
    assert "5" in p and "2" in p
    assert '{"answer"' in p and "{{" not in p


# ── the vote, decided by code ──────────────────────────────────────────────


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    store.reset_for_tests()
    state = {"answer": '{"answer": "at_lens"}', "calls": [], "idle": True, "jpeg": _jpeg(blob=True), "away": False,
             "tracks": [_track()]}

    async def llm(role, prompt, images=None, rid=""):
        state["calls"].append({"role": role, "prompt": prompt, "images": images, "rid": rid})
        if (":doubt" in rid):
            return {"text": state["answer"], "meta": {}}
        return {"text": json.dumps({"summary": "Jeg så noen.", "unusual": {"value": False, "reason": "x"},
                                    "notify_suggestion": None, "raise_at_dev_meeting": None,
                                    "confidence": {"level": "high", "reasons": ["y"]}}), "meta": {}}

    async def tracks(events):
        return state["tracks"]

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude, "_episode_tracks", tracks)
    monkeypatch.setattr(conclude, "_camera_cards", dict)
    monkeypatch.setattr(conclude, "_idle", lambda c: state["idle"])
    monkeypatch.setattr(conclude, "_is_away", lambda: state["away"])
    monkeypatch.setattr(conclude, "camera_fact_lines", lambda *a: [])
    monkeypatch.setattr(conclude, "_untracked_names", set)
    monkeypatch.setattr(conclude.frames, "kept", lambda ep: [(i, f) for i, f in enumerate(_frames(state["jpeg"]))])
    monkeypatch.setattr(stm, "remember", lambda *a, **k: state.setdefault("stm", []).append(a))
    yield state
    store.reset_for_tests()


def _closed_episode(extra_events=()):
    ev = store.FrigateUserEvent(event_id="e1", camera="cam", label="person", start_ts=T0, end_ts=T0 + 30,
                                path_data=[[[0.5, 0.5], T0], [[0.7, 0.7], T0 + 20]])
    assert store.record_event(ev)
    for e in extra_events:
        assert store.record_event(e)
    store.close_episodes(T0 + 10_000, 120)
    ep = store.episode(store.event_by_id("e1").episode_id)
    scene = {"status": "done", "by": "frames", "persons": 1, "people": [], "scene": "x", "timeline": ["t"],
             "look": {"status": "done", "person": True, "animal": None, "vehicle": False, "nothing": False},
             "frames": [{"camera": "cam", "ts": T0 + 2 + i * 3, "file": i} for i in range(4)]}
    store.set_episode_scene(ep.episode_id, scene, "route")
    return store.episode(ep.episode_id)


def _run(ep):
    events = store.episode_events(ep.episode_id)
    return asyncio.run(conclude._conclude_from_scene(ep, events, "nb", None, T0 + 10_000))


def test_at_lens_with_no_other_person_is_a_likely_false_detection_by_code(world):
    ep = _closed_episode()
    assert _run(ep) is True
    got = store.episode(ep.episode_id)
    c = got.conclusion
    assert c["status"] == "done" and c["by"] == "code"
    assert c["false_detection"]["doubt_look"]["answer"] == "at_lens"
    assert c["summary"].startswith("Trolig feildeteksjon")
    assert "linsen" in c["summary"]
    assert c["notify_suggestion"] is None and c["unusual"]["value"] is False
    assert got.scene["frames"]  # frames kept
    assert got.scene["doubt"]["flag"] is True and got.scene["doubt"]["look"]["answer"] == "at_lens"
    assert [x["rid"].split(":")[1] for x in world["calls"]] == ["doubt"]  # one 27B image look, no 27B conclusion
    assert world["calls"][0]["role"] == conclude.ROLE
    assert len(world["calls"][0]["images"]) == 5
    assert "stm" not in world  # a likely false detection is not a memory line


def test_nothing_is_treated_like_at_lens(world):
    world["answer"] = '{"answer": "nothing"}'
    ep = _closed_episode()
    assert _run(ep) is True
    assert store.episode(ep.episode_id).conclusion["by"] == "code"


def test_person_answer_means_a_normal_conclusion(world):
    world["answer"] = '{"answer": "person"}'
    ep = _closed_episode()
    assert _run(ep) is True
    c = store.episode(ep.episode_id).conclusion
    assert c["by"] == "model" and not c.get("false_detection")
    assert c["confidence"]["level"] == "high"
    assert [x["rid"].split(":")[1] for x in world["calls"]] == ["doubt", "conclude"]


def test_animal_answer_means_a_normal_conclusion(world):
    world["answer"] = '{"answer": "animal"}'
    ep = _closed_episode()
    assert _run(ep) is True
    assert store.episode(ep.episode_id).conclusion["by"] == "model"


def test_unclear_lowers_the_certainty_in_code(world):
    world["answer"] = '{"answer": "unclear"}'
    ep = _closed_episode()
    assert _run(ep) is True
    c = store.episode(ep.episode_id).conclusion
    assert c["by"] == "model" and c["confidence"]["level"] == "low"
    assert any("uklart" in r.lower() for r in c["confidence"]["reasons"])
    # end-to-end on a copy: ep 134 got «unclear» and the 27B still wrote «a person» — the stored line says it, in code
    assert c["summary"].endswith(conclude._t("doubt_unclear_suffix", "nb"))


def test_a_person_on_another_camera_keeps_the_episode_real(world):
    other = store.FrigateUserEvent(event_id="e2", camera="porch", label="person", start_ts=T0 + 5, end_ts=T0 + 20,
                                   path_data=[[[0.2, 0.2], T0 + 5], [[0.6, 0.6], T0 + 15]])
    ep = _closed_episode([other])
    assert _run(ep) is True
    c = store.episode(ep.episode_id).conclusion
    assert c["by"] == "model" and not c.get("false_detection")


def test_a_clear_track_on_the_same_camera_keeps_the_episode_real(world):
    far = [0.1, 0.1, 0.05, 0.1]  # another track on the same camera, its box is not burnt out
    world["tracks"] = [_track(), _track("e3", box=far)]
    other = store.FrigateUserEvent(event_id="e3", camera="cam", label="person", start_ts=T0 + 1, end_ts=T0 + 20,
                                   path_data=[[[0.1, 0.1], T0 + 1], [[0.5, 0.5], T0 + 15]])
    ep = _closed_episode([other])
    assert _run(ep) is True
    assert store.episode(ep.episode_id).conclusion["by"] == "model"


def test_urgent_episode_is_never_closed_by_the_vote(world):
    world["away"] = True  # a person while the house is away: the same rule as the label gate
    ep = _closed_episode()
    assert _run(ep) is True
    c = store.episode(ep.episode_id).conclusion
    assert c["by"] == "model" and not c.get("false_detection")


def test_not_flagged_means_no_look_at_all(world):
    world["jpeg"] = _jpeg()
    ep = _closed_episode()
    assert _run(ep) is True
    got = store.episode(ep.episode_id)
    assert got.conclusion["by"] == "model"
    assert got.scene["doubt"]["flag"] is False
    assert [x["rid"].split(":")[1] for x in world["calls"]] == ["conclude"]


def test_flag_waits_while_a_user_talks_and_never_uses_an_attempt(world):
    ep = _closed_episode()
    world["idle"] = False
    assert _run(ep) is False
    assert world["calls"] == []  # never in front of a user's request
    got = store.episode(ep.episode_id)
    assert (got.conclusion or {}).get("status") != "done"
    assert int((got.conclusion or {}).get("attempts", 0)) == 0
    world["idle"] = True
    assert _run(got) is True
    assert store.episode(ep.episode_id).conclusion["by"] == "code"


def test_the_look_is_asked_once_and_kept(world):
    world["answer"] = '{"answer": "person"}'
    ep = _closed_episode()
    assert _run(ep) is True
    n = sum((":doubt" in x["rid"]) for x in world["calls"])
    assert _run(store.episode(ep.episode_id)) is True
    assert sum((":doubt" in x["rid"]) for x in world["calls"]) == n == 1


def test_bad_answer_is_stored_and_the_episode_is_concluded_normally(world):
    world["answer"] = "bare prosa"
    ep = _closed_episode()
    assert _run(ep) is True
    got = store.episode(ep.episode_id)
    assert got.conclusion["by"] == "model"
    assert got.scene["doubt"]["look"]["status"] == "failed"


def test_unreachable_27b_is_not_an_answer(world, monkeypatch):
    async def down(role, prompt, images=None, rid=""):
        return {"text": "", "unreachable": True}

    monkeypatch.setattr(conclude, "ask_llm_role", down)
    ep = _closed_episode()
    _run(ep)
    got = store.episode(ep.episode_id)
    assert "look" not in got.scene["doubt"]  # asked again next time
    assert got.conclusion.get("false_detection") is None


def test_reanalyse_is_not_held_back_by_the_idle_gate(world):
    ep = _closed_episode()
    world["idle"] = False
    events = store.episode_events(ep.episode_id)
    ok = asyncio.run(conclude._conclude_from_scene(ep, events, "nb", None, T0 + 10_000, gate_idle=False))
    assert ok is True and store.episode(ep.episode_id).conclusion["by"] == "code"
