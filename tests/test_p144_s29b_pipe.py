"""P144-s29b in the pipe: Frigate «teller ikke» means its face name is never a vote (no name stored from it), and the word
of the one sensor a name stands behind is stored with the check (admin's word decides what Kåre says). Reuses the s19
pipe fakes: no broker, no Frigate, no LLM, no live users.db / frigate.db / configs."""

import json

from kaare_core.frigate_user import face_votes, query
from tests.test_p144_s19_pipe_votes import (  # noqa: F401 — s19 fixture + helpers
    _process,
    _stored,
    _vote,
    env,
)


def _cfg(tmp, frigate):
    (tmp / "frigate_user.yaml").write_text(
        "enabled: true\nface_votes: {enabled: true}\nface_sensors: " + json.dumps({"frigate": frigate}) + "\n")


def _tmp():
    return face_votes.settings.CONFIG_PATH.parent


def test_frigate_off_is_never_a_vote(env):  # noqa: F811 — the s19 fixture
    _cfg(_tmp(), {"mode": "admin", "trust": "off"})
    _process("Person A")
    e = _stored()
    assert e.person_ref is None and "persona" not in json.dumps(e.check).casefold()
    assert e.check.get("frigate_ignored") is True


def test_frigate_off_still_lets_double_take_name_someone(env):  # noqa: F811 — the s19 fixture
    _cfg(_tmp(), {"mode": "admin", "trust": "off"})
    _vote("e1", "person b")
    _process("Person A")
    e = _stored()
    assert e.person_ref == "personb" and e.check["sensors"]["relation"] == "double_take_only"


def test_admin_word_on_frigate_alone_is_what_kare_says(env):  # noqa: F811 — the s19 fixture
    _cfg(_tmp(), {"mode": "admin", "trust": "unlikely"})
    _process("Person A")
    e = _stored()
    assert e.check["claim_trust"] == {"word": "unlikely", "source": "admin"}
    assert query.certainty(e.check, e.claim_status) == "unlikely"


def test_auto_frigate_alone_keeps_todays_word(env):  # noqa: F811 — the s19 fixture
    _process("Person A")
    e = _stored()
    assert e.check["claim_trust"]["source"] == "auto"
    assert query.certainty(e.check, e.claim_status) == "likely"
