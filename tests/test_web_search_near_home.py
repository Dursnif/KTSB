"""
web_search near_home — Kåre decides whether the home place applies; code appends it; the
ModelEnforcer forces the decision when it's missing (BUG-log 2026-10-03: every search was
locked to the home place by a blanket "[Kontekst: city, country]" prefix).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_web_search_near_home.py -v
"""

import sys

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.model_enforcement as me
import kaare_core.tools.executor_library as lib
from kaare_core import log_buffer
from kaare_core.model_enforcement import (
    ModelEnforcer,
    NearHomeMissingDetector,
    StakesMissingDetector,
)
from kaare_core.tools.definitions import get_tools


@pytest.fixture(autouse=True)
def tmp_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")  # never the live log
    monkeypatch.setattr(lib, "get_home_location", lambda: {"city": "Hjemby", "country": "Hjemland"})
    return tmp_path


# ── Query building ──────────────────────────────────────────────────────────


def test_near_home_false_leaves_query_untouched():
    assert lib.build_search_query("valgresultat Sverige", False) == "valgresultat Sverige"


def test_near_home_true_appends_city():
    assert lib.build_search_query("når stenger apoteket", True) == "når stenger apoteket Hjemby"


def test_near_home_true_does_not_double_the_place():
    assert lib.build_search_query("kino hjemby i helga", True) == "kino hjemby i helga"


def test_near_home_falls_back_to_country_without_city(monkeypatch):
    monkeypatch.setattr(lib, "get_home_location", lambda: {"city": "", "country": "Hjemland"})
    assert lib.build_search_query("strømpris i dag", True) == "strømpris i dag Hjemland"


def test_near_home_true_without_any_location_is_a_noop(monkeypatch):
    monkeypatch.setattr(lib, "get_home_location", lambda: {"city": "", "country": ""})
    assert lib.build_search_query("apotek åpningstider", True) == "apotek åpningstider"


def test_no_blanket_context_prefix_anymore():
    assert "[Kontekst" not in lib.build_search_query("noe", True)
    assert "[Kontekst" not in lib.build_search_query("noe", False)


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("False", False),
        (None, None),
        ("", None),
        ("yes", None),
        (1, None),
    ],
)
def test_parse_near_home(value, expected):
    assert lib.parse_near_home(value) is expected


# ── Tool schema ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_schema_requires_near_home(lang):
    tool = next(t for t in get_tools(lang) if t["function"]["name"] == "web_search")
    params = tool["function"]["parameters"]
    assert params["properties"]["near_home"]["type"] == "boolean"
    assert "near_home" in params["required"]
    assert params["properties"]["near_home"]["description"]


# ── Enforcer ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args,fires",
    [
        ({"query": "x"}, True),
        ({"query": "x", "near_home": None}, True),
        ({"query": "x", "near_home": "maybe"}, True),
        ({"query": "x", "near_home": True}, False),
        ({"query": "x", "near_home": False}, False),
        ({"query": "x", "near_home": "false"}, False),
    ],
)
def test_detector(args, fires):
    assert NearHomeMissingDetector().matches(tool_name="web_search", args=args) is fires


def test_detector_ignores_other_tools():
    assert not NearHomeMissingDetector().matches(tool_name="library", args={"query": "x"})


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_correction_once_per_request_then_runs(lang, tmp_logs):
    enf = ModelEnforcer(lang, rid="rid-test", tool_call_detectors=[NearHomeMissingDetector()])
    first = enf.check_tool_call("web_search", {"query": "x"})
    assert first and "near_home" in first
    # Second miss in the same request: no more corrections — the tool runs with its default.
    assert enf.check_tool_call("web_search", {"query": "x"}) is None
    log_buffer.flush()
    assert '"near_home_missing"' in (tmp_logs / "enforcement.log").read_text(encoding="utf-8")


def test_tool_correction_does_not_use_up_text_retry():
    enf = ModelEnforcer("nb", rid="rid-test")
    enf.check_tool_call("web_search", {"query": "x"})
    assert enf.retry_used is False
    assert enf.pending is None


def test_valid_call_passes():
    enf = ModelEnforcer("nb", rid="rid-test")
    assert (
        enf.check_tool_call("web_search", {"query": "x", "near_home": False, "stakes": "normal"})
        is None
    )


# ── stakes (P121 Phase 4c) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args,fires",
    [
        ({"query": "x", "near_home": False}, True),
        ({"query": "x", "near_home": False, "stakes": "medium"}, True),
        ({"query": "x", "near_home": False, "stakes": "high"}, False),
        ({"query": "x", "near_home": False, "stakes": "Extraordinary"}, False),
    ],
)
def test_stakes_detector(args, fires):
    assert StakesMissingDetector().matches(tool_name="web_search", args=args) is fires


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_stakes_in_schema(lang):
    tool = next(t_ for t_ in get_tools(lang) if t_["function"]["name"] == "web_search")
    params = tool["function"]["parameters"]
    assert params["properties"]["stakes"]["enum"] == ["trivial", "normal", "high", "extraordinary"]
    assert "stakes" in params["required"]
