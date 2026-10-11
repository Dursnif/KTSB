"""
Tests for P105 Phase 7 — context budget.

A: dynamic replay window (current session first, token budget, few pairs from before).
B: tool results / images stubbed after Kåre has read them one round (Stian's rule), plus a
   budget guard that stubs oldest-first when the next prompt would not fit.
C: library fetch_article max_chars coerced + clamped (BUG-log 2026-09-21).

Background: Stian's stress test 2026-09-28 (rid-1790596038131) reached 39,770 of 43,008 input
tokens in round 6; only the 6-round cap stopped it.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_context_budget.py -v
"""

import sys

sys.path.insert(0, "/kaare")

from datetime import UTC, datetime, timedelta

from kaare_core.memory.short_term import ShortTermMemory
from kaare_core.routers import router_generate as rg
from kaare_core.tools.executor_library import _article_max_chars

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _memory(turns):
    """turns: list of (minutes_before_now, user_text, assistant_text)."""
    memory = ShortTermMemory(user_id="u", dialog_max_text=5000)
    for minutes, u, a in turns:
        memory.add_dialog(role="user", text=u, user_id="u")
        memory._dialog[-1].ts = (NOW - timedelta(minutes=minutes)).isoformat()
        memory.add_dialog(role="assistant", text=a, user_id="u")
    return memory


def _window(memory, **kw):
    args = {
        "token_budget": 4000,
        "max_pairs": 20,
        "prior_session_pairs": 2,
        "session_gap_minutes": 30,
        "chars_per_token": 3.6,
        "now": NOW,
    }
    args.update(kw)
    return memory.get_dialog_pairs_window("u", **args)


# ── A: replay window ────────────────────────────────────────────────────────────────────


def test_whole_current_session_is_replayed_not_just_four():
    turns = [
        (60 - i * 5, f"q{i}", f"a{i}") for i in range(10)
    ]  # 10 pairs, 5 min apart, last 15 min ago
    included, omitted = _window(_memory(turns))
    assert [p[0] for p in included] == [f"q{i}" for i in range(10)]
    assert omitted == []


def test_only_few_pairs_from_an_earlier_session():
    old = [(3000 + i, f"old{i}", "x") for i in range(5, 0, -1)]  # two days ago
    new = [(10, "new0", "y"), (5, "new1", "y")]
    included, omitted = _window(_memory(old + new))
    # the two newest pairs of the earlier session + the whole current session
    assert [p[0] for p in included] == ["old2", "old1", "new0", "new1"]
    assert [p[0] for p in omitted] == ["old5", "old4", "old3"]


def test_token_budget_cuts_contiguously():
    long = "x" * 3600  # ≈ 1000 tokens per pair (user part alone)
    turns = [(20 - i, f"{i}{long}", "a") for i in range(8)]
    included, omitted = _window(_memory(turns), token_budget=3000)
    assert len(included) == 2  # third would exceed 3000
    assert included[-1][0].startswith("7")  # newest kept
    assert len(omitted) == 6


def test_newest_pair_long_ago_counts_as_prior_session():
    turns = [(600, "a", "b"), (590, "c", "d"), (580, "e", "f")]  # ~10 h ago
    included, _ = _window(_memory(turns))
    assert len(included) == 2  # only prior_session_pairs


# ── B: stubbing ─────────────────────────────────────────────────────────────────────────

CFG = dict(rg._BUDGET_DEFAULTS)


def _tracked(kind, round_, tokens):
    msg = {"role": "tool" if kind == "tool" else "user", "content": "x" * int(tokens * 3.6)}
    if kind == "image":
        msg["images"] = ["base64..."]
    return msg, {
        "kind": kind,
        "round": round_,
        "tool": "library",
        "args": "title=AI",
        "tokens": float(tokens),
        "stubbed": False,
    }


def test_large_result_kept_while_read_then_stubbed():
    big = _tracked("tool", 1, 5000)
    tracked = [big]
    assert rg._stub_after_one_round(tracked, 2, CFG, "nb") == 0  # round 2 reads it
    saved = rg._stub_after_one_round(tracked, 3, CFG, "nb")  # round 3: stub
    assert saved > 4000
    assert "library(title=AI)" in big[0]["content"] and "runde 1" in big[0]["content"]


def test_small_result_is_kept():
    small = _tracked("tool", 1, 300)
    assert rg._stub_after_one_round([small], 5, CFG, "nb") == 0
    assert small[1]["stubbed"] is False


def test_images_always_stubbed_after_one_round():
    img = _tracked("image", 1, 1200)
    rg._stub_after_one_round([img], 3, CFG, "nb")
    assert "images" not in img[0] and img[1]["stubbed"]


def test_guard_stubs_oldest_first_until_it_fits():
    a, b, c = _tracked("tool", 1, 1000), _tracked("tool", 2, 1000), _tracked("tool", 3, 1000)
    saved = rg._stub_under_pressure([a, b, c], estimate=45000, limit=43500, lang="nb", cfg=CFG)
    assert a[1]["stubbed"] and b[1]["stubbed"] and not c[1]["stubbed"]
    assert saved >= 1500


def test_input_caps_from_llm_yaml(monkeypatch):
    # Fixed configs, not the live llm.yaml (its max_model_len changes with the hardware/model)
    roles = {
        "default": {
            "provider": "vllm",
            "options": {"max_tokens": 8192},
            "vllm_docker": {"max_model_len": 51200},
        },
        "fallback": {"options": {"num_ctx": 32768, "num_predict": 1024}},
    }
    monkeypatch.setattr(rg, "get_llm_config", lambda role: roles[role])
    assert rg._input_cap("kare") == 51200 - 8192
    assert rg._input_cap("fallback_9b") == 32768 - 1024

    # Ollama as the main agent: window = num_ctx, output reserve = num_predict (vllm_docker ignored)
    roles["default"] = {
        "provider": "ollama",
        "options": {"num_ctx": 32768, "num_predict": 2048},
        "vllm_docker": {"max_model_len": 51200},
    }
    assert rg._input_cap("kare") == 32768 - 2048


def test_count_today():
    today_ts = datetime.now(UTC).isoformat()
    old_ts = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    assert rg._count_today([("a", "b", today_ts), ("c", "d", old_ts)]) == 1


# ── C: max_chars ────────────────────────────────────────────────────────────────────────


def test_max_chars_text_is_coerced():
    assert _article_max_chars("20000") == 20000
    assert _article_max_chars(12000) == 12000


def test_max_chars_garbage_falls_back_and_huge_is_clamped():
    assert _article_max_chars("lots") == 8000
    assert _article_max_chars(999999) == 20000
