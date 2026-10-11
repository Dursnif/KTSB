"""P119 Phase 4 — the GUI follows the night meetings (B5) and the reflection report holds no
raw user activity (B10). No systemd, no model: the night lock check, the log file and the meeting
tool are faked."""

import asyncio

import pytest

import kaare_reflection as refl
from kaare_core.routers import router_meetings as rm
from kaare_core.tools.i18n import t

_LOG = """2026-10-04 03:00:14,177  INFO   night_sequence  ====== Night sequence starting ======
2026-10-04 03:00:46,705  INFO   night_sequence  === [reflection] starting (timeout=5400s) ===
2026-10-04 03:01:25,253  INFO   httpx  HTTP Request: POST http://127.0.0.1:11440/v1/chat/completions
2026-10-04 03:01:25,253  INFO   reflection  --- Round 2/6 (group 1, local 2) ---
"""
_LOG_DEV = (
    _LOG
    + """2026-10-04 03:36:48,092  INFO   night_sequence  === [reflection] done in 2161s ===
2026-10-04 03:36:50,000  INFO   night_sequence  === [dev_slot] starting (timeout=3600s) ===
2026-10-04 03:37:00,000  INFO   dev_slot  [DEV_SLOT] step 3/7 S1
"""
)
_LOG_DONE = (
    _LOG_DEV + "2026-10-04 03:50:00,000  INFO   night_sequence  === [dev_slot] done in 790s ===\n"
)


@pytest.fixture
def night(tmp_path, monkeypatch):
    log = tmp_path / "night_sequence.log"
    monkeypatch.setattr(rm, "_NIGHT_LOG", log)
    for key in ("reflection", "dev"):
        monkeypatch.setitem(
            rm._MEETING_STATUS,
            key,
            {
                "running": False,
                "progress": 0,
                "round": 0,
                "max_rounds": 6,
                "step": "",
                "step_code": "",
                "log": [],
                "started_at": None,
                "source": None,
            },
        )

    def set_state(text: str, unit: str = "active"):
        log.write_text(text, encoding="utf-8")
        # N17: «running» comes from the night sequence's lock, not systemctl
        monkeypatch.setattr(rm, "is_sequence_running", lambda: unit == "active")
        active, step, lines = rm._night_sequence_state()
        rm._apply_night_status(active, step, lines)
        return step

    return set_state


def test_reflection_running(night):
    assert night(_LOG) == "reflection"
    st = rm._MEETING_STATUS["reflection"]
    assert st["running"] and st["source"] == "timer"
    assert (st["round"], st["max_rounds"]) == (2, 6)
    assert not any(" httpx " in ln for ln in st["log"])
    assert not rm._MEETING_STATUS["dev"]["running"]


def test_dev_slot_step(night):
    night(_LOG)
    assert night(_LOG_DEV) == "dev_slot"
    dev = rm._MEETING_STATUS["dev"]
    assert (
        dev["running"] and dev["step_code"] == "S1" and (dev["round"], dev["max_rounds"]) == (3, 7)
    )
    assert not rm._MEETING_STATUS["reflection"]["running"]  # reflection step finished


def test_idle_after_done_and_when_unit_inactive(night):
    night(_LOG_DEV)
    assert night(_LOG_DONE) is None
    assert not rm._MEETING_STATUS["dev"]["running"]
    night(_LOG)
    assert night(_LOG, unit="inactive") is None
    assert not rm._MEETING_STATUS["reflection"]["running"]


def test_failed_step_counts_as_finished(night):
    night(_LOG_DEV)
    failed = (
        _LOG_DEV
        + "2026-10-04 03:40:00,000  ERROR  night_sequence  === [dev_slot] FAILED after 190s: boom ===\n"
    )
    assert night(failed) is None
    assert not rm._MEETING_STATUS["dev"]["running"]


def test_manual_run_is_not_overwritten(night):
    rm._MEETING_STATUS["dev"].update({"running": True, "source": "manual", "step": "manual"})
    night(_LOG_DONE)
    assert rm._MEETING_STATUS["dev"]["step"] == "manual"


def test_tail_reads_only_the_end(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("".join(f"line {i}\n" for i in range(50000)), encoding="utf-8")
    lines = rm._tail_lines(p, max_bytes=1000)
    assert lines[-1] == "line 49999" and len(lines) < 200
    assert all(ln.startswith("line ") for ln in lines)  # no partial first line


@pytest.mark.parametrize(
    "line,expected",
    [
        ("--- Round 3/6 (group 1, local 3) ---", (3, 6)),
        ("Runde 2/4", (2, 4)),
        ("meeting leader round 1/4", None),
    ],
)
def test_parse_meeting_round(line, expected):
    assert rm._parse_meeting_round(line) == expected


# ── B10 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_activity_not_in_report_and_fetched_once(monkeypatch, lang):
    activity = "\n\n".join(
        [
            t("refl_act_interactions_header", lang),
            t("refl_act_interaction", lang, n=1, topics="a") + "\nSECRET-1",
            t("refl_act_interaction", lang, n=2, topics="b") + "\nSECRET-2",
        ]
    )
    calls = []

    async def fake_tool(name, args):
        calls.append(name)
        return activity

    monkeypatch.setattr(refl, "_execute_leder_tool", fake_tool)
    monkeypatch.setattr(refl, "_get_kare_language_r", lambda: lang)
    exchanges, kare, miss = [], [], []
    for _ in range(2):
        asyncio.run(refl._handle_leder_action("hent_aktivitet", "", kare, miss, exchanges))

    assert calls == ["hent_bruker_aktivitet"]  # fetched once
    assert not any("SECRET" in text for _, text in exchanges)  # nothing raw in the report
    assert (
        refl._ACTIVITY_SPEAKER,
        t("refl_activity_report_line", lang, episodes=2, chars=len(activity)),
    ) in exchanges
    assert "SECRET-1" in kare[0]["content"] and "SECRET-1" in miss[0]["content"]
    assert kare[1]["content"] == t("refl_leder_activity_already", lang)
