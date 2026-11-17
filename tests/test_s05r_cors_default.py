"""DT04-S05r part 2 — v1.11 must not write a CORS value an older version misreads.

v1.11 wrote `cors_origins: ["same-host"]` into settings.yaml (template + step 12). v1.10.3 reads the list as literal
origins, sends no CORS header, and its GUI hangs after a rollback. Now «same-host» is the default in code: the template
ships no active `cors_origins`, and a new step removes the key when it holds exactly that default. An older version then
finds no key and uses its own default. A list the user wrote is never touched.
"""

from pathlib import Path

import yaml

from kaare_core import config_migrations as cm
from kaare_core import cors

ROOT = Path(__file__).resolve().parent.parent


def test_template_has_no_active_cors_key_and_code_gives_same_host():
    tpl_path = ROOT / "configs_default/settings.yaml"
    tpl = yaml.safe_load(tpl_path.read_text(encoding="utf-8"))
    assert "cors_origins" not in tpl
    assert "cors_origins" in tpl_path.read_text(encoding="utf-8")  # still explained in a comment
    assert cors.read_origins(tpl_path) == ["same-host"]


def test_step_removes_exactly_the_default():
    step = next(s for s in cm.STEPS if s.name == "cors_default_in_code")
    assert step.filename == "settings.yaml" and step.number == cm.LATEST
    data = {"cors_origins": ["same-host"], "other": 1}
    assert step.fn(data, {})
    assert "cors_origins" not in data and data["other"] == 1
    assert step.fn(data, {}) == []  # idempotent


def test_own_lists_are_kept():
    step = next(s for s in cm.STEPS if s.name == "cors_default_in_code")
    for own in (["same-host", "https://dash.example"], ["*"], ["https://dash.example"]):
        data = {"cors_origins": list(own)}
        assert step.fn(data, {}) == [] and data["cors_origins"] == own


def test_a_v1103_install_ends_with_no_key():
    """v1.10.3 shipped `["*"]`: step 12 moves it to same-host, the new step removes it — nothing v1.10.3 misreads."""
    data = {"cors_origins": ["*"]}
    for s in cm.STEPS:
        if s.filename == "settings.yaml" and s.number >= 12:
            s.fn(data, {})
    assert "cors_origins" not in data
