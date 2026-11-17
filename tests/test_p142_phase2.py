"""P142 Phase 2 — policy per detector and modes off / shadow / on (D4), set in the GUI (D7).

Shadow = the detector runs and its hit is logged, but nothing is corrected — how a new rule is
measured before it may change a reply. Modes come from settings.yaml model_enforcer.modes (only
overrides of a detector's own default are stored)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

import kaare_core.config as cfg
import kaare_core.model_enforcement as me
import kaare_core.routers.router_settings as rs
from kaare_core.model_enforcement import (
    MODE_OFF,
    MODE_ON,
    MODE_SHADOW,
    MODES,
    ON_EMPTY_KEEP_ORIGINAL,
    ON_EMPTY_REFRAIN,
    Detector,
    ModelEnforcer,
    detector_catalog,
    enforcement_stats,
)

_PROMISE = "Jeg tar det opp på neste dev-møte."
_LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "locales"


@pytest.fixture
def events(monkeypatch):
    out: list[tuple] = []
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: out.append(a[:3]))
    return out


# ── Modes on reply detectors ────────────────────────────────────────────────


def test_shadow_logs_once_and_corrects_nothing(events):
    enf = ModelEnforcer("nb", rid="r", modes={"tool_promise": MODE_SHADOW})
    assert enf.on_answer(_PROMISE) is None
    assert enf.on_answer(_PROMISE) is None
    assert events == [("r", "tool_promise", "shadow")]
    assert not enf.retry_used and enf.pending is None


def test_shadow_lets_the_next_detector_have_its_turn(events):
    enf = ModelEnforcer("nb", rid="r", modes={"tool_promise": MODE_SHADOW})
    action = enf.on_answer(f"Ferdig – skrevet til lessons. {_PROMISE}")
    assert action and action.detector_name == "claimed_done"
    assert ("r", "tool_promise", "shadow") in events


def test_off_does_not_run(events):
    enf = ModelEnforcer("nb", rid="r", modes={"tool_promise": MODE_OFF})
    assert enf.on_answer(_PROMISE) is None
    assert events == []


@pytest.mark.parametrize("bad", ["", "maybe", "ON", None])
def test_invalid_override_falls_back_to_default(bad, events):
    enf = ModelEnforcer("nb", rid="r", modes={"tool_promise": bad})
    assert enf.on_answer(_PROMISE), "default mode of tool_promise is on"


def test_new_detector_starts_in_shadow_by_its_own_default(events):
    class Always(Detector):
        name = "always"
        default_mode = MODE_SHADOW

        def matches(self, **kw):
            return True

        def correction_message(self, lang):
            return "x"

        def resolve(self, **kw):
            return ""

    enf = ModelEnforcer("nb", rid="r", detectors=[Always()], modes={})
    assert enf.on_answer("hei") is None
    assert events == [("r", "always", "shadow")]
    enf = ModelEnforcer("nb", rid="r", detectors=[Always()], modes={"always": MODE_ON})
    assert enf.on_answer("hei")


# ── Modes on tool-call detectors ────────────────────────────────────────────

_NO_NEAR_HOME = {"query": "vær", "stakes": "normal"}


def test_tool_call_detector_modes(events):
    on = ModelEnforcer("nb", rid="r", modes={})
    assert on.check_tool_call("web_search", dict(_NO_NEAR_HOME))
    shadow = ModelEnforcer("nb", rid="r", modes={"near_home_missing": MODE_SHADOW})
    assert shadow.check_tool_call("web_search", dict(_NO_NEAR_HOME)) is None, "the call runs"
    assert ("r", "near_home_missing", "shadow") in events
    events.clear()
    off = ModelEnforcer("nb", rid="r", modes={"near_home_missing": MODE_OFF})
    assert off.check_tool_call("web_search", dict(_NO_NEAR_HOME)) is None
    assert not any(e[1] == "near_home_missing" for e in events)


# ── Policy ──────────────────────────────────────────────────────────────────


def test_policies_in_the_catalog():
    cat = {d["name"]: d for d in detector_catalog()}
    assert cat["tool_promise"]["on_empty"] == ON_EMPTY_KEEP_ORIGINAL
    assert cat["foreign_script"]["on_empty"] == ON_EMPTY_KEEP_ORIGINAL
    assert cat["action_claim"]["on_empty"] == ON_EMPTY_KEEP_ORIGINAL  # Phase 4: side clause, good reply
    assert cat["visual_claim_no_image"]["on_empty"] == ON_EMPTY_KEEP_ORIGINAL  # R140: a side clause, like action_claim
    keep_original = ("tool_promise", "foreign_script", "action_claim", "visual_claim_no_image")
    for name, d in cat.items():
        assert all(p["default_mode"] in MODES for p in d["profiles"])
        if d["kind"] == "reply" and name not in keep_original:
            assert d["on_empty"] == ON_EMPTY_REFRAIN, name
        if d["kind"] == "tool_call":
            assert d["on_fail"] == "correct_call"


def test_catalog_covers_every_profile_detector_once():
    names = [d["name"] for d in detector_catalog()]
    assert len(names) == len(set(names))
    for profile, (reply_f, tool_f, _cap) in me.PROFILES.items():
        for d in [*reply_f(), *tool_f()]:
            assert d.name in names, (profile, d.name)


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_every_detector_has_gui_text(lang):
    loc = json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    enf = loc["settings"]["agenter"]["enforcer"]
    for d in detector_catalog():
        assert enf["detectors"][d["name"]]["label"] and enf["detectors"][d["name"]]["desc"], d["name"]
        for p in d["profiles"]:
            assert enf["profiles"][p["profile"]]
    for m in MODES:
        assert enf["modes"][m]
    assert loc["forklaringer"]["innst_agenter"]["item_4"]


# ── Stats from enforcement.log ──────────────────────────────────────────────


def test_stats_counts_and_cutoff(monkeypatch, tmp_path):
    log = tmp_path / "enforcement.log"
    now = datetime.now(UTC)
    old = (now - timedelta(days=40)).isoformat()
    lines = [
        {"ts": old, "rid": "a", "detector": "tool_promise", "event": "retry"},
        {"ts": now.isoformat(), "rid": "b", "detector": "tool_promise", "event": "retry", "phrase": "neste dev-møte"},
        {"ts": now.isoformat(), "rid": "b", "detector": "tool_promise", "event": "resolved", "outcome": "kept_original"},
        {"ts": now.isoformat(), "rid": "c", "detector": "near_home_missing", "event": "shadow"},
    ]
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
    monkeypatch.setattr(me, "_ENFORCE_LOG", log)
    st = enforcement_stats(days=30)
    tp = st["per_detector"]["tool_promise"]
    assert tp["retry"] == 1 and tp["outcomes"] == {"kept_original": 1}
    assert st["per_detector"]["near_home_missing"]["shadow"] == 1
    assert [r["rid"] for r in st["recent"]] == ["c", "b", "b"], "newest first, old line cut"
    assert st["recent"][-1]["phrase"] == "neste dev-møte"


def test_stats_without_log(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "missing.log")
    assert enforcement_stats() == {"days": 30, "per_detector": {}, "recent": []}


# ── Config accessor ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"model_enforcer": {"modes": {"tool_promise": "shadow"}}}, {"tool_promise": "shadow"}),
        ({"model_enforcer": {"modes": None}}, {}),
        ({"model_enforcer": {"modes": ["x"]}}, {}),
        ({}, {}),
    ],
)
def test_get_enforcer_modes(monkeypatch, tmp_path, content, expected):
    (tmp_path / "settings.yaml").write_text(yaml.dump(content), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_enforcer_modes_cache", {"mtime": None, "modes": {}})
    assert cfg.get_enforcer_modes() == expected


# ── GUI API ─────────────────────────────────────────────────────────────────


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.dump({"other": {"keep": 1}}), encoding="utf-8")
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    monkeypatch.setattr(me, "_ENFORCE_LOG", tmp_path / "enforcement.log")
    return p


def _mode(d, profile, name):
    det = next(x for x in d["detectors"] if x["name"] == name)
    return next(p["mode"] for p in det["profiles"] if p["profile"] == profile)


def test_api_get_shows_default_modes(settings_file):
    d = asyncio.run(rs.api_get_model_enforcer(_u=None))
    assert d["modes"] == list(MODES)
    assert _mode(d, "chat", "tool_promise") == MODE_ON
    assert _mode(d, "mechanic", "foreign_script") == MODE_SHADOW, "new caller starts in shadow"
    assert d["stats"]["per_detector"] == {}


def test_api_put_stores_only_overrides(settings_file):
    r = asyncio.run(
        rs.api_put_model_enforcer(
            {"modes": {"chat:tool_promise": "shadow", "chat:claimed_done": "on"}}, _u=None
        )
    )
    assert r["stored"] == {"chat:tool_promise": "shadow"}, "claimed_done=on is its default"
    data = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert data["model_enforcer"]["modes"] == {"chat:tool_promise": "shadow"}
    assert data["other"] == {"keep": 1}
    d = asyncio.run(rs.api_get_model_enforcer(_u=None))
    assert _mode(d, "chat", "tool_promise") == MODE_SHADOW
    assert _mode(d, "reflection", "tool_promise") == MODE_ON, "other callers untouched"
    # Back to default removes the override.
    asyncio.run(rs.api_put_model_enforcer({"modes": {"chat:tool_promise": "on"}}, _u=None))
    assert yaml.safe_load(settings_file.read_text(encoding="utf-8"))["model_enforcer"]["modes"] == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"modes": {"chat:no_such_detector": "on"}},
        {"modes": {"tool_promise": "on"}},  # Phase 3: keys are «profile:detector»
        {"modes": {"mechanic:tool_promise": "on"}},  # rule not in that caller
        {"modes": {"chat:tool_promise": "loud"}},
        {"modes": "on"},
        {},
    ],
)
def test_api_put_rejects_bad_input(settings_file, payload):
    with pytest.raises(HTTPException) as e:
        asyncio.run(rs.api_put_model_enforcer(payload, _u=None))
    assert e.value.status_code == 400
    assert "model_enforcer" not in (yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {})


def test_shipped_default_is_empty():
    tpl = Path(__file__).resolve().parents[1] / "configs_default" / "settings.yaml"
    assert yaml.safe_load(tpl.read_text(encoding="utf-8"))["model_enforcer"] == {"modes": {}}
