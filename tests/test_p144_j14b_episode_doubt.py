"""P144 / run-queue J14b — within one episode, a stronger sensor's other name lowers a weaker name (ep 266).

ep 266 (2026-11-06 07:16): the doorbell event's name came from Frigate + facebox, both «uncertain»; on another camera in
the same episode deepstack («very likely») named someone else. Main session's window: the same episode (one situation,
s26). The weak name drops to «usikkert» with a reason; a person's own «ja» on that event is never lowered. And the 27B's
«two sensors agree» line no longer says «høyst sannsynlig» (J14 / P149 D1). Placeholders only."""

from kaare_core.frigate_user import conclude, query
from kaare_core.frigate_user import store as fstore


def _ev(eid, cam, ref, sensors, claim="unverified", check_extra=None):
    check = {"decision": claim, "sensors": sensors, **(check_extra or {})}
    return fstore.FrigateUserEvent(event_id=eid, camera=cam, label="person", start_ts=1.0, person_ref=ref,
                                   person_kind="household", claim_status=claim, check=check, episode_id=266)


DOOR = _ev("door", "doorbell", "user_n", {
    "relation": "agree", "frigate": "user_n", "frigate_trust": {"word": "uncertain", "source": "admin"},
    "detectors": [{"detector": "facebox", "ref": "user_n", "trust": "uncertain"},
                  {"detector": "deepstack", "ref": None, "trust": "very_likely"}]})
SIDE = _ev("side", "east", "user_p", {
    "relation": "double_take_only",
    "detectors": [{"detector": "deepstack", "ref": "user_p", "trust": "very_likely"},
                  {"detector": "facebox", "ref": "user_n", "trust": "uncertain"}]})


def test_stronger_other_name_in_the_episode_is_found():
    assert query.stronger_other(DOOR, [DOOR, SIDE]) == "user_p"
    assert query.stronger_other(SIDE, [DOOR, SIDE]) is None  # the strong name itself is not lowered
    assert query.stronger_other(DOOR, [DOOR]) is None  # alone in its episode


def test_weak_name_is_lowered_to_uncertain_in_the_episode():
    assert query.certainty(DOOR.check, DOOR.claim_status) == "likely"
    assert query.certainty_in(DOOR, [DOOR, SIDE]) == "uncertain"


def test_a_persons_own_yes_is_never_lowered():
    human = _ev("door", "doorbell", "user_n", DOOR.check["sensors"], claim="confirmed",
                check_extra={"human": {"by": "user_n", "verdict": "yes"}})
    assert query.stronger_other(human, [human, SIDE]) is None


def test_the_27b_line_says_why_and_never_very_likely():
    line = conclude._who(DOOR, "nb", episode_events=[DOOR, SIDE])
    assert "usikkert" in line and "user_p" in line
    agree = _ev("a", "doorbell", "user_n", {"relation": "agree"}, claim="confirmed")
    assert "høyst sannsynlig" not in conclude._who(agree, "nb")
