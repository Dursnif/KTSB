"""P119 Phase 1 — B1 (read_meeting text in the active language) and B2 (fresh detectors)."""

from pathlib import Path

import pytest

from kaare_core import model_enforcement as me
from kaare_core.tools import executor_memory
from kaare_core.tools.i18n import t


@pytest.fixture
def fake_state(tmp_path, monkeypatch):
    """Redirect the module's hardcoded /kaare/state/memory paths to a temp dir."""
    monkeypatch.setattr(
        executor_memory,
        "Path",
        lambda p: Path(str(p).replace("/kaare/state/memory", str(tmp_path))),
    )
    (tmp_path / "dev_meetings").mkdir()
    (tmp_path / "reflections").mkdir()
    return tmp_path


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_dev_meeting_truncation_marker_in_language(fake_state, lang):
    (fake_state / "dev_meetings" / "2026-10-03.md").write_text("x" * 7000, encoding="utf-8")
    out = executor_memory._read_dev_meeting("2026-10-03", lang)
    assert out.endswith(t("mem_truncated", lang))
    assert len(out) < 7000


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_no_dates_fallback_in_language(fake_state, lang):
    out = executor_memory._read_reflection("2026-01-01", lang)
    assert t("mem_no_dates", lang) in out
    out = executor_memory._read_dev_meeting("2026-01-01", lang)
    assert t("mem_no_dates", lang) in out


def test_new_keys_have_three_languages():
    for key in (
        "mem_truncated",
        "mem_no_dates",
        "pers_profile_section",
        "pers_observations_section",
    ):
        assert {t(key, "nb"), t(key, "en"), t(key, "de")} and t(key, "en") != key


def test_enforcers_do_not_share_detectors():
    a, b = me.ModelEnforcer("nb"), me.ModelEnforcer("en")
    assert a._detectors and b._detectors
    assert not {id(d) for d in a._detectors} & {id(d) for d in b._detectors}
    assert not hasattr(me, "DEFAULT_DETECTORS")
