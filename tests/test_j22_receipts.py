"""J22 part 4 (2026-11-07): one receipt format for everything that is saved — built by code from what was actually
written: what · where · by whom · channel · when, nb/en/de, one fixed shape with a stable machine-readable marker
`⟦saved kind=<kind> ref=<id>⟧`, so a later Model Enforcer detector can compare «Kåre says it saved X» with the receipts of
the same turn (not built now). Red case: 2026-11-07 — Kåre said «Lagt inn: det var Userb» while only «nei» was stored."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kaare_core import receipts
from kaare_core.frigate_user import questions
from kaare_core.frigate_user import store as fstore
from tests import test_p149_j2_questions as j2

env = j2.env
NOW, H = j2.NOW, j2.H


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_the_receipt_has_one_fixed_shape_and_a_stable_marker(lang, monkeypatch):
    monkeypatch.setattr(receipts, "get_local_tz", lambda: ZoneInfo("Europe/Oslo"))
    r = receipts.build("camera_answer", ref="e1", what="«x»", where="Inngang 08:00", who="user_a", channel="chat",
                       when=datetime(2027, 1, 15, 9, 5, tzinfo=ZoneInfo("Europe/Oslo")).timestamp(), lang=lang)
    assert r.startswith("⟦saved kind=camera_answer ref=e1⟧ ")
    assert r.count(" | ") == 4 and "«x»" in r and "Inngang 08:00" in r and "user_a" in r and "09:05" in r
    assert receipts.parse(r) == [{"kind": "camera_answer", "ref": "e1"}]


def test_parse_finds_every_receipt_in_a_text():
    text = "a ⟦saved kind=camera_answer ref=e1⟧ x | y | z | w | v\\nb ⟦saved kind=note ref=n-3⟧ q | w | e | r | t"
    assert receipts.parse(text) == [{"kind": "camera_answer", "ref": "e1"}, {"kind": "note", "ref": "n-3"}]


def test_the_userb_case_the_receipt_says_no_name_was_stored(env):
    """Userone answered about his wife; the model passed «no» for himself → the receipt must say exactly that."""
    j2._store(j2._ev("e1", NOW - H))
    out = questions.verify("user_a", event_id="e1", episode_id=None, verdict="no", lang="nb")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "e1"}]
    assert "nei, ikke meg" in out and "ingen navn" in out.lower() and "Userb" not in out
    assert "gjenta" in out.lower()  # Kåre is told to repeat the receipt, not to paraphrase it


def test_admin_naming_a_member_gets_a_receipt_with_both_names(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    out = questions.verify("boss", event_id="e1", episode_id=None, verdict="yes", lang="nb", about="user_a")
    assert receipts.parse(out) == [{"kind": "camera_answer", "ref": "e1"}]
    assert "user_a" in out and "boss" in out
    assert fstore.event_by_id("e1").person_ref == "user_a"


def test_nothing_stored_gives_no_receipt(env):
    j2._store(j2._ev("e1", NOW - H, ref=None, kind="unknown", claim="none", sensors={}))
    out = questions.verify("boss", event_id="e1", episode_id=None, verdict="yes", lang="nb", about="user_c")
    assert receipts.parse(out) == [] and "Ingenting lagret" in out
