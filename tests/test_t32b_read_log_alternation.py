"""T32b job 1 — Mechanic's log search must understand «a|b» (dev meeting 2026-11-12 #823: «ws2812|benkelys»
returned «Ingen treff» although ha_events.log had 73 097 matching lines, because grep ran without -E).

Uses a temporary log directory — never the house's /kaare/logs.
"""

import pytest

from kaare_core.agents.mechanic.shell_guard import readonly_violation
from kaare_core.tools import shared_tools


@pytest.fixture
def logs(tmp_path, monkeypatch):
    (tmp_path / "ha_events.log").write_text(
        '{"entity_id": "switch.ws2812_stress_test"}\n{"entity_id": "light.benkelys_benkelys"}\n{"x": 1}\n',
        encoding="utf-8",
    )
    (tmp_path / "other.log").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setattr(shared_tools, "LOG_DIR", tmp_path)
    return tmp_path


def test_alternation_across_all_logs(logs):
    out = shared_tools.read_log({"mønster": "ws2812|benkelys"}, lang="en")
    assert "ws2812_stress_test" in out
    assert "benkelys_benkelys" in out


def test_alternation_in_one_file(logs):
    out = shared_tools.read_log({"fil": "ha_events.log", "mønster": "ws2812|benkelys"}, lang="en")
    assert "ws2812_stress_test" in out
    assert "benkelys_benkelys" in out


def test_plain_pattern_still_works_and_no_hit_is_reported(logs):
    assert "benkelys_benkelys" in shared_tools.read_log({"mønster": "benkelys"}, lang="en")
    assert "No matches" in shared_tools.read_log({"mønster": "does_not_exist_xyz"}, lang="en")


def test_bad_regex_is_reported_not_crashing(logs):
    out = shared_tools.read_log({"mønster": "a(b"}, lang="en")
    assert isinstance(out, str) and out


@pytest.mark.parametrize(
    "cmd",
    [
        "grep -E 'ws2812|benkelys' /kaare/logs/ha_events.log",
        'grep -rn -E "a|b" /kaare/logs',
        "grep -E 'a|b' /kaare/logs/x.log | head -5",
    ],
)
def test_guard_allows_quoted_alternation(cmd):
    assert readonly_violation(cmd) is None, readonly_violation(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "grep -E 'a|b' f | rm x",  # a real pipe into a writer stays forbidden
        "grep 'a' f | sh",
        "grep -E 'a|b' f > out.txt",
        "grep -E 'a|b' f; rm x",
        "grep 'a|b f",  # unbalanced quote
    ],
)
def test_guard_still_rejects_writes_around_quoted_pipe(cmd):
    assert readonly_violation(cmd) is not None
