"""P144-s28 item 4 — the 27B's memory line: it translates the image model's English instead of copying it, and marks its
own inferences with a probability word. The prompt rules are checked here; the effect was measured on the 27B (s28 log).
A code check (shadow) counts English phrases from the 9B's free text that land in a Norwegian/German line.
"""

from kaare_core.frigate_user import conclude

SCENE = {"status": "done", "by": "frames",
         "people": [{"id": "P1", "clothing": "red jacket", "carried": "trailer; potted plant"},
                    {"id": "P2", "clothing": "dark top with patterned hat", "carried": ""}],
         "frames_state": {"cams": {"porch": {"persons": {"P1": {"place": "front porch", "carried": "potted plant"}},
                                             "doors": {"wooden door": "open"}}}}}


def test_rules_ask_to_translate_and_to_mark_inferences():
    for lang, words in (("nb", ("oversett", "sannsynlighetsord")), ("en", ("put them in", "probability word")),
                        ("de", ("übersetze", "Wahrscheinlichkeitswort"))):
        text = conclude._t("rule_frames", lang, language="x") + conclude._t("rules", lang, language="x")
        for w in words:
            assert w in text


def test_rules_give_no_leading_example_of_an_inference():
    # the user's prompt rule: the word list is there, no sentence that shows what an inference could be about.
    assert "trolig" not in conclude._t("rules", "nb", language="x").lower()
    assert "bil" not in conclude._t("rules", "nb", language="x").split("sannsynlighetsord")[-1]


def test_copied_phrases_found_in_a_norwegian_line():
    line = "Jeg så noen med potted plants ved front porch."
    assert set(conclude.copied_phrases(line, SCENE, "nb")) == {"potted plant", "front porch"}


def test_a_translated_line_has_no_copied_phrases():
    assert conclude.copied_phrases("Jeg så noen med potteplanter på verandaen.", SCENE, "nb") == []


def test_single_words_do_not_count_they_are_too_often_shared():
    assert conclude.copied_phrases("Jeg så en person med trailer i rød jacket.", SCENE, "nb") == []


def test_english_household_has_nothing_to_translate():
    assert conclude.copied_phrases("I saw someone with potted plants.", SCENE, "en") == []


def test_no_scene_no_phrases():
    assert conclude.copied_phrases("Jeg så noe.", None, "nb") == []


def test_the_existing_wrong_language_detector_counts_them_in_shadow():
    # Reused on purpose (rule 25: a new detector would need its own GUI label): frigate_wrong_language in frigate_frames.
    hits, action = conclude._enforce_frames("x", "nb", "t", {"kind": "frame", "wrong_language": ["potted plant"]})
    assert "frigate_wrong_language" in hits and action is None
