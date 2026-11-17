"""P144-s16 item 4: an event's L3 ran without Frigate's label in its prompt → the stored check says so
(`l3_blind: true`), so the label gate knows which L3 person counts are blind and may keep it open."""

import asyncio

from test_p144_s06a_pipe import _event, env  # noqa: F401 — env is the s06a pipe fixture

from kaare_core.frigate_user import pipe
from kaare_core.frigate_user import store as fstore

T0 = 1_800_000_000.0


def test_stored_check_marks_the_l3_blind(env):  # noqa: F811
    asyncio.run(pipe.process(_event(), None, None))
    (e,) = fstore.events_between(0, T0 * 2)
    assert env["llm_calls"] and e.attributes.get("persons") == 1
    assert e.check.get("l3_blind") is True
    assert "person)" not in env["llm_calls"][0]["prompt"]
