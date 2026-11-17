"""N39 fix 1 (rest) — every weights save that answers a human or feeds the night meeting says whether it was saved.

Same failure class as test_n39_receipt_not_saved (16.11: weights.db read-only, `weights.flush()` failed, the caller said
OK). Here: admin's «Er dette det samme?» (thing_links), «Hva er her?» (thing_names), and the night meeting's repeat count
and park (repeat_stop). The weights DB is really read-only (chmod, fixture below); every result must say saved=False and
the GUI endpoints must answer 503 `not_saved`, never OK.
Never touches live state: everything points at tmp_path. Placeholders only."""

import pytest
from fastapi import HTTPException

from kaare_core.frigate_user import thing_links, thing_names
from kaare_core.memory import repeat_stop
from kaare_core.routers import router_frigate_user as rfu
from tests.test_n39_receipt_not_saved import readonly_weights  # noqa: F401  (fixture)
from tests.test_p149_j2_questions import env  # noqa: F401  (fixture used by readonly_weights)

WHAT_KEY = "trapp|bird|4,6"
PAIR = "cam_door|bin|1,1~cam_yard|bin|2,2"


def test_thing_link_answer_says_not_saved(readonly_weights):  # noqa: F811  (fixture imported above)
    assert thing_links.answer("boss", PAIR, "yes") == {"ok": True, "saved": False}
    with pytest.raises(HTTPException) as e:
        rfu.api_thing_answer(rfu.ThingAnswer(pair=PAIR, answer="no"), u={"sub": "boss"})
    assert e.value.status_code == 503 and e.value.detail == "not_saved"


def test_what_answer_says_not_saved(readonly_weights):  # noqa: F811
    assert thing_names.answer("boss", WHAT_KEY, "not_label", name="placeholder") == {"ok": True, "saved": False}
    with pytest.raises(HTTPException) as e:
        rfu.api_what_answer(rfu.WhatAnswer(id=WHAT_KEY, answer="wrong"), u={"sub": "boss"})
    assert e.value.status_code == 503 and e.value.detail == "not_saved"


def test_repeat_stop_record_and_park_say_not_saved(readonly_weights):  # noqa: F811
    v = repeat_stop.record("user_a", "2026-10-19", None, who="Kåre")
    assert v.saved is False
    assert repeat_stop.park("user_a", "2026-10-19") is False


def test_saved_paths_still_say_saved(env):  # noqa: F811
    assert thing_links.answer("boss", PAIR, "yes") == {"ok": True, "saved": True}
    assert thing_names.answer("boss", WHAT_KEY, "skip") == {"ok": True, "saved": True}
    assert repeat_stop.record("user_a", "2026-10-19", None).saved is True
    assert repeat_stop.park("user_a", "2026-10-19") is True
