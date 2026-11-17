"""P144 R56: the 27B's line about a person's claim carries the checks' reasons in words code writes — never a raw,
cut-off JSON dump of the check (it gave the model up to 300 characters of half a JSON object)."""

from kaare_core.frigate_user import conclude
from kaare_core.frigate_user import store as fstore


def _ev(check):
    return fstore.FrigateUserEvent(event_id="e", camera="c", label="person", start_ts=1.0, person_ref="user_a",
                                   person_kind="household", claim_status="unverified", check=check)


def test_reasons_not_json():
    check = {"pipe": "shadow-v1", "decision": "unverified",
             "l0": {"layer": "l0", "verdict": "unsure", "reason": "face_px=6688<10000"},
             "l3": {"layer": "l3", "verdict": "yes", "reason": "match: glasses, age_band"},
             "sensors": {"relation": "disagree", "detectors": [{"detector": "x", "ref": "user_b"}] * 20}}
    out = conclude._claim_text(_ev(check))
    assert "{" not in out and "pipe" not in out and "detectors" not in out
    assert "face_px=6688<10000" in out and "match: glasses, age_band" in out and "disagree" in out
    assert out.startswith("unverified")


def test_explicit_why_still_wins_and_empty_check_is_bare():
    assert conclude._claim_text(_ev({"why": "two layers agree"})) == "unverified — two layers agree"
    assert conclude._claim_text(_ev({})) == "unverified"
