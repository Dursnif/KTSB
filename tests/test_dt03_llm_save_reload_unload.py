"""LLM model save from the GUI (DT03 R2 2.9, CLAUDE.md rules 24/25).

Two bugs found on Verksted-pc 2026-10-06:
- `PUT /api/settings/llm/{role}` reloaded config BEFORE writing models.yaml, and
  `PUT /api/settings/models` never reloaded → a model change did not apply until a restart.
- Saving a new model unloaded the old one from the role's Ollama instance even when another role
  still used it → the shared 9B (on Docker: the main agent's model) was evicted.
"""

import asyncio

import yaml

from kaare_core.routers import router_settings as rs

HOUSE = {
    "default": {"provider": "vllm", "base_url": "http://127.0.0.1:11440", "model_role": "kare"},
    "fallback": {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11445",
        "model_role": "miss_kare",
    },
    "miss_kare": {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11447",
        "model_role": "miss_kare",
    },
    "library": {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11447/",
        "model_role": "library",
    },
    "mechanic": {"share_with": "miss_kare"},
}
DOCKER = {
    "default": {"provider": "ollama", "base_url": "http://ollama:11434", "model_role": "kare"},
    "miss_kare": {
        "provider": "ollama",
        "base_url": "http://ollama:11434",
        "model_role": "miss_kare",
    },
    "library": {"provider": "ollama", "base_url": "http://ollama:11434", "model_role": "library"},
    "mechanic": {"share_with": "miss_kare"},
}
NINE = "qwen3.5-abliterated:9b"


def test_house_library_switch_keeps_miss_kares_model():
    models = {"kare": "27b", "miss_kare": NINE, "library": "other:1b"}  # after the library update
    assert rs._model_used_elsewhere(HOUSE, models, "library", "http://127.0.0.1:11447", NINE)


def test_docker_agent_switch_keeps_main_agents_model():
    models = {"kare": NINE, "miss_kare": NINE, "library": "other:1b"}
    assert rs._model_used_elsewhere(DOCKER, models, "library", "http://ollama:11434", NINE)


def test_followers_change_with_the_role_so_the_old_model_is_free():
    # miss_kare switches; mechanic shares with it, fallback shares its model_role key → both follow
    house = {k: v for k, v in HOUSE.items() if k != "library"}
    models = {"kare": "27b", "miss_kare": "new:4b"}
    assert not rs._model_used_elsewhere(house, models, "miss_kare", "http://127.0.0.1:11447", NINE)


def test_other_instance_and_disabled_roles_do_not_count():
    llm = {
        "miss_kare": {
            "provider": "ollama",
            "base_url": "http://a:11434",
            "model_role": "miss_kare",
        },
        "library": {"provider": "ollama", "base_url": "http://b:11434", "model_role": "library"},
        "fallback": {
            "provider": "ollama",
            "base_url": "http://a:11434",
            "model_role": "fallback",
            "enabled": False,
        },
    }
    models = {"miss_kare": "new", "library": NINE, "fallback": NINE}
    assert not rs._model_used_elsewhere(llm, models, "miss_kare", "http://a:11434", NINE)


def _setup(tmp_path, monkeypatch, llm, models):
    llm_path, models_path = tmp_path / "llm.yaml", tmp_path / "models.yaml"
    llm_path.write_text(yaml.dump(llm), encoding="utf-8")
    models_path.write_text(yaml.dump(models), encoding="utf-8")
    monkeypatch.setattr(rs, "_LLM_PATH", llm_path)
    monkeypatch.setattr(rs, "_MODELS_PATH", models_path)
    seen, unloaded = [], []
    monkeypatch.setattr(
        rs,
        "_reload_llm_and_core",
        lambda: seen.append(yaml.safe_load(models_path.read_text(encoding="utf-8"))),
    )
    monkeypatch.setattr(rs, "_reload_agent_enabled", lambda: None)
    monkeypatch.setattr(rs, "_start_warmup_task", lambda *a, **k: None)

    async def _fake_unload(url, model):
        unloaded.append((url, model))

    monkeypatch.setattr(rs, "_unload_ollama_model", _fake_unload)
    return seen, unloaded


def test_llm_role_save_reloads_after_models_yaml_is_written(tmp_path, monkeypatch):
    seen, unloaded = _setup(
        tmp_path, monkeypatch, DOCKER, {"kare": NINE, "miss_kare": NINE, "library": NINE}
    )

    async def run():
        r = await rs.api_put_llm_role("library", {"model": "other:1b"}, _u=None)
        await asyncio.sleep(0)  # let a created unload task run
        return r

    assert asyncio.run(run())["ok"]
    assert seen and seen[-1]["library"] == "other:1b"  # the reload saw the new model
    assert unloaded == []  # 9B still used by default + miss_kare


def test_llm_role_save_unloads_when_nobody_else_uses_it(tmp_path, monkeypatch):
    llm = {"library": {"provider": "ollama", "base_url": "http://x:11434", "model_role": "library"}}
    _seen, unloaded = _setup(tmp_path, monkeypatch, llm, {"library": NINE})

    async def run():
        await rs.api_put_llm_role("library", {"model": "other:1b"}, _u=None)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert unloaded == [("http://x:11434", NINE)]


def test_models_save_reloads(tmp_path, monkeypatch):
    seen, _ = _setup(tmp_path, monkeypatch, DOCKER, {"kare": NINE})
    asyncio.run(rs.api_put_models({"kare": "new:27b"}, _u=None))
    assert seen and seen[-1]["kare"] == "new:27b"
