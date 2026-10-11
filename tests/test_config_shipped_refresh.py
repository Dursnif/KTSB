"""DT03 R10 10.1/10.2 — old shipped template copies are refreshed, old default values move to the new default.

A file in configs/ equal to a version KTSB shipped earlier was never changed by the user (an old tracked copy after
`git pull`, or copied back by hand) → it gets the current template. A user-changed file is never touched. Step 6
moves values that still equal an old default (kare_reflection.kare_max_tokens 3000) once.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from kaare_core import config_migrations as cm

DEFAULTS = Path("/kaare/configs_default")
OLD_CORE = "# Old core\nJeg er Kåre. Gammel tekst fra v1.9.\n"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    d = tmp_path / "defaults"
    shutil.copytree(DEFAULTS, d)
    c = tmp_path / "configs"
    c.mkdir()
    shipped = tmp_path / "shipped.json"
    monkeypatch.setattr(cm, "SHIPPED_VERSIONS_FILE", shipped)
    return c, d, shipped


def _ship(shipped: Path, versions: dict[str, list[str]]) -> None:
    shipped.write_text(json.dumps({"versions": {k: [cm._norm_hash(v.encode()) for v in vs] for k, vs in versions.items()}}))


def _history(c: Path, rel: str) -> list[Path]:
    return list((c / ".history" / rel).glob("*")) if (c / ".history" / rel).is_dir() else []


def test_unchanged_old_copy_gets_the_template_and_the_old_is_kept(dirs):
    c, d, shipped = dirs
    _ship(shipped, {"personality_core.md": [OLD_CORE]})
    cm.run(c, d)  # fresh install
    (c / "personality_core.md").write_text(OLD_CORE)  # what `git pull` leaves: the frozen v1.9 copy
    (c / ".config_version").write_text("steps: 0\ntemplates: ''\n")
    rep = cm.run(c, d)
    assert (c / "personality_core.md").read_bytes() == (d / "personality_core.md").read_bytes()
    assert any("refresh personality_core.md" in ch for ch in rep.changes)
    assert rep.snapshot is not None and (rep.snapshot / "personality_core.md").read_text() == OLD_CORE
    assert _history(c, "personality_core.md"), "old copy kept in history"


def test_user_changed_file_is_never_touched(dirs):
    c, d, shipped = dirs
    _ship(shipped, {"personality_core.md": [OLD_CORE]})
    cm.run(c, d)
    mine = OLD_CORE + "Min egen linje.\n"
    (c / "personality_core.md").write_text(mine)
    (c / ".config_version").write_text("steps: 0\ntemplates: ''\n")
    cm.run(c, d)
    assert (c / "personality_core.md").read_text() == mine


def test_crlf_checkout_counts_as_the_same_version(dirs):
    c, d, shipped = dirs
    _ship(shipped, {"personality_core.md": [OLD_CORE]})
    cm.run(c, d)
    (c / "personality_core.md").write_bytes(OLD_CORE.replace("\n", "\r\n").encode())  # git on Windows, autocrlf
    (c / ".config_version").write_text("steps: 0\ntemplates: ''\n")
    cm.run(c, d)
    assert (c / "personality_core.md").read_bytes() == (d / "personality_core.md").read_bytes()


def test_subfolder_and_yaml_files_refresh_too(dirs):
    c, d, shipped = dirs
    old_persona = "Gammel standard-personlighet.\n"
    old_models = "kare: qwen3:8b\n"
    _ship(shipped, {"personalities/standard.md": [old_persona], "models.yaml": [old_models]})
    cm.run(c, d)
    (c / "personalities" / "standard.md").write_text(old_persona)
    (c / "models.yaml").write_text(old_models)
    (c / ".config_version").write_text("steps: 0\ntemplates: ''\n")
    rep = cm.run(c, d)
    assert (c / "personalities" / "standard.md").read_bytes() == (d / "personalities" / "standard.md").read_bytes()
    assert (c / "models.yaml").read_bytes() == (d / "models.yaml").read_bytes()
    assert not any(ch.startswith("models.yaml:") for ch in rep.changes), "a refreshed file is not also key-filled"


def test_dry_run_reports_and_writes_nothing(dirs):
    c, d, shipped = dirs
    _ship(shipped, {"personality_core.md": [OLD_CORE]})
    cm.run(c, d)
    (c / "personality_core.md").write_text(OLD_CORE)
    (c / ".config_version").write_text("steps: 0\ntemplates: ''\n")
    rep = cm.run(c, d, dry_run=True)
    assert any("refresh personality_core.md" in ch for ch in rep.changes)
    assert (c / "personality_core.md").read_text() == OLD_CORE


def test_new_shipped_list_triggers_one_look_then_nothing(dirs):
    c, d, shipped = dirs
    _ship(shipped, {})
    cm.run(c, d)
    (c / "personality_core.md").write_text(OLD_CORE)
    assert cm.run(c, d).up_to_date, "same list → normal start writes nothing"
    _ship(shipped, {"personality_core.md": [OLD_CORE]})  # next release knows this version
    cm.run(c, d)
    assert (c / "personality_core.md").read_bytes() == (d / "personality_core.md").read_bytes()
    assert cm.run(c, d).up_to_date


def test_legacy_copy_equal_to_a_shipped_version_uses_the_template(tmp_path, monkeypatch):
    d = tmp_path / "defaults"
    shutil.copytree(DEFAULTS, d)
    c = tmp_path / "configs"
    c.mkdir()
    shipped = tmp_path / "shipped.json"
    monkeypatch.setattr(cm, "SHIPPED_VERSIONS_FILE", shipped)
    monkeypatch.setattr(cm, "_IN_DOCKER", False)
    old_cap = "services:\n  ha: true\n"
    (tmp_path / "capability_map.yaml").write_text(old_cap)  # pre-P127 root copy, never changed
    _ship(shipped, {"capability_map.yaml": [old_cap]})
    cm.run(c, d)
    assert (c / "capability_map.yaml").read_bytes() == (d / "capability_map.yaml").read_bytes()


def test_step6_old_default_cap_moves_once(dirs):
    c, d, shipped = dirs
    _ship(shipped, {})
    new = yaml.safe_load((d / "settings.yaml").read_text())["kare_reflection"]["kare_max_tokens"]
    assert new != 3000
    cm.run(c, d)
    text = (c / "settings.yaml").read_text().replace(f"kare_max_tokens: {new}", "kare_max_tokens: 3000", 1)
    (c / "settings.yaml").write_text(text)
    (c / ".config_version").write_text("steps: 5\ntemplates: ''\n")  # an install migrated before step 6
    rep = cm.run(c, d)
    assert yaml.safe_load((c / "settings.yaml").read_text())["kare_reflection"]["kare_max_tokens"] == new
    assert any("3000 → " in ch for ch in rep.changes)
    # The user sets 3000 on purpose afterwards → step 6 has run, it stays.
    (c / "settings.yaml").write_text((c / "settings.yaml").read_text().replace(f"kare_max_tokens: {new}", "kare_max_tokens: 3000", 1))
    (c / ".config_version").write_text(f"steps: {cm.LATEST}\ntemplates: ''\n")
    cm.run(c, d)
    assert yaml.safe_load((c / "settings.yaml").read_text())["kare_reflection"]["kare_max_tokens"] == 3000


def test_step6_leaves_a_user_value_alone(dirs):
    c, d, shipped = dirs
    _ship(shipped, {})
    cm.run(c, d)
    s = (c / "settings.yaml").read_text()
    new = yaml.safe_load(s)["kare_reflection"]["kare_max_tokens"]
    (c / "settings.yaml").write_text(s.replace(f"kare_max_tokens: {new}", "kare_max_tokens: 4096", 1))
    (c / ".config_version").write_text("steps: 5\ntemplates: ''\n")
    cm.run(c, d)
    assert yaml.safe_load((c / "settings.yaml").read_text())["kare_reflection"]["kare_max_tokens"] == 4096


def test_real_shipped_list_matches_the_templates():
    doc = json.loads(cm.SHIPPED_VERSIONS_FILE.read_text())
    versions = doc["versions"]
    assert versions, "generated list is empty"
    current = {p.relative_to(DEFAULTS).as_posix(): cm._norm_hash(p.read_bytes()) for p in DEFAULTS.rglob("*") if p.is_file()}
    for rel, hashes in versions.items():
        assert all(len(h) == 64 for h in hashes)
        if rel in current:
            assert current[rel] not in hashes, f"{rel}: current template listed as old — regenerate the list"
