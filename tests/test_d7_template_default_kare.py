"""The shipped template must work unmodified in Docker (DT03 D7, CLAUDE.md rule 24).

Kåre's default is the built-in Ollama with the 9B (lightest tier that works); 27B via vLLM is
the GUI upgrade. These tests pin: every Ollama role points at the built-in instance, roles that
load the same model there share one num_ctx (else Ollama reloads the model — CLAUDE.md GPU
policy), the window fits Kåre's real prompts, the token guard reads num_ctx on Ollama, and the
onboarding Ollama choice also moves Kåre's own role.
"""

import asyncio
from pathlib import Path

import yaml

from kaare_core import config as _config
from kaare_core.routers import router_generate as rg
from kaare_core.routers import router_media

_DEFAULTS = Path("/kaare/configs_default")


def _template_llm() -> dict:
    return _config.apply_agent_pool(
        yaml.safe_load((_DEFAULTS / "llm.yaml").read_text(encoding="utf-8"))
    )


def _template_models() -> dict:
    return yaml.safe_load((_DEFAULTS / "models.yaml").read_text(encoding="utf-8"))


def _resolved(llm: dict, role: str) -> dict:
    cfg = llm[role]
    return (
        dict(llm[cfg["share_with"]], enabled=cfg.get("enabled", True))
        if cfg.get("share_with")
        else cfg
    )


def test_template_kare_is_built_in_ollama_9b():
    llm, models = _template_llm(), _template_models()
    kare = llm["default"]
    assert kare["provider"] == "ollama"
    assert kare["base_url"] == "http://ollama:11434"
    assert models["kare"] == "huihui_ai/qwen3.5-abliterated:9b"
    assert kare["vllm_docker"]["max_model_len"] > 0  # upgrade path kept


def test_template_ollama_roles_use_the_built_in_instance():
    llm = _template_llm()
    services = yaml.safe_load((_DEFAULTS / "services.yaml").read_text(encoding="utf-8"))
    builtin = services["ollama"]["kare"].rstrip("/")
    for role in (
        "default",
        "fallback",
        "miss_kare",
        "library",
        "mechanic",
        "reason_freely",
        "vision",
    ):
        cfg = _resolved(llm, role)
        assert cfg["provider"] == "ollama", role
        assert cfg["base_url"].rstrip("/") == builtin, role


def test_same_model_on_same_instance_shares_num_ctx():
    llm, models = _template_llm(), _template_models()
    seen: dict[tuple[str, str], tuple[str, int]] = {}
    for role in llm:
        if role == "agent_pool" or not isinstance(llm[role], dict):
            continue
        cfg = _resolved(llm, role)
        if cfg.get("provider") != "ollama" or cfg.get("enabled") is False:
            continue
        model = models.get(cfg.get("model_role", role))
        num_ctx = int((cfg.get("options") or {}).get("num_ctx") or 0)
        key = (cfg["base_url"].rstrip("/"), model)
        if key in seen:
            assert seen[key][1] == num_ctx, (
                f"{role} num_ctx {num_ctx} != {seen[key][0]} {seen[key][1]} for {key}"
            )
        else:
            seen[key] = (role, num_ctx)


def test_template_system_prompt_names_only_real_tools():
    # the prompt pointed at «ask_miss_library» / «spør_miss_library», renamed to `library` in P100
    for lang, text in _template_llm()["default"]["system"].items():
        assert "miss_library" not in text.replace("Miss Library", ""), lang


def test_template_window_fits_kares_real_prompts():
    # live GUI chat turns on this house: p50 ~19k, p90 ~22k prompt tokens (2026-10-06)
    opts = _template_llm()["default"]["options"]
    assert int(opts["num_ctx"]) - int(opts["num_predict"]) >= 24000


def test_input_cap_on_ollama_uses_num_ctx(monkeypatch):
    cfg = {
        "provider": "ollama",
        "options": {"num_ctx": 32768, "num_predict": 2048, "max_tokens": 8192},
        "vllm_docker": {"max_model_len": 51200},
    }
    monkeypatch.setattr(rg, "get_llm_config", lambda role: cfg)
    assert rg._input_cap("kare") == 32768 - 2048
    cfg["provider"] = "vllm"
    assert rg._input_cap("kare") == 51200 - 8192


def test_onboarding_ollama_choice_moves_kare_too(monkeypatch, tmp_path):
    llm = {
        "default": {"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
        "fallback": {"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
        "mechanic": {"share_with": "miss_kare"},
        "miss_kare": {"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
        "cloud": {"provider": "huggingface", "base_url": "https://router.huggingface.co/v1"},
    }
    llm_path, svc_path = tmp_path / "llm.yaml", tmp_path / "services.yaml"
    llm_path.write_text(yaml.dump(llm), encoding="utf-8")
    svc_path.write_text(yaml.dump({"ollama": {}}), encoding="utf-8")
    monkeypatch.setattr(router_media, "_LLM_PATH", llm_path)
    monkeypatch.setattr(router_media, "_SERVICES_PATH", svc_path)
    reloaded = []
    monkeypatch.setattr(router_media, "_reload_core_config", lambda: reloaded.append("core"))
    monkeypatch.setattr(router_media, "_reload_llm_config", lambda: reloaded.append("llm"))
    asyncio.run(router_media.api_put_ollama_source({"url": "http://ollama:11434/"}, _u=None))
    assert reloaded == ["core", "llm"]  # applied at once, not only after a restart (DT03 R3)
    out = yaml.safe_load(llm_path.read_text(encoding="utf-8"))
    assert out["default"]["base_url"] == "http://ollama:11434"
    assert out["miss_kare"]["base_url"] == "http://ollama:11434"
    assert out["cloud"]["base_url"] == "https://router.huggingface.co/v1"
    got = asyncio.run(router_media.api_get_ollama_source(_u=None))
    assert got == {"url": "http://ollama:11434", "builtin": True}
