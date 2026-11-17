"""
P144-s10 — «Borrow the model from …»: validation of `share_with` in the role PUT.

- A role may only borrow from an existing role in its allowed set (Frigate roles: `default` = main agent,
  `miss_kare` = sub-agents; Mechanic: `miss_kare`), never itself, never into a cycle. 400 otherwise, and
  llm.yaml stays untouched (fail closed).
- Borrowing writes only `share_with` (+ `enabled`): no own fields, no models.yaml write, no warmup/unload — the
  lender keeps its own URL/model/num_ctx, so Ollama never reloads.
- Expert «Own model» (`share_with: null` + own fields) still works.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p144_s10_share_with.py -v
"""

import asyncio
import sys

import pytest
import yaml
from fastapi import HTTPException

sys.path.insert(0, "/kaare")

from kaare_core.routers import router_settings

_LLM = {
    "default": {"provider": "vllm", "base_url": "http://127.0.0.1:11440", "model_role": "kare"},
    "miss_kare": {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11447",
        "model_role": "miss_kare",
        "options": {"num_ctx": 32768},
    },
    "mechanic": {"enabled": True, "share_with": "miss_kare"},
    "library": {"provider": "ollama", "base_url": "http://127.0.0.1:11447", "model_role": "library"},
    "frigate": {"enabled": True, "share_with": "default"},
    "frigate_conclude": {"enabled": True, "share_with": "default"},
}
_MODELS = {"kare": "big-27b", "miss_kare": "agent-9b", "library": "agent-9b", "frigate": "old-vlm"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    llm_path = tmp_path / "llm.yaml"
    models_path = tmp_path / "models.yaml"
    llm_path.write_text(yaml.safe_dump(_LLM), encoding="utf-8")
    models_path.write_text(yaml.safe_dump(_MODELS), encoding="utf-8")
    monkeypatch.setattr(router_settings, "_LLM_PATH", llm_path)
    monkeypatch.setattr(router_settings, "_MODELS_PATH", models_path)
    monkeypatch.setattr(router_settings, "_reload_agent_enabled", lambda: None)
    monkeypatch.setattr(router_settings, "_reload_llm_and_core", lambda: None)
    side = {"warmups": [], "spawned": []}
    monkeypatch.setattr(router_settings, "_start_warmup_task", lambda *a, **kw: side["warmups"].append(a))
    monkeypatch.setattr(router_settings, "spawn", lambda coro: (side["spawned"].append(coro), coro.close()))

    def put(role, payload):
        return asyncio.run(router_settings.api_put_llm_role(role, payload, _u=None))

    def llm():
        return yaml.safe_load(llm_path.read_text(encoding="utf-8"))

    def models():
        return yaml.safe_load(models_path.read_text(encoding="utf-8"))

    return put, llm, models, side


def _status(exc_info):
    return exc_info.value.status_code


@pytest.mark.parametrize("role", ["frigate", "frigate_conclude"])
def test_borrow_from_sub_agents(env, role):
    put, llm, _, _ = env
    put(role, {"share_with": "miss_kare"})
    assert llm()[role] == {"share_with": "miss_kare", "enabled": True}


@pytest.mark.parametrize("role", ["frigate", "frigate_conclude"])
def test_borrow_from_main_agent(env, role):
    put, llm, _, _ = env
    put(role, {"share_with": "miss_kare"})
    put(role, {"share_with": "default"})
    assert llm()[role] == {"share_with": "default", "enabled": True}


def test_mechanic_still_borrows_from_miss_kare(env):
    put, llm, _, _ = env
    put("mechanic", {"share_with": "miss_kare"})
    assert llm()["mechanic"]["share_with"] == "miss_kare"


@pytest.mark.parametrize("target", ["nonexistent", "library", "cloud", "mechanic", 5, ["default"]])
def test_unknown_or_disallowed_target_is_refused(env, target):
    put, llm, _, _ = env
    before = llm()
    with pytest.raises(HTTPException) as e:
        put("frigate", {"share_with": target})
    assert _status(e) == 400
    assert llm() == before


def test_self_is_refused(env):
    put, llm, _, _ = env
    before = llm()
    with pytest.raises(HTTPException) as e:
        put("frigate", {"share_with": "frigate"})
    assert _status(e) == 400
    assert llm() == before


def test_cycle_is_refused(env, tmp_path):
    put, llm, _, _ = env
    # Hand-edited llm.yaml: miss_kare borrows from frigate. frigate → miss_kare would close a loop.
    data = llm()
    data["miss_kare"] = {"enabled": True, "share_with": "frigate"}
    router_settings._LLM_PATH.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(HTTPException) as e:
        put("frigate", {"share_with": "miss_kare"})
    assert _status(e) == 400
    assert llm() == data


def test_role_without_borrow_option_is_refused(env):
    put, llm, _, _ = env
    before = llm()
    with pytest.raises(HTTPException) as e:
        put("library", {"share_with": "default"})
    assert _status(e) == 400
    assert llm() == before


def test_borrowing_writes_no_own_fields_and_never_warms_the_lender(env):
    # The card used to send the lender's resolved provider/base_url/model along with share_with: the router then
    # wrote models.yaml[frigate] and started a warmup on the lender's Ollama with num_ctx 8192 (the borrower has
    # no options) — a differing num_ctx forces a full Ollama reload of the sub-agents' model.
    put, llm, models, side = env
    put("frigate", {
        "share_with": "miss_kare", "provider": "ollama", "base_url": "http://127.0.0.1:11447",
        "model": "agent-9b", "options": {"num_ctx": 8192}, "keep_warm": True,
    })
    assert llm()["frigate"] == {"share_with": "miss_kare", "enabled": True}
    assert models() == _MODELS
    assert side["warmups"] == [] and side["spawned"] == []


def test_expert_own_model_ok(env):
    put, llm, models, _ = env
    put("frigate", {
        "share_with": None, "provider": "ollama", "base_url": "http://10.9.9.9:11434", "model": "own-vlm",
    })
    cfg = llm()["frigate"]
    assert "share_with" not in cfg
    assert cfg["provider"] == "ollama" and cfg["base_url"] == "http://10.9.9.9:11434"
    assert models()["frigate"] == "own-vlm"
