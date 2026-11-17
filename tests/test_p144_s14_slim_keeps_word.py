"""P144-s14 item 5 — slimming keeps what the probability word needs (s08 finding 1).

`events.slim` used to keep only decision / storage_gate / episode_rule: an «usikkert» (doubt, L0 unsure, L3 contradiction)
became «sannsynlig» after 7 days. Now the slim check keeps `doubt` and the L0/L3 verdicts (+ L3 reason), so
`query.certainty()` gives the same word on the slim check as on the full one, and the read view words slimmed events.
"""

import pytest

from kaare_core.frigate_user import query
from kaare_core.frigate_user import store as fstore

L0_UNSURE = {"layer": "l0", "verdict": "unsure", "strong": False, "reason": "face_px=900", "contradictions": []}
L3_CONTRA = {"layer": "l3", "verdict": "no", "strong": False, "reason": "hair: dark ≠ blond",
             "contradictions": [{"layer": "l3", "field": "hair", "seen": "dark", "profile": "blond"}]}
FULL = {"pipe": 3, "claim": True, "drive": {"selected": True, "why": "selected"}, "image": "snapshot",
        "face_px": 900, "ir": True, "l1": [{"layer": "l1", "verdict": "pass"}], "decision": "unverified",
        "storage_gate": "kept"}


@pytest.mark.parametrize("check", [
    {**FULL, "l0": L0_UNSURE},
    {**FULL, "l0": {**L0_UNSURE, "verdict": "pass"}, "l3": L3_CONTRA},
    {**FULL, "doubt": {"layer": "l3", "reason": "beard"}},
    {**FULL, "l0": {**L0_UNSURE, "verdict": "pass"}},
])
def test_slim_check_gives_the_same_word(check):
    slim = fstore.slim_check(check)
    assert query.certainty(slim, "unverified") == query.certainty(check, "unverified")


def test_slim_check_stays_small():
    slim = fstore.slim_check({**FULL, "l0": L0_UNSURE, "l3": L3_CONTRA, "doubt": {"layer": "l3", "reason": "x"}})
    assert set(slim) <= {"decision", "storage_gate", "episode_rule", "doubt", "l0", "l3"}
    assert slim["l0"] == {"verdict": "unsure"}
    assert slim["l3"] == {"verdict": "no", "reason": "hair: dark ≠ blond"}
    assert "face_px" not in slim and "l1" not in slim


def test_a_slimmed_event_still_gets_a_word():
    ev = fstore.FrigateUserEvent(event_id="e", camera="door", label="person", start_ts=0, person_ref="Userb",
                                 claim_status="unverified", check=fstore.slim_check({**FULL, "l0": L0_UNSURE}),
                                 tier="slim")
    assert query._combined([ev]) == "uncertain"
