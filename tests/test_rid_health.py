"""
RID-based system health tests.

Reads real production logs via trace_reader and flags anomalies.
Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_rid_health.py -v
"""
import sys
sys.path.insert(0, "/kaare")

from kaare_core.tools.trace_reader import get_recent_traces


def _user_traces(n=50):
    return get_recent_traces(n=n, source="user")


def _refl_traces(n=20):
    return get_recent_traces(n=n, source="refl")


# ── Grunnleggende struktur ────────────────────────────────────────────────────

def test_logs_have_recent_traces():
    """Minst 1 bruker-trace finnes i loggene."""
    traces = _user_traces(10)
    assert len(traces) > 0, "Ingen bruker-traces funnet — er kaare.service oppe?"


def test_all_traces_have_rid():
    """Alle traces har en rid."""
    traces = _user_traces()
    missing = [t for t in traces if not t.get("rid")]
    assert not missing, f"{len(missing)} traces mangler rid"


def test_all_traces_have_llm_call():
    """Alle bruker-traces som ikke er fastpath skal ha minst ett LLM-kall."""
    traces = _user_traces()
    no_llm = [
        t for t in traces
        if t.get("llm_call_count", 0) == 0
        and not any(s.get("stage") == "fastpath_done" for s in t.get("stages", []))
    ]
    assert not no_llm, (
        f"{len(no_llm)} traces uten LLM-kall og uten fastpath:\n"
        + "\n".join(t["rid"] for t in no_llm[:5])
    )


# ── Latency ───────────────────────────────────────────────────────────────────

def test_no_extreme_latency():
    """Ingen traces over 3 minutter — flere tool-runder kan legitimt ta 120s."""
    traces = _user_traces()
    slow = [t for t in traces if t.get("total_latency_ms", 0) > 180_000]
    assert not slow, (
        f"{len(slow)} traces over 3 min:\n"
        + "\n".join(f"  {t['rid']} — {t['total_latency_ms']}ms ({t.get('llm_call_count',0)} LLM-kall)" for t in slow[:5])
    )


def test_median_latency_reasonable():
    """Median latency under 45 sekunder."""
    traces = [t for t in _user_traces() if t.get("total_latency_ms", 0) > 0]
    if not traces:
        return
    latencies = sorted(t["total_latency_ms"] for t in traces)
    median = latencies[len(latencies) // 2]
    assert median < 45_000, f"Median latency {median}ms er over 45s — noe er tregt"


# ── Feil og fallback ──────────────────────────────────────────────────────────

def test_no_failed_llm_calls():
    """Ingen LLM-kall med status error i siste 50 traces."""
    traces = _user_traces()
    failed = [
        t for t in traces
        if any(c.get("status") == "error" for c in t.get("llm_calls", []))
    ]
    assert not failed, (
        f"{len(failed)} traces med LLM-feil:\n"
        + "\n".join(t["rid"] for t in failed[:5])
    )


def test_fallback_rate_acceptable():
    """9B-fallback brukes i maks 20% av traces."""
    traces = _user_traces()
    if not traces:
        return
    fallback_count = sum(1 for t in traces if t.get("used_fallback"))
    rate = fallback_count / len(traces)
    assert rate <= 0.20, (
        f"9B-fallback rate {rate:.0%} er over 20% — er 27B-modellen nede?"
    )


def test_no_empty_recoveries():
    """Ingen traces der modellen ga tomt svar og måtte recovery-es."""
    traces = _user_traces()
    recovered = [
        t for t in traces
        if any(c.get("recovered") for c in t.get("llm_calls", []))
    ]
    # Advarsel, ikke hard feil — recovered=True er ikke kritisk
    if recovered:
        print(f"\nINFO: {len(recovered)} traces med recovered=True (tomt svar → retry)")


# ── Verktøy ───────────────────────────────────────────────────────────────────

def test_tool_calls_have_duration():
    """Alle tool-kall har duration_ms satt."""
    traces = _user_traces()
    missing_duration = [
        (t["rid"], tc.get("tool"))
        for t in traces
        for tc in t.get("tool_calls", [])
        if tc.get("duration_ms") is None
    ]
    assert not missing_duration, (
        f"{len(missing_duration)} tool-kall mangler duration_ms:\n"
        + "\n".join(f"  {r} — {tool}" for r, tool in missing_duration[:5])
    )


# ── Refleksjonsmøte ───────────────────────────────────────────────────────────

def test_reflection_has_run():
    """Minst én refleksjonstrace finnes (møtet har kjørt minst én gang)."""
    traces = _refl_traces()
    assert len(traces) > 0, (
        "Ingen rid-refl-* traces funnet — har refleksjonsmøtet kjørt siden oppstart?"
    )
