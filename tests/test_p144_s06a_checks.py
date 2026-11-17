"""P144-s06a: the code layers of the claim check — L0 quality, L1 context, L3 attribute comparison and the fixed
decision rules (P144 Research «hybrid claim check»). Pure functions, no I/O."""

import io

import pytest
from PIL import Image

from kaare_core.frigate_user import checks

T0 = 1_800_000_000.0


def _img(color=(180, 120, 60), size=(960, 720)) -> Image.Image:
    return Image.new("RGB", size, color)


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


# ── L0 ─────────────────────────────────────────────────────────────────────────


def test_face_px_from_frigate_attributes():
    data = {"attributes": [{"label": "face", "box": [0.5, 0.5, 0.1, 0.2], "score": 0.7}]}
    assert checks.face_px(data, (1000, 500)) == 100 * 100


def test_face_px_largest_face_and_none_without_face():
    data = {"attributes": [
        {"label": "face", "box": [0, 0, 0.05, 0.05]},
        {"label": "face", "box": [0, 0, 0.2, 0.2]},
        {"label": "license_plate", "box": [0, 0, 0.9, 0.9]},
    ]}
    assert checks.face_px(data, (1000, 1000)) == 200 * 200
    assert checks.face_px({}, (1000, 1000)) is None
    assert checks.face_px({"attributes": "junk"}, (1000, 1000)) is None


def test_ir_detection():
    assert checks.is_ir(_img((90, 90, 90)))
    assert checks.is_ir(Image.open(io.BytesIO(_jpeg(_img((120, 121, 119))))))
    assert not checks.is_ir(_img((180, 120, 60)))


def test_l0_small_face_is_unsure():
    vote = checks.l0_quality(face_px=6_000, ir=False, claim_score=0.95, min_face_px=10_000, min_claim_score=0.0)
    assert vote.verdict == "unsure" and not vote.passed
    assert "face_px" in vote.reason


def test_l0_ir_and_low_score_and_no_face_are_unsure():
    assert not checks.l0_quality(face_px=20_000, ir=True, claim_score=0.9, min_face_px=10_000,
                                 min_claim_score=0.0).passed
    assert not checks.l0_quality(face_px=20_000, ir=False, claim_score=0.5, min_face_px=10_000,
                                 min_claim_score=0.8).passed
    assert not checks.l0_quality(face_px=None, ir=False, claim_score=0.9, min_face_px=10_000,
                                 min_claim_score=0.0).passed


def test_l0_good_quality_passes():
    vote = checks.l0_quality(face_px=20_000, ir=False, claim_score=0.9, min_face_px=10_000, min_claim_score=0.8)
    assert vote.passed and vote.verdict == "pass"


# ── L1 ─────────────────────────────────────────────────────────────────────────


def _other(camera="hage", start=T0, end=T0 + 30, closed=True):
    return checks.Seen(camera=camera, start_ts=start, end_ts=end, episode_closed=closed)


def test_l1_same_person_elsewhere_in_other_episode_is_strong():
    votes = checks.l1_context(camera="inngang", start_ts=T0 + 5, end_ts=T0 + 20, seen=[_other()],
                              household_member=True, household_away=False)
    assert any(v.verdict == "no" and v.strong for v in votes)


def test_l1_same_person_same_episode_or_same_camera_is_fine():
    same_episode = checks.l1_context(camera="inngang", start_ts=T0 + 5, end_ts=T0 + 20,
                                     seen=[_other(closed=False)], household_member=True, household_away=False)
    same_camera = checks.l1_context(camera="inngang", start_ts=T0 + 5, end_ts=T0 + 20,
                                    seen=[_other(camera="inngang")], household_member=True, household_away=False)
    no_overlap = checks.l1_context(camera="inngang", start_ts=T0 + 100, end_ts=T0 + 120, seen=[_other()],
                                   household_member=True, household_away=False)
    for votes in (same_episode, same_camera, no_overlap):
        assert not any(v.strong for v in votes)


def test_l1_household_away_is_not_a_contradiction():
    votes = checks.l1_context(camera="inngang", start_ts=T0, end_ts=T0 + 10, seen=[],
                              household_member=True, household_away=True)
    assert votes and all(not v.strong and v.verdict == "info" for v in votes)


# ── L3 ─────────────────────────────────────────────────────────────────────────


def test_parse_l3_normalises_values():
    text = ('```json\n{"persons": 1, "beard": "Yes", "glasses": "no", "age_band": "adult", '
            '"headwear": "cap", "description": "d", "analysis": "a"}\n```')
    attrs, err = checks.parse_l3(text)
    assert err == ""
    assert attrs["beard"] == "yes" and attrs["glasses"] == "no" and attrs["headwear"] == "unknown"
    assert attrs["persons"] == 1 and attrs["description"] == "d" and attrs["analysis"] == "a"


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"persons": 1'])
def test_parse_l3_errors(text):
    attrs, err = checks.parse_l3(text)
    assert attrs is None and err


def _attrs(**kw):
    base = {"persons": 1, "beard": "unknown", "glasses": "unknown", "age_band": "unknown", "headwear": "unknown"}
    base.update(kw)
    return base


def _profile(**kw):
    base = {"beard": "unknown", "glasses": "unknown", "age_band": "unknown", "headwear_often": "unknown"}
    base.update(kw)
    return base


@pytest.mark.parametrize("seen", ["full", "circle_beard"])
def test_beard_on_no_beard_profile_is_strong_contradiction(seen):
    vote = checks.l3_compare(_attrs(beard=seen), _profile(beard="no"))
    assert vote.verdict == "no" and vote.strong
    back = checks.l3_compare(_attrs(beard="no"), _profile(beard=seen))
    assert back.verdict == "no" and back.strong


@pytest.mark.parametrize(("seen", "profile"), [
    ("moustache", "no"), ("no", "moustache"),  # a small moustache can be missed
    ("moustache", "full"), ("circle_beard", "full"), ("full", "moustache"),  # beards get trimmed
    ("yes", "no"), ("no", "yes"),  # legacy «some beard» could be a moustache
])
def test_beard_type_differences_are_weak(seen, profile):
    vote = checks.l3_compare(_attrs(beard=seen), _profile(beard=profile))
    assert not vote.strong and vote.verdict == "unsure"


@pytest.mark.parametrize(("seen", "profile"), [("yes", "full"), ("moustache", "yes"), ("full", "full")])
def test_legacy_yes_matches_any_beard_type(seen, profile):
    vote = checks.l3_compare(_attrs(beard=seen, age_band="adult"), _profile(beard=profile, age_band="adult"))
    assert vote.verdict == "yes"


def test_parse_l3_keeps_beard_types():
    attrs, _err = checks.parse_l3('{"persons": 1, "beard": "circle_beard"}')
    assert attrs["beard"] == "circle_beard"


def test_unknown_never_contradicts():
    assert not checks.l3_compare(_attrs(beard="unknown"), _profile(beard="no")).strong
    assert not checks.l3_compare(_attrs(beard="yes"), _profile(beard="unknown")).strong
    assert not checks.l3_compare(_attrs(beard="full"), _profile(beard="unknown")).strong


def test_glasses_and_age_rules():
    assert checks.l3_compare(_attrs(glasses="yes"), _profile(glasses="no")).strong
    assert not checks.l3_compare(_attrs(glasses="no"), _profile(glasses="yes")).strong  # taken off
    assert not checks.l3_compare(_attrs(glasses="yes"), _profile(glasses="sometimes")).strong
    assert checks.l3_compare(_attrs(age_band="child"), _profile(age_band="adult")).strong
    assert not checks.l3_compare(_attrs(age_band="teen"), _profile(age_band="adult")).strong


def test_l3_match_is_one_yes():
    vote = checks.l3_compare(_attrs(beard="yes", glasses="yes", age_band="adult"),
                             _profile(beard="yes", glasses="yes", age_band="adult"))
    assert vote.verdict == "yes"


def test_l3_several_persons_cannot_be_compared():
    vote = checks.l3_compare(_attrs(persons=2, beard="yes"), _profile(beard="no"))
    assert vote.verdict == "unsure" and not vote.strong


# ── decision ───────────────────────────────────────────────────────────────────


def V(layer, verdict, strong=False):  # short name: reads like a table below
    return checks.Vote(layer=layer, verdict=verdict, strong=strong, reason="t")


def test_decide_rules():
    assert checks.decide(has_claim=False, votes=[V("l3", "yes")], check_ran=True) == "none"
    assert checks.decide(has_claim=True, votes=[], check_ran=False) == "none"
    assert checks.decide(has_claim=True, votes=[V("l0", "unsure")], check_ran=True) == "unverified"
    # P144-s06c: one strong contradiction never rejects alone — unverified with a doubt (was "rejected")
    assert checks.decide(has_claim=True, votes=[V("l3", "yes"), V("l1", "no", True)], check_ran=True) == "unverified"
    assert checks.decide(has_claim=True, votes=[V("l3", "no", True), V("l1", "no", True)], check_ran=True) == "rejected"
    assert checks.decide(has_claim=True, votes=[V("l3", "yes")], check_ran=True) == "unverified"
    assert checks.decide(has_claim=True, votes=[V("l3", "yes"), V("l1", "yes")], check_ran=True) == "confirmed"
    # two yes from the same layer are not independent
    assert checks.decide(has_claim=True, votes=[V("l3", "yes"), V("l3", "yes")], check_ran=True) == "unverified"
