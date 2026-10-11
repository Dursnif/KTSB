"""One household time for all of KTSB (N16, CLAUDE.md rules 24/25).

The timezone is set in the GUI (settings.yaml location.timezone, read by get_local_tz()). Before
N16 the clock tool, «at HH:MM» timers, quiet hours and dates used the machine clock — UTC in Docker —
so a Norwegian Docker user got the clock 2 h wrong and a «23:50» reminder at 01:50 (DT03 R2 2.3).

Two kinds of test:
- a guard that scans shipped code for machine-clock calls, so the bug cannot come back;
- behaviour tests that pin a timezone far from this machine's (UTC+14 / UTC-12), so a test only
  passes if the code really uses the household timezone and not the host clock.
"""

import ast
import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import adapters.llm_adapter as llm_adapter
from kaare_core import config, normalcy, notify
from kaare_core.tools import executor, timer_service

ROOT = Path(__file__).resolve().parent.parent

# Shipped runtime code. Not scanned, on purpose:
# - scripts/: command-line tools printing to the operator's own terminal;
# - services/inner_voices/: standalone package on another node, no access to KTSB config; it writes
#   and re-reads its own timestamps with the same clock, and pushed thoughts are stamped by the API.
_GUARDED = ["kaare_core/**/*.py", "adapters/**/*.py", "kaare_*.py", "services/**/*.py"]
_UNGUARDED_PARTS = {"inner_voices", "venv", "convert_venv", "__pycache__", "node_modules"}

FAR_EAST = ZoneInfo("Pacific/Kiritimati")  # UTC+14 — never this machine's zone
FAR_WEST = ZoneInfo("Etc/GMT+12")  # UTC-12


def _machine_clock_calls(tree: ast.AST):
    """Calls that read the host's local clock instead of the household timezone."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = node.func.value
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        attr = node.func.attr
        has_tz = any(k.arg == "tz" for k in node.keywords)
        if base_name == "datetime" and attr in ("now", "today") and not node.args and not has_tz:
            yield node.lineno, f"datetime.{attr}()"
        elif base_name == "date" and attr == "today":
            yield node.lineno, "date.today()"
        elif (
            base_name == "datetime"
            and attr == "fromtimestamp"
            and len(node.args) < 2
            and not has_tz
        ):
            yield node.lineno, "datetime.fromtimestamp(ts) without tz"
        elif base_name in ("time", "_time") and attr in (
            "localtime",
            "strftime",
            "ctime",
            "mktime",
        ):
            yield node.lineno, f"time.{attr}()"


def test_guard_no_machine_clock_in_shipped_code():
    found = []
    for pattern in _GUARDED:
        for path in sorted(ROOT.glob(pattern)):
            if _UNGUARDED_PARTS & set(path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found += [
                f"{path.relative_to(ROOT)}:{ln}: {what}" for ln, what in _machine_clock_calls(tree)
            ]
    assert not found, (
        "Machine-clock time in shipped code (UTC in Docker). Use kaare_core.config.now_local() / "
        "today_local() / local_from_ts() for anything a person or the model sees or for clock-time "
        "scheduling; datetime.now(timezone.utc) for stored timestamps; time.monotonic() for durations.\n"
        + "\n".join(found)
    )


def test_guard_catches_the_old_patterns():
    src = (
        "from datetime import datetime, date\nimport time\n"
        "a = datetime.now()\nb = date.today()\nc = time.strftime('%H')\n"
        "d = datetime.fromtimestamp(1.0)\ne = time.localtime().tm_hour\n"
        "ok1 = datetime.now(timezone.utc)\nok2 = datetime.now(tz=x)\nok3 = datetime.fromtimestamp(1.0, tz)\n"
    )
    hits = [what for _, what in _machine_clock_calls(ast.parse(src))]
    assert len(hits) == 5, hits


@pytest.fixture
def household_tz(monkeypatch):
    """Pin the household timezone (what the GUI would write) without touching settings.yaml —
    in config and in every loaded module that imported get_local_tz by name."""
    original = config.get_local_tz

    def _set(tz: ZoneInfo):
        for mod in list(sys.modules.values()):
            for attr, val in list(getattr(mod, "__dict__", {}).items()):
                if val is original:
                    monkeypatch.setattr(mod, attr, lambda: tz)
        return tz

    return _set


def test_now_helpers_follow_the_household_timezone(household_tz):
    tz = household_tz(FAR_EAST)
    now = config.now_local()
    assert now.tzinfo is tz
    assert abs(now.timestamp() - time.time()) < 5
    assert config.today_local() == datetime.now(tz).date().isoformat()
    assert config.local_from_ts(time.time()).utcoffset() == timedelta(hours=14)


def test_clock_tool_answers_in_household_time(household_tz):
    tz = household_tz(FAR_EAST)
    before = datetime.now(tz)
    result = asyncio.run(executor._dispatch("timer", {"action": "clock", "_user_id": "global"}))
    after = datetime.now(tz)
    assert any(d.strftime("%H:%M") in result for d in (before, after)), result
    assert before.strftime("%d.%m.%Y") in result or after.strftime("%d.%m.%Y") in result


@pytest.mark.parametrize("tz", [FAR_EAST, FAR_WEST])
def test_at_time_is_household_clock_time(household_tz, tz):
    household_tz(tz)
    fire = timer_service._parse_at_time("23:50")
    assert fire.tzinfo is not None
    local = datetime.fromtimestamp(fire.timestamp(), tz)
    assert (local.hour, local.minute) == (23, 50)
    assert 0 < fire.timestamp() - time.time() <= 24 * 3600


def test_dated_at_time_is_household_clock_time(household_tz):
    tz = household_tz(FAR_WEST)
    fire = timer_service._parse_at_time("2030-01-02 07:15")
    assert datetime.fromtimestamp(fire.timestamp(), tz) == datetime(2030, 1, 2, 7, 15, tzinfo=tz)


def test_daily_repeat_keeps_clock_time_across_dst():
    oslo = ZoneInfo("Europe/Oslo")
    before = datetime(2026, 10, 24, 7, 0, tzinfo=oslo)  # CEST, the day before the change
    nxt = timer_service._next_occurrence(before, "daily")
    after = datetime.fromtimestamp(nxt.timestamp(), oslo)
    assert (after.date().isoformat(), after.hour, after.minute) == ("2026-10-25", 7, 0)
    assert nxt.timestamp() - before.timestamp() == 25 * 3600  # the night is one hour longer


def test_timer_list_shows_household_time(household_tz, monkeypatch):
    tz = household_tz(FAR_EAST)
    fires = time.time() + 3 * 3600
    info = {
        "id": "n16test",
        "user_id": "global",
        "repeat": "daily",
        "fires_at_ts": fires,
        "tts_text": "N16",
        "prompt": "",
        "fires_at": "",
    }
    monkeypatch.setattr(timer_service, "_timers", {"n16test": info})
    out = timer_service.list_timers(user_id=None, lang="nb")
    assert datetime.fromtimestamp(fires, tz).strftime("%d.%m %H:%M") in out, out


def _etc_zone_with_local_hour(hour: int) -> ZoneInfo:
    """A fixed-offset zone where the local hour right now is `hour`."""
    utc_hour = datetime.now(UTC).hour
    offset = (hour - utc_hour + 12) % 24 - 12  # -12 … +11
    return ZoneInfo(
        "Etc/UTC" if offset == 0 else f"Etc/GMT{'-' if offset > 0 else '+'}{abs(offset)}"
    )


@pytest.mark.parametrize("local_hour,quiet", [(23, True), (12, False)])
def test_quiet_hours_use_household_time(household_tz, monkeypatch, local_hour, quiet):
    household_tz(_etc_zone_with_local_hour(local_hour))
    monkeypatch.setattr(
        notify,
        "get_settings",
        lambda: {"notifications": {"quiet_hours_start": 22, "quiet_hours_end": 7}},
    )
    if datetime.now(UTC).minute == 59 and datetime.now(UTC).second > 55:
        time.sleep(6)  # don't straddle an hour boundary
    assert notify._is_quiet_hours() is quiet


def test_time_block_reads_timezone_live(household_tz):
    tz = household_tz(FAR_EAST)
    block = llm_adapter.time_block("en")
    assert (
        datetime.now(tz).strftime("%H:%M") in block
        or (datetime.now(tz) - timedelta(minutes=1)).strftime("%H:%M") in block
    )


def test_normalcy_live_score_uses_utc_like_its_baseline(monkeypatch):
    now = datetime.now(UTC)
    key = f"cam|person|{now.hour}|{now.weekday()}"
    monkeypatch.setattr(
        normalcy,
        "_load_baseline",
        lambda: {"confidence": 1.0, "days_of_data": 30, "buckets": {key: {"p_normal": 1.0}}},
    )
    score, _ = normalcy.get_deviation_score("cam", "person")
    assert score == 0.0  # found the UTC bucket — very normal
    # an aware event time in another zone is converted to UTC before the lookup
    score, _ = normalcy.get_deviation_score("cam", "person", dt=now.astimezone(FAR_EAST))
    assert score == 0.0
