"""
P118 B5 — switch-over: night sequence step 4 = dev slot, GUI start/progress, settings API.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b5_switch.py -v
"""

import asyncio
import inspect
import sys

import pytest
import yaml

sys.path.insert(0, "/kaare")

from fastapi import HTTPException

import kaare_core.dev_slot.run as slot_run
import kaare_core.dev_slot.steps as steps
import kaare_core.memory.learning_store as ls
import kaare_core.routers.router_meetings as rm
import kaare_core.routers.router_settings as rs
import kaare_night_sequence as ns


def test_night_sequence_runs_dev_slot_after_reflection():
    src = inspect.getsource(ns.run_sequence)
    assert "dev_slot_main()" in src and "dev_meeting_main" not in src
    assert src.index('"reflection"') < src.index('"dev_slot"'), (
        "dev slot only after the reflection step"
    )
    assert ns.dev_slot_main is slot_run.main


def test_gui_start_spawns_dev_slot():
    src = inspect.getsource(rm.api_dev_meetings_start)
    assert '"kaare_core.dev_slot.run"' in src and '"/kaare/kaare_dev_meeting.py"' not in src


@pytest.mark.parametrize(
    "line,expected",
    [
        ("[DEV_SLOT] step 3/6 S1", (3, 6, "S1")),
        ("2026 INFO [DEV_SLOT] step 2/6 janus", (2, 6, "janus")),
        ("Runde 2/6", None),
    ],
)
def test_progress_parser(line, expected):
    assert rm._parse_dev_slot_step(line) == expected


def test_stream_sets_step_code():
    class FakeProc:
        returncode = 0

        def __init__(self, lines):
            async def gen():
                for ln in lines:
                    yield (ln + "\n").encode()

            self.stdout = gen()

        async def wait(self):
            return 0

    st = {"log": [], "running": True}
    rm._MEETING_STATUS["test"] = st
    asyncio.run(
        rm._stream_meeting_proc(
            "test", FakeProc(["[DEV_SLOT] step 5/6 S3", "[DEV_SLOT] done — report x"])
        )
    )
    assert st["step_code"] == "S3" and st["round"] == 5 and st["max_rounds"] == 6
    assert st["progress"] == 100 and st["running"] is False
    del rm._MEETING_STATUS["test"]


def test_run_main_reports_every_step_and_finishes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")

    async def fake_collect(run_id, **kw):
        ls.start_run(run_id, "S0")
        return {"inbox": {"topics": []}, "traces": {"items": []}}

    async def fake_b3(bundle, *, on_step, **kw):
        for code in ("janus", "S1", "S2", "S3"):
            on_step(code)
        return {"s1": [], "s2": [], "s3": []}

    async def fake_b4(b3, *, on_step, report_dir=None, **kw):
        on_step("S5")
        p = tmp_path / "2026-10-04.md"
        p.write_text("report")
        return p

    monkeypatch.setattr(slot_run.collect, "collect", fake_collect)
    monkeypatch.setattr(slot_run.steps, "run_b3", fake_b3)
    monkeypatch.setattr(slot_run.steps, "run_b4", fake_b4)

    async def fake_s6(janus_call, **kw):
        return {"written": [], "retired": 0}

    monkeypatch.setattr(slot_run.consolidate, "run_s6", fake_s6)
    path = asyncio.run(slot_run.main())
    out = capsys.readouterr().out
    assert [ln.split()[2] for ln in out.splitlines() if " step " in ln] == [
        "1/7",
        "2/7",
        "3/7",
        "4/7",
        "5/7",
        "6/7",
        "7/7",
    ]
    assert path.name == "2026-10-04.md" and ls.last_finished_run_start() is not None


def test_run_main_failure_is_reported_not_raised(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")

    async def boom(run_id, **kw):
        raise RuntimeError("collect broke")

    monkeypatch.setattr(slot_run.collect, "collect", boom)
    assert asyncio.run(slot_run.main()) is None
    assert "[DEV_SLOT] failed: collect broke" in capsys.readouterr().out


# ── settings API ────────────────────────────────────────────────────────────


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.dump({"dev_meeting": {"max_rounds": 6}, "other": {"keep": 1}}))
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    return p


def test_get_dev_settings_defaults(settings_file):
    d = asyncio.run(rs.api_get_dev_meeting(_u=None))
    assert d["enabled"] is True
    # B8-D4: budget 25 min is the ceiling, 3 topics per meeting.
    assert (
        d["time_budget_minutes"],
        d["topics_per_meeting"],
        d["max_user_traces"],
        d["round_cap_per_topic"],
    ) == (25, 3, 20, 4)


def test_put_dev_settings_writes_both_blocks(settings_file):
    asyncio.run(
        rs.api_put_dev_meeting(
            {
                "enabled": False,
                "time_budget_minutes": 20,
                "max_user_traces": 0,
                "round_cap_per_topic": 3,
            },
            _u=None,
        )
    )
    data = yaml.safe_load(settings_file.read_text())
    assert data["dev_meeting"]["enabled"] is False and data["dev_meeting"]["max_rounds"] == 6
    assert data["dev_slot"] == {
        "time_budget_minutes": 20,
        "max_user_traces": 0,
        "round_cap_per_topic": 3,
    }
    assert data["other"] == {"keep": 1}
    d = asyncio.run(rs.api_get_dev_meeting(_u=None))
    assert d["enabled"] is False and d["max_user_traces"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"time_budget_minutes": 0},
        {"round_cap_per_topic": 99},
        {"max_user_traces": "lots"},
        {"bogus": 1},
    ],
)
def test_put_dev_settings_rejects_bad_values(settings_file, payload):
    with pytest.raises(HTTPException):
        asyncio.run(rs.api_put_dev_meeting(payload, _u=None))


def test_report_is_never_overwritten(tmp_path):
    (tmp_path / "2026-10-03.md").write_text("old meeting")
    p1 = steps._free_report_path(tmp_path, "2026-10-03")
    assert p1.name == "2026-10-03-2.md"
    p1.write_text("slot")
    assert steps._free_report_path(tmp_path, "2026-10-03").name == "2026-10-03-3.md"
    assert (tmp_path / "2026-10-03.md").read_text() == "old meeting"
    assert steps._free_report_path(tmp_path, "2026-10-04").name == "2026-10-04.md"


def test_gui_can_open_suffixed_report(monkeypatch, tmp_path):
    with pytest.raises(HTTPException) as e:
        rm.api_dev_meeting_get("2026-10-03-2")  # format accepted → 404 (file absent), not 400
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        rm.api_dev_meeting_get("../../etc/passwd")
    assert e.value.status_code == 400
