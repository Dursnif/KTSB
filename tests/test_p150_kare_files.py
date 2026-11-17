"""P150 phase 4 — Kåre's own files are kept by SIZE, not by age (2026-11-13, two yes).

think_cache (was 150 lines + 30 days) · inner thoughts (was 24 h) · STM history (was 7 days) · observations (was 90 days,
hardcoded) each get an MB limit in Settings → Logs/Storage (settings.yaml storage_limits.*, 0 = off); oldest first.
Portraits lose their 90-day rule but keep their 3000-character cap (the whole portrait goes into Miss Kåre's prompt).
Readers of the bigger files read from the end, never the whole file (IO, rule 10). Never the house: tmp files only.
"""

import inspect
import json
import time
from pathlib import Path

import pytest
import yaml

import kaare_miss_kare_nightjob as mk
import kaare_night_sequence as ns
from kaare_core import config, inner_voice_runner, reflection_loop
from kaare_core.memory import long_term
from kaare_core.storage import budget, limits, rotation, stm_history
from kaare_core.tools import think_cache
from kaare_core.users import profile_manager

KB = 1024


# ── the shared helpers ────────────────────────────────────────────────────────

def test_lines_are_read_newest_first_without_reading_everything(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("".join(f'{{"i": {i}}}\n' for i in range(10_000)), encoding="utf-8")
    got = [json.loads(line)["i"] for line in rotation.iter_lines_reverse(p, chunk=512)][:3]
    assert got == [9999, 9998, 9997]
    scanned = list(rotation.iter_lines_reverse(p, chunk=512, max_bytes=2 * KB))
    assert 0 < len(scanned) < 300, "a scan cap stops early"


def test_trim_jsonl_keeps_the_newest_lines_under_the_limit(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("".join(f'{{"i": {i}, "pad": "{"x" * 90}"}}\n' for i in range(1000)), encoding="utf-8")
    removed = rotation.trim_lines_to_bytes(p, 20 * KB)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert removed > 0 and p.stat().st_size <= 20 * KB
    assert json.loads(lines[-1])["i"] == 999 and json.loads(lines[0])["i"] > 0
    assert rotation.trim_lines_to_bytes(p, 20 * KB) == 0, "under the limit: nothing"


def test_trim_blocks_keeps_newest_and_always_the_last():
    blocks = [f"[{i}] " + "x" * 100 for i in range(10)]
    kept = rotation.trim_blocks(blocks, 350)
    assert kept == blocks[-3:]
    assert rotation.trim_blocks(blocks, 10) == blocks[-1:]


def test_dated_sections_drop_oldest_keep_preamble():
    text = "Top facts\n---\n" + "".join(f"## 2026-0{m}-01\nline {m}\n" + "y" * 200 + "\n" for m in range(1, 10))
    out = rotation.trim_dated_sections(text, 800)
    assert out.startswith("Top facts\n---\n") and "## 2026-09-01" in out and "## 2026-01-01" not in out
    assert len(out.encode()) <= 800
    assert rotation.trim_dated_sections(text, 0) == text, "0 = off"


def test_prune_dirs_oldest_first_only_with_a_summary_never_the_newest(tmp_path):
    for d in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "u.json").write_bytes(b"x" * 10 * KB)
    gone = rotation.prune_dirs_oldest(tmp_path, 15 * KB, can_delete=lambda name: name != "2026-01-01")
    assert gone == ["2026-01-02", "2026-01-03"], "01-01 has no summary → kept; the newest is never pruned"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-01-01", "2026-01-04"]


# ── the GUI-written limits ────────────────────────────────────────────────────

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    (tmp_path / "settings.yaml").write_text(yaml.dump({"storage_limits": {"think_cache_mb": 3}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def test_limit_defaults_are_the_light_ones(cfg):
    assert limits.get_mb("think_cache_mb") == 3
    assert limits.get_mb("inner_thoughts_mb") == 10
    assert limits.get_mb("stm_history_mb") == 50
    assert limits.get_mb("observations_mb") == 5
    limits.save_mb("stm_history_mb", 500)
    assert limits.get_mb("stm_history_mb") == 500
    with pytest.raises(ValueError):
        limits.save_mb("stm_history_mb", -1)


def test_budget_has_the_four_controls_here(cfg):
    rows = {c["key"]: c for c in budget.report()["classes"]}
    for key in ("think_cache", "inner_thoughts", "stm_history", "observations"):
        assert rows[key]["controlled"] and rows[key]["home"] == ""
    budget.set_class_limit("think_cache", 7)
    assert limits.get_mb("think_cache_mb") == 7


# ── think_cache ───────────────────────────────────────────────────────────────

@pytest.fixture
def tc(tmp_path, monkeypatch):
    path = tmp_path / "think_cache.jsonl"
    monkeypatch.setattr(think_cache, "_CACHE_PATH", path)
    return path


def test_think_cache_keeps_more_than_150_entries_under_its_limit(tc, monkeypatch):
    monkeypatch.setattr(limits, "get_mb", lambda key: 20)
    tc.write_text("".join(json.dumps({"id": str(i), "think_text": "t"}) + "\n" for i in range(400)), encoding="utf-8")
    think_cache._trim_cache()
    assert len(tc.read_text(encoding="utf-8").splitlines()) == 400, "the old 150-line cap is gone"


def test_think_cache_over_its_limit_drops_the_oldest(tc, monkeypatch):
    monkeypatch.setattr(think_cache, "_limit_bytes", lambda: 8 * KB)
    tc.write_text("".join(json.dumps({"id": str(i), "think_text": "t" * 100}) + "\n" for i in range(400)),
                  encoding="utf-8")
    think_cache._trim_cache()
    lines = tc.read_text(encoding="utf-8").splitlines()
    assert tc.stat().st_size <= 8 * KB and json.loads(lines[-1])["id"] == "399"


def test_think_history_reads_from_the_end(tc, monkeypatch):
    tc.write_text("".join(json.dumps({"id": str(i), "think_text": f"t{i}", "role": "kare"}) + "\n"
                          for i in range(5000)), encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("must not read the whole file"))
    got = think_cache.read_think_history(n=3)
    assert [e["id"] for e in got] == ["4999", "4998", "4997"]


def test_night_sequence_no_longer_deletes_think_cache_by_days():
    src = inspect.getsource(ns._run_retention_cleanup)
    assert "think_cache_retention_days" not in src


# ── inner thoughts ────────────────────────────────────────────────────────────

def test_inner_thoughts_older_than_24h_are_kept_under_the_limit(tmp_path, monkeypatch):
    path = tmp_path / "inner_thoughts.txt"
    monkeypatch.setattr(inner_voice_runner, "INNER_THOUGHTS", path)
    path.write_text("[Jang 2026-01-01 10:00]\nold thought\n", encoding="utf-8")
    inner_voice_runner.roll_inner("new thought", 10 * 1_048_576, now=time.time())
    text = path.read_text(encoding="utf-8")
    assert "old thought" in text and "new thought" in text


def test_inner_thoughts_over_the_limit_drop_the_oldest(tmp_path, monkeypatch):
    path = tmp_path / "inner_thoughts.txt"
    monkeypatch.setattr(inner_voice_runner, "INNER_THOUGHTS", path)
    path.write_text("".join(f"[Jang 2026-01-{d:02d} 10:00]\n{'x' * 300}\n\n" for d in range(1, 29)), encoding="utf-8")
    inner_voice_runner.roll_inner("new thought", 2 * KB)
    text = path.read_text(encoding="utf-8")
    assert len(text.encode()) <= 2 * KB and "new thought" in text and "2026-01-01" not in text


def test_reflection_reads_only_the_tail_of_inner_thoughts(tmp_path, monkeypatch):
    path = tmp_path / "inner_thoughts.txt"
    path.write_text("".join(f"[Jang 2026-01-{d % 28 + 1:02d} 10:00]\n{'z' * 1000}\n\n" for d in range(2000)),
                    encoding="utf-8")
    text = reflection_loop._read_inner_tail(path)
    assert 0 < len(text.encode()) <= reflection_loop.INNER_READ_MAX_BYTES
    assert text.startswith("[Jang"), "starts at a whole entry"


# ── observations + portrait ───────────────────────────────────────────────────

def test_observations_older_than_90_days_are_kept_under_the_limit(monkeypatch):
    store = {"t": "## 2025-01-01\nvery old\n## 2026-11-01\nnew\n"}
    monkeypatch.setattr(profile_manager, "_read_obs_text", lambda u, allow_system_key=False: store["t"])
    monkeypatch.setattr(profile_manager, "_write_obs_text",
                        lambda u, text, allow_system_key=False: store.__setitem__("t", text))
    monkeypatch.setattr(limits, "get_mb", lambda key: 5)
    profile_manager._trim_observations("someone")
    assert "very old" in store["t"]


def test_observations_over_the_limit_drop_the_oldest(monkeypatch):
    text = "".join(f"## 2026-{m:02d}-01\n{'o' * 500}\n" for m in range(1, 13))
    store = {"t": text}
    monkeypatch.setattr(profile_manager, "_read_obs_text", lambda u, allow_system_key=False: store["t"])
    monkeypatch.setattr(profile_manager, "_write_obs_text",
                        lambda u, text, allow_system_key=False: store.__setitem__("t", text))
    monkeypatch.setattr(profile_manager, "_obs_limit_bytes", lambda: 2 * KB)
    profile_manager._trim_observations("someone")
    assert "## 2026-12-01" in store["t"] and "## 2026-01-01" not in store["t"]


def test_portrait_has_no_day_rule_but_keeps_its_cap():
    src = inspect.getsource(mk)
    assert "_OBS_MAX_DAYS" not in src and "_trim_portrait(" not in src
    assert mk._PORTRAIT_CAP == 3000


def test_profile_manager_has_no_day_rule():
    assert not hasattr(profile_manager, "OBS_MAX_DAYS")


# ── STM history: a day with no conversations has nothing to summarise ────────
# Side session 2026-11-13: the night job skips the daily summary when a day had 0 interactions (2026-11-08/09), and a summary
# is dated the day it was made (it covers the day before). Such a day must still be prunable, or empty days pile up forever.

def test_stm_day_without_conversations_can_go(tmp_path, monkeypatch):

    for d in ("2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "u.json").write_bytes(b"x" * 10 * KB)
    monkeypatch.setattr(limits, "get_bytes", lambda key: 15 * KB)
    monkeypatch.setattr(long_term, "summary_dates", lambda: {"2026-11-08"})  # covers 11-07; 11-08/09 had none
    monkeypatch.setattr(long_term, "skipped_dates", set)  # no live interactions.db
    monkeypatch.setattr(long_term, "interaction_dates", lambda: {"2026-11-07", "2026-11-10"})
    gone = stm_history.prune(tmp_path)
    assert gone == ["2026-11-07", "2026-11-08", "2026-11-09"]


def test_stm_day_with_conversations_but_no_summary_stays(tmp_path, monkeypatch):

    for d in ("2026-11-07", "2026-11-08"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "u.json").write_bytes(b"x" * 10 * KB)
    monkeypatch.setattr(limits, "get_bytes", lambda key: 5 * KB)
    monkeypatch.setattr(long_term, "summary_dates", set)
    monkeypatch.setattr(long_term, "skipped_dates", set)  # no live interactions.db
    monkeypatch.setattr(long_term, "interaction_dates", lambda: {"2026-11-07"})
    assert stm_history.prune(tmp_path) == []


def test_stm_nothing_pruned_when_the_lists_cannot_be_read(tmp_path, monkeypatch):
    for d in ("2026-11-07", "2026-11-08"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "u.json").write_bytes(b"x" * 10 * KB)
    monkeypatch.setattr(limits, "get_bytes", lambda key: 1)
    monkeypatch.setattr(long_term, "summary_dates", lambda: None)
    monkeypatch.setattr(long_term, "skipped_dates", set)  # no live interactions.db
    monkeypatch.setattr(long_term, "interaction_dates", set)
    assert stm_history.prune(tmp_path) == []
