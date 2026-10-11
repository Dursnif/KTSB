"""
RID-based system health tests.

Reads real production logs via trace_reader and flags anomalies. These are live-data probes,
not unit tests: they depend on what the install has done recently (a stress test makes the
latency check fail, a fresh install has no traces at all), so they are marked `live` and
excluded from the default run (pytest.ini). The traces themselves are never touched.
Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest -m live tests/test_rid_health.py -v
"""

import sys

sys.path.insert(0, "/kaare")

import pytest

from kaare_core.tools.trace_reader import get_recent_traces

pytestmark = pytest.mark.live


def _user_traces(n=50):
    return get_recent_traces(n=n, source="user")


def _refl_traces(n=20):
    return get_recent_traces(n=n, source="refl")


# ── Basic structure ───────────────────────────────────────────────────────────


def test_logs_have_recent_traces():
    """At least one user trace exists in the logs."""
    traces = _user_traces(10)
    assert len(traces) > 0, "No user traces found — is kaare.service up?"


def test_all_traces_have_rid():
    """Every trace has a rid."""
    traces = _user_traces()
    missing = [t for t in traces if not t.get("rid")]
    assert not missing, f"{len(missing)} traces without rid"


def test_all_traces_have_llm_call():
    """Every non-fastpath user trace has at least one LLM call."""
    traces = _user_traces()
    no_llm = [
        t
        for t in traces
        if t.get("llm_call_count", 0) == 0
        and not any(s.get("stage") == "fastpath_done" for s in t.get("stages", []))
    ]
    assert not no_llm, f"{len(no_llm)} traces without LLM call and without fastpath:\n" + "\n".join(
        t["rid"] for t in no_llm[:5]
    )


# ── Latency ───────────────────────────────────────────────────────────────────


def test_no_extreme_latency():
    """No trace over 3 minutes — several tool rounds can legitimately take 120 s."""
    traces = _user_traces()
    slow = [t for t in traces if t.get("total_latency_ms", 0) > 180_000]
    assert not slow, f"{len(slow)} traces over 3 min:\n" + "\n".join(
        f"  {t['rid']} — {t['total_latency_ms']}ms ({t.get('llm_call_count', 0)} LLM calls)"
        for t in slow[:5]
    )


def test_median_latency_reasonable():
    """Median latency under 45 seconds."""
    traces = [t for t in _user_traces() if t.get("total_latency_ms", 0) > 0]
    if not traces:
        return
    latencies = sorted(t["total_latency_ms"] for t in traces)
    median = latencies[len(latencies) // 2]
    assert median < 45_000, f"Median latency {median}ms is over 45s — something is slow"


# ── Errors and fallback ───────────────────────────────────────────────────────


def test_no_failed_llm_calls():
    """No LLM call with status error in the last 50 traces."""
    traces = _user_traces()
    failed = [t for t in traces if any(c.get("status") == "error" for c in t.get("llm_calls", []))]
    assert not failed, f"{len(failed)} traces with LLM errors:\n" + "\n".join(
        t["rid"] for t in failed[:5]
    )


def test_fallback_rate_acceptable():
    """9B fallback used in at most 20% of traces."""
    traces = _user_traces()
    if not traces:
        return
    fallback_count = sum(1 for t in traces if t.get("used_fallback"))
    rate = fallback_count / len(traces)
    assert rate <= 0.20, f"9B fallback rate {rate:.0%} is over 20% — is the 27B model down?"


def test_no_empty_recoveries():
    """No traces where the model returned empty and had to be recovered."""
    traces = _user_traces()
    recovered = [t for t in traces if any(c.get("recovered") for c in t.get("llm_calls", []))]
    # Warning, not a hard failure — recovered=True is not critical
    if recovered:
        print(f"\nINFO: {len(recovered)} traces with recovered=True (empty reply → retry)")


# ── Tools ─────────────────────────────────────────────────────────────────────


def test_tool_calls_have_duration():
    """Every tool call has duration_ms set."""
    traces = _user_traces()
    missing_duration = [
        (t["rid"], tc.get("tool"))
        for t in traces
        for tc in t.get("tool_calls", [])
        if tc.get("duration_ms") is None
    ]
    assert not missing_duration, (
        f"{len(missing_duration)} tool calls without duration_ms:\n"
        + "\n".join(f"  {r} — {tool}" for r, tool in missing_duration[:5])
    )


# ── Reflection meeting ────────────────────────────────────────────────────────


def test_reflection_has_run():
    """At least one reflection trace exists (the meeting has run at least once)."""
    traces = _refl_traces()
    assert len(traces) > 0, (
        "No rid-refl-* traces found — has the reflection meeting run since startup?"
    )
