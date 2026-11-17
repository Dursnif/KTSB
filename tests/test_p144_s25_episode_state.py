"""P144-s25 — the frame-by-frame episode memory held by code (`kaare_core/frigate_user/episode_state.py`).

Frigate facts per frame (presence from the timeline's last sighting, not the lingering event end; best first frame;
skip without movement; persons at once on one camera), the state (full description → changes → absence, correction,
identity merge by code), the code-written prompt text and timeline, and the language guess. Pure code, no I/O.
Numbers mirror the s25 measurement episodes (ep 102 / 108 timelines, relative boxes).
"""

from kaare_core.frigate_user import episode_state as es

T0 = 1_793_300_000.0


def _tl(*rows):
    return [{"timestamp": T0 + ts, "class_type": cls, "data": {"box": box}} for ts, cls, box in rows]


def _track(start, end, rows, camera="cam", label="person", eid="e"):
    return es.track_from(eid, camera, label, T0 + start, T0 + end, _tl(*rows))


# ── Frigate facts ──


def test_presence_ends_at_the_last_sighting_not_the_lingering_event_end():
    # ep 102: track 0 → 70 s, timeline «gone» at 62.2 s; frame 9 at 69 s is empty in the image
    k = _track(0, 70, [(0, "visible", [0.4, 0.1, 0.04, 0.2]), (62.2, "gone", [0.2, 0.1, 0.04, 0.2])])
    assert es.present([k], "cam", T0 + 30)
    assert not es.present([k], "cam", T0 + 69)
    assert not es.present([k], "cam", T0 + 62.9)  # ep 108 #9 lay 0.68 s after «gone»


def test_without_a_timeline_the_linger_is_taken_off_the_end():
    k = es.track_from("e", "cam", "person", T0, T0 + 20, None)
    assert es.present([k], "cam", T0 + 10)
    assert not es.present([k], "cam", T0 + 19)


def test_a_single_instant_event_still_covers_its_moment():
    k = _track(30, 30, [])
    assert es.present([k], "cam", T0 + 30)


def test_presence_is_per_camera_and_label():
    k = _track(0, 10, [(9, "gone", None)], camera="a")
    assert es.present([k], "a", T0 + 5) and not es.present([k], "b", T0 + 5)
    assert not es.present([k], "a", T0 + 5, label="dog")


def test_best_first_frame_is_the_largest_person_box():
    near = _track(0, 20, [(0, "visible", [0.5, 0.3, 0.5, 0.7]), (19, "gone", None)], camera="door")
    far = _track(0, 20, [(0, "visible", [0.1, 0.5, 0.05, 0.1]), (19, "gone", None)], camera="yard")
    frames = [("yard", T0 + 1), ("door", T0 + 2), ("yard", T0 + 3)]
    assert es.best_first(frames, [near, far]) == 1


def test_best_first_frame_without_any_track_is_the_first():
    assert es.best_first([("a", T0), ("a", T0 + 5)], []) == 0


def test_no_movement_since_the_previous_frame_means_skip():
    k = _track(0, 100, [(0, "visible", [0.4, 0.5, 0.1, 0.3]), (10, "stationary", [0.4, 0.5, 0.1, 0.3]),
                        (60, "active", [0.4, 0.5, 0.1, 0.3]), (90, "gone", None)])
    assert not es.moved_between([k], "cam", T0 + 20, T0 + 40)  # stationary all along → skip
    assert es.moved_between([k], "cam", T0 + 40, T0 + 70)  # became active in between
    assert es.moved_between([k], "cam", T0 + 5, T0 + 20)  # still moving at the previous frame
    assert es.moved_between([k], "cam", T0 + 80, T0 + 95)  # the person left


def test_persons_at_once_counts_one_camera_at_a_time():
    a = _track(0, 30, [(29, "gone", None)], camera="door", eid="a")
    b = _track(5, 30, [(29, "gone", None)], camera="yard", eid="b")  # same person, other camera, same time
    assert es.max_at_once([a, b]) == 1
    c = _track(10, 20, [(19, "gone", None)], camera="door", eid="c")
    assert es.max_at_once([a, b, c]) == 2


# ── the state ──


FIRST = {"persons": [{"age": "adult", "gender": "woman", "clothing": "rødbrun hettegenser", "place": "ved døra",
                      "posture": "standing", "carried": ""}], "doors": [{"door": "inngangsdør", "state": "open"}],
         "things": []}


def _state():
    st = es.EpisodeState()
    st.apply_full(FIRST, "door", T0, 1)
    return st


def test_full_description_becomes_the_state():
    st = _state()
    assert list(st.persons) == ["P1"] and st.persons["P1"]["clothing"] == "rødbrun hettegenser"
    assert st.present_ids("door") == ["P1"] and st.cam("door")["doors"] == {"inngangsdør": "open"}


def test_changes_are_applied_and_listed():
    st = _state()
    ch = st.apply_change({"known": [{"id": "P1", "in_image": "yes", "posture": "sitting", "place": "i stolen"}],
                          "new_persons": [], "doors": [], "things": []}, "door", T0 + 14, 3, max_persons=1)
    assert {c["what"] for c in ch} == {"posture", "place"}
    assert st.cam("door")["persons"]["P1"]["posture"] == "sitting"
    ch = st.apply_change({"known": [{"id": "P1", "in_image": "no"}]}, "door", T0 + 105, 9, max_persons=1)
    assert ch == [{"what": "presence", "id": "P1", "in_image": "no"}] and st.present_ids("door") == []


def test_an_omitted_field_keeps_its_value():
    st = _state()
    st.apply_change({"known": [{"id": "P1", "in_image": "yes"}]}, "door", T0 + 5, 2, max_persons=1)
    assert st.cam("door")["persons"]["P1"]["place"] == "ved døra"


def test_a_new_person_beyond_frigates_count_is_merged_by_code():
    st = _state()
    ch = st.apply_change({"known": [{"id": "P1", "in_image": "yes"}],
                          "new_persons": [{"age": "adult", "gender": "man", "clothing": "mørk jakke"}]},
                         "door", T0 + 5, 2, max_persons=1)
    assert list(st.persons) == ["P1"] and {"what": "merged", "id": "P1"} in ch


def test_a_new_person_within_frigates_count_is_added():
    st = _state()
    st.apply_change({"known": [{"id": "P1", "in_image": "yes"}], "new_persons": [{"age": "child"}]},
                    "door", T0 + 5, 2, max_persons=2)
    assert list(st.persons) == ["P1", "P2"]


def test_another_camera_has_its_own_place():
    st = _state()
    text = st.prompt_text("yard", "nb")
    assert "P1" in text and "Ikke sett på dette kameraet ennå" in text and "ved døra" not in text
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "place": "på plenen"}]}, "yard", T0 + 8, 2, 1)
    assert st.cam("door")["persons"]["P1"]["place"] == "ved døra"
    assert st.cam("yard")["persons"]["P1"]["place"] == "på plenen"


def test_code_overrides_presence():
    st = _state()
    st.apply_change({"known": [{"id": "P1", "in_image": "yes"}]}, "door", T0 + 105, 9, max_persons=1)
    assert st.set_absent("door", T0 + 105, "frigate_no_track") == ["P1"]
    assert st.present_ids("door") == [] and st.rows[-1]["override"] == "frigate_no_track"
    assert {"what": "presence", "id": "P1", "in_image": "no", "by": "frigate_no_track"} in st.rows[-1]["changes"]


def test_a_correction_is_kept():
    st = _state()
    st.apply_change({"known": [{"id": "P1", "in_image": "yes"}], "correction": "det var en jakke, ikke en genser"},
                    "door", T0 + 5, 2, 1)
    assert st.corrections[0]["text"] == "det var en jakke, ikke en genser"


def test_state_survives_storage():
    st = _state()
    st.skip("door", T0 + 20, 4)
    back = es.EpisodeState.from_dict(st.to_dict())
    assert back.to_dict() == st.to_dict() and back.rows[-1]["kind"] == "skipped"


def test_placeholders_are_not_values():
    st = es.EpisodeState()
    st.apply_full({"persons": [{"clothing": "...", "place": "unclear", "carried": "usynlig"}]}, "a", T0, 1)
    assert st.persons["P1"]["clothing"] == "" and st.cam("a")["persons"]["P1"]["carried"] == ""


# ── what the 27B and the GUI get ──


def test_timeline_is_written_by_code_in_time_order():
    st = es.EpisodeState()
    st.apply_full(FIRST, "door", T0 + 14, 3)  # best first frame is not the first in time
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "posture": "sitting"}]}, "door", T0 + 30, 4, 1)
    st.apply_change({"known": [{"id": "P1", "in_image": "yes"}]}, "door", T0 + 105, 9, 1)
    st.set_absent("door", T0 + 105, "frigate_no_track")
    lines = es.timeline_lines(st, "nb", camera_name=lambda c: "ringeklokke")
    assert len(lines) == 3 and lines[0].split(" ")[1] == "ringeklokke:"
    assert "sitter" in lines[1]
    assert "ingen person i bildet" in lines[2] and "Frigate ser ingen person" in lines[2]


def test_scene_fields_for_the_gui_and_lookup():
    st = _state()
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "carried": "flaske"}]}, "door", T0 + 5, 2, 1)
    f = es.scene_fields(st, "nb")
    assert f["persons"] == 1 and f["people"][0]["carried"] == "flaske" and "inngangsdør" in f["at_door"]
    assert f["scene"]


def test_prompt_text_in_three_languages():
    st = _state()
    for lang, word in (("nb", "Personer"), ("en", "Persons"), ("de", "Personen")):
        assert st.prompt_text("door", lang).startswith(word)


# ── answers ──


def test_parse_answer_accepts_a_fence_and_refuses_junk():
    assert es.parse_answer('```json\n{"known": []}\n```') == ({"known": []}, "")
    assert es.parse_answer("")[1] == "empty"
    assert es.parse_answer("ingen json")[1] == "no_json_object"
    assert es.parse_answer("{oops}")[1].startswith("json_error")


def test_text_language_guess():
    nb = {"persons": [{"clothing": "mørk jakke og bukse", "place": "bak bilen", "carried": "en flaske"}]}
    en = {"known": [{"id": "P1", "place": "near the car", "carried": "a bottle"}],
          "doors": [{"door": "front door"}]}
    assert es.text_language(es.answer_texts(nb)) == "nb"
    assert es.text_language(es.answer_texts(en)) == "en"
    assert es.text_language(["ok"]) is None


def test_something_carried_at_any_frame_is_kept_and_the_scene_text_is_a_summary():
    st = _state()
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "carried": "flaske"}]}, "door", T0 + 5, 2, 1)
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "carried": ""}]}, "door", T0 + 9, 3, 1)
    st.apply_change({"known": [{"id": "P1", "in_image": "no"}]}, "door", T0 + 20, 4, 1)
    f = es.scene_fields(st, "nb")
    assert f["people"][0]["carried"] == "flaske"  # not lost because a later frame carried nothing
    assert f["scene"].count("→") == 3 and f["scene"].endswith("ingen person i bildet")
