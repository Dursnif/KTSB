"""P134 Phase 2 — versioned, run-once config migrations; the concurrent-start race (BUG-log 2026-10-09)."""

import multiprocessing as mp
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from kaare_core import config_migrations as cm
from kaare_core import config_store as cs

DEFAULTS = Path("/kaare/configs_default")
RACE_ROUNDS = int(os.environ.get("P134_RACE_ROUNDS", "20"))

USER_LLM = (
    "# my own notes about the model\n"
    "default:\n"
    "  provider: ollama\n"
    "  base_url: http://USER-SERVER:11434  # my box\n"
    "  system: Du er min egen Kåre.\n"
    "  options:\n"
    "    num_ctx: 12345\n"
    "    temperature: null\n"
    "USER_MARKER: keep-me\n"
)


@pytest.fixture
def dirs(tmp_path):
    d = tmp_path / "defaults"
    shutil.copytree(DEFAULTS, d)
    c = tmp_path / "configs"
    c.mkdir()
    return c, d


def _mtimes(c: Path) -> dict:
    return {
        str(p): p.stat().st_mtime_ns
        for p in c.rglob("*")
        if p.is_file() and cs.LOCK_DIRNAME not in p.parts
    }


def test_fresh_install_gets_every_template_then_writes_nothing(dirs):
    c, d = dirs
    rep = cm.run(c, d)
    for src in d.rglob("*"):
        if src.is_file():
            assert (c / src.relative_to(d)).read_bytes() == src.read_bytes()
    assert cs.read_yaml(c / cm.VERSION_FILE)["steps"] == cm.LATEST
    assert rep.snapshot is not None
    before = _mtimes(c)
    rep2 = cm.run(c, d)
    assert rep2.up_to_date and _mtimes(c) == before


def test_upgrade_keeps_user_values_comments_and_converts_old_forms(dirs, monkeypatch):
    monkeypatch.setattr(cm, "_IN_DOCKER", True)
    c, d = dirs
    (c / "llm.yaml").write_text(USER_LLM)
    (c / "nodes.yaml").write_text(
        "nodes:\n  stue:\n    is_tv: true\n    entity: media_player.tv  # living room\n"
    )
    (c / "settings.yaml").write_text("network:\n  local_subnet: 10.9.8.0/24\nassistant_name: Bob\n")
    (c / "services.yaml").write_text(
        "memory_embed:\n  model_dir: /data/old\ninternal:\n  semantic_embed: http://kaare-memory-embed:11500\n"
        "voice:\n  stt:\n    faster_whisper_model: large-v3\n"
    )
    rep = cm.run(c, d)
    llm = cs.read_yaml(c / "llm.yaml")
    assert llm["default"]["base_url"] == "http://USER-SERVER:11434"
    assert llm["default"]["options"]["num_ctx"] == 12345
    assert llm["USER_MARKER"] == "keep-me"
    assert llm["default"]["system"]["nb"] == "Du er min egen Kåre."
    assert set(llm["default"]["system"]) == {"nb", "en", "de"}
    tpl_llm = yaml.safe_load((d / "llm.yaml").read_text())
    assert set(tpl_llm) <= set(llm)  # every template section present
    t = (tpl_llm.get("default") or {}).get("options", {}).get("temperature")
    assert llm["default"]["options"]["temperature"] == t  # null = missing → template (D6)
    text = (c / "llm.yaml").read_text()
    assert "# my own notes about the model" in text and "# my box" in text
    nodes = cs.read_yaml(c / "nodes.yaml")["nodes"]["stue"]
    assert (
        nodes["has_display"] is True
        and "is_tv" not in nodes
        and nodes["entity"] == "media_player.tv"
    )
    st = cs.read_yaml(c / "settings.yaml")
    assert st["network"]["local_subnets"] == ["10.9.8.0/24"] and "local_subnet" not in st["network"]
    assert st["assistant_name"] == "Bob"
    svc = cs.read_yaml(c / "services.yaml")
    assert svc["memory_embed"]["model"] == "minilm"
    assert svc["internal"]["semantic_embed"] == "http://kaare-semantic-embed:11500"
    assert svc["voice"]["stt"]["faster_whisper_model"] == ""
    assert rep.snapshot is not None and (rep.snapshot / "llm.yaml").read_text() == USER_LLM
    assert any("step 4" in ch for ch in rep.changes)


def test_steps_run_once(dirs):
    c, d = dirs
    cm.run(c, d)
    nodes = c / "nodes.yaml"
    nodes.write_text("nodes:\n  x:\n    is_tv: true\n")
    (d / "radio_stations.yaml").write_text(
        (d / "radio_stations.yaml").read_text() + "\np134_new_key: 1\n"
    )
    cm.run(c, d)  # template changed → fill runs, but step 3 is done
    assert cs.read_yaml(nodes)["nodes"]["x"] == {"is_tv": True}
    assert cs.read_yaml(c / "radio_stations.yaml")["p134_new_key"] == 1


def test_new_template_key_reaches_existing_install(dirs):
    c, d = dirs
    cm.run(c, d)
    (c / "settings.yaml").write_text((c / "settings.yaml").read_text() + "\nassistant_name: Bob\n")
    with cs.edit_yaml(d / "settings.yaml", "test") as tpl:
        tpl["p134_feature"] = {"enabled": False}
    cm.run(c, d)
    st = cs.read_yaml(c / "settings.yaml")
    assert st["p134_feature"] == {"enabled": False} and st["assistant_name"] == "Bob"


@pytest.mark.parametrize("content", ["", "a: [broken\n"])
def test_corrupt_or_empty_file_is_kept_then_replaced(dirs, content):
    c, d = dirs
    cm.run(c, d)
    (c / "models.yaml").write_text(content)
    (d / "models.yaml").write_text(
        (d / "models.yaml").read_text() + "\n# touched\n"
    )  # new templates
    cm.run(c, d)
    kept = [h for h in cs.history(c / "models.yaml") if cs.CORRUPT_TAG in h.name]
    assert len(kept) == 1 and kept[0].read_text() == content
    assert cs.read_yaml(c / "models.yaml") == yaml.safe_load((d / "models.yaml").read_text())


def test_non_mapping_file_is_left_alone(dirs):
    c, d = dirs
    cm.run(c, d)
    (c / "models.yaml").write_text("- a\n- b\n")
    (d / "models.yaml").write_text((d / "models.yaml").read_text() + "\n# touched\n")
    rep = cm.run(c, d)
    assert (c / "models.yaml").read_text() == "- a\n- b\n"
    assert any("not a mapping" in w for w in rep.warnings)


def test_dry_run_writes_nothing(dirs):
    c, d = dirs
    (c / "llm.yaml").write_text(USER_LLM)
    before = _mtimes(c)
    rep = cm.run(c, d, dry_run=True)
    assert _mtimes(c) == before and not (c / cm.VERSION_FILE).exists()
    assert any("llm.yaml" in ch for ch in rep.changes)
    assert not (c / cs.HISTORY_DIRNAME).exists()


def test_legacy_root_capability_map_is_preferred(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_IN_DOCKER", False)
    root = tmp_path / "kaare"
    c = root / "configs"
    c.mkdir(parents=True)
    d = tmp_path / "defaults"
    shutil.copytree(DEFAULTS, d)
    (root / "capability_map.yaml").write_text("profile: mine\n")
    cm.run(c, d)
    assert cs.read_yaml(c / "capability_map.yaml")["profile"] == "mine"
    assert (root / "capability_map.yaml").exists()  # never removed


def test_cli_dry_run_and_exit_code(dirs):
    c, d = dirs
    cmd = [
        sys.executable,
        "/kaare/scripts/migrate_configs.py",
        "--config-dir",
        str(c),
        "--defaults-dir",
        str(d),
    ]
    r = subprocess.run([*cmd, "--dry-run"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "DRY RUN" in r.stdout
    assert [p.name for p in c.iterdir()] == [cs.LOCK_DIRNAME]  # only the lock, no config written
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "applied" in r.stdout
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "up to date" in r.stdout


# ── the race that lost user settings (206/300 before P134) ────────────────────


def _run_migration(c: str, d: str, delay: float) -> None:
    time.sleep(delay)
    cm.run(Path(c), Path(d))


def _race_round(tmp: Path, stagger: bool) -> bool:
    c, d = tmp / "configs", tmp / "defaults"
    if c.exists():
        shutil.rmtree(c)
    c.mkdir()
    (c / "llm.yaml").write_text(USER_LLM)
    ctx = mp.get_context("spawn")  # not fork: pytest is multi-threaded by now
    procs = [
        ctx.Process(
            target=_run_migration, args=(str(c), str(d), random.uniform(0, 0.3) if stagger else 0)
        )
        for _ in range(9)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    llm = cs.read_yaml(c / "llm.yaml")
    ok = all(p.exitcode == 0 for p in procs)
    return (
        ok
        and llm["default"]["base_url"] == "http://USER-SERVER:11434"
        and llm.get("USER_MARKER") == "keep-me"
    )


@pytest.mark.parametrize("stagger", [False, True], ids=["simultaneous", "staggered"])
def test_nine_containers_starting_at_once_lose_nothing(tmp_path, stagger):
    shutil.copytree(DEFAULTS, tmp_path / "defaults")
    lost = sum(not _race_round(tmp_path, stagger) for _ in range(RACE_ROUNDS))
    assert lost == 0, f"{lost}/{RACE_ROUNDS} rounds lost the user's values"


def test_template_examples_never_enter_user_collections(dirs):
    c, d = dirs
    (c / "nodes.yaml").write_text("nodes:\n  my_speaker:\n    entity: media_player.x\n")
    (c / "aliases.yaml").write_text("rooms:\n  loft: [loft]\n")
    cm.run(c, d)
    assert list(cs.read_yaml(c / "nodes.yaml")["nodes"]) == ["my_speaker"]
    al = cs.read_yaml(c / "aliases.yaml")
    assert list(al["rooms"]) == ["loft"]
    assert (
        al["room_entities"] == yaml.safe_load((d / "aliases.yaml").read_text())["room_entities"]
    )  # whole map missing


def test_user_collection_paths_exist_in_templates():
    for name, paths in cm.USER_COLLECTIONS.items():
        tpl = yaml.safe_load((DEFAULTS / name).read_text())
        for p in paths:
            node = tpl
            for part in p.split("."):
                node = node[part]
            assert isinstance(node, dict), f"{name}:{p}"


def test_large_v3_stays_on_bare_metal(dirs, monkeypatch):
    monkeypatch.setattr(cm, "_IN_DOCKER", False)
    c, d = dirs
    (c / "services.yaml").write_text("voice:\n  stt:\n    faster_whisper_model: large-v3\n")
    cm.run(c, d)
    assert cs.read_yaml(c / "services.yaml")["voice"]["stt"]["faster_whisper_model"] == "large-v3"


def test_old_trusted_category_names_are_renamed_not_duplicated(dirs):
    c, d = dirs
    (c / "trusted_sources.yaml").write_text(
        "sources:\n  film_og_serier:  # mine\n  - domain: mysite.no\n    trust: high\n  vær_og_natur: []\n"
    )
    cm.run(c, d)
    src = cs.read_yaml(c / "trusted_sources.yaml")["sources"]
    assert "film_og_serier" not in src and src["film_and_tv"] == [
        {"domain": "mysite.no", "trust": "high"}
    ]
    assert src["weather_and_nature"] == []
    tpl = yaml.safe_load((d / "trusted_sources.yaml").read_text())["sources"]
    assert set(src) == set(tpl)  # the new categories arrive, no duplicates


def test_pre_p127_voice_settings_move_with_the_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_IN_DOCKER", False)
    root = tmp_path / "kaare"
    c = root / "configs"
    c.mkdir(parents=True)
    d = tmp_path / "defaults"
    shutil.copytree(DEFAULTS, d)
    old = root / "services/voice/configs/voice_settings.yaml"
    old.parent.mkdir(parents=True)
    old.write_text("tts:\n  voice: my-own-voice  # chosen in the GUI\n")
    cm.run(c, d)
    new = cs.read_yaml(c / "voice_settings.yaml")
    assert new["tts"]["voice"] == "my-own-voice"
    tpl = yaml.safe_load((d / "voice_settings.yaml").read_text())
    assert set(tpl) <= set(new)  # the moved file got the template's other keys in the same run
    assert "# chosen in the GUI" in (c / "voice_settings.yaml").read_text()
    assert old.exists()  # never removed


def test_null_in_template_and_file_is_not_a_change():
    data = {"a": None, "b": {"c": None}}
    assert cm.fill_missing_keys(data, {"a": None, "b": {"c": None}, "d": None}) == ["d"]
    assert data == {"a": None, "b": {"c": None}, "d": None}


def test_docker_ignores_legacy_copies_inside_the_image(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_IN_DOCKER", True)
    root = tmp_path / "kaare"
    c = root / "configs"
    c.mkdir(parents=True)
    d = tmp_path / "defaults"
    shutil.copytree(DEFAULTS, d)
    (root / "capability_map.yaml").write_text("profile: stale-image-copy\n")
    cm.run(c, d)
    assert (c / "capability_map.yaml").read_bytes() == (d / "capability_map.yaml").read_bytes()


def test_rerun_with_nothing_to_change_reports_up_to_date(dirs):
    c, d = dirs
    cm.run(c, d)
    cm.request_rerun(c)
    rep = cm.run(c, d)
    assert rep.lines() == ["[migrate] All configs up to date."]
    assert cs.read_yaml(c / cm.VERSION_FILE)["steps"] == cm.LATEST
