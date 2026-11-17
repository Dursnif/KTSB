"""Background saves write one receipt line per batch to logs/receipts.log under the job's rid (main session 2026-11-12 (3)):
weights.db flushes, frigate.db events, knowledge.db add/update/retire, the night's STM summaries (and LTM episodes, Argus →
Qdrant, same helper). Only type, place, ids and a count — never content. One line per batch, not one per row.
No live state: log_buffer is captured, every DB is in tmp_path. Placeholders only."""

import json
import sqlite3

import pytest

import kaare_core.memory.knowledge_store as ks
from adapters import llm_adapter
from kaare_core import receipts, weights
from kaare_core.memory import long_term
from kaare_core.weights import store as wstore


@pytest.fixture
def lines(monkeypatch):
    out = []
    monkeypatch.setattr(receipts.log_buffer, "write", lambda path, line: out.append((path, line)))
    return out


def test_switched_on():
    assert receipts.BACKGROUND_ON is True


def test_weights_flush_is_one_line_per_batch(lines, tmp_path, monkeypatch):
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    for i in range(7):
        weights.record(("belief", f"b{i}"), source_class="self", pointer=f"p{i}")
    weights.flush()
    mine = [ln for p, ln in lines if ln["kind"] == "weights_flush"]
    assert len(mine) == 1 and mine[0]["n"] == 7 and mine[0]["refs"] == ["evidence:7"]
    assert all(p == receipts.RECEIPTS_LOG for p, _ in lines)
    wstore.reset_for_tests()


def test_knowledge_writes_carry_ids_not_content(lines, tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    fid = ks.add_fact(domain=ks.DOMAINS[0], content="hemmelig 4711")
    ks.update_fact(fid, content="fortsatt hemmelig 4711")
    ks.retire_fact(fid)
    kinds = [ln["kind"] for _, ln in lines]
    assert kinds == ["knowledge_add", "knowledge_update", "knowledge_retire"]
    assert all(ln["refs"] == [str(fid)] for _, ln in lines)
    assert "4711" not in json.dumps([ln for _, ln in lines])


def test_rid_comes_from_the_running_job(lines, tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)
    tok = llm_adapter._current_rid.set("rid-night-nightjob-1")
    try:
        ks.add_fact(domain=ks.DOMAINS[0], content="x")
    finally:
        llm_adapter._current_rid.reset(tok)
    assert lines[0][1]["rid"] == "rid-night-nightjob-1"


def test_stm_daily_summary(lines, tmp_path, monkeypatch):
    monkeypatch.setattr(long_term, "DB_PATH", tmp_path / "interactions.db", raising=False)
    monkeypatch.setattr(long_term, "_enc", lambda uid, text, allow_system_key=False: text)
    if hasattr(long_term, "_get_conn"):
        def conn():
            c = sqlite3.connect(tmp_path / "interactions.db")
            c.execute("CREATE TABLE IF NOT EXISTS stm_daily_summary (date TEXT, user_id TEXT, summary TEXT, "
                      "interaction_count INT, ts_created TEXT, PRIMARY KEY (date, user_id))")
            return c
        monkeypatch.setattr(long_term, "_get_conn", conn)
    long_term.save_daily_summary("2026-11-12", "dagens hemmelige sammendrag", 3, user_id="user_x")
    mine = [ln for _, ln in lines if ln["kind"] == "stm_daily_summary"]
    assert mine and mine[0]["refs"] == ["user_x:2026-11-12"] and "hemmelige" not in json.dumps(mine)


# ── streamed kinds are coalesced (main session: «én linje per batch, ikke én per punkt») ──

def test_camera_and_argus_receipts_are_coalesced(lines, monkeypatch):
    assert {"frigate_event", "argus_index"} <= receipts.COALESCE_KINDS
    monkeypatch.setattr(receipts, "_pending", {})
    for i in range(120):
        receipts.log_saved("frigate_event", where="frigate.db", refs=[f"ev{i}"], rid="")
    full = [ln for _, ln in lines if ln["kind"] == "frigate_event"]
    assert [ln["n"] for ln in full] == [50, 50]  # two full batches written, 20 still in RAM
    assert receipts.flush_pending() == 1
    full = [ln for _, ln in lines if ln["kind"] == "frigate_event"]
    assert [ln["n"] for ln in full] == [50, 50, 20] and full[2]["refs"][-1] == "ev119"


def test_old_pending_batch_is_written_on_the_next_save(lines, monkeypatch):
    monkeypatch.setattr(receipts, "_pending", {})
    receipts.log_saved("argus_index", where="qdrant/argus_events", n=3, rid="argus")
    assert not [ln for _, ln in lines if ln["kind"] == "argus_index"]
    monkeypatch.setattr(receipts, "COALESCE_S", 0.0)
    receipts.log_saved("argus_index", where="qdrant/argus_events", n=4, rid="argus")
    assert [ln["n"] for _, ln in lines if ln["kind"] == "argus_index"] == [7]
