"""P150 phase 5 — raw logs and Argus are independent (2026-11-13: «rålogg filen og argus burde være uavhengige …
argus kan bruke det, men ikke avhengig som i dag»).

The raw event streams (ha_events, frigate_mqtt) are written as day segments in logs/raw/<stream>/; Argus keeps its own
position per segment; retention deletes whole old segments (never one in use, never in the middle of a file), so neither
Argus nor Kåre has to stop; readers walk segments through one helper. The old single file is read as the oldest segment
(«legacy») until it is split. Never the house: everything under tmp_path.
"""

import inspect
import json
import os
import time
from datetime import date

import pytest

import kaare_night_sequence as ns
from kaare_core.storage import budget, limits, rawlog

KB = 1024


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(rawlog, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(rawlog, "LEGACY", {"ha_events": tmp_path / "ha_events.log",
                                           "frigate_mqtt": tmp_path / "frigate_mqtt.log"})
    monkeypatch.setattr(rawlog, "_today", lambda: date(2026, 11, 14))
    rawlog.reset_cache()
    return tmp_path


def _seg(raw, stream, name, lines, age_s=0):
    p = raw / "raw" / stream / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"ts": f"{name[:10]}T10:{i:02d}:00+00:00", "i": i}) + "\n" for i in lines),
                 encoding="utf-8")
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


# ── where writers write ───────────────────────────────────────────────────────

def test_writers_write_into_todays_segment(raw):
    p = rawlog.current_segment("ha_events")
    assert p == raw / "raw" / "ha_events" / "2026-11-14.log"


def test_a_full_segment_rolls_to_the_next_part(raw, monkeypatch):
    monkeypatch.setattr(rawlog, "SEGMENT_MAX_BYTES", 1 * KB)
    _seg(raw, "ha_events", "2026-11-14.log", range(40))
    rawlog.reset_cache()
    assert rawlog.current_segment("ha_events").name == "2026-11-14.1.log"


def test_segments_are_in_time_order_with_legacy_first(raw):
    (raw / "ha_events.log").write_text('{"ts": "2026-09-13T00:00:00+00:00"}\n', encoding="utf-8")
    _seg(raw, "ha_events", "2026-11-14.log", [1])
    _seg(raw, "ha_events", "2026-11-13.1.log", [1])
    _seg(raw, "ha_events", "2026-11-13.log", [1])
    names = [n for n, _p in rawlog.segments("ha_events")]
    assert names == ["legacy", "2026-11-13.log", "2026-11-13.1.log", "2026-11-14.log"]


# ── Argus' reading position ───────────────────────────────────────────────────

def test_reader_walks_segments_and_never_rereads(raw):
    _seg(raw, "ha_events", "2026-11-13.log", range(3), age_s=600)
    _seg(raw, "ha_events", "2026-11-14.log", range(2))
    lines, st, gaps = rawlog.read_new("ha_events", {})
    assert len(lines) == 5 and st["segment"] == "2026-11-14.log" and not gaps
    again, st2, _ = rawlog.read_new("ha_events", st)
    assert again == [] and st2 == st


def test_reader_waits_on_a_segment_that_may_still_get_lines(raw):
    _seg(raw, "ha_events", "2026-11-13.log", range(3))  # just written: a writer may still append
    _seg(raw, "ha_events", "2026-11-14.log", range(2))
    lines, st, _ = rawlog.read_new("ha_events", {})
    assert len(lines) == 3 and st["segment"] == "2026-11-13.log", "stays until the old segment is quiet"


def test_a_deleted_segment_is_a_gap_not_a_reread(raw):
    _seg(raw, "ha_events", "2026-11-14.log", range(2))
    lines, _st, gaps = rawlog.read_new("ha_events", {"segment": "2026-11-10.log", "offset": 500})
    assert len(lines) == 2 and gaps == ["2026-11-10.log"]


def test_old_single_file_position_carries_over(raw):
    legacy = raw / "ha_events.log"
    legacy.write_text('{"ts": "2026-11-13T09:00:00+00:00"}\n{"ts": "2026-11-13T09:01:00+00:00"}\n', encoding="utf-8")
    t = time.time() - 600
    os.utime(legacy, (t, t))
    first_line = len(legacy.read_text(encoding="utf-8").splitlines()[0]) + 1
    _seg(raw, "ha_events", "2026-11-14.log", [7])
    old_state = {"file_id": str(legacy.stat().st_ino), "offset": first_line}  # Argus' pre-P150 state of the file
    lines, st, _ = rawlog.read_new("ha_events", old_state)
    assert [json.loads(x)["ts"][11:16] for x in lines] == ["09:01", "10:07"]
    assert st["segment"] == "2026-11-14.log"


# ── retention: whole old segments, oldest first ───────────────────────────────

def test_prune_deletes_oldest_whole_segments_never_today(raw):
    for d in ("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-14"):
        _seg(raw, "ha_events", f"{d}.log", range(100))
    one = (raw / "raw" / "ha_events" / "2026-11-10.log").stat().st_size
    gone = rawlog.prune("ha_events", 2 * one, min_keep_days=0)
    assert gone == ["2026-11-10.log", "2026-11-11.log"]
    assert rawlog.prune("ha_events", 1, min_keep_days=0) == ["2026-11-12.log"], "today's segment is never deleted"


def test_prune_keeps_a_floor_of_days_for_readers(raw):
    for d in ("2026-11-01", "2026-11-13"):
        _seg(raw, "frigate_mqtt", f"{d}.log", range(100))
    assert rawlog.prune("frigate_mqtt", 1, min_keep_days=7) == ["2026-11-01.log"]


def test_prune_zero_means_off(raw):
    _seg(raw, "ha_events", "2026-11-10.log", range(100))
    assert rawlog.prune("ha_events", 0, min_keep_days=0) == []


def test_legacy_is_split_into_day_segments_once_argus_is_past_it(raw):
    legacy = raw / "ha_events.log"
    legacy.write_text("".join(json.dumps({"ts": f"2026-11-{d:02d}T10:00:00+00:00", "d": d}) + "\n"
                              for d in (11, 11, 12, 13)), encoding="utf-8")
    t = time.time() - 7200
    os.utime(legacy, (t, t))
    _seg(raw, "ha_events", "2026-11-14.log", [1])
    assert rawlog.split_legacy("ha_events", reader_done=False) is False, "not while Argus may still read it"
    assert rawlog.split_legacy("ha_events", reader_done=True) is True
    assert not legacy.exists()
    names = [n for n, _p in rawlog.segments("ha_events")]
    assert names == ["2026-11-11.legacy.log", "2026-11-12.legacy.log", "2026-11-13.legacy.log", "2026-11-14.log"]
    assert len((raw / "raw" / "ha_events" / "2026-11-11.legacy.log").read_text().splitlines()) == 2


# ── readers ───────────────────────────────────────────────────────────────────

def test_tail_bytes_spans_segments(raw):
    _seg(raw, "frigate_mqtt", "2026-11-13.log", range(5))
    _seg(raw, "frigate_mqtt", "2026-11-14.log", range(2))
    text = rawlog.tail_text("frigate_mqtt", 10 * KB)
    assert text.count("\n") == 7 and text.splitlines()[-1].endswith('"i": 1}')


def test_lines_since_skips_old_segments_by_name(raw):
    _seg(raw, "frigate_mqtt", "2026-10-01.log", range(3))
    _seg(raw, "frigate_mqtt", "2026-10-31.log", range(4))  # the day before the cut: read (time-zone slack), lines filtered by caller
    _seg(raw, "frigate_mqtt", "2026-11-13.log", range(2))
    got = list(rawlog.lines_since("frigate_mqtt", "2026-11-01T00:00:00+00:00"))
    assert len(got) == 6


def test_tailer_starts_at_the_end_and_follows(raw):
    p = _seg(raw, "ha_events", "2026-11-14.log", range(3))
    tail = rawlog.Tail("ha_events")
    assert tail.read_new() == []
    with p.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-11-14T11:00:00+00:00"}\n')
    assert len(tail.read_new()) == 1


# ── the night step (2026-11-14, two yes: house 3584 MB / 500 MB) ────────

def _argus_state(raw, ha_segment):
    p = raw / "argus_state.json"
    p.write_text(json.dumps({"files": {str(rawlog.LEGACY["ha_events"]): {"segment": ha_segment, "offset": 0},
                                       str(rawlog.LEGACY["frigate_mqtt"]): {"segment": "2026-11-14.log", "offset": 0}}}),
                 encoding="utf-8")
    return p


def test_reader_past_legacy_reads_argus_state(raw, monkeypatch):
    monkeypatch.setattr(rawlog, "ARGUS_STATE", _argus_state(raw, "legacy"))
    assert rawlog.reader_past_legacy("ha_events") is False
    assert rawlog.reader_past_legacy("frigate_mqtt") is True
    monkeypatch.setattr(rawlog, "ARGUS_STATE", raw / "missing.json")
    assert rawlog.reader_past_legacy("ha_events") is False, "unknown → never split"


def test_night_step_splits_then_prunes_by_the_gui_limits(raw, monkeypatch):
    legacy = raw / "ha_events.log"
    legacy.write_text("".join(json.dumps({"ts": f"2026-11-{d:02d}T10:00:00+00:00", "pad": "x" * 2000}) + "\n"
                              for d in (1, 2, 3, 4)), encoding="utf-8")
    t = time.time() - 7200
    os.utime(legacy, (t, t))
    _seg(raw, "ha_events", "2026-11-14.log", [1])
    monkeypatch.setattr(rawlog, "ARGUS_STATE", _argus_state(raw, "2026-11-14.log"))
    one_day = len(legacy.read_text().splitlines()[0]) + 1
    monkeypatch.setattr(limits, "get_bytes", lambda key: (2 * one_day + 200) if key == "raw_ha_events_mb" else 0)
    rep = rawlog.run_retention()
    assert rep["ha_events"]["split"] is True
    assert rep["ha_events"]["deleted"] == ["2026-11-01.legacy.log", "2026-11-02.legacy.log"]
    assert [n for n, _p in rawlog.segments("ha_events")] == ["2026-11-03.legacy.log", "2026-11-04.legacy.log",
                                                              "2026-11-14.log"]
    assert rep["frigate_mqtt"]["deleted"] == []


def test_night_step_never_splits_while_argus_reads_the_old_file(raw, monkeypatch):
    legacy = raw / "ha_events.log"
    legacy.write_text('{"ts": "2026-11-01T10:00:00+00:00"}\n', encoding="utf-8")
    t = time.time() - 7200
    os.utime(legacy, (t, t))
    monkeypatch.setattr(rawlog, "ARGUS_STATE", _argus_state(raw, "legacy"))
    monkeypatch.setattr(limits, "get_bytes", lambda key: 1)
    rep = rawlog.run_retention()
    assert rep["ha_events"]["split"] is False and legacy.exists()


def test_frigate_keeps_the_normalcy_window(raw, monkeypatch):
    for d in ("2026-10-01", "2026-11-01", "2026-11-13"):
        _seg(raw, "frigate_mqtt", f"{d}.log", range(50))
    monkeypatch.setattr(rawlog, "ARGUS_STATE", raw / "none.json")
    monkeypatch.setattr(limits, "get_bytes", lambda key: 1)
    rep = rawlog.run_retention()
    assert rep["frigate_mqtt"]["deleted"] == ["2026-10-01.log"], "2026-11-01 is inside the 28-day floor"


def test_night_sequence_runs_the_raw_log_step():
    assert '"raw_log_retention"' in inspect.getsource(ns.run_sequence)


def test_budget_has_the_two_raw_streams_and_logs_without_them(raw, monkeypatch, tmp_path):
    logs = tmp_path / "logs_top"
    logs.mkdir()
    (logs / "llm_calls.log").write_bytes(b"x" * KB)
    monkeypatch.setattr(budget, "LOGS_DIR", logs)
    _seg(raw, "ha_events", "2026-11-14.log", range(10))
    rows = {c["key"]: c for c in budget.report()["classes"]}
    assert rows["raw_ha_events"]["controlled"] and rows["raw_frigate_mqtt"]["controlled"]
    assert rows["logs"]["used_mb"] == pytest.approx(KB / 1_048_576, abs=0.001)
