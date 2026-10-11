"""
P118 B6 — S6 consolidation: week → month → half-year → year, hybrid (code counts/links, Janus
summarises), nothing deleted, PR3 (open rows leave the active inbox after 7 days + week done).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b6_consolidate.py -v
"""

import asyncio
import json
import sys
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.dev_slot.consolidate as cons
import kaare_core.dev_slot.steps as steps
import kaare_core.memory.learning_store as ls

OSLO = ZoneInfo("Europe/Oslo")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(cons, "get_local_tz", lambda: OSLO)


def _janus(replies=None):
    calls = []

    async def call(system, user, rid):
        calls.append(user)
        if replies is not None:
            return replies.pop(0) if replies else ""
        return json.dumps({"summary": f"summary #{len(calls)}"})

    call.calls = calls
    return call


@pytest.mark.parametrize(
    "level,d,label,start,end",
    [
        ("week", date(2026, 10, 3), "2026-W40", date(2026, 9, 28), date(2026, 10, 5)),
        ("week", date(2027, 1, 1), "2026-W53", date(2026, 12, 28), date(2027, 1, 4)),
        ("month", date(2026, 9, 15), "2026-09", date(2026, 9, 1), date(2026, 10, 1)),
        ("half_year", date(2026, 9, 15), "2026-H2", date(2026, 7, 1), date(2027, 1, 1)),
        ("year", date(2026, 3, 1), "2026", date(2026, 1, 1), date(2027, 1, 1)),
    ],
)
def test_period_of(level, d, label, start, end):
    assert cons.period_of(level, d) == (label, start, end)


def test_week_then_month_with_children_rule():
    ls.add_inbox_item(
        "Sensor flapping on the north camera",
        source="legacy",
        pointer="p1",
        ts="2026-09-02T10:00:00+00:00",
    )
    ls.add_inbox_item(
        "Doorbell clock skew", source="refl", rid="r2", ts="2026-09-10T10:00:00+00:00"
    )
    janus = _janus()
    now = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)  # Saturday: week 40 not ended yet
    res = asyncio.run(cons.run_s6(janus, lang="en", rid="rid-meet-1", now=now))
    weeks = [w for w in res["written"] if w.startswith("week:")]
    assert weeks == ["week:2026-W36", "week:2026-W37", "week:2026-W38", "week:2026-W39"]
    assert "month:2026-09" not in res["written"], (
        "September waits: week 40 (28.09–04.10) has not ended"
    )
    assert len(janus.calls) == 2, (
        "only weeks with activity cost a Janus call; empty weeks are mechanical"
    )
    w36 = ls.get_consolidations("week", ["2026-W36"])[0]
    assert (
        w36["summary"].startswith("summary")
        and json.loads(w36["covers"])
        and json.loads(w36["counts"])["inbox_rows"] == 1
    )

    later = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)  # Tuesday after week 40
    res2 = asyncio.run(cons.run_s6(janus, lang="en", rid="rid-meet-2", now=later))
    assert "week:2026-W40" in res2["written"] and "month:2026-09" in res2["written"]
    month = ls.get_consolidations("month", ["2026-09"])[0]
    assert len(json.loads(month["covers"])) == 5, "the month links its five overlapping weeks"
    assert "summary #" in janus.calls[-1] or "parts" in janus.calls[-1]
    res3 = asyncio.run(cons.run_s6(janus, lang="en", rid="rid-meet-3", now=later))
    assert res3["written"] == [], "no period is consolidated twice"


def test_no_summary_means_no_row_and_retry_next_night():
    ls.add_inbox_item("Something happened", source="refl", rid="r1", ts="2026-09-02T10:00:00+00:00")
    now = datetime(2026, 9, 10, 1, 0, tzinfo=UTC)
    res = asyncio.run(cons.run_s6(_janus(replies=["not json"]), lang="nb", rid="x", now=now))
    assert res["skipped_no_summary"] == ["week:2026-W36"] and not ls.consolidation_exists(
        "week", "2026-W36"
    )
    res2 = asyncio.run(cons.run_s6(_janus(), lang="nb", rid="x", now=now))
    assert "week:2026-W36" in res2["written"]


def test_cap_per_night():
    for d in range(1, 29, 7):
        ls.add_inbox_item(
            f"Distinct item {d} " + "abcdefghij"[d % 10] * 20,
            source="refl",
            rid=f"r{d}",
            ts=f"2026-09-{d:02d}T10:00:00+00:00",
        )
    janus = _janus()
    asyncio.run(
        cons.run_s6(
            janus, lang="en", rid="x", now=datetime(2026, 11, 1, tzinfo=UTC), max_per_night=2
        )
    )
    assert len(janus.calls) == 2


def test_pr3_open_rows_leave_active_inbox_only_after_week_done_and_7_days():
    old = ls.add_inbox_item(
        "Old open item", source="refl", rid="r1", ts="2026-09-22T10:00:00+00:00"
    )  # week 39
    handled = ls.add_inbox_item(
        "Handled item different text", source="refl", rid="r2", ts="2026-09-23T10:00:00+00:00"
    )
    ls.apply_topic_verdict(handled["topic_id"], "event", label="x")
    fresh = ls.add_inbox_item(
        "Fresh item from this week", source="refl", rid="r3", ts="2026-09-30T10:00:00+00:00"
    )
    # Wednesday 30.09: week 39 is done, but the old row is only 8 days old? 22.09 → 30.09 = 8 days.
    res = asyncio.run(
        cons.run_s6(_janus(), lang="en", rid="x", now=datetime(2026, 9, 30, 12, 0, tzinfo=UTC))
    )
    status = {r["id"]: r["status"] for r in ls.list_inbox()}
    assert status[old["item_id"]] == "consolidated" and res["retired"] == 1
    assert status[handled["item_id"]] == "event", "handled rows keep their verdict status"
    assert status[fresh["item_id"]] == "new", "week 40 not consolidated → stays active"
    assert len(ls.list_inbox()) == 3, "nothing deleted"


def test_report_shows_consolidation():
    text = steps.build_report(
        date="2026-10-06",
        lang="nb",
        summary_text="x",
        findings=[],
        notes={},
        proposals=[],
        b3={"s1": [], "s2": [], "s6": {"written": ["week:2026-W40"], "retired": 3}},
    )
    assert "## Konsolidert i natt" in text and "uke 2026-W40" in text and "3" in text
