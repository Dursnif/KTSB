"""P134 Phase 3 — the GUI's settings writers go through the config store: two saves at once both survive,
comments survive, history is kept."""

import asyncio
import multiprocessing as mp
import threading
from pathlib import Path

import yaml

import kaare_core.config as config
import kaare_core.routers.router_settings as rs
import kaare_core.users.auth as auth
from kaare_core import config_store as cs


def _settings(tmp_path, monkeypatch):
    p = tmp_path / "settings.yaml"
    p.write_text(
        "# household settings — my notes\nlanguage: nb\nlocation:\n  city: A  # home\nother: keep\n"
    )
    monkeypatch.setattr(rs, "_SETTINGS_PATH", p)
    return p


def test_two_endpoints_saving_at_once_lose_nothing(tmp_path, monkeypatch):
    p = _settings(tmp_path, monkeypatch)
    n = 40

    def loc():
        for i in range(n):
            asyncio.run(rs.api_put_location({"city": f"c{i}"}, None))

    def lang():
        for i in range(n):
            asyncio.run(rs.api_put_language({"kare_language": f"l{i}"}, None))

    threads = [threading.Thread(target=loc), threading.Thread(target=lang)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    data = yaml.safe_load(p.read_text())
    assert data["location"]["city"] == f"c{n - 1}"
    assert data["kare_language"] == f"l{n - 1}"
    assert data["other"] == "keep" and data["language"] == "nb"
    assert "# household settings — my notes" in p.read_text()


def test_gui_save_keeps_comments_and_history(tmp_path, monkeypatch):
    p = _settings(tmp_path, monkeypatch)
    asyncio.run(rs.api_put_location({"city": "B"}, None))
    assert "# home" in p.read_text() and yaml.safe_load(p.read_text())["location"]["city"] == "B"
    hist = cs.history(p)
    assert len(hist) == 1 and "gui-put_location" in hist[0].name
    assert yaml.safe_load(hist[0].read_text())["location"]["city"] == "A"


def test_env_key_writer_keeps_other_lines(tmp_path):
    env = tmp_path / "x.env"
    env.write_text("# comment\nA=1\nB=2\n")
    rs._write_env_key(env, "B", "3")
    rs._write_env_key(env, "C", "4")
    assert env.read_text() == "# comment\nA=1\nB=3\nC=4\n"


def test_mqtt_credentials_replace_only_their_lines(tmp_path, monkeypatch):
    env = tmp_path / "mqtt.env"
    env.write_text("MQTT_HOST=h\nMQTT_USER=old\nMQTT_PASSWORD=old\n")
    monkeypatch.setattr(rs, "_MQTT_ENV_PATH", env)
    rs._write_mqtt_env("u", "p")
    assert env.read_text() == "MQTT_HOST=h\nMQTT_USER=u\nMQTT_PASSWORD=p\n"


def _jwt_secret(path: str, q) -> None:
    auth.SECRET_PATH = Path(path)  # child process: patch only its own copy
    q.put(auth._load_or_create_secret())


def test_processes_creating_the_jwt_secret_at_once_agree(tmp_path):
    p = tmp_path / "auth.env"
    ctx = mp.get_context("spawn")  # not fork: pytest is multi-threaded by now
    q = ctx.Queue()
    procs = [ctx.Process(target=_jwt_secret, args=(str(p), q)) for _ in range(8)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(30)
    got = {q.get(timeout=5) for _ in procs}
    assert len(got) == 1
    assert p.read_text().count("JWT_SECRET=") == 1


def test_config_import_migrates_or_waits_in_docker_only(monkeypatch):
    calls = []
    monkeypatch.setattr(config.config_migrations, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(config, "_IN_DOCKER", False)
    config._migrate_or_wait_in_docker()
    assert calls == []
    monkeypatch.setattr(config, "_IN_DOCKER", True)
    config._migrate_or_wait_in_docker()
    assert calls == [(config.CONFIG_DIR, config._DEFAULT_DIR)]


def test_stray_top_level_value_in_llm_yaml_does_not_break_container_list(monkeypatch):
    monkeypatch.setattr(
        config, "_LLM_CFG", {"default": {"container": "c1"}, "USER_MARKER": "keep-me"}
    )
    assert config.get_known_containers() == ["c1"]
