"""T32b job 3 — dev proposal #78 (confirmed 2026-10-22, never built) and the false «0 nye rader» OK.

#78: Argus must drop the house's routine `call_service weather.get_forecasts` (every 5 min, two at a time) from event
capture, and identical lines within one digest must not repeat. The nightly «Sett mot løste saker» line must say whether
a confirmed proposal is built and which topic its row count is for. Temp DB / in-memory only — never the house's data.
"""

import collections

import pytest

import kaare_argus
import kaare_core.dev_slot.steps as steps
import kaare_core.memory.learning_store as ls


def _call(domain, service, entity="['weather.forecast_home']"):
    return {"source": "ha-events", "stage": "call_service", "domain": domain, "service": service, "entity_id": entity}


def test_routine_forecast_call_is_noise():
    assert kaare_argus.is_noise(_call("weather", "get_forecasts"), "ha-events") is True


@pytest.mark.parametrize("domain,service", [("light", "turn_on"), ("switch", "turn_off"), ("weather", "reload")])
def test_other_service_calls_are_kept(domain, service):
    assert kaare_argus.is_noise(_call(domain, service, "['light.x']"), "ha-events") is False


def test_identical_digest_line_is_not_repeated():
    buf = collections.deque(maxlen=200)
    line = "11-12 17:48 | ha-events | HA: binary_sensor.x off → on"
    for _ in range(3):
        kaare_argus.digest_add(None, buf, "ha-events", {}, line)
    kaare_argus.digest_add(None, buf, "ha-events", {}, "11-12 17:49 | ha-events | HA: binary_sensor.x off → on")
    assert list(buf).count(line) == 1
    assert len(buf) == 2


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    return tmp_path


def _confirmed_proposal():
    tid = ls.add_inbox_item("Argus duplikater av weather.get_forecasts", source="gui")["topic_id"]
    fid = ls.add_finding("r", "funn", [], True, topic_id=tid)
    pid = ls.add_proposal(fid, "Filtrer weather.get_forecasts")
    ls.decide_proposal(pid, "confirmed", "userone")
    return pid, tid


def test_followup_says_not_built_until_marked(db):
    pid, _ = _confirmed_proposal()
    row = ls.proposal_followups("2000-01-01T00:00:00+00:00")[0]
    assert row["id"] == pid and not row.get("built_at")
    assert ls.mark_proposal_built(pid, "claude") is True
    row = ls.proposal_followups("2000-01-01T00:00:00+00:00")[0]
    assert row["built_at"]


def test_mark_built_only_for_confirmed(db):
    tid = ls.add_inbox_item("noe", source="gui")["topic_id"]
    fid = ls.add_finding("r", "funn", [], True, topic_id=tid)
    pid = ls.add_proposal(fid, "pending one")
    assert ls.mark_proposal_built(pid, "claude") is False


def _report(followups):
    return steps.build_report(
        date="2026-11-12",
        lang="nb",
        summary_text="ok",
        findings=[],
        notes={},
        proposals=[],
        b3={"s1": [], "s2": [], "proposal_followups": followups},
    )


def test_report_line_not_built_is_honest():
    rep = _report([{"id": 78, "topic_id": 745, "decided_at": "2026-10-22T13:03", "new_rows_since": 0}])
    assert "Bekreftet forslag #78 (sak #745, 2026-10-22): ikke bygget · 0 nye rader på sak #745 siden" in rep


def test_report_line_built_shows_date():
    rep = _report(
        [
            {
                "id": 78,
                "topic_id": 745,
                "decided_at": "2026-10-22T13:03",
                "new_rows_since": 0,
                "built_at": "2026-11-12T18:30:00+00:00",
            }
        ]
    )
    assert "bygget 2026-11-12 · 0 nye rader på sak #745 siden" in rep
