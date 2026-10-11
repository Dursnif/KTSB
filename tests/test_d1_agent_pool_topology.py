"""Agent pool topology comes from llm.yaml, never hardcoded (DT03 D1, CLAUDE.md rule 24).

Before 2026-10-06 the pool was (11445, 11447) and callers called http://127.0.0.1:<port>, so a
Docker install (http://ollama:11434) reached nothing. These tests pin both topologies: this
house (two local instances that overflow onto each other) and a plain Docker install (one
instance by container name, no overflow).
"""

import asyncio
import copy
import subprocess
import sys

import pytest

from kaare_core import config as _config
from kaare_core import llm_model_check, model_lock

_POOL = {
    "options": {"num_ctx": 8192},
    "think": False,
    "roles": ["fallback", "miss_kare", "library", "mechanic"],
}


def _raw(urls: dict[str, str]) -> dict:
    """llm.yaml-shaped dict: Kåre on vLLM, the four pool roles on the given base_urls."""
    raw = {
        "agent_pool": copy.deepcopy(_POOL),
        "default": {"provider": "vllm", "base_url": "http://127.0.0.1:11440", "model_role": "kare"},
        "fallback": {"provider": "ollama", "base_url": urls["fallback"], "model_role": "miss_kare"},
        "miss_kare": {
            "provider": "ollama",
            "base_url": urls["miss_kare"],
            "model_role": "miss_kare",
        },
        "library": {"provider": "ollama", "base_url": urls["library"], "model_role": "library"},
        "mechanic": {"share_with": "miss_kare"},
    }
    return raw


HOUSE = {
    "fallback": "http://127.0.0.1:11445",
    "miss_kare": "http://127.0.0.1:11447",
    "library": "http://127.0.0.1:11447",
}
DOCKER = {
    "fallback": "http://ollama:11434",
    "miss_kare": "http://ollama:11434",
    "library": "http://ollama:11434/",
}


@pytest.fixture
def llm(monkeypatch, tmp_path):
    monkeypatch.setattr(model_lock, "_LOCK_DIR", str(tmp_path))

    def _set(urls: dict[str, str], **extra) -> None:
        raw = _raw(urls)
        raw.update(extra)
        monkeypatch.setattr(_config, "_LLM_CFG", _config.apply_agent_pool(raw))

    return _set


def test_house_pool_is_both_local_instances_in_yaml_order(llm):
    llm(HOUSE)
    assert model_lock.pool_urls() == ["http://127.0.0.1:11445", "http://127.0.0.1:11447"]
    assert model_lock.pool_order(model_lock.role_url("miss_kare")) == [
        "http://127.0.0.1:11447",
        "http://127.0.0.1:11445",
    ]
    # share_with resolves: Mechanic lands where Miss Kåre lives, with overflow
    assert model_lock.role_url("mechanic") == "http://127.0.0.1:11447"
    assert model_lock.pool_order(model_lock.role_url("fallback"))[0] == "http://127.0.0.1:11445"


def test_docker_pool_is_one_instance_by_container_name(llm):
    llm(DOCKER)
    assert model_lock.pool_urls() == ["http://ollama:11434"]
    for role in ("fallback", "miss_kare", "library", "mechanic"):
        order = model_lock.pool_order(model_lock.role_url(role))
        assert order == ["http://ollama:11434"], role
        assert not any("127.0.0.1" in u for u in order)


def test_instance_outside_pool_gets_no_overflow(llm):
    llm(HOUSE)
    assert model_lock.pool_order("http://192.168.0.50:11434") == ["http://192.168.0.50:11434"]


def test_disabled_and_non_ollama_roles_are_not_pool_members(llm):
    llm(HOUSE)
    _config._LLM_CFG["fallback"]["enabled"] = False
    assert model_lock.pool_urls() == ["http://127.0.0.1:11447"]
    _config._LLM_CFG["fallback"]["enabled"] = True
    _config._LLM_CFG["library"]["provider"] = "vllm"
    _config._LLM_CFG["miss_kare"]["provider"] = "vllm"
    assert model_lock.pool_urls() == ["http://127.0.0.1:11445"]


def test_lock_file_names(llm):
    llm(HOUSE)
    # local instances keep the pre-D1 name so old and new processes share one lock
    assert model_lock._lock_path("http://127.0.0.1:11447").endswith("/model_agent_11447.lock")
    assert model_lock._lock_path("http://localhost:11445/").endswith("/model_agent_11445.lock")
    assert model_lock._lock_path("http://ollama:11434").endswith("/model_agent_ollama_11434.lock")
    assert model_lock._lock_path("http://192.168.0.50:11434").endswith(
        "/model_agent_192.168.0.50_11434.lock"
    )


def test_pool_lock_yields_url_and_overflows_to_free_instance(llm):
    llm(HOUSE)

    async def run():
        order = model_lock.pool_order("http://127.0.0.1:11447")
        async with model_lock.lock_agent_pool(order, "first") as first:
            assert first == "http://127.0.0.1:11447"
            async with model_lock.lock_agent_pool(order, "second", max_wait=2) as second:
                assert second == "http://127.0.0.1:11445"
            with pytest.raises(model_lock.LockTimeout):
                async with model_lock.lock_agent_pool(
                    ["http://127.0.0.1:11447"], "third", max_wait=0.5
                ):
                    pass

    asyncio.run(run())


def test_docker_single_instance_serialises(llm):
    llm(DOCKER)

    async def run():
        order = model_lock.pool_order(model_lock.role_url("mechanic"))
        async with model_lock.lock_agent_pool(order, "mechanic") as url:
            assert url == "http://ollama:11434"
            with pytest.raises(model_lock.LockTimeout):
                async with model_lock.lock_agent_pool(order, "miss_kare", max_wait=0.5):
                    pass

    asyncio.run(run())


def test_empty_pool_list_raises_lock_timeout(llm):
    llm(HOUSE)

    async def run():
        with pytest.raises(model_lock.LockTimeout):
            async with model_lock.lock_agent_pool([""], "nobody", max_wait=0.1):
                pass

    asyncio.run(run())


def test_model_check_follows_the_same_topology(llm):
    llm(HOUSE)
    assert llm_model_check._instance_urls("http://127.0.0.1:11447") == [
        "http://127.0.0.1:11447",
        "http://127.0.0.1:11445",
    ]
    llm(DOCKER)
    assert llm_model_check._instance_urls("http://ollama:11434") == ["http://ollama:11434"]


def test_model_lock_imports_cleanly_before_config():
    """config → tools.definitions → model_lock is a cycle; model_lock must not import config
    at module level, or importing it first breaks the tool-permission check."""
    r = subprocess.run(
        [sys.executable, "-c", "import kaare_core.model_lock"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd="/kaare",
        env={"PYTHONPATH": "/kaare", "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stderr
    assert "circular import" not in r.stderr and "Traceback" not in r.stderr, r.stderr
