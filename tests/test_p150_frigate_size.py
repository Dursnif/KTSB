"""P150 phase 3 — the Frigate memory is kept by SIZE, not by age (2026-11-13, two yes).

`frigate_db_max_mb` (Integrations → Frigate → Lagringsgrenser; 0 = off) replaces event_detail_days / hourly_days /
daily_days. Over the limit, the oldest rolled-up detail goes first (slim in an episode, delete outside one) — the same steps
as before, only the cut is chosen by size. Hourly counts go only in a later run, when no trimmable detail is left; daily
counts never go. Unrolled events are never touched. Names of unclassified faces keep their own (privacy) days — phase 6.
Never touches live state: the s07 fixtures point frigate.db and the configs at tmp_path.
"""

import pytest
import yaml

from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.tools.i18n import t
from tests.test_p144_s04_frigate_store import T0
from tests.test_p144_s04_frigate_store import db as _s04_db  # noqa: F401 — s07 fixture base
from tests.test_p144_s07_retention import DAY, TEMPLATE, _car, _episode_with_scene, _raw, _set
from tests.test_p144_s07_retention import db as _s07_db  # noqa: F401 — fixture

MB = 1_048_576


@pytest.fixture
def db(_s07_db, monkeypatch):  # noqa: F811
    _set(retention_dry_run=False, frigate_db_max_mb=1)
    return _s07_db


def _size(monkeypatch, nbytes):
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: nbytes)


def test_old_detail_under_the_limit_is_kept(db, monkeypatch):
    """The old rule slimmed/deleted after 7 days; now 100 days old is fine while the store is under its limit."""
    _size(monkeypatch, MB // 2)
    _episode_with_scene()
    fstore.record_event(_car("road", T0 + 30))
    rep = fstore.run_retention(now=T0 + 100 * DAY)
    assert rep["steps"]["events.delete"] == 0 and rep["steps"]["events.slim"] == 0
    assert {r["event_id"] for r in _raw("SELECT event_id FROM events WHERE tier='event'")} == {"p", "road"}


def test_limit_zero_means_off(db, monkeypatch):
    _set(frigate_db_max_mb=0)
    _size(monkeypatch, 50 * MB)
    fstore.record_event(_car("road", T0))
    fstore.run_retention(now=T0 + 400 * DAY)
    assert len(_raw("SELECT * FROM events")) == 1


def test_oldest_goes_first_only_as_much_as_needed(db, monkeypatch):
    for i in range(3):
        fstore.record_event(_car(f"c{i}", T0 + i * DAY))
    fstore.run_retention(now=T0 + 10 * DAY)  # roll up while under the limit (size patched below)
    _size(monkeypatch, MB + 1)  # 1 byte over → the oldest event covers it
    fstore.run_retention(now=T0 + 10 * DAY)
    assert {r["event_id"] for r in _raw("SELECT event_id FROM events")} == {"c1", "c2"}


def test_hourly_goes_only_when_no_detail_is_left_and_daily_never(db, monkeypatch):
    _size(monkeypatch, 50 * MB)
    fstore.record_event(_car("road", T0))
    rep1 = fstore.run_retention(now=T0 + 2 * DAY)
    assert rep1["steps"]["events.delete"] == 1 and rep1["steps"]["hourly.delete"] == 0
    assert len(_raw("SELECT * FROM hourly")) == 1
    rep2 = fstore.run_retention(now=T0 + 2 * DAY)
    assert rep2["steps"]["hourly.delete"] == 1
    assert _raw("SELECT * FROM hourly") == []
    for _ in range(3):
        fstore.run_retention(now=T0 + 3 * DAY)
    assert _raw("SELECT event_count FROM daily")[0]["event_count"] == 1, "daily counts are never deleted"


def test_measured_size_is_the_used_part_of_the_file(db):
    fstore.record_event(_car("road", T0))
    assert 0 < fstore.db_logical_bytes() <= fstore.DB_PATH.stat().st_size


def test_settings_key_and_template(db):
    s = fsettings.get_settings()
    assert s["frigate_db_max_mb"] == 1
    for gone in ("event_detail_days", "hourly_days", "daily_days"):
        assert gone not in s
        with pytest.raises(ValueError):
            fsettings.update({gone: 7})
    with pytest.raises(ValueError):
        fsettings.update({"frigate_db_max_mb": -1})
    data = yaml.safe_load(TEMPLATE.read_text())
    assert data["frigate_db_max_mb"] == 500 and data["retention_dry_run"] is True
    assert not {"event_detail_days", "hourly_days", "daily_days"} & set(data)


def test_kare_is_not_told_a_day_rule(db):
    """Kåre's camera answers must not claim «details trimmed after 7 days» — the cut is the storage limit."""
    for key in ("fq_ep_slim", "fq_level_slim", "fq_eps_old_note"):
        for lang in ("nb", "en", "de"):
            assert "{days}" not in t(key, lang), (key, lang)


def test_detail_since_is_the_oldest_full_detail_event(db):
    assert fstore.detail_since() is None
    fstore.record_event(_car("a", T0 + 5))
    fstore.record_event(_car("b", T0 + 50))
    assert fstore.detail_since() == pytest.approx(T0 + 5)
